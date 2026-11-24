#!/usr/bin/env bash
# =============================================================================
# Membre 4 — Supervision : Log Analytics + Application Insights, branchement des
# services, groupe d'actions et alertes.
#
# Noms communs à l'équipe : infra/pipeline/env.sh (RG, ST, FUNC, AI, LAW, API_APP).
# Ordre conseillé : lancer CE script AVANT 01_pipeline.sh (la Function App est alors créée
# directement avec Application Insights), puis le relancer une fois tout déployé pour
# créer les alertes sur le stockage et brancher la Container App. Ré-exécutable.
#
# Usage : source infra/pipeline/env.sh && ALERT_EMAIL=<adresse> bash infra/monitoring.sh
# (l'adresse est passée en variable d'environnement, jamais commitée)
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/pipeline/env.sh"
[[ "$EQ" == "<equipe>" || "$LOC" == "<region>" ]] && { echo "Renseigner EQ et LOC dans infra/pipeline/env.sh"; exit 1; }
ALERT_EMAIL="${ALERT_EMAIL:?ALERT_EMAIL=<adresse qui reçoit les alertes>}"
LATENCY_SLO_MS="${LATENCY_SLO_MS:-2000}"        # critère de succès : réponse < 2 s
exists() { "$@" -o none 2>/dev/null; }

echo "== 1. Log Analytics + Application Insights (mode espace de travail)"
exists az group show -n "$RG" || az group create -n "$RG" -l "$LOC" --tags projet=dscc equipe="$EQ" -o none
az monitor log-analytics workspace create -g "$RG" -n "$LAW" -l "$LOC" --retention-time 30 --only-show-errors -o none
# Plafond de collecte journalier : protège le budget (0,1 Go/jour, largement suffisant pour la démo)
az monitor log-analytics workspace update -g "$RG" -n "$LAW" --quota 0.1 --only-show-errors -o none
LAW_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query id -o tsv)
az monitor app-insights component create -g "$RG" -a "$AI" -l "$LOC" \
  --kind web --application-type web --workspace "$LAW_ID" --only-show-errors -o none
AI_ID=$(az monitor app-insights component show -g "$RG" -a "$AI" --query id -o tsv)
AI_CS=$(az monitor app-insights component show -g "$RG" -a "$AI" --query connectionString -o tsv)

echo "== 2. Branchement des services (la chaîne de connexion n'est pas un secret d'accès,"
echo "      mais elle reste hors du dépôt : elle est lue ici et passée directement à Azure)"
if exists az functionapp show -g "$RG" -n "$FUNC"; then
  az functionapp config appsettings set -g "$RG" -n "$FUNC" \
    --settings "APPLICATIONINSIGHTS_CONNECTION_STRING=$AI_CS" --only-show-errors -o none
  echo "   Function App $FUNC : OK"
fi
if exists az containerapp show -g "$RG" -n "$API_APP"; then
  az containerapp secret set -g "$RG" -n "$API_APP" --secrets "appinsights-cs=$AI_CS" -o none
  az containerapp update -g "$RG" -n "$API_APP" \
    --set-env-vars APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appinsights-cs -o none
  echo "   Container App $API_APP : OK"
fi

echo "== 3. Groupe d'actions (e-mail)"
az monitor action-group create -g "$RG" -n "ag-$EQ" --short-name "churn$EQ" \
  --action email equipe "$ALERT_EMAIL" --only-show-errors -o none
AG_ID=$(az monitor action-group show -g "$RG" -n "ag-$EQ" --query id -o tsv)

echo "== 4. Alertes"
# Requête planifiée : déclenchée dès que la requête KQL renvoie au moins une ligne sur la fenêtre.
kql_alert() {  # nom sévérité fenêtre description requête
  az monitor scheduled-query create -g "$RG" -n "$1" --scopes "$AI_ID" --location "$LOC" \
    --condition "count 'Q' > 0" --condition-query Q="$5" \
    --window-size "$3" --evaluation-frequency 5m --severity "$2" \
    --description "$4" --action-groups "$AG_ID" --only-show-errors -o none
  echo "   $1"
}

# 4.1 Fichier rejeté par le dispatcher (mauvaise extension, colonnes manquantes, encodage…).
#     C'est l'alerte qu'on DÉCLENCHE VOLONTAIREMENT pour la démo : infra/declencher-alerte.sh
kql_alert alert-fichier-rejete 3 5m \
  "Au moins un fichier déposé dans input/ a été rejeté (déplacé dans rejected/)" \
  "traces | where message startswith 'DISPATCH_REJECTED'"

# 4.2 Échec du worker (API injoignable, Cosmos DB…) : le message sera retenté puis mis en poison
kql_alert alert-echec-worker 2 5m \
  "Le worker a échoué sur au moins un message (nouvelle tentative ou file poison)" \
  "traces | where message startswith 'WORKER_FAILED'"

# 4.3 Erreurs serveur (5xx) de l'API ou de /api/recent
az monitor metrics alert create -g "$RG" -n alert-erreurs-5xx --scopes "$AI_ID" \
  --condition "count requests/failed > 3" --window-size 5m --evaluation-frequency 1m --severity 2 \
  --description "Plus de 3 requêtes en échec en 5 min (API /predict ou Functions)" \
  --action "$AG_ID" --only-show-errors -o none
echo "   alert-erreurs-5xx"

# 4.4 Temps de réponse moyen (API + Functions) supérieur à l'objectif de 2 s
az monitor metrics alert create -g "$RG" -n alert-latence --scopes "$AI_ID" \
  --condition "avg requests/duration > $LATENCY_SLO_MS" --window-size 15m --evaluation-frequency 5m --severity 3 \
  --description "Temps de réponse moyen > ${LATENCY_SLO_MS} ms sur 15 min" \
  --action "$AG_ID" --only-show-errors -o none
echo "   alert-latence"

# 4.5 Messages en file poison : un fichier n'a pas pu être traité après 5 tentatives
if exists az storage account show -g "$RG" -n "$ST"; then
  ST_ID=$(az storage account show -g "$RG" -n "$ST" --query id -o tsv)
  az monitor metrics alert create -g "$RG" -n alert-file-en-attente \
    --scopes "$ST_ID/queueServices/default" --condition "avg QueueMessageCount > 20" \
    --window-size 1h --evaluation-frequency 15m --severity 3 \
    --description "Plus de 20 messages en attente dans les files de $ST (worker arrêté ?)" \
    --action "$AG_ID" --only-show-errors -o none
  echo "   alert-file-en-attente"
fi

cat <<FIN

OK. Application Insights : $AI (région $LOC)
Requêtes KQL : docs/kql/requetes.kql
Déclencher une alerte pour la démo : source infra/pipeline/env.sh && bash infra/declencher-alerte.sh
FIN
