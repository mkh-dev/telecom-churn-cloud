# API d'inférence du churn (membre 2)

API FastAPI qui charge le Pipeline scikit-learn du membre 1 (`models/model-<version>.joblib`) et prédit
le risque de désabonnement d'un lot de clients télécom. Elle est appelée par le worker du membre 3.

> [!IMPORTANT]
> **scikit-learn est figé en `1.9.1`** (avec `numpy 2.5.3`, `pandas 3.0.5`, `joblib 1.6.0`), exactement comme
> `model/requirements.txt`. Un joblib exporté avec une autre version ne se charge pas, ou donne des
> résultats faux. Toute montée de version se fait **dans les deux fichiers en même temps** ; le test
> `test_ml_versions_match_model_requirements` échoue sinon.

## Arborescence

```
api/
├── app/
│   ├── main.py           # lifespan (chargement du modèle), endpoints, compteurs /metrics
│   ├── schemas.py        # contrat Pydantic v2
│   ├── model_loader.py   # joblib -> DataFrame -> predict_proba
│   ├── settings.py       # variables d'environnement
│   └── telemetry.py      # Azure Monitor (optionnel) + métriques personnalisées
├── models/               # model-1.0.0 (LogReg, défaut), model-2.0.0 (Random Forest, A/B), model-0.1.0 (provisoire)
├── dev/                  # train_placeholder.py, sample_request.json (5 vrais clients)
├── tests/                # pytest
├── deploy/               # paramètres pour Container Apps (membre 3) et la CI (membre 4)
├── Dockerfile
└── requirements.txt / requirements-dev.txt
```

## Lancer en local

Python 3.12 requis.

```bash
cd api
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

Les modèles `model-1.0.0.joblib` et `model-2.0.0.joblib` sont copiés dans `models/` par `model/train.py` (membre 1).
Le modèle provisoire `model-0.1.0.joblib` reste disponible pour tester sans eux ; pour le régénérer :
`python dev/train_placeholder.py` (télécharge le CSV IBM si `model/data/` est vide).

Tests et lint (comme la CI) :

```bash
ruff check . && ruff format --check .
pytest -q
```

## Tester avec curl

```bash
curl -s localhost:8000/health
curl -s localhost:8000/version
curl -s -X POST localhost:8000/predict -H 'Content-Type: application/json' -d @dev/sample_request.json
curl -s localhost:8000/metrics
```

Documentation interactive : http://localhost:8000/docs

## Image Docker

L'image cible Azure Container Apps (`linux/amd64`). Depuis un Mac (Apple Silicon) :

```bash
cd api
docker buildx build --platform linux/amd64 -t telecom-churn-api:0.2.0 --load .
docker run --rm -p 8000:8000 telecom-churn-api:0.2.0
docker run --rm -p 8001:8000 -e MODEL_PATH=/app/models/model-2.0.0.joblib telecom-churn-api:0.2.0   # modèle B
docker image ls telecom-churn-api:0.2.0
```

- Multi-étapes : venv construit dans une première étape, copié dans une image `python:3.12-slim` propre.
- Utilisateur non root (uid 10001), port 8000, `HEALTHCHECK` sur `/health` en Python standard (pas de curl).
- Taille mesurée : environ 123 Mo compressée, 355 Mo décompressée (limite : 500 Mo).
- Le modèle utilisé est choisi par `MODEL_PATH` (par défaut `/app/models/model-1.0.0.joblib`,
  modifiable au build avec `--build-arg MODEL_FILE=model-2.0.0.joblib` ou au lancement avec `-e MODEL_PATH=...`).
- Latence mesurée pour 1 000 clients : 8 ms (1.0.0) et 35 ms (2.0.0, Random Forest).

## Configuration (variables d'environnement uniquement)

| Variable | Défaut | Rôle |
|---|---|---|
| `MODEL_PATH` | `models/model-1.0.0.joblib` | Fichier joblib chargé une fois au démarrage |
| `MODEL_VERSION` | déduite du nom (`model-1.0.0.joblib` → `1.0.0`) | Version renvoyée par `/predict` et `/version` |
| `RISK_THRESHOLD` | `0.5` | Seuil de `churn` / `churn_prediction` (entre 0 et 1) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | absente | Active Azure Monitor ; sans elle l'API tourne sans Azure |

Aucun secret n'est dans le code ni dans l'image : la chaîne de connexion est fournie par Container Apps
(secret référencé par `secretref:`).

Bonus A/B : une deuxième révision Container Apps avec un autre `MODEL_PATH` / `MODEL_VERSION`
suffit, sans changer le code (voir `deploy/containerapp.md`).

## Contrat de l'API

Contrat commun de `docs/contrats.md` (section 2) et de l'accord avec le membre 3 : la réponse contient
`churn` (booléen) **et** `churn_prediction` / `risk_level`, et la limite est de 1 000 clients, comme un
fichier CSV.

### `POST /predict`

Corps : `{"customers": [ ... ]}`, **1 à 1 000 clients**. Exemple complet : `dev/sample_request.json`.

| Champ | Valeurs acceptées |
|---|---|
| `customerID` | chaîne non vide |
| `gender` | `Male`, `Female` |
| `SeniorCitizen` | `0`, `1` |
| `Partner`, `Dependents`, `PhoneService`, `PaperlessBilling` | `Yes`, `No` |
| `tenure` | entier ≥ 0 |
| `MultipleLines` | `Yes`, `No`, `No phone service` |
| `InternetService` | `DSL`, `Fiber optic`, `No` |
| `OnlineSecurity`, `OnlineBackup`, `DeviceProtection`, `TechSupport`, `StreamingTV`, `StreamingMovies` | `Yes`, `No`, `No internet service` |
| `Contract` | `Month-to-month`, `One year`, `Two year` |
| `PaymentMethod` | `Electronic check`, `Mailed check`, `Bank transfer (automatic)`, `Credit card (automatic)` |
| `MonthlyCharges` | nombre ≥ 0 |
| `TotalCharges` | nombre ≥ 0, ou `""` / `" "` / `null` (imputé par le Pipeline) |

Les nombres peuvent arriver en chaînes (`"29.85"`, `"0"`), comme après un `csv.DictReader`. Les colonnes
en plus (par exemple `Churn`) sont ignorées.

Réponse 200 :

```json
{
  "predictions": [
    {"customerID": "7590-VHVEG", "churn_probability": 0.8021, "churn": true, "churn_prediction": 1, "risk_level": "élevé"}
  ],
  "model_version": "1.0.0",
  "latency_ms": 10.7
}
```

- `churn` = `churn_prediction` = probabilité ≥ `RISK_THRESHOLD`.
- `risk_level` : `élevé` si ≥ 0,7, `moyen` si ≥ 0,4, sinon `faible` (seuils fixes, comme `model/churn_model.py`).
- `latency_ms` : temps de calcul de la requête dans l'API (hors réseau et validation).

Erreurs : 422 si le corps est absent, mal formé, invalide, vide ou dépasse 1 000 clients (détail
Pydantic dans `detail`) ; 503 si le modèle n'est pas chargé.

### `GET /health`

`{"status": "ok", "model_loaded": true, "model_load_time_ms": 1047.6}` ; **503** avec
`{"status": "error", "model_loaded": false, ...}` si le modèle n'a pas pu être chargé (la sonde
readiness bloque alors le trafic).

### `GET /version`

`{"api_version": "0.2.0", "model_version": "1.0.0"}`

### `GET /metrics`

`{"calls": 12, "avg_latency_ms": 9.8, "customers_scored": 3400, "errors": 1}` : compteurs en mémoire depuis
le démarrage du réplica. `calls` compte les appels `/predict` réussis, `avg_latency_ms` est leur latence
moyenne et `errors` compte les appels `/predict` en échec (422, 503, 500).

### Télémétrie

Avec `APPLICATIONINSIGHTS_CONNECTION_STRING`, `azure-monitor-opentelemetry` envoie les requêtes, les
dépendances et les journaux à Application Insights, plus deux métriques personnalisées (table
`customMetrics`) :

- `churn_customers_scored` : nombre de clients scorés (compteur) ;
- `churn_model_latency_ms` : latence de `predict_proba` par appel (histogramme).

Les deux portent la dimension `model_version`, ce qui permet de comparer deux révisions en A/B.
