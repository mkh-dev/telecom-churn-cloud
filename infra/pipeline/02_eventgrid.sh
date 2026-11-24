#!/usr/bin/env bash
# Membre 3 — Abonnement Event Grid : BlobCreated sur input/ -> fonction dispatcher.
# À lancer APRÈS le premier déploiement du code (la validation du point de terminaison
# échoue tant que la fonction dispatcher n'existe pas).
set -euo pipefail
source "$(dirname "$0")/env.sh"

ST_ID=$(az storage account show -n "$ST" -g "$RG" --query id -o tsv)
FUNC_ID=$(az functionapp show -n "$FUNC" -g "$RG" --query id -o tsv)

az eventgrid event-subscription create -n blob-to-dispatcher \
  --source-resource-id "$ST_ID" \
  --endpoint-type azurefunction --endpoint "$FUNC_ID/functions/dispatcher" \
  --included-event-types Microsoft.Storage.BlobCreated \
  --subject-begins-with /blobServices/default/containers/input/ \
  --max-delivery-attempts 10 --event-ttl 1440 -o table

az eventgrid event-subscription show -n blob-to-dispatcher --source-resource-id "$ST_ID" \
  --query "{etat:provisioningState, filtre:filter}" -o json
