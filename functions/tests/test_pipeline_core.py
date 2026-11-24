"""Tests du pipeline (sans Azure) : pytest functions/tests"""

import json
import sys
from pathlib import Path

import pytest

FUNCTIONS = Path(__file__).resolve().parents[1]
ROOT = FUNCTIONS.parent
sys.path.insert(0, str(FUNCTIONS))

from pipeline_core import (  # noqa: E402
    ALLOWED_VALUES,
    INPUT_COLUMNS,
    NUMERIC_BOUNDS,
    ApiError,
    PoisonMessageError,
    Settings,
    WorkerDeps,
    build_queue_message,
    check_metadata,
    file_name_of,
    inference_id,
    iso_z,
    normalize_prediction,
    parse_and_validate,
    parse_queue_message,
    parse_subject,
    process_message,
    risk_level,
)

DATA = FUNCTIONS / "tests" / "data"
SAMPLES = FUNCTIONS / "samples"
S = Settings()


def valid_csv() -> bytes:
    return (DATA / "clients_valide.csv").read_bytes()


N_VALID = len(valid_csv().decode().strip().splitlines()) - 1


# ---------------- dispatcher ----------------


def test_parse_subject():
    assert parse_subject("/blobServices/default/containers/input/blobs/lot/jan.csv") == ("input", "lot/jan.csv")
    with pytest.raises(ValueError):
        parse_subject("/autre/chose")


@pytest.mark.parametrize(
    "name,size,code",
    [
        ("image.png", 70, "bad_extension"),
        ("sans_extension", 10, "bad_extension"),
        ("vide.csv", 0, "empty_file"),
        ("gros.csv", 5_000_000, "too_large"),
    ],
)
def test_check_metadata_rejects(name, size, code):
    assert check_metadata(name, size, S).code == code


def test_check_metadata_accepts_csv_any_case():
    assert check_metadata("lot/Clients.CSV", 1200, S) is None


def test_valid_file_is_typed_for_the_api():
    rows, rej = parse_and_validate(valid_csv(), S)
    assert rej is None and len(rows) == N_VALID
    assert list(rows[0]) == list(INPUT_COLUMNS)  # contrat §2 : les 20 colonnes, dans l'ordre
    assert isinstance(rows[0]["tenure"], int) and isinstance(rows[0]["MonthlyCharges"], float)


@pytest.mark.parametrize(
    "fname,code",
    [
        ("clients_colonnes_manquantes.csv", "bad_schema"),
        ("clients_latin1.csv", "bad_encoding"),
        ("clients_vide.csv", "empty_file"),
    ],
)
def test_invalid_samples_are_rejected(fname, code):
    rows, rej = parse_and_validate((SAMPLES / fname).read_bytes(), S)
    assert rows is None and rej.code == code


def test_semicolon_separator_is_rejected_with_hint():
    _, rej = parse_and_validate(valid_csv().decode().replace(",", ";").encode(), S)
    assert rej.code == "bad_schema" and "virgule" in rej.message


def test_bad_numeric_value_rejected():
    lines = valid_csv().decode().splitlines()
    fields = lines[1].split(",")
    fields[-2] = "abc"  # MonthlyCharges
    lines[1] = ",".join(fields)
    _, rej = parse_and_validate("\n".join(lines).encode(), S)
    assert rej.code == "bad_value" and "MonthlyCharges" in rej.message


def test_blank_total_charges_becomes_null_and_churn_column_is_dropped():
    lines = valid_csv().decode().splitlines()
    lines[0] += ",Churn"
    fields = lines[1].split(",")
    fields[-1] = " "
    lines[1] = ",".join(fields) + ",Yes"
    for i in range(2, len(lines)):
        lines[i] += ",No"
    rows, rej = parse_and_validate("\n".join(lines).encode(), S)
    assert rej is None and rows[0]["TotalCharges"] is None and "Churn" not in rows[0]


def _with_field(column: str, value: str) -> bytes:
    lines = valid_csv().decode().splitlines()
    fields = lines[1].split(",")
    fields[INPUT_COLUMNS.index(column)] = value
    lines[1] = ",".join(fields)
    return "\n".join(lines).encode()


@pytest.mark.parametrize(
    "column,value",
    [
        ("Contract", "Monthly"),
        ("gender", "male"),
        ("PaymentMethod", "Cash"),
        ("SeniorCitizen", "2"),
        ("tenure", "-1"),
        ("MonthlyCharges", "-5"),
        ("TotalCharges", "nan"),
        ("tenure", "inf"),
    ],
)
def test_values_refused_by_the_api_are_rejected_by_the_dispatcher(column, value):
    """Sans ce contrôle, l'API répondrait 422 et le fichier finirait en file poison."""
    _, rej = parse_and_validate(_with_field(column, value), S)
    assert rej is not None and rej.code == "bad_value" and column in rej.message


def _api_customer_schema() -> tuple[dict[str, tuple[str, ...]], dict[str, tuple]]:
    """Lit api/app/schemas.py SANS importer pydantic : valeurs Literal et bornes ge/le de Customer."""
    import ast

    tree = ast.parse((ROOT / "api" / "app" / "schemas.py").read_text(encoding="utf-8"))

    def literal_values(node):
        if isinstance(node, ast.Subscript) and getattr(node.value, "id", None) == "Literal":
            elts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return tuple(e.value for e in elts)
        return None

    aliases, amount_bounds = {}, {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
            values = literal_values(stmt.value)
            if values:
                aliases[stmt.targets[0].id] = values
            if stmt.targets[0].id == "Amount":
                amount_bounds = {k.arg: k.value.value for c in ast.walk(stmt.value) if isinstance(c, ast.Call)
                                 for k in c.keywords if k.arg in ("ge", "le")}  # fmt: skip
    customer = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Customer")
    allowed, bounds = {}, {}
    for stmt in customer.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        name, ann = stmt.target.id, stmt.annotation
        values = literal_values(ann) or (aliases.get(ann.id) if isinstance(ann, ast.Name) else None)
        if values:
            allowed[name] = values
        src = ast.unparse(ann)
        if "Amount" in src:
            bounds[name] = (amount_bounds.get("ge"), amount_bounds.get("le"))
        else:
            kw = {k.arg: k.value.value for c in ast.walk(ann) if isinstance(c, ast.Call) for k in c.keywords
                  if k.arg in ("ge", "le")}  # fmt: skip
            if kw:
                bounds[name] = (kw.get("ge"), kw.get("le"))
    return allowed, bounds


def test_allowed_values_match_api_schema():
    if not (ROOT / "api" / "app" / "schemas.py").exists():
        pytest.skip("api/ absent")
    allowed, bounds = _api_customer_schema()
    assert {k: set(v) for k, v in allowed.items()} == {k: set(v) for k, v in ALLOWED_VALUES.items()}
    assert bounds == NUMERIC_BOUNDS


def test_too_many_rows():
    _, rej = parse_and_validate(valid_csv(), Settings(max_rows=3))
    assert rej.code == "too_many_rows"


def test_queue_message_matches_contract():
    msg = build_queue_message("clients.csv", '"0x8DD0A1B2C3D4E5F"', 48213, 120, "2026-11-25T10:42:07.1234567Z")
    assert msg == {
        "blobName": "input/clients.csv",
        "etag": "0x8DD0A1B2C3D4E5F",
        "size": 48213,
        "rows": 120,
        "timestamp": "2026-11-25T10:42:07Z",
    }
    assert parse_queue_message(json.dumps(msg))["blobName"] == "input/clients.csv"


def test_iso_z_handles_event_grid_and_python_formats():
    assert iso_z("2026-11-25T10:42:07.1234567Z") == "2026-11-25T10:42:07Z"
    assert iso_z("2026-11-25T11:42:07+01:00") == "2026-11-25T10:42:07Z"


@pytest.mark.parametrize("text", ["pas du json", "[]", '{"blobName": "a.csv"}', '{"etag": "x"}'])
def test_bad_queue_message_is_poison(text):
    with pytest.raises(PoisonMessageError):
        parse_queue_message(text)


# ---------------- worker ----------------


def test_inference_id_matches_contract_and_is_cosmos_safe():
    assert inference_id("clients-2026-11-25.csv", '"0x8DD0A1B2C3D4E5F"') == "clients-2026-11-25.csv_0x8DD0A1B2C3D4E5F"
    weird = inference_id("lot/jan?.csv", "0x1")
    assert not any(c in weird for c in "/\\?#")
    assert file_name_of("input/lot/a.csv") == "lot/a.csv"


@pytest.mark.parametrize("p,level", [(0.82, "élevé"), (0.7, "élevé"), (0.5, "moyen"), (0.39, "faible")])
def test_risk_levels_match_contract(p, level):
    assert risk_level(p) == level


def test_normalize_prediction_fills_missing_fields_and_checks_order():
    assert normalize_prediction({"churn_probability": 0.82}, "A") == {
        "customerID": "A",
        "churn_prediction": 1,
        "churn_probability": 0.82,
        "risk_level": "élevé",
    }
    with pytest.raises(ApiError):
        normalize_prediction({"customerID": "B", "churn_probability": 0.1}, "A")
    with pytest.raises(ApiError):
        normalize_prediction({"churn_probability": 3}, "A")
    with pytest.raises(ApiError):
        normalize_prediction({"proba": 0.3}, "A")


def contract_api(payload):
    """Fausse API qui respecte le contrat §2."""
    preds = []
    for c in payload["customers"]:
        p = 0.82 if c["Contract"] == "Month-to-month" else 0.1
        preds.append({"customerID": c["customerID"], "churn_prediction": int(p >= 0.5), "churn_probability": p})
    return {"model_version": "1.0.0", "latency_ms": 3.2, "predictions": preds}, 15.0


class FakeWorld:
    def __init__(self, predict=contract_api):
        self.outputs, self.db, self.calls, self.metrics = {}, {}, [], []
        self._predict = predict

    def predict(self, payload):
        self.calls.append(payload)
        return self._predict(payload)

    def deps(self):
        return WorkerDeps(
            read_blob=lambda name, etag: valid_csv(),
            predict=self.predict,
            write_output=self.outputs.__setitem__,
            upsert=lambda d: self.db.__setitem__((d["day"], d["id"]), d),  # clé = (partition, id)
            on_metrics=self.metrics.append,
        )


def ticket():
    return json.dumps(build_queue_message("clients.csv", "0x8DD1", 1960, N_VALID, "2026-11-25T23:59:58Z"))


def test_worker_end_to_end_matches_cosmos_contract():
    w = FakeWorld()
    doc = process_message(ticket(), w.deps(), S)
    assert len(w.calls) == 1 and len(w.calls[0]["customers"]) == N_VALID
    assert w.calls[0]["customers"][0]["customerID"]  # contrat §2 : customerID envoyé
    for key in ("id", "day", "fileName", "timestamp", "nCustomers", "nHighRisk", "churnRate",
                "modelVersion", "durationMs", "status"):  # fmt: skip
        assert key in doc
    assert doc["id"] == "clients.csv_0x8DD1" and doc["fileName"] == "clients.csv"
    assert doc["day"] == "2026-11-25" and doc["status"] == "ok" and doc["nCustomers"] == N_VALID
    out = w.outputs["clients.csv_0x8DD1.json"]
    assert len(out["predictions"]) == N_VALID and out["predictions"][0]["risk_level"] in ("élevé", "faible")
    assert w.metrics[0]["rows"] == N_VALID


def test_worker_is_idempotent_even_after_midnight():
    w = FakeWorld()
    process_message(ticket(), w.deps(), S)
    process_message(ticket(), w.deps(), S)  # même ticket livré deux fois (« au moins une fois »)
    assert len(w.db) == 1 and len(w.outputs) == 1  # day = jour du dépôt => même partition


def test_worker_raises_when_api_down_and_writes_nothing():
    def down(payload):
        raise ApiError("API coupée")

    w = FakeWorld(predict=down)
    with pytest.raises(ApiError):
        process_message(ticket(), w.deps(), S)
    assert w.db == {} and w.outputs == {}


@pytest.mark.parametrize(
    "body",
    [
        {"predictions": [{"churn_probability": 0.5}]},  # mauvais nombre de prédictions
        {"detail": "erreur"},  # pas de liste predictions
        ["pas", "un", "objet"],
    ],
)
def test_worker_rejects_malformed_api_responses(body):
    w = FakeWorld(predict=lambda payload: (body, 1.0))
    with pytest.raises(ApiError):
        process_message(ticket(), w.deps(), S)


# ---------------- intégration avec le vrai modèle du membre 1 ----------------


def test_worker_with_real_model_predict_batch():
    """Vérifie que la sortie de model/churn_model.predict_batch (utilisée par l'API)
    est bien comprise par le worker, avec le vrai model-1.0.0.joblib."""
    pytest.importorskip("sklearn")
    model_path = ROOT / "model" / "artifacts" / "model-1.0.0.joblib"
    if not model_path.exists():
        pytest.skip("modèle absent")
    sys.path.insert(0, str(ROOT / "model"))
    from churn_model import load_model, predict_batch

    model = load_model(str(model_path))

    def real_api(payload):
        preds = predict_batch(model, payload["customers"])
        return {"model_version": "1.0.0", "latency_ms": 1.0, "predictions": preds}, 5.0

    w = FakeWorld(predict=real_api)
    doc = process_message(ticket(), w.deps(), S)
    assert doc["nCustomers"] == N_VALID and 0 <= doc["churnRate"] <= 1
    assert 0 < doc["nHighRisk"] < N_VALID  # le vrai modèle distingue les clients
