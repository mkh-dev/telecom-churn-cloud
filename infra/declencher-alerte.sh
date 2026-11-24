#!/usr/bin/env bash
# =============================================================================
# Membre 4 — Déclenche VOLONTAIREMENT l'alerte « alert-fichier-rejete » (exigence du sujet :
# une alerte déclenchée au moins une fois), puis attend qu'elle apparaisse dans Azure Monitor.
#
#   1. dépose un CSV sans les colonnes obligatoires dans input/ ;
#   2. le dispatcher le rejette (rejected/) et écrit la trace DISPATCH_REJECTED ;
#   3. la règle KQL, évaluée toutes les 5 min, se déclenche et envoie l'e-mail.
#
# Délai typique : 5 à 10 min (ingestion des traces + évaluation de la règle).
# Prérequis : rôle Storage Blob Data Contributor sur le compte (ou remplacer --auth-mode login).
# Usage : source infra/pipeline/env.sh && bash infra/declencher-alerte.sh
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/pipeline/env.sh"
SAMPLE="$(dirname "$0")/../functions/samples/clients_colonnes_manquantes.csv"
NAME="alerte-demo-$(date +%Y%m%d-%H%M%S).csv"

echo "== Dépôt de $NAME (colonnes manquantes) dans input/"
az storage blob upload --account-name "$ST" --auth-mode login -c input -f "$SAMPLE" -n "$NAME" --overwrite -o none
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "== Attente du rejet par le dispatcher"
for i in $(seq 1 12); do
  if az storage blob list --account-name "$ST" --auth-mode login -c rejected --prefix "$NAME" \
       --query "[0].name" -o tsv | grep -q .; then
    echo "   rejeté après $((i * 5)) s (rejected/$NAME)"; break
  fi
  sleep 5
done

echo "== Attente du déclenchement de l'alerte (jusqu'à 15 min)"
SUB=$(az account show --query id -o tsv)
URL="https://management.azure.com/subscriptions/$SUB/providers/Microsoft.AlertsManagement/alerts?api-version=2019-05-05-preview&timeRange=1h&alertRule=alert-fichier-rejete"
for i in $(seq 1 30); do
  FIRED=$(az rest --method get --url "$URL" \
    --query "value[?properties.essentials.startDateTime >= '$START'] | [0].properties.essentials.{etat:monitorCondition, debut:startDateTime, severite:severity}" -o json)
  if [ "$FIRED" != "null" ] && [ -n "$FIRED" ]; then
    echo "   ALERTE DÉCLENCHÉE : $FIRED"
    echo "   Capture : portail > Monitor > Alertes (et l'e-mail reçu) -> docs/captures/10-alertes.png"
    exit 0
  fi
  echo "   $((i * 30)) s : pas encore…"; sleep 30
done
echo "Pas d'alerte après 15 min : vérifier la trace dans Application Insights :"
echo "  traces | where message startswith 'DISPATCH_REJECTED' | order by timestamp desc"
exit 1
