"""Modèle provisoire pour faire tourner l'API avant la livraison du modèle du membre 1.

Entraîne rapidement un Pipeline scikit-learn équivalent à celui de model/train.py
(imputation + normalisation des numériques, one-hot des catégorielles, régression logistique)
et l'exporte en api/models/model-0.1.0.joblib. Le vrai model-<version>.joblib le remplace
sans changer le code de l'API.

Usage (depuis le dossier api/) :
    python dev/train_placeholder.py                          # télécharge le CSV IBM si besoin
    python dev/train_placeholder.py --data chemin/Telco-Customer-Churn.csv
    python dev/train_placeholder.py --sample 5               # régénère aussi dev/sample_request.json
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

DATA_URL = "https://raw.githubusercontent.com/IBM/telco-customer-churn-on-icp4d/master/data/Telco-Customer-Churn.csv"
API_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA = API_DIR.parent / "model" / "data" / "Telco-Customer-Churn.csv"
SEED = 42

NUMERIC = ["SeniorCitizen", "tenure", "MonthlyCharges", "TotalCharges"]
CATEGORICAL = [
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


def load(path: Path | None) -> pd.DataFrame:
    if path is None:
        path = DEFAULT_DATA
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            print(f"Téléchargement de {DATA_URL} ...")
            urllib.request.urlretrieve(DATA_URL, path)
    df = pd.read_csv(path).drop_duplicates(subset="customerID")
    df["TotalCharges"] = pd.to_numeric(df["TotalCharges"], errors="coerce")
    return df


def build_pipeline() -> Pipeline:
    numeric = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    prep = ColumnTransformer(
        [("num", numeric, NUMERIC), ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL)]
    )
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=SEED)
    return Pipeline([("prep", prep), ("clf", clf)])


def write_sample(df: pd.DataFrame, n: int) -> Path:
    """Écrit n vrais clients (sans Churn) au format de POST /predict, dont un avec TotalCharges vide."""
    blank = df[df["TotalCharges"].isna()].head(1)
    rows = pd.concat([df.head(n - len(blank)), blank]).drop(columns="Churn")
    customers = []
    for row in rows.to_dict(orient="records"):
        row["TotalCharges"] = None if pd.isna(row["TotalCharges"]) else row["TotalCharges"]
        customers.append(row)
    out = API_DIR / "dev" / "sample_request.json"
    out.write_text(json.dumps({"customers": customers}, indent=2, ensure_ascii=False) + "\n")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=None, help="CSV IBM Telco (sinon model/data/ ou téléchargé)")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--sample", type=int, default=0, help="Nombre de clients à écrire dans sample_request.json")
    args = parser.parse_args()

    df = load(args.data)
    y = (df["Churn"].str.strip() == "Yes").astype(int)
    model = build_pipeline().fit(df[NUMERIC + CATEGORICAL], y)

    out = API_DIR / "models" / f"model-{args.version}.joblib"
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out, compress=3)
    print(f"{len(df)} clients, modèle écrit dans {out} ({out.stat().st_size / 1e3:.0f} Ko)")

    if args.sample:
        print(f"Exemple de requête : {write_sample(df, args.sample)}")


if __name__ == "__main__":
    main()
