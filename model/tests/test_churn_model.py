import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from churn_model import (
    CATEGORICAL_FEATURES,
    INPUT_COLUMNS,
    build_preprocessor,
    predict_batch,
    prepare_features,
    risk_level,
)


def make_customers(n: int = 60, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    choices = {
        "gender": ["Male", "Female"],
        "Contract": ["Month-to-month", "One year", "Two year"],
        "InternetService": ["DSL", "Fiber optic", "No"],
        "PaymentMethod": ["Electronic check", "Mailed check", "Credit card (automatic)"],
    }
    df = pd.DataFrame({c: rng.choice(choices.get(c, ["Yes", "No"]), n) for c in CATEGORICAL_FEATURES})
    df["customerID"] = [f"C-{i:04d}" for i in range(n)]
    df["SeniorCitizen"] = rng.integers(0, 2, n)
    df["tenure"] = rng.integers(0, 72, n)
    df["MonthlyCharges"] = rng.uniform(20, 110, n).round(2)
    df["TotalCharges"] = (df["tenure"] * df["MonthlyCharges"]).round(2).astype(str)
    df.loc[0, "TotalCharges"] = " "  # valeur vide comme dans le vrai jeu de données
    return df[INPUT_COLUMNS]


@pytest.fixture(scope="module")
def model():
    df = make_customers()
    y = (df["Contract"] == "Month-to-month").astype(int)
    pipe = Pipeline([("prep", build_preprocessor()), ("clf", LogisticRegression(max_iter=500))])
    return pipe.fit(prepare_features(df), y)


def test_predict_batch_format(model):
    customers = make_customers(5, seed=1).to_dict(orient="records")
    out = predict_batch(model, customers)
    assert len(out) == 5
    for row, customer in zip(out, customers, strict=True):
        assert row["customerID"] == customer["customerID"]
        assert row["churn_prediction"] in (0, 1)
        assert 0.0 <= row["churn_probability"] <= 1.0
        assert row["risk_level"] in {"faible", "moyen", "élevé"}


def test_blank_total_charges_is_handled(model):
    customer = make_customers(1, seed=2).iloc[0].to_dict()
    customer["TotalCharges"] = " "
    assert len(predict_batch(model, [customer])) == 1


def test_missing_column_raises(model):
    customer = make_customers(1).iloc[0].to_dict()
    del customer["tenure"]
    with pytest.raises(ValueError, match="tenure"):
        predict_batch(model, [customer])


def test_unknown_category_does_not_crash(model):
    customer = make_customers(1).iloc[0].to_dict()
    customer["Contract"] = "Three year"
    assert len(predict_batch(model, [customer])) == 1


def test_empty_batch(model):
    assert predict_batch(model, []) == []


@pytest.mark.parametrize(("p", "level"), [(0.9, "élevé"), (0.7, "élevé"), (0.5, "moyen"), (0.1, "faible")])
def test_risk_level(p, level):
    assert risk_level(p) == level
