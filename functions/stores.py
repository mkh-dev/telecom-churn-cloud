"""Accès au stockage : Blob (input/output/rejected) et base des résultats.

Connexions :
- en Azure : identité managée de la Function App (DefaultAzureCredential),
  à partir des paramètres PipelineStorage__blobServiceUri et COSMOS_ENDPOINT ;
- en local : Azurite via PipelineStorage=UseDevelopmentStorage=true.
Aucune clé ni chaîne de connexion dans le code ou dans le dépôt.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import ResourceModifiedError
from azure.storage.blob import BlobServiceClient, ContentSettings

from pipeline_core import StaleMessageError, utc_now

CONNECTION = "PipelineStorage"
INPUT, OUTPUT, REJECTED = "input", "output", "rejected"
META_PREFIX = "_meta/"  # utilisé seulement par le store de repli "blob" (local)


@lru_cache(maxsize=1)
def credential():
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


@lru_cache(maxsize=1)
def blob_service() -> BlobServiceClient:
    conn = os.environ.get(CONNECTION)
    if conn:  # local (Azurite)
        return BlobServiceClient.from_connection_string(conn)
    return BlobServiceClient(os.environ[f"{CONNECTION}__blobServiceUri"], credential=credential())


def read_input(blob_name: str, etag: str | None = None) -> bytes:
    """Lit input/<nom>. Si l'ETag ne correspond plus (fichier remplacé entre-temps),
    lève StaleMessageError."""
    client = blob_service().get_blob_client(INPUT, blob_name)
    if not etag:
        return client.download_blob().readall()
    # Event Grid donne l'ETag sans guillemets ; l'en-tête If-Match les attend.
    quoted = etag if etag.startswith('"') else f'"{etag}"'
    try:
        return client.download_blob(etag=quoted, match_condition=MatchConditions.IfNotModified).readall()
    except ResourceModifiedError as exc:
        raise StaleMessageError(f"{blob_name} a changé depuis l'événement") from exc


def write_json(container: str, name: str, doc: dict[str, Any]) -> None:
    data = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    blob_service().get_blob_client(container, name).upload_blob(
        data, overwrite=True, content_settings=ContentSettings(content_type="application/json; charset=utf-8")
    )


def reject(blob_name: str, content: bytes | None, code: str, message: str, etag: str | None) -> None:
    """Copie le fichier dans rejected/ avec le motif :
    - métadonnée du blob (ASCII uniquement) : reason=<code> ;
    - fichier compagnon rejected/<nom>.reason.json (message lisible, UTF-8)."""
    meta = {"reason": code, "sourceetag": (etag or "").strip('"')}
    if content is not None:
        blob_service().get_blob_client(REJECTED, blob_name).upload_blob(content, overwrite=True, metadata=meta)
    write_json(
        REJECTED,
        f"{blob_name}.reason.json",
        {
            "fileName": blob_name,
            "etag": etag,
            "reasonCode": code,
            "reason": message,
            "rejectedAt": utc_now(),
            "contentCopied": content is not None,
        },
    )


# --------------------------------------------------------------------------
# Base des résultats : Cosmos DB (production) ou blobs output/_meta/ (repli local)
# --------------------------------------------------------------------------

RECENT_FIELDS = (
    "id", "day", "fileName", "timestamp", "nCustomers", "nHighRisk", "nMediumRisk", "nChurn", "churnRate",
    "avgProbability", "topRisk", "modelVersion", "durationMs", "apiLatencyMs", "status", "outputBlob",
)  # fmt: skip


class CosmosStore:
    """Base pipeline, conteneur inferences, clé de partition /day (contrat §4)."""

    def __init__(self) -> None:
        from azure.cosmos import CosmosClient

        client = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=credential())
        db = client.get_database_client(os.environ.get("COSMOS_DATABASE", "pipeline"))
        self.container = db.get_container_client(os.environ.get("COSMOS_CONTAINER", "inferences"))

    def upsert(self, doc: dict[str, Any]) -> None:
        self.container.upsert_item(doc)

    def recent(self, limit: int) -> list[dict[str, Any]]:
        # Requête inter-partitions : TOP + ORDER BY sur l'ensemble du conteneur.
        fields = ", ".join(f"c.{f}" for f in RECENT_FIELDS)
        query = f"SELECT TOP @n {fields} FROM c ORDER BY c.timestamp DESC"
        items = self.container.query_items(
            query, parameters=[{"name": "@n", "value": limit}], enable_cross_partition_query=True
        )
        return list(items)


class BlobMetaStore:
    """Repli pour le développement local (Azurite), sans émulateur Cosmos DB."""

    def upsert(self, doc: dict[str, Any]) -> None:
        write_json(OUTPUT, f"{META_PREFIX}{doc['id']}.json", doc)

    def recent(self, limit: int) -> list[dict[str, Any]]:
        cont = blob_service().get_container_client(OUTPUT)
        blobs = sorted(cont.list_blobs(name_starts_with=META_PREFIX), key=lambda b: b.last_modified, reverse=True)
        docs = [json.loads(cont.download_blob(b.name).readall()) for b in blobs[:limit]]
        docs.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
        return [{k: d.get(k) for k in RECENT_FIELDS} for d in docs]


@lru_cache(maxsize=1)
def result_store():
    kind = os.environ.get("RESULT_STORE", "cosmos" if os.environ.get("COSMOS_ENDPOINT") else "blob")
    return CosmosStore() if kind == "cosmos" else BlobMetaStore()
