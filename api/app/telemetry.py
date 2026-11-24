"""Télémétrie Application Insights, activée seulement si la chaîne de connexion est définie."""

from __future__ import annotations

import logging

from opentelemetry import metrics

logger = logging.getLogger(__name__)

# Sans fournisseur configuré (en local), l'API OpenTelemetry renvoie des instruments sans effet.
_meter = metrics.get_meter("churn-api")
customers_scored = _meter.create_counter(
    "churn_customers_scored", unit="{customer}", description="Nombre de clients scorés"
)
model_latency = _meter.create_histogram(
    "churn_model_latency_ms", unit="ms", description="Latence du modèle par appel à /predict"
)


def setup(connection_string: str | None) -> bool:
    """À appeler avant de créer l'application FastAPI, pour que ses requêtes soient tracées."""
    if not connection_string:
        logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING absente : télémétrie Azure désactivée")
        return False
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(connection_string=connection_string)
    logger.info("Télémétrie Azure Monitor activée")
    return True
