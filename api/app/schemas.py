"""Schémas Pydantic du contrat d'API (docs/contrats.md, section 2)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

MAX_CUSTOMERS = 1000

YesNo = Literal["Yes", "No"]
InternetOption = Literal["Yes", "No", "No internet service"]
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]


def _blank_to_none(value: object) -> object:
    # Dans le CSV d'origine, TotalCharges est vide (" ") pour les clients avec tenure = 0
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Customer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customerID: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    gender: Literal["Male", "Female"]
    SeniorCitizen: Annotated[int, Field(ge=0, le=1)]
    Partner: YesNo
    Dependents: YesNo
    tenure: Annotated[int, Field(ge=0)]
    PhoneService: YesNo
    MultipleLines: Literal["Yes", "No", "No phone service"]
    InternetService: Literal["DSL", "Fiber optic", "No"]
    OnlineSecurity: InternetOption
    OnlineBackup: InternetOption
    DeviceProtection: InternetOption
    TechSupport: InternetOption
    StreamingTV: InternetOption
    StreamingMovies: InternetOption
    Contract: Literal["Month-to-month", "One year", "Two year"]
    PaperlessBilling: YesNo
    PaymentMethod: Literal["Electronic check", "Mailed check", "Bank transfer (automatic)", "Credit card (automatic)"]
    MonthlyCharges: Amount
    TotalCharges: Annotated[Amount | None, BeforeValidator(_blank_to_none)] = None


class PredictRequest(BaseModel):
    customers: list[Customer] = Field(min_length=1, max_length=MAX_CUSTOMERS)


class Prediction(BaseModel):
    customerID: str
    churn_probability: float
    churn: bool
    churn_prediction: Literal[0, 1]
    risk_level: Literal["faible", "moyen", "élevé"]


class PredictResponse(BaseModel):
    predictions: list[Prediction]
    model_version: str
    latency_ms: float


class Health(BaseModel):
    status: Literal["ok", "error"]
    model_loaded: bool
    model_load_time_ms: float


class Version(BaseModel):
    api_version: str
    model_version: str


class Metrics(BaseModel):
    calls: int
    avg_latency_ms: float
    customers_scored: int
    errors: int
