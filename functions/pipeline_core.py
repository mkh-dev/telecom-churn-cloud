"""Logique métier pure du pipeline (aucune dépendance Azure), testée avec pytest.

Formats conformes à docs/contrats.md :
  §1 fichier CSV déposé dans input/   §2 API POST /predict
  §3 message de la file               §4 document Cosmos DB
`function_app.py` ne fait que brancher ces fonctions sur les déclencheurs Azure.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------------------
# Contrat §1 : colonnes du CSV (identiques à model/churn_model.py INPUT_COLUMNS)
# --------------------------------------------------------------------------

ID_COLUMN = "customerID"
LABEL_COLUMN = "Churn"  # interdite par le contrat : retirée si présente
INPUT_COLUMNS = (
    "customerID", "gender", "SeniorCitizen", "Partner", "Dependents", "tenure",
    "PhoneService", "MultipleLines", "InternetService", "OnlineSecurity",
    "OnlineBackup", "DeviceProtection", "TechSupport", "StreamingTV",
    "StreamingMovies", "Contract", "PaperlessBilling", "PaymentMethod",
    "MonthlyCharges", "TotalCharges",
)  # fmt: skip
INT_COLUMNS = ("SeniorCitizen", "tenure")
FLOAT_COLUMNS = ("MonthlyCharges", "TotalCharges")
NULLABLE_COLUMNS = ("TotalCharges",)  # vide pour tenure = 0 : le modèle l'impute

# Seuils de risque du contrat §2 (mêmes valeurs que churn_model.HIGH_RISK / MEDIUM_RISK)
HIGH_RISK, MEDIUM_RISK = 0.7, 0.4

INPUT_CONTAINER = "input"
SUBJECT_RE = re.compile(r"^/blobServices/default/containers/(?P<container>[^/]+)/blobs/(?P<name>.+)$")
COSMOS_FORBIDDEN = re.compile(r"[/\\?#]")


def _csv_env(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(v.strip() for v in value.split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    allowed_extensions: tuple[str, ...] = (".csv",)
    max_bytes: int = 1_000_000  # contrat §1 : 1 Mo maximum
    max_rows: int = 1_000  # contrat §1 : 1 à 1 000 lignes
    required_columns: tuple[str, ...] = INPUT_COLUMNS
    top_risk: int = 5

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        return cls(
            allowed_extensions=_csv_env(env.get("ALLOWED_EXTENSIONS"), (".csv",)),
            max_bytes=int(env.get("MAX_FILE_BYTES", 1_000_000)),
            max_rows=int(env.get("MAX_ROWS", 1_000)),
            required_columns=_csv_env(env.get("REQUIRED_COLUMNS"), INPUT_COLUMNS),
            top_risk=int(env.get("TOP_RISK", 5)),
        )


# --------------------------------------------------------------------------
# Erreurs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rejection:
    """Motif de rejet : code ASCII (métadonnée du blob) + message lisible."""

    code: str
    message: str


class PoisonMessageError(ValueError):
    """Message de file illisible : retenté puis envoyé dans la file poison."""


class StaleMessageError(Exception):
    """Le blob a été remplacé depuis l'événement (ETag différent) : un nouvel
    événement traitera la nouvelle version, ce message est ignoré."""


class ApiError(RuntimeError):
    """L'API de prédiction n'a pas répondu correctement (=> nouvelle tentative)."""


# --------------------------------------------------------------------------
# Dispatcher : événement Event Grid et validation du fichier
# --------------------------------------------------------------------------


def parse_subject(subject: str) -> tuple[str, str]:
    """'/blobServices/default/containers/input/blobs/a/b.csv' -> ('input', 'a/b.csv')."""
    m = SUBJECT_RE.match(subject or "")
    if not m:
        raise ValueError(f"Sujet Event Grid inattendu : {subject!r}")
    return m.group("container"), m.group("name")


def check_metadata(blob_name: str, size: int | None, settings: Settings) -> Rejection | None:
    """Contrôles bon marché faits AVANT de télécharger le fichier."""
    lower = blob_name.lower()
    if not any(lower.endswith(ext) for ext in settings.allowed_extensions):
        ext = ("." + lower.rsplit(".", 1)[-1]) if "." in lower.rsplit("/", 1)[-1] else "(aucune)"
        allowed = ", ".join(settings.allowed_extensions)
        return Rejection("bad_extension", f"Extension {ext} refusée, attendu : {allowed}")
    if size is not None:
        if size == 0:
            return Rejection("empty_file", "Fichier vide")
        if size > settings.max_bytes:
            return Rejection("too_large", f"Taille {size} o > maximum {settings.max_bytes} o")
    return None


def _to_number(value: Any, column: str) -> int | float | None:
    value = (value or "").strip() if isinstance(value, str) else value
    if value in ("", None):
        if column in NULLABLE_COLUMNS:
            return None
        raise ValueError("valeur vide")
    return int(float(value)) if column in INT_COLUMNS else float(value)


def parse_and_validate(content: bytes, settings: Settings) -> tuple[list[dict[str, Any]] | None, Rejection | None]:
    """Vérifie encodage, schéma (en-tête), nombre de lignes et types numériques.

    Retourne (clients typés prêts pour /predict, None) ou (None, motif de rejet).
    """
    if len(content) > settings.max_bytes:
        return None, Rejection("too_large", f"Taille {len(content)} o > maximum {settings.max_bytes} o")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, Rejection("bad_encoding", f"Le fichier n'est pas en UTF-8 (octet {exc.start})")
    if not text.strip():
        return None, Rejection("empty_file", "Fichier vide")
    if "\x00" in text:
        return None, Rejection("binary_content", "Contenu binaire détecté")

    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in settings.required_columns if c not in header]
    if missing:
        hint = " (le séparateur doit être la virgule)" if ";" in text.splitlines()[0] else ""
        return None, Rejection("bad_schema", f"Colonnes manquantes : {', '.join(missing)}{hint}")

    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(reader, start=2):
        if None in raw:  # plus de champs que de colonnes
            return None, Rejection("bad_row", f"Ligne {line_no} : trop de champs")
        row = {(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        if not any(row.values()):
            continue  # ligne vide ignorée
        if len(rows) >= settings.max_rows:
            return None, Rejection("too_many_rows", f"Plus de {settings.max_rows} lignes")
        if not row.get(ID_COLUMN):
            return None, Rejection("bad_value", f"Ligne {line_no} : {ID_COLUMN} vide")
        for col in (*INT_COLUMNS, *FLOAT_COLUMNS):
            try:
                row[col] = _to_number(row.get(col), col)
            except (ValueError, TypeError):
                return None, Rejection("bad_value", f"Ligne {line_no} : {col}={row.get(col)!r} n'est pas un nombre")
        row.pop(LABEL_COLUMN, None)
        rows.append({c: row[c] for c in INPUT_COLUMNS if c in row})

    if not rows:
        return None, Rejection("no_rows", "Aucune ligne de données")
    return rows, None


def build_queue_message(blob_name: str, etag: str, size: int, rows: int, timestamp: str | None) -> dict[str, Any]:
    """Contrat §3 : le « ticket » posé dans inference-requests."""
    return {
        "blobName": f"{INPUT_CONTAINER}/{blob_name}",
        "etag": clean_etag(etag),
        "size": size,
        "rows": rows,
        "timestamp": iso_z(timestamp) if timestamp else utc_now(),
    }


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------


def parse_queue_message(text: str) -> dict[str, Any]:
    try:
        msg = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PoisonMessageError(f"Message non JSON : {exc}") from exc
    if not isinstance(msg, dict):
        raise PoisonMessageError("Le message doit être un objet JSON")
    for key in ("blobName", "etag"):
        if not msg.get(key) or not isinstance(msg[key], str):
            raise PoisonMessageError(f"Champ obligatoire manquant : {key}")
    return msg


def file_name_of(blob_name: str) -> str:
    """'input/clients.csv' -> 'clients.csv' (nom relatif au conteneur input)."""
    prefix = f"{INPUT_CONTAINER}/"
    return blob_name[len(prefix) :] if blob_name.startswith(prefix) else blob_name


def clean_etag(etag: str) -> str:
    return (etag or "").strip().strip('"')


def inference_id(file_name: str, etag: str) -> str:
    """Contrat §4 : id FIXE = <nom du fichier>_<ETag>. Rejouer un message
    réécrit le même document (upsert) au lieu de créer un doublon."""
    raw = f"{COSMOS_FORBIDDEN.sub('_', file_name)}_{clean_etag(etag)}"
    if len(raw) <= 250:
        return raw
    return hashlib.sha256(f"{file_name}|{etag}".encode()).hexdigest()


def output_blob_name(file_name: str, etag: str) -> str:
    return f"{inference_id(file_name, etag)}.json"


def risk_level(probability: float) -> str:
    if probability >= HIGH_RISK:
        return "élevé"
    if probability >= MEDIUM_RISK:
        return "moyen"
    return "faible"


def normalize_prediction(obj: Any, customer_id: str) -> dict[str, Any]:
    """Vérifie une prédiction de l'API (contrat §2) et complète risk_level si absent."""
    if not isinstance(obj, Mapping):
        raise ApiError(f"Prédiction illisible : {obj!r}")
    proba = obj.get("churn_probability")
    if proba is None:
        raise ApiError(f"churn_probability absent : {obj!r}")
    proba = float(proba)
    if not 0.0 <= proba <= 1.0:
        raise ApiError(f"Probabilité hors [0,1] : {proba}")
    pred = obj.get("churn_prediction")
    pred = int(proba >= 0.5) if pred is None else int(pred)
    returned_id = obj.get(ID_COLUMN)
    if returned_id is not None and str(returned_id) != str(customer_id):
        raise ApiError(f"Ordre des prédictions incohérent : {returned_id} au lieu de {customer_id}")
    return {
        ID_COLUMN: str(customer_id),
        "churn_prediction": pred,
        "churn_probability": round(proba, 4),
        "risk_level": obj.get("risk_level") or risk_level(proba),
    }


# Client API injecté : corps de la requête -> (réponse JSON, latence aller-retour en ms)
PredictFn = Callable[[dict[str, Any]], tuple[Any, float]]


def run_predictions(customers: list[dict[str, Any]], predict: PredictFn) -> dict[str, Any]:
    """Un seul appel POST /predict par fichier (lot de 1 à 1 000 clients)."""
    body, roundtrip_ms = predict({"customers": customers})
    if not isinstance(body, Mapping) or not isinstance(body.get("predictions"), list):
        raise ApiError("Réponse de l'API sans liste 'predictions'")
    preds = body["predictions"]
    if len(preds) != len(customers):
        raise ApiError(f"{len(preds)} prédictions reçues pour {len(customers)} clients")
    results = [normalize_prediction(p, c[ID_COLUMN]) for c, p in zip(customers, preds, strict=True)]
    return {
        "predictions": results,
        "modelVersion": str(body.get("model_version") or "inconnue"),
        "modelLatencyMs": body.get("latency_ms"),
        "apiLatencyMs": round(roundtrip_ms, 1),
    }


def summarize(results: list[dict[str, Any]], top_n: int) -> dict[str, Any]:
    n = len(results)
    n_churn = sum(r["churn_prediction"] for r in results)
    n_high = sum(1 for r in results if r["risk_level"] == "élevé")
    top = sorted(results, key=lambda r: r["churn_probability"], reverse=True)[:top_n]
    return {
        "nCustomers": n,
        "nHighRisk": n_high,
        "nMediumRisk": sum(1 for r in results if r["risk_level"] == "moyen"),
        "nChurn": n_churn,
        "churnRate": round(n_churn / n, 4) if n else 0.0,
        "avgProbability": round(sum(r["churn_probability"] for r in results) / n, 4) if n else 0.0,
        "topRisk": [{ID_COLUMN: r[ID_COLUMN], "churn_probability": r["churn_probability"]} for r in top],
    }


@dataclass
class WorkerDeps:
    """Dépendances injectées (vraies en production, factices dans les tests)."""

    read_blob: Callable[[str, str], bytes]  # (nom dans input/, etag) -> contenu
    predict: PredictFn
    write_output: Callable[[str, dict[str, Any]], None]  # (nom dans output/, document)
    upsert: Callable[[dict[str, Any]], None]  # document Cosmos DB
    on_metrics: Callable[[dict[str, Any]], None] = field(default=lambda m: None)
    clock: Callable[[], float] = time.perf_counter


def process_message(text: str, deps: WorkerDeps, settings: Settings) -> dict[str, Any]:
    """Traite un ticket de bout en bout. Toute exception => nouvelle tentative,
    puis file poison après maxDequeueCount (5) échecs. Rien n'est écrit tant que
    l'API n'a pas répondu correctement pour tout le lot."""
    start = deps.clock()
    msg = parse_queue_message(text)
    file_name, etag = file_name_of(msg["blobName"]), clean_etag(msg["etag"])

    content = deps.read_blob(file_name, etag)  # peut lever StaleMessageError
    customers, rejection = parse_and_validate(content, settings)
    if rejection:  # déjà validé par le dispatcher : ne devrait pas arriver
        raise PoisonMessageError(f"Fichier invalide au moment du traitement : {rejection.message}")

    api = run_predictions(customers, deps.predict)
    summary = summarize(api["predictions"], settings.top_risk)
    doc_id = inference_id(file_name, etag)
    out_name = output_blob_name(file_name, etag)
    uploaded_at = msg.get("timestamp") or utc_now()
    processed_at = utc_now()
    duration_ms = round((deps.clock() - start) * 1000, 1)

    deps.write_output(
        out_name,
        {
            "id": doc_id,
            "fileName": file_name,
            "etag": etag,
            "uploadedAt": uploaded_at,
            "timestamp": processed_at,
            "modelVersion": api["modelVersion"],
            "summary": summary,
            "predictions": api["predictions"],
        },
    )  # overwrite => idempotent

    # Contrat §4 (+ champs complémentaires pour le tableau de bord)
    doc = {
        "id": doc_id,
        # Jour du DÉPÔT (et non du traitement) : un message rejoué après minuit
        # retombe dans la même partition, donc sur le même document.
        "day": uploaded_at[:10],
        "fileName": file_name,
        "timestamp": processed_at,
        "nCustomers": summary["nCustomers"],
        "nHighRisk": summary["nHighRisk"],
        "churnRate": summary["churnRate"],
        "modelVersion": api["modelVersion"],
        "durationMs": duration_ms,
        "status": "ok",
        "nMediumRisk": summary["nMediumRisk"],
        "nChurn": summary["nChurn"],
        "avgProbability": summary["avgProbability"],
        "topRisk": summary["topRisk"],
        "apiLatencyMs": api["apiLatencyMs"],
        "modelLatencyMs": api["modelLatencyMs"],
        "uploadedAt": uploaded_at,
        "etag": etag,
        "outputBlob": f"output/{out_name}",
    }
    deps.upsert(doc)  # upsert => idempotent

    deps.on_metrics(
        {
            "fileName": file_name,
            "rows": len(customers),
            "apiLatencyMs": api["apiLatencyMs"],
            "durationMs": duration_ms,
            "nHighRisk": summary["nHighRisk"],
            "modelVersion": api["modelVersion"],
        }
    )
    return doc


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_z(value: str) -> str:
    """Normalise un horodatage ISO (Event Grid : 7 décimales) en '2026-11-25T10:42:07Z'."""
    try:
        v = value.strip().replace("Z", "+00:00")
        v = re.sub(r"(\.\d{6})\d+", r"\1", v)  # Python n'accepte que 6 décimales
        return datetime.fromisoformat(v).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        return utc_now()
