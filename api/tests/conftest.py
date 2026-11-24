import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def sample_customers() -> list[dict]:
    return json.loads((API_DIR / "dev" / "sample_request.json").read_text())["customers"]


@pytest.fixture
def customer(sample_customers) -> dict:
    return copy.deepcopy(sample_customers[0])


@pytest.fixture
def client(monkeypatch):
    for var in ("MODEL_PATH", "MODEL_VERSION", "RISK_THRESHOLD", "APPLICATIONINSIGHTS_CONNECTION_STRING", "GIT_SHA"):
        monkeypatch.delenv(var, raising=False)
    with TestClient(app) as c:
        yield c
