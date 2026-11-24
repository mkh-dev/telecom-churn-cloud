"""Fausse API de prédiction (contrat §2), pour développer le pipeline sans l'API du membre 2.

Si scikit-learn est installé, elle sert le VRAI modèle (model/artifacts/model-1.0.0.joblib
via model/churn_model.predict_batch) ; sinon une heuristique simple.

    python tools/mock_api.py               # http://localhost:8000
    MOCK_FAIL=1 python tools/mock_api.py   # répond 503 -> tester reprises + file poison
    MOCK_DELAY=15 python tools/mock_api.py # répond en 15 s -> tester le délai d'expiration
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FAIL = os.environ.get("MOCK_FAIL") == "1"
DELAY = float(os.environ.get("MOCK_DELAY", "0"))
ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "model" / "artifacts" / "model-1.0.0.joblib"

try:
    sys.path.insert(0, str(ROOT / "model"))
    from churn_model import load_model, predict_batch

    MODEL = load_model(str(MODEL_PATH))
    VERSION = "1.0.0"
except Exception as exc:  # scikit-learn absent : heuristique
    print(f"Modèle réel indisponible ({exc.__class__.__name__}), heuristique utilisée")
    MODEL, VERSION = None, "mock-0.0.1"


def heuristic(customers: list[dict]) -> list[dict]:
    out = []
    for c in customers:
        p = 0.15
        p += 0.35 if c.get("Contract") == "Month-to-month" else 0
        p += 0.2 if float(c.get("tenure") or 0) < 12 else 0
        p += 0.15 if c.get("InternetService") == "Fiber optic" else 0
        p = round(min(p, 0.99), 4)
        level = "élevé" if p >= 0.7 else "moyen" if p >= 0.4 else "faible"
        out.append({"customerID": c.get("customerID"), "churn_prediction": int(p >= 0.5),
                    "churn_probability": p, "risk_level": level})  # fmt: skip
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok", "model_loaded": MODEL is not None})
        if self.path == "/version":
            return self._send(200, {"api_version": "mock", "model_version": VERSION})
        self._send(404, {"detail": "Not Found"})

    def do_POST(self):
        if self.path != "/predict":
            return self._send(404, {"detail": "Not Found"})
        if DELAY:
            time.sleep(DELAY)
        if FAIL:
            return self._send(503, {"detail": "MOCK_FAIL=1"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
            customers = body["customers"]
            assert isinstance(customers, list) and 1 <= len(customers) <= 1000
        except Exception:
            return self._send(422, {"detail": 'Corps attendu : {"customers": [1 à 1000 clients]}'})
        t0 = time.perf_counter()
        preds = predict_batch(MODEL, customers) if MODEL is not None else heuristic(customers)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        self._send(200, {"model_version": VERSION, "latency_ms": ms, "predictions": preds})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Fausse API sur http://localhost:{port} (modèle {VERSION}, FAIL={FAIL}, DELAY={DELAY}s)")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
