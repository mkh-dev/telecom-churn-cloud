#!/usr/bin/env bash
# Membre 3 — Scénarios de la démo / vidéo (preuves de la section 4.3).
#   bash infra/pipeline/03_demo.sh valide      -> fichier valide : résultat en < 1 min
#   bash infra/pipeline/03_demo.sh invalide    -> image.png + CSV sans colonnes -> rejected/
#   bash infra/pipeline/03_demo.sh poison      -> message illisible -> 5 essais -> file poison
#   bash infra/pipeline/03_demo.sh api-coupee  -> API coupée, dépôt d'un CSV valide -> file poison, puis API rétablie
#   bash infra/pipeline/03_demo.sh etat        -> état des files et derniers blobs
#   bash infra/pipeline/03_demo.sh rejouer     -> remet les messages poison dans la file principale
# L'utilisateur doit avoir "Storage Blob Data Contributor" + "Storage Queue Data Contributor"
# sur le compte (sinon remplacer --auth-mode login par --account-key).
set -euo pipefail
source "$(dirname "$0")/env.sh"
SAMPLES="$(dirname "$0")/../../functions/samples"
A=(--account-name "$ST" --auth-mode login -o none)

up() { az storage blob upload "${A[@]}" -c input -f "$1" -n "${2:-$(basename "$1")}" --overwrite; echo "déposé : ${2:-$(basename "$1")}"; }
etat() {
  for q in "$QUEUE" "$QUEUE-poison"; do
    n=$(az storage message peek --account-name "$ST" --auth-mode login -q "$q" --num-messages 32 --query "length(@)" -o tsv)
    echo "file $q : $n message(s) visibles"
  done
  for c in output rejected; do
    echo "--- $c/ (5 derniers)"
    az storage blob list --account-name "$ST" --auth-mode login -c "$c" \
      --query "sort_by(@, &properties.lastModified)[-5:].{nom:name, modifie:properties.lastModified}" -o table
  done
}

case "${1:-}" in
  valide)
    NAME="clients_$(date +%H%M%S).csv"; up "$SAMPLES/clients_valide.csv" "$NAME"
    for i in $(seq 1 12); do
      sleep 5
      if az storage blob list --account-name "$ST" --auth-mode login -c output --prefix "${NAME}_" --query "[0].name" -o tsv | grep -q .; then
        echo "résultat présent après $((i*5)) s"; break; fi
    done ;;
  invalide)
    up "$SAMPLES/image.png"; up "$SAMPLES/clients_colonnes_manquantes.csv"; up "$SAMPLES/clients_latin1.csv"
    sleep 15; az storage blob list --account-name "$ST" --auth-mode login -c rejected -o table --query "[].name" ;;
  poison)
    # Le runtime attend du base64 : un texte non-JSON => PoisonMessageError à chaque essai
    az storage message put --account-name "$ST" --auth-mode login -q "$QUEUE" \
      --content "$(printf 'ceci n est pas un ticket' | base64)" -o none
    echo "message empoisonné envoyé ; ~1 min (5 essais x 10 s de visibilité)"; sleep 75; etat ;;
  api-coupee)
    echo "1) Coupure de l'API (ingress désactivé)"
    az containerapp ingress disable -n "$API_APP" -g "$RG" -o none
    up "$SAMPLES/clients_valide.csv" "api_coupee_$(date +%H%M%S).csv"
    echo "2) Attente des 5 tentatives..."; sleep 90; etat
    echo "3) Rétablissement de l'API"
    az containerapp ingress enable -n "$API_APP" -g "$RG" --type external --target-port 8000 --transport auto -o none ;;
  rejouer)
    # Après correction : on vide la file poison vers la file principale (traitement idempotent)
    while :; do
      m=$(az storage message get --account-name "$ST" --auth-mode login -q "$QUEUE-poison" -o json)
      [[ "$m" == "[]" ]] && break
      az storage message put --account-name "$ST" --auth-mode login -q "$QUEUE" \
        --content "$(jq -r '.[0].content' <<<"$m")" -o none
      az storage message delete --account-name "$ST" --auth-mode login -q "$QUEUE-poison" \
        --id "$(jq -r '.[0].id' <<<"$m")" --pop-receipt "$(jq -r '.[0].popReceipt' <<<"$m")" -o none
      echo "rejoué"
    done ;;
  etat) etat ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
