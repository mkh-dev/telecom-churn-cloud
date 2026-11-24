#!/usr/bin/env bash
# =============================================================================
# Membre 2 — Déploiement de l'API d'inférence sur Azure Container Apps.
#   Fournisseurs -> groupe de ressources -> Log Analytics -> environnement Container Apps
#   -> Container App (0,25 vCPU / 0,5 Gi, 0 à 2 réplicas, sondes /health) -> Application Insights
# Idempotent : relancer le script met à jour l'image et les variables sans recréer l'application.
#
# Usage :
#   bash infra/api/deploy_api.sh                                  # image ghcr.io/...:<IMAGE_TAG>
#   IMAGE_TAG=sha-3f2c1e9 bash infra/api/deploy_api.sh             # déploiement d'un commit précis
#   MODEL_FILE=model-2.0.0.joblib REVISION_SUFFIX=modelb bash ...  # bonus A/B (voir api/deploy/containerapp.md)
# Noms communs à l'équipe : infra/pipeline/env.sh (membre 3) et infra/monitoring.sh (membre 4).
# =============================================================================
set -euo pipefail

RG="${RG:-rg-churn}"
LOC="${LOC:-francecentral}"            # seule région retenue (README, section 2)
EQ="${EQ:-churn}"
LAW="${LAW:-law-churn}"                # même espace que infra/monitoring.sh
APPI="${APPI:-appi-churn}"
CAE="${CAE:-cae-churn}"                # environnement Container Apps
API_APP="${API_APP:-api-churn}"        # nom attendu par infra/pipeline/01_pipeline.sh
IMAGE_TAG="${IMAGE_TAG:-0.3.0}"
IMAGE="${IMAGE:-ghcr.io/mkh-dev/telecom-churn-cloud/api:$IMAGE_TAG}"
MODEL_FILE="${MODEL_FILE:-model-1.0.0.joblib}"
RISK_THRESHOLD="${RISK_THRESHOLD:-0.5}"
REVISION_SUFFIX="${REVISION_SUFFIX:-}"

echo "== 0. Fournisseurs de ressources (une fois par abonnement)"
for ns in Microsoft.App Microsoft.OperationalInsights; do
  az provider register -n "$ns" --wait -o none
done

echo "== 1. Groupe de ressources $RG ($LOC), étiquettes FinOps"
az group create -n "$RG" -l "$LOC" --tags projet=dscc equipe="$EQ" -o none

echo "== 2. Log Analytics $LAW (rétention 30 j, plafond 0,1 Go/jour)"
if ! az monitor log-analytics workspace show -g "$RG" -n "$LAW" -o none 2>/dev/null; then
  az monitor log-analytics workspace create -g "$RG" -n "$LAW" -l "$LOC" --retention-time 30 \
    --only-show-errors -o none
fi
az monitor log-analytics workspace update -g "$RG" -n "$LAW" --quota 0.1 --only-show-errors -o none

echo "== 3. Environnement Container Apps $CAE"
if ! az containerapp env show -g "$RG" -n "$CAE" -o none 2>/dev/null; then
  LAW_CID=$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query customerId -o tsv)
  # La clé n'est ni affichée ni écrite sur disque : elle passe directement à az.
  az containerapp env create -g "$RG" -n "$CAE" -l "$LOC" \
    --logs-workspace-id "$LAW_CID" \
    --logs-workspace-key "$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LAW" \
      --query primarySharedKey -o tsv)" \
    --tags projet=dscc equipe="$EQ" -o none
fi

ENV_VARS=(MODEL_PATH="/app/models/$MODEL_FILE" RISK_THRESHOLD="$RISK_THRESHOLD")

echo "== 4. Container App $API_APP ($IMAGE)"
if ! az containerapp show -g "$RG" -n "$API_APP" -o none 2>/dev/null; then
  CAE_ID=$(az containerapp env show -g "$RG" -n "$CAE" --query id -o tsv)
  SPEC=$(mktemp -t containerapp.XXXXXX)
  trap 'rm -f "$SPEC"' EXIT
  # YAML : seul moyen de définir les sondes dès la création (az containerapp create n'a pas d'option).
  cat > "$SPEC" <<EOF
location: $LOC
tags: {projet: dscc, equipe: $EQ}
properties:
  managedEnvironmentId: $CAE_ID
  configuration:
    activeRevisionsMode: Single
    ingress:
      external: true
      targetPort: 8000
      transport: auto
      allowInsecure: false
  template:
    containers:
      - name: api
        image: $IMAGE
        resources: {cpu: 0.25, memory: 0.5Gi}
        env:
          - {name: MODEL_PATH, value: /app/models/$MODEL_FILE}
          - {name: RISK_THRESHOLD, value: "$RISK_THRESHOLD"}
        probes:
          - type: Liveness
            httpGet: {path: /health, port: 8000}
            initialDelaySeconds: 10
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 3
          - type: Readiness
            httpGet: {path: /health, port: 8000}
            initialDelaySeconds: 3
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
    scale:
      minReplicas: 0
      maxReplicas: 2
      rules:
        - name: http
          http: {metadata: {concurrentRequests: "20"}}
EOF
  az containerapp create -g "$RG" -n "$API_APP" --yaml "$SPEC" -o none
else
  # Mise à jour sans toucher aux secrets ni aux sondes déjà en place
  SUFFIX_ARGS=()
  [ -n "$REVISION_SUFFIX" ] && SUFFIX_ARGS=(--revision-suffix "$REVISION_SUFFIX")
  az containerapp update -g "$RG" -n "$API_APP" --image "$IMAGE" \
    --cpu 0.25 --memory 0.5Gi --min-replicas 0 --max-replicas 2 \
    --set-env-vars "${ENV_VARS[@]}" ${SUFFIX_ARGS[@]+"${SUFFIX_ARGS[@]}"} -o none
fi

echo "== 5. Application Insights (si $APPI existe : secret + variable, sinon infra/monitoring.sh s'en charge)"
if az monitor app-insights component show -g "$RG" -a "$APPI" -o none 2>/dev/null; then
  az containerapp secret set -g "$RG" -n "$API_APP" -o none --secrets \
    "appinsights-cs=$(az monitor app-insights component show -g "$RG" -a "$APPI" --query connectionString -o tsv)"
  az containerapp update -g "$RG" -n "$API_APP" -o none \
    --set-env-vars APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appinsights-cs
fi

URL="https://$(az containerapp show -g "$RG" -n "$API_APP" --query properties.configuration.ingress.fqdn -o tsv)"
echo "== 6. Vérification de $URL (démarrage à froid possible : 0 réplica au repos)"
for _ in $(seq 1 30); do
  curl -sf --max-time 30 "$URL/health" >/dev/null && break
  sleep 5
done
curl -s "$URL/health"; echo
curl -s "$URL/version"; echo
echo "API : $URL   (à mettre dans API_URL du membre 3 et API_BASE_URL du membre 4)"
