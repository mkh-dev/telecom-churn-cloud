#!/usr/bin/env bash
# =============================================================================
# Membre 3 — Socle du pipeline événementiel. À lancer par le PROPRIÉTAIRE de
# l'abonnement d'intégration (membre 2), une fois ; le script est ré-exécutable.
#   Stockage (input/output/rejected/models + versionnage + $web) -> file d'attente
#   -> Cosmos DB Free Tier -> Function App Flex Consumption -> identité + rôles -> CORS
# Pré-requis : groupe de ressources créé (+ Log Analytics / App Insights si possible).
# Usage : source infra/pipeline/env.sh && bash infra/pipeline/01_pipeline.sh
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/env.sh"
echo "Groupe $RG, région $LOC, stockage $ST, Function App $FUNC, Cosmos DB $COSMOS"

echo "== 0. Groupe de ressources (étiquettes FinOps)"
az group create -n "$RG" -l "$LOC" --tags projet=dscc equipe="$EQ" -o none

echo "== 1. Cosmos DB EN PREMIER (service le plus souvent refusé faute de capacité)"
# Free Tier : à cocher à la création, impossible à ajouter ensuite. Un seul par abonnement.
if ! az cosmosdb show -n "$COSMOS" -g "$RG" -o none 2>/dev/null; then
  az cosmosdb create -n "$COSMOS" -g "$RG" --enable-free-tier true \
    --locations regionName="$LOC" failoverPriority=0 isZoneRedundant=False \
    --default-consistency-level Session -o none \
  || { echo "ÉCHEC Cosmos DB dans $LOC : changer de région AVANT de créer le reste (noter dans le journal)"; exit 2; }
fi
az cosmosdb sql database create -a "$COSMOS" -g "$RG" -n pipeline --throughput 400 -o none 2>/dev/null || true
az cosmosdb sql container create -a "$COSMOS" -g "$RG" -d pipeline -n inferences \
  --partition-key-path /day -o none 2>/dev/null || true

echo "== 2. Compte de stockage (Standard LRS, TLS 1.2, pas d'accès public anonyme)"
az storage account create -n "$ST" -g "$RG" -l "$LOC" --sku Standard_LRS --kind StorageV2 \
  --access-tier Hot --min-tls-version TLS1_2 --allow-blob-public-access false -o none
az storage account blob-service-properties update -n "$ST" -g "$RG" --enable-versioning true -o none

# Opérations plan de données avec la clé récupérée par ARM (le propriétaire n'a pas
# de rôle "Storage Blob Data *" par défaut). Rien n'est écrit sur disque.
KEY=$(az storage account keys list -n "$ST" -g "$RG" --query [0].value -o tsv)
for c in input output rejected models; do
  az storage container create --account-name "$ST" --account-key "$KEY" -n "$c" -o none
done
for q in "$QUEUE" "$QUEUE-poison"; do
  az storage queue create --account-name "$ST" --account-key "$KEY" -n "$q" -o none
done
az storage blob service-properties update --account-name "$ST" --account-key "$KEY" \
  --static-website --index-document index.html --404-document index.html -o none
WEB_ORIGIN=$(az storage account show -n "$ST" -g "$RG" --query primaryEndpoints.web -o tsv | sed 's#/$##')
unset KEY

echo "== 3. Function App Flex Consumption (Python 3.12, 512 Mo)"
AI_ARGS=()
az monitor app-insights component show --app "$AI" -g "$RG" -o none 2>/dev/null && AI_ARGS=(--app-insights "$AI")
if ! az functionapp show -n "$FUNC" -g "$RG" -o none 2>/dev/null; then
  # En cas de 429 "throttled" : attendre quelques minutes et relancer le script.
  az functionapp create -n "$FUNC" -g "$RG" --storage-account "$ST" "${AI_ARGS[@]}" \
    --flexconsumption-location "$LOC" --runtime python --runtime-version 3.12 \
    --instance-memory 512 -o none
fi

echo "== 4. Identité managée (attribuée par le système) + rôles (moindre privilège)"
az functionapp identity assign -n "$FUNC" -g "$RG" -o none
PID=$(az functionapp identity show -n "$FUNC" -g "$RG" --query principalId -o tsv)
ST_ID=$(az storage account show -n "$ST" -g "$RG" --query id -o tsv)
for r in "Storage Blob Data Owner" "Storage Queue Data Contributor"; do
  az role assignment create --role "$r" --assignee-object-id "$PID" \
    --assignee-principal-type ServicePrincipal --scope "$ST_ID" -o none 2>/dev/null || true
done
# Cosmos DB : rôle du PLAN DE DONNÉES "Built-in Data Contributor" (pas un rôle Azure RBAC)
az cosmosdb sql role assignment create -a "$COSMOS" -g "$RG" --scope / \
  --principal-id "$PID" --role-definition-id 00000000-0000-0000-0000-000000000002 -o none 2>/dev/null || true

echo "== 5. Paramètres d'application (aucun secret : URI + identité)"
API_URL="http://localhost:8000"
if az containerapp show -n "$API_APP" -g "$RG" -o none 2>/dev/null; then
  API_URL="https://$(az containerapp show -n "$API_APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
fi
COSMOS_EP=$(az cosmosdb show -n "$COSMOS" -g "$RG" --query documentEndpoint -o tsv)
az functionapp config appsettings set -n "$FUNC" -g "$RG" -o none --settings \
  "PipelineStorage__blobServiceUri=https://$ST.blob.core.windows.net" \
  "PipelineStorage__queueServiceUri=https://$ST.queue.core.windows.net" \
  "RESULT_STORE=cosmos" "COSMOS_ENDPOINT=$COSMOS_EP" \
  "COSMOS_DATABASE=pipeline" "COSMOS_CONTAINER=inferences" \
  "API_URL=$API_URL" "API_TIMEOUT_SECONDS=20" \
  "MAX_FILE_BYTES=1000000" "MAX_ROWS=1000"

echo "== 6. CORS : seul le site du tableau de bord peut appeler /api/recent"
az functionapp cors add -n "$FUNC" -g "$RG" --allowed-origins "$WEB_ORIGIN" -o none

cat <<FIN

OK. À noter dans le README :
  Région ............ $LOC
  Stockage .......... $ST   (site : $WEB_ORIGIN)
  Function App ...... https://$(az functionapp show -n "$FUNC" -g "$RG" --query defaultHostName -o tsv)
  Cosmos DB ......... $COSMOS_EP
  API_URL ........... $API_URL   (relancer ce script quand l'API du membre 2 est en ligne)

Étape suivante : déployer le code (func azure functionapp publish $FUNC  ou  deploy.yml),
puis lancer  bash infra/pipeline/02_eventgrid.sh
FIN
