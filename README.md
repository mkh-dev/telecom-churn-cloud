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

## Sections à compléter

- Schéma d'architecture, région choisie et justification (membre 2)
- API et exemple `curl` (membre 2)
- Exemple d'événement Event Grid et de message de file (membre 3)
- Déploiement pas à pas (membres 2 et 4)
- 3 requêtes KQL avec captures (membre 4)
- Restrictions Azure for Students rencontrées et solutions (tous)
