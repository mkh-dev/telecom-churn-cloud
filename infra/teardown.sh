#!/usr/bin/env bash
# =============================================================================
# Membre 2 — Suppression finale de toutes les ressources du projet (après la soutenance).
#   1. Relevé des coûts final (infra/cost_report.sh) AVANT de supprimer quoi que ce soit
#   2. Suppression définitive de Log Analytics (sinon 14 jours de suppression réversible)
#   3. Suppression du groupe de ressources (API, environnement, Functions, stockage, Cosmos DB,
#      Event Grid, Application Insights) et attente de la fin
#   4. Vérification : aucune ressource restante étiquetée projet=dscc
#   5. Budget : supprimé seulement avec DELETE_BUDGET=1 (utile tant qu'il reste du crédit)
# Irréversible : le script demande de retaper le nom du groupe, sauf avec YES=1.
#
# Usage : bash infra/teardown.sh            (ou RG=rg-churn YES=1 DELETE_BUDGET=1 bash infra/teardown.sh)
# =============================================================================
set -euo pipefail

RG="${RG:-rg-churn}"
LAW="${LAW:-law-churn}"
BUDGET_NAME="${BUDGET_NAME:-budget-churn}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if ! az group show -n "$RG" -o none 2>/dev/null; then
  echo "Le groupe $RG n'existe pas (déjà supprimé ?)."
else
  echo "== Ressources qui vont être supprimées :"
  az resource list -g "$RG" --query "[].{nom:name, type:type}" -o table

  if [ "${YES:-0}" != "1" ]; then
    read -r -p "Suppression DÉFINITIVE. Retaper le nom du groupe ($RG) pour confirmer : " answer
    [ "$answer" = "$RG" ] || { echo "Annulé."; exit 1; }
  fi

  echo "== 1. Relevé des coûts final (à copier dans docs/rapport-couts.md)"
  RG="$RG" bash "$HERE/cost_report.sh" || echo "(relevé impossible, à faire dans le portail : Gestion des coûts)"

  echo "== 2. Log Analytics $LAW : suppression définitive"
  az monitor log-analytics workspace delete -g "$RG" -n "$LAW" --force true --yes -o none 2>/dev/null || true

  echo "== 3. Groupe de ressources $RG (plusieurs minutes)"
  az group delete -n "$RG" --yes
fi

echo "== 4. Vérification"
LEFT=$(az resource list --tag projet=dscc --query "length(@)" -o tsv)
echo "Ressources restantes étiquetées projet=dscc : $LEFT"
az group list --query "[].name" -o tsv | sed 's/^/  groupe présent : /'
echo "  (NetworkWatcherRG, DefaultResourceGroup-* et les groupes managés sont créés par Azure ; les vérifier dans le portail)"

if [ "${DELETE_BUDGET:-0}" = "1" ]; then
  echo "== 5. Budget $BUDGET_NAME"
  SUB=$(az account show --query id -o tsv)
  az rest --method delete -o none \
    --url "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.Consumption/budgets/$BUDGET_NAME?api-version=2023-05-01"
fi

echo "Penser aussi à : supprimer les secrets GitHub Actions (AZURE_*) et les variables API_BASE_URL / FUNCTIONAPP_NAME."
