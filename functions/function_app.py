"""Function App du pipeline (Python 3.12, modèle de programmation v2, Flex Consumption).

Trois fonctions dans une seule application :
  dispatcher : Event Grid (BlobCreated sur input/)  -> valide -> file inference-requests ou rejected/
  worker     : file inference-requests -> API /predict -> output/ + Cosmos DB (idempotent)
  recent     : GET /api/recent -> 20 dernières inférences (pour le tableau de bord)

Reprises : host.json -> maxDequeueCount = 5, puis inference-requests-poison.
"""

from __future__ import annotations

import json
import logging
import os
import time

import azure.functions as func
import requests
from requests.adapters import HTTPAdapter

import stores
from pipeline_core import (
    ApiError,
    Settings,
    StaleMessageError,
    WorkerDeps,
    build_queue_message,
    check_metadata,
    parse_and_validate,
    parse_subject,
    process_message,
)

app = func.FunctionApp()
log = logging.getLogger("pipeline")
SETTINGS = Settings.from_env(os.environ)

# --------------------------------------------------------------------------
# Télémétrie : métriques personnalisées vers Application Insights (OpenTelemetry)
# -> table customMetrics : pipeline.inferences, pipeline.model_latency_ms, ...
# --------------------------------------------------------------------------


class _Metrics:
    def __init__(self) -> None:
        self.enabled = False
        if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
            return
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
            from opentelemetry import metrics

            # logger_name : seuls les logs "pipeline" passent par OTel (pas de doublon
            # avec la journalisation déjà faite par l'hôte Functions).
            configure_azure_monitor(logger_name="pipeline.otel")
            meter = metrics.get_meter("pipeline.worker")
            self.inferences = meter.create_counter(
                "pipeline.inferences", unit="1", description="Fichiers traités par le worker"
            )
            self.customers = meter.create_counter(
                "pipeline.customers_scored", unit="1", description="Clients évalués par le modèle"
            )
            self.latency = meter.create_histogram(
                "pipeline.model_latency_ms", unit="ms", description="Latence de l'appel à l'API /predict"
            )
            self.duration = meter.create_histogram(
                "pipeline.processing_ms", unit="ms", description="Durée totale du traitement d'un fichier"
            )
            self.errors = meter.create_counter(
                "pipeline.errors", unit="1", description="Erreurs du worker (avant nouvelle tentative)"
            )
            self.rejected = meter.create_counter(
                "pipeline.rejected", unit="1", description="Fichiers rejetés par le dispatcher"
            )
            self.enabled = True
        except Exception:  # la télémétrie ne doit jamais casser le pipeline
            log.exception("Télémétrie OpenTelemetry désactivée")

    def done(self, m: dict) -> None:
        if self.enabled:
            attrs = {"model_version": m.get("modelVersion") or "unknown"}
            self.inferences.add(1, attrs)
            self.customers.add(m["rows"], attrs)
            self.latency.record(m["apiLatencyMs"], attrs)
            self.duration.record(m["durationMs"], attrs)

    def error(self, kind: str) -> None:
        if self.enabled:
            self.errors.add(1, {"error_type": kind})

    def reject(self, code: str) -> None:
        if self.enabled:
            self.rejected.add(1, {"reason": code})


METRICS = _Metrics()

# --------------------------------------------------------------------------
# Client HTTP réutilisé (créé une seule fois par instance, pas à chaque message)
# --------------------------------------------------------------------------

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
API_PREDICT_PATH = os.environ.get("API_PREDICT_PATH", "/predict")
# Délai max par appel. Le premier appel après une mise à l'échelle à 0 de Container Apps
# peut prendre plusieurs secondes (démarrage à froid) : s'il expire, la file réessaie.
API_TIMEOUT = float(os.environ.get("API_TIMEOUT_SECONDS", "20"))

HTTP = requests.Session()
HTTP.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=0))
HTTP.mount("http://", HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=0))
HTTP.headers.update({"User-Agent": "pipeline-worker/1.0", "Accept": "application/json"})


def call_api(payload: dict) -> tuple[object, float]:
    t0 = time.perf_counter()
    try:
        resp = HTTP.post(f"{API_URL}{API_PREDICT_PATH}", json=payload, timeout=(5, API_TIMEOUT))
    except requests.RequestException as exc:  # délai dépassé, API coupée, DNS...
        raise ApiError(f"API injoignable : {exc.__class__.__name__}: {exc}") from exc
    ms = (time.perf_counter() - t0) * 1000
    if resp.status_code >= 400:
        raise ApiError(f"API HTTP {resp.status_code} : {resp.text[:300]}")
    try:
        return resp.json(), ms
    except ValueError as exc:
        raise ApiError("Réponse de l'API non JSON") from exc


# --------------------------------------------------------------------------
# 1) dispatcher — déclencheur Event Grid
# --------------------------------------------------------------------------


@app.function_name(name="dispatcher")
@app.event_grid_trigger(arg_name="event")
@app.queue_output(arg_name="ticket", queue_name="inference-requests", connection=stores.CONNECTION)
def dispatcher(event: func.EventGridEvent, ticket: func.Out[str]) -> None:
    data = event.get_json() or {}
    container, blob_name = parse_subject(event.subject)
    if container != stores.INPUT:  # sécurité : l'abonnement est déjà filtré sur input/
        log.info("Ignoré (conteneur %s) : %s", container, blob_name)
        return

    etag = data.get("eTag") or data.get("etag") or ""
    size = data.get("contentLength")
    log.info("DISPATCH_RECEIVED %s", json.dumps({"fileName": blob_name, "size": size, "eventId": event.id}))

    def _reject(code: str, message: str, content: bytes | None) -> None:
        stores.reject(blob_name, content, code, message, etag)
        METRICS.reject(code)
        log.warning(
            "DISPATCH_REJECTED %s",
            json.dumps({"fileName": blob_name, "reasonCode": code, "reason": message}, ensure_ascii=False),
        )

    # 1. Contrôles bon marché sur les métadonnées de l'événement (extension, taille)
    rejection = check_metadata(blob_name, size, SETTINGS)
    if rejection:
        # On ne copie le contenu que s'il est raisonnable (évite de recopier un énorme fichier)
        content = None
        if (size or 0) <= 10 * SETTINGS.max_bytes:
            try:
                content = stores.read_input(blob_name)
            except Exception:
                log.exception("Lecture impossible pour copie dans rejected/")
        _reject(rejection.code, rejection.message, content)
        return

    # 2. Contrôles sur le contenu (encodage UTF-8, schéma CSV, types)
    try:
        content = stores.read_input(blob_name, etag or None)
    except StaleMessageError:
        log.info("Fichier remplacé entre-temps, événement ignoré : %s", blob_name)
        return
    rows, rejection = parse_and_validate(content, SETTINGS)
    if rejection:
        _reject(rejection.code, rejection.message, content)
        return

    # 3. Fichier valide -> ticket dans la file
    event_time = event.event_time.isoformat() if event.event_time else None
    msg = build_queue_message(blob_name, etag, size if size is not None else len(content), len(rows), event_time)
    ticket.set(json.dumps(msg))
    log.info("DISPATCH_QUEUED %s", json.dumps(msg))


# --------------------------------------------------------------------------
# 2) worker — déclencheur file d'attente (5 tentatives puis file poison)
# --------------------------------------------------------------------------


def _worker_deps() -> WorkerDeps:
    store = stores.result_store()
    return WorkerDeps(
        read_blob=stores.read_input,
        predict=call_api,
        write_output=lambda name, doc: stores.write_json(stores.OUTPUT, name, doc),
        upsert=store.upsert,
        on_metrics=METRICS.done,
    )


@app.function_name(name="worker")
@app.queue_trigger(arg_name="qmsg", queue_name="inference-requests", connection=stores.CONNECTION)
def worker(qmsg: func.QueueMessage) -> None:
    text = qmsg.get_body().decode("utf-8", errors="replace")
    attempt = qmsg.dequeue_count
    try:
        meta = process_message(text, _worker_deps(), SETTINGS)
    except StaleMessageError as exc:
        log.info("WORKER_SKIPPED_STALE %s", exc)  # une version plus récente sera traitée
        return
    except Exception as exc:
        kind = type(exc).__name__
        METRICS.error(kind)
        last = attempt >= 5
        log.error(
            "WORKER_FAILED %s",
            json.dumps(
                {
                    "messageId": qmsg.id,
                    "attempt": attempt,
                    "goingToPoison": last,
                    "errorType": kind,
                    "error": str(exc)[:500],
                },
                ensure_ascii=False,
            ),
        )
        raise  # => le runtime remet le message dans la file (nouvelle tentative / poison)

    log.info(
        "INFERENCE_DONE %s",
        json.dumps(
            {
                "fileName": meta["fileName"],
                "id": meta["id"],
                "attempt": attempt,
                "nCustomers": meta["nCustomers"],
                "nHighRisk": meta["nHighRisk"],
                "apiLatencyMs": meta["apiLatencyMs"],
                "modelLatencyMs": meta["modelLatencyMs"],
                "durationMs": meta["durationMs"],
                "modelVersion": meta["modelVersion"],
            }
        ),
    )


# --------------------------------------------------------------------------
# 3) recent — GET /api/recent (CORS limité à l'origine du site, réglé sur la Function App)
# --------------------------------------------------------------------------


@app.function_name(name="recent")
@app.route(route="recent", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def recent(req: func.HttpRequest) -> func.HttpResponse:
    try:
        limit = max(1, min(int(req.params.get("limit", "20")), 50))
    except ValueError:
        limit = 20
    try:
        items = stores.result_store().recent(limit)
    except Exception:
        log.exception("Lecture des résultats impossible")
        return func.HttpResponse(
            json.dumps({"error": "store_unavailable"}), status_code=503, mimetype="application/json"
        )
    body = {"count": len(items), "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "items": items}
    return func.HttpResponse(
        json.dumps(body, ensure_ascii=False), mimetype="application/json", headers={"Cache-Control": "no-store"}
    )
