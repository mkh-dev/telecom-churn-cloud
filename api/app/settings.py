"""Configuration de l'API, lue uniquement dans les variables d'environnement."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "model-1.0.0.joblib"


def version_from_path(path: Path) -> str:
    """model-1.0.0.joblib -> 1.0.0 (sinon le nom du fichier sans extension)."""
    match = re.fullmatch(r"model-(.+)\.joblib", path.name)
    return match.group(1) if match else path.stem


@dataclass(frozen=True)
class Settings:
    model_path: Path
    model_version: str
    risk_threshold: float
    appinsights_connection_string: str | None

    @classmethod
    def from_env(cls) -> Settings:
        model_path = Path(os.getenv("MODEL_PATH") or DEFAULT_MODEL_PATH)
        threshold = float(os.getenv("RISK_THRESHOLD") or 0.5)
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"RISK_THRESHOLD doit être entre 0 et 1 (reçu : {threshold})")
        return cls(
            model_path=model_path,
            model_version=os.getenv("MODEL_VERSION") or version_from_path(model_path),
            risk_threshold=threshold,
            appinsights_connection_string=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") or None,
        )
