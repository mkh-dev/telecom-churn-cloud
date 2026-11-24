# Telecom Churn Cloud : pipeline ML événementiel sur Azure

Projet de synthèse ECE Paris, module « Systèmes distribués et IA » (2026-2027).

Un fichier CSV de clients télécom déposé dans Blob Storage (`input/`) est validé, mis en file d'attente,
envoyé à une API de Machine Learning (FastAPI sur Azure Container Apps) qui prédit le risque de désabonnement
de chaque client ; les résultats sont stockés (Blob `output/` + Cosmos DB) et affichés dans un tableau de bord.

## Équipe

| Membre | Rôle | Dossiers |
|---|---|---|
| Membre 1 | Machine Learning | `/model` |
| Membre 2 | API, Docker, compte Azure, coûts | `/api`, `/infra` |
| Membre 3 | Pipeline événementiel (Functions) | `/functions` |
| Membre 4 | Dashboard, CI/CD, supervision, documentation | `/web`, `/.github/workflows`, `/docs` |

Les formats échangés entre les parties sont dans [`docs/contrats.md`](docs/contrats.md).

## Structure

```
/model               train.py, churn_model.py (fonction de prédiction), tests, requirements.txt
/api                 FastAPI, Dockerfile multi-étapes, tests
/functions           function_app.py (dispatcher, worker, recent), host.json
/web                 tableau de bord statique
/infra               script az commenté (Bicep en bonus)
/docs                contrats, schéma, captures, rapport de coûts, journal de temps
/.github/workflows   ci.yml, deploy.yml
```

## 1. Jeu de données et modèle (membre 1)

- **Jeu de données** : IBM Telco Customer Churn, dépôt [IBM/telco-customer-churn-on-icp4d](https://github.com/IBM/telco-customer-churn-on-icp4d), licence Apache-2.0. Environ 7 000 clients, 21 colonnes, moins de 1 Mo.
- **Tâche** : classification binaire, le client part-il (`Churn = Yes`) ?
- **Préparation** : `TotalCharges` converti en nombre (valeurs vides imputées par la médiane), numériques normalisées, catégorielles en one-hot, le tout dans un seul `Pipeline` scikit-learn exporté.
- **Protocole** : jeu de test stratifié de 20 %, graine 42, versions figées dans `model/requirements.txt` (Python 3.12, numpy 2.5.3, pandas 3.0.5, scikit-learn 1.9.1, joblib 1.6.0).
- **Fichiers produits** (versionnés dans le dépôt) : `model/artifacts/model-1.0.0.joblib`, `metrics-1.0.0.json`, `sample-clients.csv` (fichier de démo pour le pipeline) et une copie du modèle dans `api/models/`. L'API doit installer les mêmes versions de numpy, pandas, scikit-learn et joblib, sinon le modèle ne se charge pas.
- **Modèles** : régression logistique et Random Forest (`class_weight="balanced"`), comparés à une référence naïve qui prédit toujours « ne part pas ». Le meilleur en ROC-AUC devient `model-1.0.0.joblib`.

Reproduire :

```bash
cd model
pip install -r requirements.txt
python train.py        # télécharge les données, entraîne, écrit artifacts/
pytest -q tests
```

Résultats sur le jeu de test (1 409 clients, 26,5 % de churn ; détail dans `model/artifacts/metrics-1.0.0.json`) :

| Modèle | ROC-AUC | F1 churn | Précision | Rappel | Accuracy |
|---|---|---|---|---|---|
| Référence naïve (toujours « Non ») | 0,500 | 0,000 | 0,000 | 0,000 | 0,735 |
| **Régression logistique (v1.0.0)** | **0,841** | 0,614 | 0,504 | 0,783 | 0,738 |
| Random Forest | 0,840 | 0,632 | 0,535 | 0,773 | 0,761 |

L'accuracy seule est trompeuse : la référence naïve obtient 73,5 % d'accuracy sans détecter un seul départ (F1 = 0).
Les deux modèles détectent environ 78 % des clients qui partent (rappel). La régression logistique est retenue :
meilleure ROC-AUC (à égalité ou presque avec la Random Forest), plus légère et ses coefficients sont explicables.

Version retenue : `model-1.0.0.joblib`, environ 3 Ko (limite : 100 Mo), 16 ms pour prédire un lot de 1 000 clients sur CPU (limite : 1 s).

## 4. Tableau de bord (membre 4)

Page statique `web/` (HTML, CSS, JavaScript, [Chart.js 4.4.1](https://www.chartjs.org/)), sans framework ni build,
hébergée dans le **site web statique** du compte de stockage (`$web`). Elle lit `GET /api/recent`
(contrat : [`docs/contrats.md`](docs/contrats.md), section 5) toutes les 30 s.

| Élément | Contenu |
|---|---|
| Indicateurs | fichiers traités (et en erreur), clients scorés, clients à risque élevé, taux de churn prédit (moyenne pondérée), durée moyenne de traitement, version du modèle |
| Graphiques | taux de churn par fichier, répartition des niveaux de risque, clients par fichier (risque élevé / autres), durée de traitement comparée à l'objectif de 2 s |
| Tableaux | derniers fichiers (documents Cosmos DB, statut `ok` / `error`) ; clients du dernier fichier triés par probabilité, filtrables par risque, recherche par `customerID`, export CSV |
| Robustesse | mode démo sans API (`demo/recent.json`, données fictives) ; si l'API ne répond pas, bandeau d'erreur et données de démo ; thème clair/sombre ; affichage mobile |

Lancer en local :

```bash
cd web
python -m http.server 8080
# http://localhost:8080/?demo=1                                  données fictives
# http://localhost:8080/?api=https://<function-app>.azurewebsites.net   vraies données
```

En production, `deploy.yml` réécrit `web/config.js` avec la variable GitHub `API_BASE_URL`.

## 5. Intégration et déploiement continus (membre 4)

| Workflow | Déclencheur | Jobs |
|---|---|---|
| [`ci.yml`](.github/workflows/ci.yml) | push sur `main`, toute pull request | **model** : ruff + pytest ; **api** : ruff + pytest + image Docker (publiée sur GHCR seulement sur `main`) ; **functions** : ruff + pytest ; **web** : syntaxe JS, conformité de `demo/recent.json` au contrat, validation HTML |
| [`deploy.yml`](.github/workflows/deploy.yml) | push sur `main` touchant `web/` ou `functions/`, ou manuel | **web** : envoi de `web/` dans `$web` ; **functions** : publication de la Function App |

Un job dont le dossier n'est pas encore fusionné (`api/`, `functions/`) est ignoré avec un message, au lieu d'échouer.
À activer dans *Settings > Branches* : protéger `main` (pull request obligatoire, CI verte).

Configuration GitHub (*Settings > Secrets and variables > Actions*) :

| Nom | Type | Valeur |
|---|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | secret | `az storage account show-connection-string -g <rg> -n <stockage> -o tsv` |
| `AZURE_FUNCTIONAPP_PUBLISH_PROFILE` | secret | portail > Function App > *Télécharger le profil de publication* |
| `API_BASE_URL` | variable | `https://<function-app>.azurewebsites.net` |
| `FUNCTIONAPP_NAME` | variable | nom de la Function App |

Azure for Students ne permet généralement pas de créer un principal de service : les déploiements utilisent
donc des secrets limités à une ressource (chaîne de connexion du stockage, profil de publication) plutôt qu'une
connexion OIDC à toute la souscription.

## 6. Supervision et alertes (membre 4)

[`infra/monitoring.sh`](infra/monitoring.sh) crée, en `francecentral` :

- un espace **Log Analytics** (rétention 30 jours, **plafond 0,1 Go/jour** pour protéger le budget) et
  **Application Insights** `appi-churn` basé sur cet espace ;
- le branchement de la Function App (paramètre `APPLICATIONINSIGHTS_CONNECTION_STRING`) et de la Container App
  (secret `appinsights-cs`, voir `api/deploy/containerapp.md`) ;
- un groupe d'actions e-mail et 4 alertes :

| Alerte | Condition | Sévérité |
|---|---|---|
| `alert-requetes-en-echec` | plus de 5 requêtes en échec en 5 min | 2 |
| `alert-latence` | temps de réponse moyen > 2 000 ms sur 15 min (critère de succès < 2 s) | 3 |
| `alert-exceptions-pipeline` | au moins une exception dans les Functions sur 10 min | 2 |
| `alert-file-en-attente` | plus de 20 messages en attente dans la file sur 1 h | 3 |

- le site web statique du tableau de bord et le **CORS** de la Function App pour son origine.

```bash
RG=<rg> STORAGE=<stockage> FUNC_APP=<function-app> CONTAINER_APP=<container-app> \
ALERT_EMAIL=<adresse> bash infra/monitoring.sh
```

### Requêtes KQL

Dans [`docs/kql/requetes.kql`](docs/kql/requetes.kql) (portail > Application Insights > *Journaux*) :

1. **Temps de réponse de `/predict`** : p50, p95 et échecs par tranche de 15 min, comparés à l'objectif de 2 s.
2. **A/B des modèles** : clients scorés et latence moyenne du modèle par `model_version` (métriques
   OpenTelemetry `churn_customers_scored` et `churn_model_latency_ms` de l'API).
3. **Santé du pipeline** : exécutions, échecs et exceptions par composant (dispatcher, worker, recent, API).

Deux requêtes bonus : dernières exceptions, et trajet de bout en bout d'un fichier à partir de son nom.
Captures : [`docs/captures/`](docs/captures/).

## Sections à compléter

- Schéma d'architecture, région choisie et justification (membre 2)
- API et exemple `curl` (membre 2)
- Exemple d'événement Event Grid et de message de file (membre 3)
- Déploiement pas à pas (membres 2 et 4)
- Captures du tableau de bord et des requêtes KQL sur les vraies données (membre 4, après déploiement)
- Restrictions Azure for Students rencontrées et solutions (tous)
