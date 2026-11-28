#!/usr/bin/env bash
# =============================================================================
# Membre 3 — Étape réservée au PROPRIÉTAIRE de la souscription (membre 2).
# Un « Contributor » sur rg-churn peut créer les ressources du pipeline
# (01_pipeline.sh), mais pas enregistrer un fournisseur ni attribuer des rôles.
# Ce script fait uniquement ces deux choses, puis branche Event Grid si le code
# est déjà déployé. Il est ré-exécutable.
# Usage (Cloud Shell, souscription du projet) :
#   git clone https://github.com/mkh-dev/telecom-churn-cloud && cd telecom-churn-cloud
#   source infra/pipeline/env.sh && bash infra/pipeline/00_owner.sh
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/env.sh"

echo "== 1. Fournisseur Event Grid (abonnement)"
az provider register -n Microsoft.EventGrid --wait -o none
echo "   Microsoft.EventGrid : $(az provider show -n Microsoft.EventGrid --query registrationState -o tsv)"

echo "== 2. Rôles de l'identité managée de la Function App $FUNC"
PID=$(az functionapp identity show -n "$FUNC" -g "$RG" --query principalId -o tsv)
ST_ID=$(az storage account show -n "$ST" -g "$RG" --query id -o tsv)
for r in "Storage Blob Data Owner" "Storage Queue Data Contributor"; do
  az role assignment create --role "$r" --assignee-object-id "$PID" \
    --assignee-principal-type ServicePrincipal --scope "$ST_ID" -o none
  echo "   $r sur $ST"
done

echo "== 3. Rôles de données pour les membres qui lancent la démo (03_demo.sh, --auth-mode login)"
# Membres de l'équipe ayant déjà un rôle sur le groupe de ressources (ex. Contributor)
RG_ID=$(az group show -n "$RG" --query id -o tsv)
for oid in $(az role assignment list --scope "$RG_ID" --query "[?principalType=='User'].principalId" -o tsv | sort -u) \
           $(az ad signed-in-user show --query id -o tsv); do
  for r in "Storage Blob Data Contributor" "Storage Queue Data Contributor"; do
    az role assignment create --role "$r" --assignee-object-id "$oid" \
      --assignee-principal-type User --scope "$ST_ID" -o none 2>/dev/null || true
  done
done
echo "   rôles de données attribués aux utilisateurs du groupe"

echo "== 4. Event Grid (si le code des Functions est déjà déployé)"
if az functionapp function show -g "$RG" -n "$FUNC" --function-name dispatcher -o none 2>/dev/null; then
  bash "$(dirname "$0")/02_eventgrid.sh"
else
  echo "   dispatcher pas encore déployé : lancer infra/pipeline/02_eventgrid.sh après le déploiement"
fi
