"""Chargement du Pipeline scikit-learn exporté par le membre 1 et prédiction par lot."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd

from app.schemas import Customer

NUMERIC_FEATURES = ["SeniorCitizen", "tenure", "MonthlyCharges", "TotalCharges"]
CATEGORICAL_FEATURES = [
    "gender",
    "Partner",
    "Dependents",
    "PhoneService",
    "MultipleLines",
    "InternetService",
    "OnlineSecurity",
    "OnlineBackup",
    "DeviceProtection",
    "TechSupport",
    "StreamingTV",
    "StreamingMovies",
    "Contract",
    "PaperlessBilling",
    "PaymentMethod",
]

# Mêmes seuils que model/churn_model.py
HIGH_RISK = 0.7
MEDIUM_RISK = 0.4


def risk_level(probability: float) -> str:
    if probability >= HIGH_RISK:
        return "élevé"
    if probability >= MEDIUM_RISK:
        return "moyen"
    return "faible"


@dataclass
class ChurnModel:
    pipeline: object
    version: str
    load_time_ms: float

    @classmethod
    def load(cls, path: Path, version: str) -> ChurnModel:
        t0 = time.perf_counter()
        pipeline = joblib.load(path)
        if not hasattr(pipeline, "predict_proba"):
            raise TypeError(f"{path} ne contient pas de modèle avec predict_proba")
        return cls(pipeline, version, (time.perf_counter() - t0) * 1000)

    @property
    def features(self) -> list[str]:
        # Colonnes vues à l'entraînement, dans le même ordre ; sinon les 19 colonnes du contrat
        names = getattr(self.pipeline, "feature_names_in_", None)
        return list(names) if names is not None else NUMERIC_FEATURES + CATEGORICAL_FEATURES

    def predict_proba(self, customers: list[Customer]) -> list[float]:
        df = pd.DataFrame([c.model_dump() for c in customers])
        # TotalCharges None -> NaN, imputé par le Pipeline
        df[NUMERIC_FEATURES] = df[NUMERIC_FEATURES].astype(float)
        return self.pipeline.predict_proba(df[self.features])[:, 1].tolist()
