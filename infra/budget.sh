#!/usr/bin/env bash
# =============================================================================
# Membre 2 — Budget de l'abonnement avec alertes par e-mail.
#   Budget mensuel sur tout l'abonnement : alertes à 50 %, 80 % et 100 % du coût réel,
#   et à 100 % du coût prévu (Azure prévient avant le dépassement).
# Le cahier des charges fixe 25 $ ; Azure exprime un budget dans la devise de facturation de
# l'abonnement (EUR ici) : 23 € ≈ 25 $. Idempotent : relancer le script met le budget à jour.
#
# Usage : ALERT_EMAIL=prenom.nom@edu.ece.fr bash infra/budget.sh
# L'adresse n'est jamais commitée : elle est passée en variable d'environnement.
# =============================================================================
set -euo pipefail

ALERT_EMAIL="${ALERT_EMAIL:?adresse e-mail qui reçoit les alertes (jamais commitée)}"
BUDGET_NAME="${BUDGET_NAME:-budget-churn}"
BUDGET_AMOUNT="${BUDGET_AMOUNT:-23}"            # devise de facturation de l'abonnement (EUR ≈ 25 $)
START_DATE="${START_DATE:-$(date -u +%Y-%m-01)}" # 1er jour du mois en cours (exigé par Azure)
END_DATE="${END_DATE:-2027-06-30}"

SUB=$(az account show --query id -o tsv)
URL="https://management.azure.com/subscriptions/$SUB/providers/Microsoft.Consumption/budgets/$BUDGET_NAME?api-version=2023-05-01"

notification() { # seuil (%) type (Actual|Forecasted)
  printf '{"enabled": true, "operator": "GreaterThan", "threshold": %s, "thresholdType": "%s", "contactEmails": ["%s"], "locale": "fr-fr"}' \
    "$1" "$2" "$ALERT_EMAIL"
}

BODY=$(cat <<EOF
{
  "properties": {
    "category": "Cost",
    "amount": $BUDGET_AMOUNT,
    "timeGrain": "Monthly",
    "timePeriod": {"startDate": "${START_DATE}T00:00:00Z", "endDate": "${END_DATE}T00:00:00Z"},
    "notifications": {
      "reel50":   $(notification 50 Actual),
      "reel80":   $(notification 80 Actual),
      "reel100":  $(notification 100 Actual),
      "prevu100": $(notification 100 Forecasted)
    }
  }
}
EOF
)

# az consumption budget create ne gère pas les alertes : appel direct de l'API REST.
# Un ETag est exigé pour modifier un budget existant.
ETAG=$(az rest --method get --url "$URL" --query eTag -o tsv 2>/dev/null || true)
if [ -n "$ETAG" ]; then
  BODY=$(python3 -c 'import json,sys; b=json.loads(sys.argv[1]); b["eTag"]=sys.argv[2]; print(json.dumps(b))' "$BODY" "$ETAG")
fi
az rest --method put --url "$URL" --body "$BODY" -o none

echo "Budget $BUDGET_NAME : $BUDGET_AMOUNT (devise de l'abonnement) par mois, du $START_DATE au $END_DATE"
az rest --method get --url "$URL" \
  --query "{budget:name, montant:properties.amount, depense:properties.currentSpend, alertes:keys(properties.notifications)}" -o json
