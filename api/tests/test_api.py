import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.main import app
from app.schemas import MAX_CUSTOMERS

API_DIR = Path(__file__).resolve().parents[1]


def batch(customer: dict, n: int) -> dict:
    return {"customers": [{**customer, "customerID": f"C-{i:05d}"} for i in range(n)]}


# --- POST /predict ---------------------------------------------------------------------------


def test_predict_success(client, sample_customers):
    r = client.post("/predict", json={"customers": sample_customers})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"predictions", "model_version", "latency_ms"}
    assert body["model_version"] == "0.1.0"
    assert body["latency_ms"] >= 0
    assert [p["customerID"] for p in body["predictions"]] == [c["customerID"] for c in sample_customers]
    for p in body["predictions"]:
        assert 0.0 <= p["churn_probability"] <= 1.0
        assert isinstance(p["churn"], bool)
        assert p["churn_prediction"] == int(p["churn"])
        assert p["risk_level"] in {"faible", "moyen", "élevé"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Contract", "Three year"),
        ("tenure", -1),
        ("tenure", 1.5),
        ("gender", "male"),
        ("SeniorCitizen", 2),
        ("MonthlyCharges", -10),
        ("TotalCharges", "abc"),
        ("customerID", ""),
    ],
)
def test_predict_invalid_field(client, customer, field, value):
    customer[field] = value
    r = client.post("/predict", json={"customers": [customer]})
    assert r.status_code == 422
    assert field in str(r.json()["detail"])


def test_predict_missing_field(client, customer):
    del customer["Contract"]
    assert client.post("/predict", json={"customers": [customer]}).status_code == 422


def test_predict_without_body(client):
    assert client.post("/predict").status_code == 422


@pytest.mark.parametrize("payload", [{}, {"customers": []}, {"customers": "x"}, []])
def test_predict_malformed_body(client, payload):
    assert client.post("/predict", json=payload).status_code == 422


def test_predict_too_many_customers(client, customer):
    r = client.post("/predict", json=batch(customer, MAX_CUSTOMERS + 1))
    assert r.status_code == 422


@pytest.mark.parametrize("total_charges", ["", " ", None])
def test_predict_blank_total_charges(client, customer, total_charges):
    customer["TotalCharges"] = total_charges
    r = client.post("/predict", json={"customers": [customer]})
    assert r.status_code == 200
    assert 0.0 <= r.json()["predictions"][0]["churn_probability"] <= 1.0


def test_predict_accepts_csv_strings(client, customer):
    # Valeurs lues par csv.DictReader côté worker : tout arrive en chaînes
    customer.update(SeniorCitizen="0", tenure="1", MonthlyCharges="29.85", TotalCharges="29.85")
    assert client.post("/predict", json={"customers": [customer]}).status_code == 200


def test_predict_latency_max_batch(client, customer):
    payload = batch(customer, MAX_CUSTOMERS)
    t0 = time.perf_counter()
    r = client.post("/predict", json=payload)
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    assert len(r.json()["predictions"]) == MAX_CUSTOMERS
    assert r.json()["latency_ms"] < 1000  # inférence < 1 s
    assert elapsed < 2.0  # réponse complète < 2 s (cahier des charges)


def test_risk_threshold_from_env(monkeypatch, customer):
    monkeypatch.setenv("RISK_THRESHOLD", "0")
    with TestClient(app) as c:
        p = c.post("/predict", json={"customers": [customer]}).json()["predictions"][0]
    assert p["churn"] is True
    assert p["churn_prediction"] == 1


# --- GET /health, /version, /metrics ---------------------------------------------------------


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["model_load_time_ms"] > 0


def test_health_without_model(monkeypatch, customer, tmp_path):
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "absent.joblib"))
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 503
        assert r.json() == {"status": "error", "model_loaded": False, "model_load_time_ms": 0.0}
        assert c.post("/predict", json={"customers": [customer]}).status_code == 503


def test_version(client):
    assert client.get("/version").json() == {"api_version": __version__, "model_version": "0.1.0"}


def test_model_version_from_env(monkeypatch):
    # Bonus A/B : une 2e révision ne change que MODEL_PATH / MODEL_VERSION
    monkeypatch.setenv("MODEL_VERSION", "0.1.0-b")
    with TestClient(app) as c:
        assert c.get("/version").json()["model_version"] == "0.1.0-b"


def test_metrics_increment(client, sample_customers, customer):
    assert client.get("/metrics").json() == {"calls": 0, "avg_latency_ms": 0.0, "customers_scored": 0, "errors": 0}

    client.post("/predict", json={"customers": sample_customers})
    client.post("/predict", json={"customers": [customer]})
    client.post("/predict", json={"customers": []})

    m = client.get("/metrics").json()
    assert m["calls"] == 2
    assert m["customers_scored"] == len(sample_customers) + 1
    assert m["errors"] == 1
    assert m["avg_latency_ms"] > 0


# --- Compatibilité avec le modèle du membre 1 ------------------------------------------------


def pinned(path: Path) -> dict[str, str]:
    pins = re.findall(r"^([A-Za-z0-9_-]+)==(\S+)", path.read_text(), flags=re.MULTILINE)
    return {name.lower(): version for name, version in pins}


def test_ml_versions_match_model_requirements():
    model_reqs = API_DIR.parent / "model" / "requirements.txt"
    if not model_reqs.exists():
        pytest.skip("model/requirements.txt absent (build Docker)")
    api, model = pinned(API_DIR / "requirements.txt"), pinned(model_reqs)
    for lib in ("scikit-learn", "numpy", "pandas", "joblib"):
        assert api[lib] == model[lib], f"{lib} : api {api[lib]} != model {model[lib]}"
