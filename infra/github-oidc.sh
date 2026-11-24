#!/usr/bin/env bash
# =============================================================================
# Membre 4 — Connexion GitHub Actions -> Azure par OIDC, SANS SECRET.
#
# Azure for Students bloque souvent la création d'applications Entra ID
# (az ad sp create-for-rbac => « Insufficient privileges »). On utilise donc une
# IDENTITÉ MANAGÉE ATTRIBUÉE PAR L'UTILISATEUR, avec des identifiants fédérés :
# GitHub présente son jeton OIDC, Azure le vérifie (émetteur + dépôt + environnement)
# et délivre un jeton d'accès de quelques minutes. Rien n'est stocké dans GitHub.
#
# À lancer UNE FOIS par le propriétaire de l'abonnement, après infra/pipeline/01_pipeline.sh
# et la création de la Container App de l'API :
#   source infra/pipeline/env.sh && bash infra/github-oidc.sh
# Ré-exécutable.
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/pipeline/env.sh"
[[ "$EQ" == "<equipe>" ]] && { echo "Renseigner EQ et LOC dans infra/pipeline/env.sh"; exit 1; }

REPO="${REPO:-mkh-dev/telecom-churn-cloud}"
ID_NAME="id-github-$EQ"

echo "== 1. Identité managée $ID_NAME"
az identity create -g "$RG" -n "$ID_NAME" -l "$LOC" --tags projet=dscc equipe="$EQ" -o none
CLIENT_ID=$(az identity show -g "$RG" -n "$ID_NAME" --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show -g "$RG" -n "$ID_NAME" --query principalId -o tsv)

echo "== 2. Identifiants fédérés (seuls ces jetons GitHub sont acceptés)"
# Tous les jobs de deploy.yml utilisent « environment: production » : le sujet du jeton est alors
# repo:<owner>/<repo>:environment:production, quel que soit le déclencheur.
fed() {
  az identity federated-credential create -g "$RG" --identity-name "$ID_NAME" -n "$1" \
    --issuer "https://token.actions.githubusercontent.com" \
    --subject "$2" --audiences "api://AzureADTokenExchange" -o none 2>/dev/null \
  || az identity federated-credential update -g "$RG" --identity-name "$ID_NAME" -n "$1" \
    --issuer "https://token.actions.githubusercontent.com" \
    --subject "$2" --audiences "api://AzureADTokenExchange" -o none
}
fed github-production "repo:$REPO:environment:production"
fed github-main "repo:$REPO:ref:refs/heads/main"

echo "== 3. Rôles (moindre privilège : limités au groupe de ressources du projet)"
RG_ID=$(az group show -n "$RG" --query id -o tsv)
ST_ID=$(az storage account show -g "$RG" -n "$ST" --query id -o tsv)
role() {
  az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
    --role "$1" --scope "$2" -o none 2>/dev/null || true
  echo "   $1 sur $(basename "$2")"
}
# Mise à jour de la Container App et déploiement de la Function App (plan de gestion)
role "Contributor" "$RG_ID"
# Envoi du tableau de bord dans $web et du paquet Flex Consumption (plan de données, sans clé)
role "Storage Blob Data Contributor" "$ST_ID"

SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

cat <<FIN

OK. À créer dans GitHub : Settings > Secrets and variables > Actions > onglet « Variables »
(ce sont des identifiants publics, PAS des secrets) — ou avec la CLI GitHub :

  gh variable set AZURE_CLIENT_ID       -R $REPO -b "$CLIENT_ID"
  gh variable set AZURE_TENANT_ID       -R $REPO -b "$TENANT_ID"
  gh variable set AZURE_SUBSCRIPTION_ID -R $REPO -b "$SUB_ID"
  gh variable set AZURE_RG              -R $REPO -b "$RG"
  gh variable set API_APP               -R $REPO -b "$API_APP"
  gh variable set FUNC_APP              -R $REPO -b "$FUNC"
  gh variable set STORAGE_ACCOUNT       -R $REPO -b "$ST"

Puis : Settings > Environments > New environment > « production » (règle conseillée : branche main seulement).
Supprimer les anciens secrets AZURE_STORAGE_CONNECTION_STRING / AZURE_FUNCTIONAPP_PUBLISH_PROFILE s'ils existent.
Test : Actions > Deploy > Run workflow.
FIN
