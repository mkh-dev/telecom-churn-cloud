#!/usr/bin/env bash
# =============================================================================
# Membre 2 — Relevé des coûts réels du projet (API Cost Management), en tableau Markdown
# prêt à coller dans docs/rapport-couts.md. Lecture seule.
# Les coûts apparaissent avec 8 à 24 h de retard dans Cost Management.
#
# Usage : bash infra/cost_report.sh                         # groupe rg-churn, depuis le 1er du mois
#         FROM=2026-11-24 TO=2026-12-05 bash infra/cost_report.sh
# =============================================================================
set -euo pipefail

RG="${RG:-rg-churn}"
FROM="${FROM:-$(date -u +%Y-%m-01)}"
TO="${TO:-$(date -u +%Y-%m-%d)}"

SUB=$(az account show --query id -o tsv)
BODY=$(cat <<EOF
{
  "type": "ActualCost",
  "timeframe": "Custom",
  "timePeriod": {"from": "${FROM}T00:00:00Z", "to": "${TO}T23:59:59Z"},
  "dataset": {
    "granularity": "None",
    "aggregation": {"cost": {"name": "Cost", "function": "Sum"}},
    "grouping": [{"type": "Dimension", "name": "ServiceName"}],
    "filter": {"dimensions": {"name": "ResourceGroupName", "operator": "In", "values": ["$RG"]}}
  }
}
EOF
)

# Cost Management limite le débit (erreur 429) : jusqu'à 4 tentatives espacées de 30 s.
ROWS=""
for attempt in 1 2 3 4; do
  if ROWS=$(az rest --method post -o json --body "$BODY" --query "properties.rows" \
      --url "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.CostManagement/query?api-version=2023-11-01" \
      2>/tmp/cost_report.err); then
    break
  fi
  ROWS=""
  echo "Tentative $attempt : $(tail -1 /tmp/cost_report.err)" >&2
  [ "$attempt" -lt 4 ] && sleep 30
done
[ -n "$ROWS" ] || { echo "Relevé impossible pour l'instant (voir le message ci-dessus) : réessayer plus tard." >&2; exit 1; }

echo "$ROWS" | python3 -c '
import json, sys
rows = json.loads(sys.stdin.read() or "[]") or []
fr = lambda x: f"{x:.2f}".replace(".", ",")
rg, start, end = sys.argv[1:4]
print(f"Coûts réels du groupe {rg}, du {start} au {end} :\n")
print("| Service | Coût |")
print("|---|---|")
total, currency = 0.0, "EUR"
for cost, service, currency in sorted(rows, key=lambda r: -r[0]):
    total += cost
    print(f"| {service} | {fr(cost)} {currency} |")
print(f"| **Total** | **{fr(total)} {currency}** |")
' "$RG" "$FROM" "$TO"
