#!/usr/bin/env bash
# Supervision du pipeline (membre 4) : Application Insights, branchement des services, alertes,
# et hébergement du tableau de bord (site web statique + CORS).
#
# Prérequis : az login ; ressources du membre 2/3 déjà créées (groupe, stockage, Function App, Container App).
# Usage    : RG=rg-churn ALERT_EMAIL=moi@exemple.fr bash infra/monitoring.sh
# Idempotent : relancer le script met à jour les ressources existantes.
set -euo pipefail

# ---------- Paramètres (à adapter, ou passer en variables d'environnement) ----------
RG="${RG:-rg-churn}"
LOCATION="${LOCATION:-francecentral}"           # seule région retenue (voir README, section 2)
LAW="${LAW:-law-churn}"                         # espace de travail Log Analytics
APPI="${APPI:-appi-churn}"                      # Application Insights
STORAGE="${STORAGE:?nom du compte de stockage (input/, output/, file, \$web)}"
FUNC_APP="${FUNC_APP:-}"                        # Function App du membre 3 (facultatif)
CONTAINER_APP="${CONTAINER_APP:-}"              # Container App de l'API (facultatif)
ALERT_EMAIL="${ALERT_EMAIL:?adresse e-mail qui reçoit les alertes (jamais commitée)}"
LATENCY_SLO_MS="${LATENCY_SLO_MS:-2000}"        # critère de succès : réponse < 2 s

echo "== 1. Log Analytics + Application Insights (mode espace de travail)"
az monitor log-analytics workspace create -g "$RG" -n "$LAW" -l "$LOCATION" \
  --retention-time 30 --only-show-errors -o none
# Plafond de collecte journalier : protège le budget Azure for Students (0,1 Go/jour)
az monitor log-analytics workspace update -g "$RG" -n "$LAW" --quota 0.1 --only-show-errors -o none
LAW_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query id -o tsv)

az monitor app-insights component create -g "$RG" -a "$APPI" -l "$LOCATION" \
  --kind web --application-type web --workspace "$LAW_ID" --only-show-errors -o none
APPI_ID=$(az monitor app-insights component show -g "$RG" -a "$APPI" --query id -o tsv)
APPI_CS=$(az monitor app-insights component show -g "$RG" -a "$APPI" --query connectionString -o tsv)

echo "== 2. Branchement des services sur Application Insights"
if [ -n "$FUNC_APP" ]; then
  az functionapp config appsettings set -g "$RG" -n "$FUNC_APP" \
    --settings "APPLICATIONINSIGHTS_CONNECTION_STRING=$APPI_CS" --only-show-errors -o none
  echo "   Function App $FUNC_APP : OK"
fi
if [ -n "$CONTAINER_APP" ]; then
  # Secret + variable, comme décrit dans api/deploy/containerapp.md (membre 2)
  az containerapp secret set -g "$RG" -n "$CONTAINER_APP" --secrets "appinsights-cs=$APPI_CS" -o none
  az containerapp update -g "$RG" -n "$CONTAINER_APP" \
    --set-env-vars APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appinsights-cs -o none
  echo "   Container App $CONTAINER_APP : OK"
fi

echo "== 3. Groupe d'actions (e-mail)"
az monitor action-group create -g "$RG" -n ag-churn --short-name churn \
  --action email equipe "$ALERT_EMAIL" --only-show-errors -o none
AG_ID=$(az monitor action-group show -g "$RG" -n ag-churn --query id -o tsv)

echo "== 4. Alertes"
# 4.1 Échecs HTTP (API + Functions) : plus de 5 requêtes en échec en 5 minutes
az monitor metrics alert create -g "$RG" -n "alert-requetes-en-echec" \
  --scopes "$APPI_ID" --condition "count requests/failed > 5" \
  --window-size 5m --evaluation-frequency 1m --severity 2 \
  --description "Plus de 5 requêtes en échec en 5 min (API /predict ou Functions)" \
  --action "$AG_ID" --only-show-errors -o none

# 4.2 Temps de réponse moyen supérieur à l'objectif de 2 s
az monitor metrics alert create -g "$RG" -n "alert-latence" \
  --scopes "$APPI_ID" --condition "avg requests/duration > $LATENCY_SLO_MS" \
  --window-size 15m --evaluation-frequency 5m --severity 3 \
  --description "Temps de réponse moyen > ${LATENCY_SLO_MS} ms sur 15 min" \
  --action "$AG_ID" --only-show-errors -o none

# 4.3 Exceptions dans les Functions (fichier invalide mal géré, API injoignable, Cosmos DB…)
az monitor scheduled-query create -g "$RG" -n "alert-exceptions-pipeline" \
  --scopes "$APPI_ID" --location "$LOCATION" \
  --condition "count 'Exc' > 0" \
  --condition-query Exc="exceptions${FUNC_APP:+ | where cloud_RoleName =~ '$FUNC_APP'}" \
  --window-size 10m --evaluation-frequency 5m --severity 2 \
  --description "Au moins une exception levée dans le pipeline sur 10 min" \
  --action-groups "$AG_ID" --only-show-errors -o none

# 4.4 Messages bloqués dans la file inference-requests (le worker ne consomme plus)
STORAGE_ID=$(az storage account show -g "$RG" -n "$STORAGE" --query id -o tsv)
az monitor metrics alert create -g "$RG" -n "alert-file-en-attente" \
  --scopes "$STORAGE_ID/queueServices/default" --condition "avg QueueMessageCount > 20" \
  --window-size 1h --evaluation-frequency 15m --severity 3 \
  --description "Plus de 20 messages en attente dans les files du compte $STORAGE" \
  --action "$AG_ID" --only-show-errors -o none

echo "== 5. Tableau de bord : site web statique + CORS"
az storage blob service-properties update --account-name "$STORAGE" --auth-mode login \
  --static-website --index-document index.html --404-document index.html -o none
WEB_URL=$(az storage account show -g "$RG" -n "$STORAGE" --query primaryEndpoints.web -o tsv)
WEB_ORIGIN="${WEB_URL%/}"
if [ -n "$FUNC_APP" ]; then
  # /api/recent est appelé depuis le navigateur : la Function App doit autoriser l'origine du site
  az functionapp cors add -g "$RG" -n "$FUNC_APP" --allowed-origins "$WEB_ORIGIN" -o none || true
fi

echo
echo "Application Insights : $APPI"
echo "Tableau de bord      : $WEB_URL"
echo "Secret GitHub AZURE_STORAGE_CONNECTION_STRING :"
echo "  az storage account show-connection-string -g $RG -n $STORAGE -o tsv | gh secret set AZURE_STORAGE_CONNECTION_STRING"
if [ -n "$FUNC_APP" ]; then
  echo "Variable GitHub API_BASE_URL : https://$(az functionapp show -g "$RG" -n "$FUNC_APP" --query defaultHostName -o tsv)"
fi
