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
- **Protocole** : jeu de test stratifié de 20 %, graine 42, versions figées dans `model/requirements.txt`.
- **Modèles** : régression logistique et Random Forest (`class_weight="balanced"`), comparés à une référence naïve qui prédit toujours « ne part pas ». Le meilleur en ROC-AUC devient `model-1.0.0.joblib`.

Reproduire :

```bash
cd model
pip install -r requirements.txt
python train.py        # télécharge les données, entraîne, écrit artifacts/
pytest -q tests
```

Résultats sur le jeu de test (à compléter avec `model/artifacts/metrics-1.0.0.json`) :

| Modèle | ROC-AUC | F1 churn | Précision | Rappel | Accuracy |
|---|---|---|---|---|---|
| Référence naïve (toujours « Non ») | 0,500 | 0 | 0 | 0 | |
| Régression logistique | | | | | |
| Random Forest | | | | | |

L'accuracy seule est trompeuse : la référence naïve dépasse 70 % d'accuracy sans détecter un seul départ.
Version retenue : `model-1.0.0.joblib` (taille : … Mo, latence : … ms pour 1 000 clients).

## Sections à compléter

- Schéma d'architecture, région choisie et justification (membre 2)
- API et exemple `curl` (membre 2)
- Exemple d'événement Event Grid et de message de file (membre 3)
- Déploiement pas à pas (membres 2 et 4)
- 3 requêtes KQL avec captures (membre 4)
- Restrictions Azure for Students rencontrées et solutions (tous)
