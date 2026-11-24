# Contrats d'interface

Figés le jour 1. Tout changement passe par une pull request relue par les membres concernés.

## 1. Fichier déposé dans `input/` (membres 3 et 1)

CSV UTF-8, séparateur virgule, en-tête obligatoire, 1 à 1 000 lignes, 1 Mo maximum. Pas de colonne `Churn`.
Exemple complet : `model/artifacts/sample-clients.csv` (généré par `model/train.py`).

```csv
customerID,gender,SeniorCitizen,Partner,Dependents,tenure,PhoneService,MultipleLines,InternetService,OnlineSecurity,OnlineBackup,DeviceProtection,TechSupport,StreamingTV,StreamingMovies,Contract,PaperlessBilling,PaymentMethod,MonthlyCharges,TotalCharges
7590-VHVEG,Female,0,Yes,No,1,No,No phone service,DSL,No,Yes,No,No,No,No,Month-to-month,Yes,Electronic check,29.85,29.85
```

`TotalCharges` peut être vide (clients avec `tenure` = 0) : le modèle l'impute.

## 2. API `POST /predict` (membres 1, 2 et 3)

Requête : 1 à 1 000 clients, chacun avec les 20 colonnes ci-dessus.

```json
{"customers": [{"customerID": "7590-VHVEG", "gender": "Female", "SeniorCitizen": 0, "Partner": "Yes", "Dependents": "No", "tenure": 1, "PhoneService": "No", "MultipleLines": "No phone service", "InternetService": "DSL", "OnlineSecurity": "No", "OnlineBackup": "Yes", "DeviceProtection": "No", "TechSupport": "No", "StreamingTV": "No", "StreamingMovies": "No", "Contract": "Month-to-month", "PaperlessBilling": "Yes", "PaymentMethod": "Electronic check", "MonthlyCharges": 29.85, "TotalCharges": 29.85}]}
```

Réponse :

```json
{"model_version": "1.0.0", "latency_ms": 12.4, "predictions": [{"customerID": "7590-VHVEG", "churn_prediction": 1, "churn_probability": 0.82, "risk_level": "élevé"}]}
```

`risk_level` : `élevé` si probabilité ≥ 0,7 ; `moyen` si ≥ 0,4 ; sinon `faible`.
L'API appelle `predict_batch(model, customers)` de `model/churn_model.py`, qui produit exactement la liste `predictions`.

## 3. Message de la file `inference-requests` (membre 3)

```json
{"blobName": "input/clients-2026-11-25.csv", "etag": "0x8DD0A1B2C3D4E5F", "size": 48213, "rows": 120, "timestamp": "2026-11-25T10:42:07Z"}
```

## 4. Document Cosmos DB (membres 3 et 4)

Base `pipeline`, conteneur `inferences`, clé de partition `/day`. Un document par fichier ; le détail par client est dans `output/`.

```json
{"id": "clients-2026-11-25.csv_0x8DD0A1B2C3D4E5F", "day": "2026-11-25", "fileName": "clients-2026-11-25.csv", "timestamp": "2026-11-25T10:42:09Z", "nCustomers": 120, "nHighRisk": 31, "churnRate": 0.258, "modelVersion": "1.0.0", "durationMs": 1840, "status": "ok"}
```

## 5. API `GET /api/recent` (membres 3 et 4) — proposition du membre 4, à valider en revue

Fonction HTTP `recent` de la Function App (préfixe de route par défaut `api`), appelée par le tableau de bord
depuis le navigateur : **CORS** à autoriser pour l'origine du site web statique (`infra/monitoring.sh` le fait).

Requête : `GET /api/recent?limit=20` (`limit` entre 1 et 100, 20 par défaut).

Réponse (`200`, `application/json`) :

```json
{
  "generatedAt": "2026-11-25T10:45:00Z",
  "count": 1,
  "items": [
    {"id": "clients-2026-11-25.csv_0x8DD0A1B2C3D4E5F", "day": "2026-11-25", "fileName": "clients-2026-11-25.csv", "timestamp": "2026-11-25T10:42:09Z", "nCustomers": 120, "nHighRisk": 31, "churnRate": 0.258, "modelVersion": "1.0.0", "durationMs": 1840, "status": "ok"}
  ],
  "latest": {
    "fileName": "clients-2026-11-25.csv",
    "modelVersion": "1.0.0",
    "timestamp": "2026-11-25T10:42:09Z",
    "predictions": [{"customerID": "7590-VHVEG", "churn_prediction": 1, "churn_probability": 0.82, "risk_level": "élevé"}]
  }
}
```

- `items` : documents Cosmos DB de la section 4, **du plus récent au plus ancien**
  (`SELECT TOP @limit * FROM c ORDER BY c.timestamp DESC`, requête inter-partitions).
- `latest` : facultatif. Contenu du fichier `output/` du dernier document `status = "ok"` (liste `predictions`
  de la section 2). S'il est absent, le tableau de bord affiche seulement les agrégats.
- `status` : `ok` ou `error` (fichier rejeté ou échec du worker) ; un document en erreur peut avoir `nCustomers = 0`.
- Le tableau de bord accepte aussi un simple tableau de documents (`[...]`) pour faciliter les premiers tests.
- Exemple complet (données fictives) : `web/demo/recent.json`.
