"""Contrat partagé du modèle de churn (membre 1).

Ce module est utilisé :
- par train.py pour construire et entraîner le pipeline ;
- par l'API (membre 2), qui le copie dans /api/app/ pour appeler predict_batch().
"""

from __future__ import annotations

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ID_COLUMN = "customerID"
TARGET = "Churn"

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
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

# Ordre exact des colonnes du CSV déposé dans input/ (contrat 5.1 du cahier des charges)
INPUT_COLUMNS = [
    "customerID",
    "gender",
    "SeniorCitizen",
    "Partner",
    "Dependents",
    "tenure",
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
    "MonthlyCharges",
    "TotalCharges",
]

HIGH_RISK = 0.7
MEDIUM_RISK = 0.4


def build_preprocessor() -> ColumnTransformer:
    """Imputation + normalisation des numériques, one-hot des catégorielles."""
    numeric = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    categorical = OneHotEncoder(handle_unknown="ignore")
    return ColumnTransformer([("num", numeric, NUMERIC_FEATURES), ("cat", categorical, CATEGORICAL_FEATURES)])


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Vérifie les colonnes et convertit les types (TotalCharges peut être vide)."""
    missing = [c for c in FEATURES if c not in df.columns]
    if missing:
        raise ValueError(f"Colonnes manquantes : {missing}")
    x = df[FEATURES].copy()
    for col in NUMERIC_FEATURES:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    for col in CATEGORICAL_FEATURES:
        x[col] = x[col].astype(str).str.strip()
    return x


def risk_level(probability: float) -> str:
    if probability >= HIGH_RISK:
        return "élevé"
    if probability >= MEDIUM_RISK:
        return "moyen"
    return "faible"


def load_model(path: str):
    return joblib.load(path)


def predict_batch(model, customers: list[dict], threshold: float = 0.5) -> list[dict]:
    """Prédit le churn d'un lot de clients (liste de dictionnaires aux colonnes INPUT_COLUMNS)."""
    if not customers:
        return []
    df = pd.DataFrame(customers)
    probas = model.predict_proba(prepare_features(df))[:, 1]
    ids = df[ID_COLUMN].astype(str) if ID_COLUMN in df.columns else pd.Series(range(len(df))).astype(str)
    return [
        {
            "customerID": cid,
            "churn_prediction": int(p >= threshold),
            "churn_probability": round(float(p), 4),
            "risk_level": risk_level(float(p)),
        }
        for cid, p in zip(ids, probas, strict=True)
    ]
