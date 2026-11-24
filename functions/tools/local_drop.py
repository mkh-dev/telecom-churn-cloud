"""Simule en local « un fichier arrive dans input/ » :
1) dépose le fichier dans Azurite (conteneur input),
2) envoie l'événement Event Grid BlobCreated à la fonction dispatcher (func start).

    python tools/local_drop.py ../samples/clients_valide.csv
    python tools/local_drop.py ../samples/image.png
    python tools/local_drop.py --poison          # met un message illisible dans la file

Prérequis : azurite et `func start` lancés, local.settings.json copié depuis l'exemple.
"""

import argparse
import base64
import json
import sys
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueClient

CONN = "UseDevelopmentStorage=true"
FUNC = "http://localhost:7071/runtime/webhooks/EventGrid?functionName=dispatcher"


def ensure_containers(svc: BlobServiceClient) -> None:
    for name in ("input", "output", "rejected", "models"):
        try:
            svc.create_container(name)
        except Exception:
            pass


def drop(path: Path, name: str | None) -> None:
    svc = BlobServiceClient.from_connection_string(CONN)
    ensure_containers(svc)
    blob_name = name or path.name
    props = svc.get_blob_client("input", blob_name).upload_blob(path.read_bytes(), overwrite=True)
    event = [
        {
            "id": str(uuid.uuid4()),
            "topic": "/subscriptions/local/resourceGroups/local/providers/Microsoft.Storage/"
            "storageAccounts/devstoreaccount1",
            "subject": f"/blobServices/default/containers/input/blobs/{blob_name}",
            "eventType": "Microsoft.Storage.BlobCreated",
            "eventTime": datetime.now(UTC).isoformat(),
            "dataVersion": "",
            "metadataVersion": "1",
            "data": {
                "api": "PutBlob",
                "contentType": "application/octet-stream",
                "contentLength": path.stat().st_size,
                "blobType": "BlockBlob",
                "eTag": props["etag"],
                "url": f"http://127.0.0.1:10000/devstoreaccount1/input/{blob_name}",
            },
        }
    ]
    req = urllib.request.Request(
        FUNC,
        data=json.dumps(event).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "aeg-event-type": "Notification"},
    )
    with urllib.request.urlopen(req) as resp:
        print(f"{blob_name} déposé, événement envoyé -> HTTP {resp.status}")


def poison() -> None:
    q = QueueClient.from_connection_string(CONN, "inference-requests")
    try:
        q.create_queue()
    except Exception:
        pass
    # Le runtime Functions attend du base64 (messageEncoding par défaut)
    q.send_message(base64.b64encode(b"ceci n'est pas un ticket JSON").decode())
    print("Message illisible envoyé : 5 tentatives puis inference-requests-poison")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", type=Path)
    ap.add_argument("--name", help="nom du blob dans input/ (défaut : nom du fichier)")
    ap.add_argument("--poison", action="store_true")
    a = ap.parse_args()
    if a.poison:
        poison()
    elif a.file:
        drop(a.file, a.name)
    else:
        sys.exit(ap.format_usage())
