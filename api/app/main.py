"""API FastAPI d'inférence du churn : POST /predict, GET /health, /version, /metrics."""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__, telemetry
from app.model_loader import ChurnModel, risk_level
from app.schemas import Health, Metrics, Prediction, PredictRequest, PredictResponse, Version
from app.settings import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
logger = logging.getLogger("churn-api")


class CallStats:
    """Compteurs en mémoire de /predict (remis à zéro à chaque redémarrage du conteneur)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.errors = 0
        self.customers_scored = 0
        self._total_latency_ms = 0.0

    def record_success(self, n_customers: int, latency_ms: float) -> None:
        with self._lock:
            self.calls += 1
            self.customers_scored += n_customers
            self._total_latency_ms += latency_ms

    def record_error(self) -> None:
        with self._lock:
            self.errors += 1

    def snapshot(self) -> Metrics:
        with self._lock:
            avg = self._total_latency_ms / self.calls if self.calls else 0.0
            return Metrics(
                calls=self.calls,
                avg_latency_ms=round(avg, 2),
                customers_scored=self.customers_scored,
                errors=self.errors,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.settings = settings
    app.state.stats = CallStats()
    app.state.model = None
    try:
        app.state.model = ChurnModel.load(settings.model_path, settings.model_version)
        logger.info(
            "Modèle %s chargé depuis %s en %.0f ms",
            settings.model_version,
            settings.model_path,
            app.state.model.load_time_ms,
        )
    except Exception:
        # L'API démarre quand même : /health renvoie 503 et la sonde readiness bloque le trafic
        logger.exception("Impossible de charger le modèle %s", settings.model_path)
    yield


# Avant la création de l'application, pour que l'instrumentation FastAPI trace les requêtes
telemetry.setup(os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"))

app = FastAPI(title="Telecom Churn API", version=__version__, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def count_validation_errors(request: Request, exc: RequestValidationError):
    if request.url.path == "/predict":
        request.app.state.stats.record_error()
    return await request_validation_exception_handler(request, exc)


@app.post("/predict", response_model=PredictResponse)
def predict(body: PredictRequest, request: Request) -> PredictResponse:
    state = request.app.state
    model: ChurnModel | None = state.model
    if model is None:
        state.stats.record_error()
        raise HTTPException(status_code=503, detail="Modèle non chargé")

    t0 = time.perf_counter()
    try:
        probas = model.predict_proba(body.customers)
    except Exception:
        state.stats.record_error()
        raise
    model_ms = (time.perf_counter() - t0) * 1000

    threshold = state.settings.risk_threshold
    predictions = [
        Prediction(
            customerID=customer.customerID,
            churn_probability=round(p, 4),
            churn=p >= threshold,
            churn_prediction=int(p >= threshold),
            risk_level=risk_level(p),
        )
        for customer, p in zip(body.customers, probas, strict=True)
    ]
    latency_ms = (time.perf_counter() - t0) * 1000

    state.stats.record_success(len(predictions), latency_ms)
    telemetry.customers_scored.add(len(predictions), {"model_version": model.version})
    telemetry.model_latency.record(model_ms, {"model_version": model.version})
    return PredictResponse(predictions=predictions, model_version=model.version, latency_ms=round(latency_ms, 2))


@app.get("/health", response_model=Health)
def health(request: Request):
    model: ChurnModel | None = request.app.state.model
    if model is None:
        body = Health(status="error", model_loaded=False, model_load_time_ms=0.0)
        return JSONResponse(status_code=503, content=body.model_dump())
    return Health(status="ok", model_loaded=True, model_load_time_ms=round(model.load_time_ms, 2))


@app.get("/version", response_model=Version)
def version(request: Request) -> Version:
    return Version(api_version=__version__, model_version=request.app.state.settings.model_version)


@app.get("/metrics", response_model=Metrics)
def get_metrics(request: Request) -> Metrics:
    return request.app.state.stats.snapshot()
