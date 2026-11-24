#!/usr/bin/env bash
# =============================================================================
# Membre 4 — Collecte des PREUVES texte sur l'environnement réel, pour le README et la soutenance :
#   API (health, version, predict), /api/recent, tableau de bord, blobs output/ et rejected/,
#   files d'attente, les 3 requêtes KQL du sujet, règles d'alerte et alertes déclenchées.
# Résultats : docs/captures/preuves/  (texte et Markdown, à commiter avec les captures PNG).
#
# Aucun identifiant de souscription, clé ou adresse e-mail n'est écrit dans les fichiers.
# Prérequis : az login ; déploiement fait (README §8) ; quelques fichiers déjà traités
#   (bash infra/pipeline/03_demo.sh valide, bash infra/declencher-alerte.sh).
# Usage : source infra/pipeline/env.sh && bash infra/preuves.sh
# =============================================================================
set -uo pipefail                      # pas de -e : une preuve manquante ne bloque pas les autres
source "$(dirname "$0")/pipeline/env.sh"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/captures/preuves"
KQL="$ROOT/docs/kql"
mkdir -p "$OUT"
az config set extension.use_dynamic_install=yes_without_prompt -o none 2>/dev/null
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ok() { echo "   OK  $1"; }
ko() { echo "   --  $1 : $2"; }

echo "== 1. API (Container Apps)"
API_FQDN=$(az containerapp show -g "$RG" -n "$API_APP" --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null)
if [ -n "$API_FQDN" ]; then
  API="https://$API_FQDN"
  {
    echo "# API en ligne — relevé du $NOW"
    echo; echo "URL : $API"
    for p in health version metrics; do
      echo; echo "## GET /$p"; echo '```json'
      curl -s --max-time 40 "$API/$p"; echo; echo '```'
    done
    echo; echo "## POST /predict (api/dev/sample_request.json)"; echo '```'
    curl -s --max-time 40 -o /tmp/predict.json -w "HTTP %{http_code} en %{time_total} s\n" \
      -X POST "$API/predict" -H 'Content-Type: application/json' -d @"$ROOT/api/dev/sample_request.json"
    python3 -c "import json;d=json.load(open('/tmp/predict.json'));print('model_version',d['model_version'],'| latency_ms',d['latency_ms']);[print(p) for p in d['predictions'][:3]]"
    echo '```'
  } > "$OUT/api.md" && ok "api.md"
else ko "API" "Container App $API_APP introuvable dans $RG"; fi

echo "== 2. /api/recent (Function App) et tableau de bord"
FUNC_HOST=$(az functionapp show -g "$RG" -n "$FUNC" --query defaultHostName -o tsv 2>/dev/null)
if [ -n "$FUNC_HOST" ]; then
  curl -s --max-time 40 "https://$FUNC_HOST/api/recent?limit=20" | python3 -m json.tool > "$OUT/api-recent.json" \
    && ok "api-recent.json" || ko "/api/recent" "réponse non JSON"
  az functionapp function list -g "$RG" -n "$FUNC" --query "[].{fonction:name, declencheur:config.bindings[0].type}" -o table \
    > "$OUT/functions.txt" 2>&1 && ok "functions.txt"
else ko "Function App" "$FUNC introuvable dans $RG"; fi
WEB=$(az storage account show -g "$RG" -n "$ST" --query primaryEndpoints.web -o tsv 2>/dev/null)
if [ -n "$WEB" ]; then
  echo "$WEB" > "$OUT/dashboard-url.txt"
  echo "   URL du tableau de bord : $WEB  (captures : pwsh docs/captures/capture-dashboard.ps1 -Url $WEB)"
fi

echo "== 3. Stockage : output/, rejected/, files d'attente"
{
  echo "# Stockage $ST — relevé du $NOW"
  for c in input output rejected; do
    echo; echo "## $c/ (10 derniers)"; echo '```'
    az storage blob list --account-name "$ST" --auth-mode login -c "$c" \
      --query "sort_by(@, &properties.lastModified)[-10:].{nom:name, taille:properties.contentLength, modifie:properties.lastModified}" \
      -o table 2>&1
    echo '```'
  done
  echo; echo "## Files d'attente"; echo '```'
  for q in "$QUEUE" "$QUEUE-poison"; do
    n=$(az storage message peek --account-name "$ST" --auth-mode login -q "$q" --num-messages 32 --query "length(@)" -o tsv 2>&1)
    echo "$q : $n message(s) visibles"
  done
  echo '```'
} > "$OUT/stockage.md" && ok "stockage.md"

echo "== 4. Requêtes KQL (Application Insights $AI)"
kql() {  # fichier.kql période sortie titre
  az monitor app-insights query -g "$RG" --app "$AI" --offset "$2" \
    --analytics-query "$(grep -v '^ *//' "$KQL/$1")" -o json > /tmp/kql.json 2>/tmp/kql.err \
  || { ko "$1" "$(head -c 200 /tmp/kql.err)"; return; }
  python3 - "$4" "$1" "$2" > "$OUT/$3" <<'PY'
import json, sys
title, src, period = sys.argv[1:4]
t = json.load(open("/tmp/kql.json"))["tables"][0]
cols = [c["name"] for c in t["columns"]]
print(f"# {title}\n\nRequête : `docs/kql/{src}` — période : {period}\n")
print("| " + " | ".join(cols) + " |"); print("|" + "---|" * len(cols))
for r in t["rows"]:
    print("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
print(f"\n{len(t['rows'])} ligne(s)." + ("" if t["rows"] else " Aucune donnée : déposer des fichiers puis relancer (délai d'ingestion 2 à 5 min)."))
PY
  ok "$3"
}
kql 1-inferences-par-heure.kql 24h kql-1-inferences-par-heure.md "Nombre d'inférences par heure"
kql 2-fichiers-les-plus-lents.kql 7d kql-2-fichiers-les-plus-lents.md "Les 5 fichiers les plus lents"
kql 3-codes-http.kql 24h kql-3-codes-http.md "Répartition des codes HTTP"
kql 3b-erreurs-pipeline.kql 7d kql-3b-erreurs-pipeline.md "Répartition des erreurs du pipeline"
kql bonus-latence-ab.kql 7d kql-bonus-latence-ab.md "Latence du modèle par version"

echo "== 5. Alertes"
{
  echo "# Alertes — relevé du $NOW"
  echo; echo "## Règles"; echo '```'
  az monitor metrics alert list -g "$RG" --query "[].{regle:name, severite:severity, active:enabled}" -o table 2>&1
  az monitor scheduled-query list -g "$RG" --query "[].{regle:name, severite:severity, active:enabled}" -o table 2>&1
  echo '```'
  echo; echo "## Alertes déclenchées (7 derniers jours)"; echo '```'
  SUB=$(az account show --query id -o tsv)
  az rest --method get \
    --url "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.AlertsManagement/alerts?api-version=2019-05-05-preview&timeRange=7d" \
    --query "value[].properties.essentials.{regle:alertRule, etat:monitorCondition, severite:severity, debut:startDateTime}" -o table 2>&1 \
    | sed 's#/subscriptions/[^ ]*/providers/[^ ]*/##g'
  echo '```'
} > "$OUT/alertes.md" && ok "alertes.md"

echo
echo "Preuves enregistrées dans docs/captures/preuves/. À vérifier avant de commiter (aucune donnée personnelle)."
