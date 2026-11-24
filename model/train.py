"""Entraînement reproductible du modèle de churn télécom (membre 1).

Jeu de données : IBM Telco Customer Churn, dépôt IBM/telco-customer-churn-on-icp4d (licence Apache-2.0).
https://github.com/IBM/telco-customer-churn-on-icp4d

Usage (depuis le dossier model/) :
    python train.py                       # entraîne LogReg et Random Forest, garde le meilleur en v1.0.0
    python train.py --data mon_fichier.csv
    python train.py --model rf --version 2.0.0   # bonus A/B : forcer un modèle pour la v2
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import time
import urllib.request
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from churn_model import ID_COLUMN, INPUT_COLUMNS, TARGET, build_preprocessor, predict_batch, prepare_features

DATA_URL = "https://raw.githubusercontent.com/IBM/telco-customer-churn-on-icp4d/master/data/Telco-Customer-Churn.csv"
HERE = Path(__file__).resolve().parent
DATA_FILE = HERE / "data" / "Telco-Customer-Churn.csv"
ARTIFACTS_DIR = HERE / "artifacts"
API_MODELS_DIR = HERE.parent / "api" / "models"


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)


def download_dataset() -> Path:
    if not DATA_FILE.exists():
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        print(f"Téléchargement de {DATA_URL} ...")
        urllib.request.urlretrieve(DATA_URL, DATA_FILE)
    return DATA_FILE


def load_dataset(path: Path) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    df = pd.read_csv(path)
    df = df.drop_duplicates(subset=ID_COLUMN)
    y = (df[TARGET].astype(str).str.strip() == "Yes").astype(int)
    x = prepare_features(df)
    return x, y, df


def build_models(seed: int) -> dict[str, Pipeline]:
    return {
        "logreg": Pipeline(
            [
                ("prep", build_preprocessor()),
                ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed)),
            ]
        ),
        "rf": Pipeline(
            [
                ("prep", build_preprocessor()),
                (
                    "clf",
                    RandomForestClassifier(
                        n_estimators=300,
                        min_samples_leaf=5,
                        class_weight="balanced",
                        n_jobs=-1,
                        random_state=seed,
                    ),
                ),
            ]
        ),
    }


def evaluate(name: str, model, x_test, y_test) -> dict:
    proba = model.predict_proba(x_test)[:, 1]
    pred = (proba >= 0.5).astype(int)
    metrics = {
        "roc_auc": round(roc_auc_score(y_test, proba), 4),
        "f1_churn": round(f1_score(y_test, pred, zero_division=0), 4),
        "precision_churn": round(precision_score(y_test, pred, zero_division=0), 4),
        "recall_churn": round(recall_score(y_test, pred, zero_division=0), 4),
        "accuracy": round(accuracy_score(y_test, pred), 4),
        "confusion_matrix [[TN, FP], [FN, TP]]": confusion_matrix(y_test, pred).tolist(),
    }
    print(
        f"{name:<32} AUC={metrics['roc_auc']:.3f}  F1={metrics['f1_churn']:.3f}  "
        f"précision={metrics['precision_churn']:.3f}  rappel={metrics['recall_churn']:.3f}  "
        f"accuracy={metrics['accuracy']:.3f}"
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=None, help="CSV Telco (sinon téléchargé)")
    parser.add_argument("--model", choices=["best", "logreg", "rf"], default="best")
    parser.add_argument("--version", default="1.0.0", help="Version sémantique du modèle exporté")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-size", type=float, default=0.2)
    args = parser.parse_args()

    set_seed(args.seed)
    x, y, raw = load_dataset(args.data or download_dataset())
    print(f"{len(x)} clients, {x.shape[1]} variables, taux de churn = {y.mean():.1%}\n")

    # Jeu de test séparé et stratifié (même proportion de churn des deux côtés)
    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=args.test_size, stratify=y, random_state=args.seed
    )

    results = {}
    baseline = DummyClassifier(strategy="most_frequent").fit(x_train, y_train)
    results["baseline_always_no_churn"] = evaluate("Référence naïve (toujours Non)", baseline, x_test, y_test)

    models = build_models(args.seed)
    for name, model in models.items():
        model.fit(x_train, y_train)
        results[name] = evaluate(name, model, x_test, y_test)

    chosen = args.model
    if chosen == "best":
        chosen = max(models, key=lambda n: results[n]["roc_auc"])
    model = models[chosen]
    print(f"\nModèle retenu pour la version {args.version} : {chosen}")

    # Latence : un lot de 1 000 clients (taille maximale d'un fichier) doit passer en < 1 s sur CPU
    batch = raw.loc[x_test.index[:1000], INPUT_COLUMNS].to_dict(orient="records")
    t0 = time.perf_counter()
    predict_batch(model, batch)
    batch_ms = (time.perf_counter() - t0) * 1000

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    model_file = ARTIFACTS_DIR / f"model-{args.version}.joblib"
    joblib.dump(model, model_file, compress=3)
    size_mb = model_file.stat().st_size / 1e6

    report = {
        "dataset": "IBM Telco Customer Churn (IBM/telco-customer-churn-on-icp4d, Apache-2.0)",
        "n_customers": len(x),
        "churn_rate": round(float(y.mean()), 4),
        "n_train": len(x_train),
        "n_test": len(x_test),
        "seed": args.seed,
        "version": args.version,
        "chosen_model": chosen,
        "metrics_test": results,
        "latency_ms_batch": {"rows": len(batch), "ms": round(batch_ms, 1)},
        "model_size_mb": round(size_mb, 3),
    }
    (ARTIFACTS_DIR / f"metrics-{args.version}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    # Fichier d'exemple pour la démo du pipeline (20 clients du jeu de test, sans la colonne Churn)
    raw.loc[x_test.index[:20], INPUT_COLUMNS].to_csv(ARTIFACTS_DIR / "sample-clients.csv", index=False)

    # Copie pour l'image Docker de l'API (membre 2)
    API_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model_file, API_MODELS_DIR / model_file.name)

    print(f"Latence : {batch_ms:.0f} ms pour {len(batch)} clients")
    print(f"Modèle : {model_file} ({size_mb:.2f} Mo), copié dans {API_MODELS_DIR}")
    print(f"Exemple de fichier pour la démo : {ARTIFACTS_DIR / 'sample-clients.csv'}")


if __name__ == "__main__":
    main()
