# Captures d'écran (membre 4)

Format PNG, nom `NN-sujet.png`. Masquer clés, chaînes de connexion, adresses e-mail et identifiants de souscription.

## Comment les produire (environ 20 min, une fois le pipeline déployé)

1. Remplir le système : `source infra/pipeline/env.sh`, puis `bash infra/pipeline/03_demo.sh valide` (3 fois),
   `bash infra/pipeline/03_demo.sh invalide` et `bash infra/declencher-alerte.sh`. Attendre 10 min.
2. Preuves texte (API, `/api/recent`, stockage, les 3 KQL, alertes) : `bash infra/preuves.sh`.
   Résultats dans [`preuves/`](preuves/), à relire puis commiter.
3. Tableau de bord, sous Windows (Edge suffit), avec l'URL de `preuves/dashboard-url.txt` :
   `.\docs\captures\capture-dashboard.ps1 -Url https://<site>/` produit 02 (et sa version sombre) et 03.
4. Captures du portail Azure et de GitHub (04 à 11) : `Win + Maj + S`, fenêtre du navigateur, zoom 100 %.
   Pour les KQL : *Application Insights > Journaux*, coller le fichier de `docs/kql/`, *Exécuter*, puis capturer
   la requête **et** son résultat.


| Fichier | Contenu | État |
|---|---|---|
| `01-dashboard-demo.png`, `-sombre`, `-mobile` | Tableau de bord en mode démo (données fictives) : bureau, thème sombre, téléphone | fait |
| `02-dashboard-live.png`, `-sombre` | Tableau de bord sur les vraies données (« En direct ») | `capture-dashboard.ps1` |
| `03-dashboard-mobile.png` | Tableau de bord sur téléphone (390 px) | `capture-dashboard.ps1` |
| `04-blob-input-output.png` | Conteneurs `input/`, `output/`, `rejected/` | après déploiement |
| `05-cosmos-inferences.png` | Documents du conteneur `pipeline/inferences` | après déploiement |
| `06-appinsights-map.png` | Carte d'application (Functions → API → Cosmos) | après déploiement |
| `07-kql-1-inferences-heure.png` | Requête KQL 1 : nombre d'inférences par heure | après déploiement |
| `08-kql-2-fichiers-lents.png` | Requête KQL 2 : les 5 fichiers les plus lents | après déploiement |
| `09-kql-3-codes-http.png` | Requête KQL 3 : répartition des codes HTTP et des erreurs | après déploiement |
| `10-alertes.png` | Alerte `alert-fichier-rejete` déclenchée (`infra/declencher-alerte.sh`) + e-mail reçu | après déploiement |
| `11-github-actions.png` | Runs verts de `CI` et `Deploy` (résumé avec `/version` et le commit) | après la première fusion |
