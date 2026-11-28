# Membre 3 : Azure et pipeline événementiel

> Documentation du membre 3 (pipeline événementiel). Formats : [`docs/contrats.md`](contrats.md) §1, §3 et §4.

**Périmètre** : stockage Blob, Event Grid, file `inference-requests` et sa file poison, Function App (`dispatcher`, `worker`, `recent`), communication avec l'API du membre 2, enregistrement dans Cosmos DB, gestion des erreurs et des nouvelles tentatives.

## 1. Le flux en une image

```
 dépôt CSV                    Event Grid                    dispatcher (Event Grid trigger)
 input/clients.csv ──BlobCreated (filtré input/)──►  extension .csv ? taille ≤ 1 Mo ? UTF-8 ?
                                                     colonnes Telco présentes ? nombres valides ?
                                                         │ oui                         │ non
                                                         ▼                             ▼
                                         file inference-requests          rejected/clients.csv
                                          (contrat §3)                    + clients.csv.reason.json
                                                                          (métadonnée reason=<code>)
                                                         │
                                                         ▼
                              worker (Queue trigger, client HTTP réutilisé, délai 20 s)
                         lit input/ (If-Match ETag) ─► POST {API}/predict {"customers": [...]} (contrat §2)
                                                         │
                      ┌──────────────────────────────────┼─────────────────────────────┐
                      ▼                                  ▼                             ▼
         output/<nom>_<etag>.json             Cosmos DB pipeline/inferences      métriques App Insights
         (résultat complet, par client)       (/day, upsert, id = nom_ETag)      pipeline.inferences, ...
                                                         │
                                                         ▼
                                        recent (HTTP GET /api/recent) ─► tableau de bord (membre 4)

 Échec (API coupée, délai, réponse invalide) : exception → le message réapparaît après 10 s
 → 5 tentatives (maxDequeueCount) → inference-requests-poison
```

## 2. Fichiers

| Fichier | Rôle |
|---|---|
| `functions/function_app.py` | Les 3 fonctions (modèle v2), client HTTP partagé, métriques OpenTelemetry |
| `functions/pipeline_core.py` | Logique pure, testée sans Azure : validation, ticket, identifiant idempotent, adaptation de l'API, résumé |
| `functions/stores.py` | Accès Blob et Cosmos DB par identité managée (Azurite en local) |
| `functions/host.json` | `maxDequeueCount = 5`, visibilité 10 s, sondage de la file toutes les 2 s maximum |
| `functions/tests/` | 33 tests pytest (validation, file poison, idempotence, API coupée, et un test avec le vrai `model-1.0.0.joblib` via `predict_batch`) |
| `functions/tools/mock_api.py` | Fausse API au format du contrat §2 : sert le vrai modèle si scikit-learn est installé (`MOCK_FAIL=1` pour simuler une panne) |
| `functions/tools/local_drop.py` | Simule un dépôt avec son événement Event Grid en local |
| `infra/pipeline/01_pipeline.sh` | Cosmos DB, stockage, file, Function App, identité, rôles, paramètres, CORS |
| `infra/pipeline/02_eventgrid.sh` | Abonnement Event Grid (après le premier déploiement) |
| `infra/pipeline/03_demo.sh` | Scénarios de la vidéo : valide, invalide, poison, API coupée, état, rejouer |
| `functions/samples/` | `clients_valide.csv` (= `model/artifacts/sample-clients.csv`), `image.png`, `clients_colonnes_manquantes.csv`, `clients_latin1.csv`, `clients_vide.csv` |
| `docs/exemples-pipeline/` | Événement Event Grid, message de file, résultat `output/`, document Cosmos, réponse `/api/recent` |

## 3. Contrats avec les autres membres

Ma partie respecte les formats figés dans [`docs/contrats.md`](contrats.md). Des exemples réels, produits avec le vrai modèle 1.0.0, se trouvent dans `docs/exemples-pipeline/`.

### 3.1 Appel de l'API (§2)

Le worker fait **un seul appel `POST /predict` par fichier**, avec `{"customers": [...]}` contenant les 20 colonnes, `customerID` compris.

- Les types sont convertis avant l'envoi : `tenure` et `SeniorCitizen` en entiers, les montants en décimaux, et `TotalCharges` vide devient `null`.
- Une colonne `Churn` éventuelle est retirée.
- Le worker vérifie la réponse : même nombre de prédictions, même ordre de `customerID`, `churn_probability` dans [0,1]. Toute incohérence déclenche une nouvelle tentative.
- `risk_level` est recalculé avec les mêmes seuils (0,7 / 0,4) s'il est absent.

### 3.2 Message de file (§3)

Voir `docs/exemples-pipeline/message_file.json`.

```json
{"blobName": "input/clients-2026-11-25.csv", "etag": "0x8DD0A1B2C3D4E5F", "size": 4123, "rows": 20, "timestamp": "2026-11-25T10:42:07Z"}
```

### 3.3 Document Cosmos DB (§4)

Il contient tous les champs du contrat (`id`, `day`, `fileName`, `timestamp`, `nCustomers`, `nHighRisk`, `churnRate`, `modelVersion`, `durationMs`, `status`), plus des champs **ajoutés** pour le tableau de bord : `nMediumRisk`, `nChurn`, `avgProbability`, `topRisk` (les 5 clients les plus à risque), `apiLatencyMs`, `modelLatencyMs`, `uploadedAt`, `etag` et `outputBlob`.

- `churnRate` vaut `nChurn / nCustomers`, c'est-à-dire la part de clients avec `churn_prediction = 1`.
- `day` est le **jour du dépôt** (horodatage du message) et non celui du traitement. Ainsi, un message rejoué après minuit retombe dans la même partition `/day`, donc sur le même document : l'idempotence est préservée.

### 3.4 Tableau de bord (membre 4) : `GET /api/recent?limit=20`

Voir `docs/exemples-pipeline/api_recent_reponse.json`. La réponse a la forme `{"count", "generatedAt", "items": [document §4 ...]}`, triée du plus récent au plus ancien.

Pour le graphique, grouper `items` par `day` et calculer la somme de `nHighRisk` divisée par la somme de `nCustomers`. Le CORS est limité à l'origine du site `$web`.

## 4. Validation par le dispatcher (motifs de rejet)

| Code (métadonnée `reason`) | Cas |
|---|---|
| `bad_extension` | Fichier qui n'est pas un `.csv` (ex. `image.png`) |
| `empty_file` | Fichier de 0 octet ou rempli d'espaces |
| `too_large` | Plus de `MAX_FILE_BYTES` (1 Mo) |
| `bad_encoding` | Fichier qui n'est pas en UTF-8 (ex. export Excel en Latin-1) |
| `binary_content` | Octets nuls dans le fichier |
| `bad_schema` | Une des 20 colonnes du contrat §1 est manquante, ou le séparateur n'est pas la virgule (la liste est donnée dans le motif) |
| `bad_value` / `bad_row` | Valeur refusée par l'API : nombre invalide, négatif ou infini, `SeniorCitizen` différent de 0/1, catégorie inconnue (ex. `Contract=Monthly`), `customerID` vide, ou ligne mal formée |

Les valeurs autorisées sont celles de `api/app/schemas.py`. Le test `test_allowed_values_match_api_schema` relit ce fichier et échoue si les deux listes divergent. Sans ce contrôle, un fichier refusé par l'API (422) échouerait 5 fois dans le worker et finirait dans la file poison au lieu de `rejected/`.
| `too_many_rows` / `no_rows` | Plus de 1 000 lignes (contrat §1), ou en-tête seul |

Le fichier rejeté est copié dans `rejected/` avec la métadonnée `reason=<code>`. Un fichier `rejected/<nom>.reason.json` l'accompagne avec le message en clair.

## 5. Fiabilité : idempotence, reprises, file poison

- **Idempotence** : l'identifiant vaut `<nom du fichier>_<ETag>` (contrat §4), par exemple `clients-2026-11-25.csv_0x8DD0A1B2C3D4E5F`. Le document Cosmos est écrit en `upsert` (partition `day` = jour du dépôt) et le blob `output/` avec `overwrite`. Si le même ticket est livré deux fois (Event Grid et les files garantissent « au moins une fois »), on obtient donc 1 seul document. Le test `test_worker_is_idempotent` le vérifie.
- **Version remplacée** : le worker lit le blob avec `If-Match: ETag`. Si le fichier a été écrasé entre-temps, le message est ignoré, car un nouvel événement traitera la nouvelle version.
- **Délai et client réutilisé** : une seule `requests.Session` est partagée par l'instance (pool de connexions). Le délai est de 5 s pour se connecter et de `API_TIMEOUT_SECONDS=20` s pour la réponse. Ces 20 s laissent le temps au démarrage à froid de Container Apps quand il repart de 0 réplica.
- **Reprises** : toute erreur (API injoignable, HTTP ≥ 400, réponse illisible, nombre de prédictions incohérent) lève une exception. Rien n'est écrit à moitié : le résultat n'est écrit qu'après la réponse complète de l'API. Le message redevient visible 10 s plus tard. Après 5 échecs, il part dans **`inference-requests-poison`**, qui joue le rôle de DLQ.
- **Journal** : chaque échec écrit `WORKER_FAILED {"attempt": n, "goingToPoison": true|false, "errorType": ...}`. Ces lignes sont visibles dans App Insights.

### Scénario d'échec documenté (preuve pour la vidéo)

| Étape | Commande | Ce qu'on observe (capture) |
|---|---|---|
| 1. Couper l'API | `bash infra/pipeline/03_demo.sh api-coupee` (désactive l'ingress) | – |
| 2. Déposer un CSV valide | (fait par le script) | Le dispatcher l'accepte : le ticket est dans la file |
| 3. Tentatives 1 à 5 | (~1 min) | App Insights : 5 lignes `WORKER_FAILED`, `attempt` de 1 à 5, `ApiError` |
| 4. File poison | `bash infra/pipeline/03_demo.sh etat` | `inference-requests-poison : 1 message` |
| 5. Rétablir l'API | (fait par le script) | `/health` répond de nouveau |
| 6. Rejouer | `bash infra/pipeline/03_demo.sh rejouer` | Le fichier apparaît dans `output/` et dans le tableau de bord, sans doublon |

Variante sans toucher à l'API : `bash infra/pipeline/03_demo.sh poison` dépose un message illisible, qui part dans la file poison après 5 `PoisonMessageError`.

## 6. Lancer en local (aucun coût, aucun accès Azure nécessaire)

```bash
npm i -g azurite azure-functions-core-tools@4      # une fois
azurite --silent --location /tmp/azurite &         # faux Azure Storage
cd functions && python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp local.settings.json.example local.settings.json # ignoré par git
python tools/mock_api.py &                         # fausse API (vrai modèle si scikit-learn installé)
func start                                          # dispatcher, worker, recent
# dans un autre terminal :
python tools/local_drop.py samples/clients_valide.csv      # -> output/ + _meta/
python tools/local_drop.py samples/image.png               # -> rejected/
python tools/local_drop.py --poison                        # -> inference-requests-poison
curl http://localhost:7071/api/recent
pytest tests && ruff check .
```

En local, `RESULT_STORE=blob` enregistre les métadonnées dans `output/_meta/`, ce qui évite d'installer l'émulateur Cosmos DB. En Azure, c'est `RESULT_STORE=cosmos`.

## 7. Déployer sur Azure (abonnement d'intégration)

```bash
# 1. Vérifier les noms dans infra/pipeline/env.sh (rg-churn, francecentral, stchurnpipe, func-churn...)
#    Le membre 2 (propriétaire de l'abonnement) lance ce script : il crée des attributions de rôles.
source infra/pipeline/env.sh
bash infra/pipeline/01_pipeline.sh   # Cosmos DB en premier, puis stockage, file, Function App
#    Un « Contributor » sur rg-churn crée tout cela, mais ne peut ni enregistrer Microsoft.EventGrid
#    ni attribuer de rôles : le PROPRIÉTAIRE de la souscription (membre 2) lance ensuite
bash infra/pipeline/00_owner.sh      # fournisseur Event Grid + rôles Blob/Queue de l'identité (+ Event Grid si déployé)
# 2. Premier déploiement du code (en local, ou automatiquement par deploy.yml)
cd functions && func azure functionapp publish "$FUNC" --python && cd ..
bash infra/pipeline/02_eventgrid.sh  # Event Grid -> dispatcher (échoue si le code n'est pas déployé)
# 3. Quand l'API du membre 2 est en ligne : relancer 01_pipeline.sh (met à jour API_URL)
bash infra/pipeline/03_demo.sh valide
```

**Paramètres d'application (sans aucun secret)** : `PipelineStorage__blobServiceUri`, `PipelineStorage__queueServiceUri`, `COSMOS_ENDPOINT`, `COSMOS_DATABASE`, `COSMOS_CONTAINER`, `RESULT_STORE`, `API_URL`, `API_TIMEOUT_SECONDS`, `MAX_FILE_BYTES`, `MAX_ROWS`. Toutes les connexions passent par l'**identité managée système** de la Function App :

- `Storage Blob Data Owner` sur le compte de stockage ;
- `Storage Queue Data Contributor` sur le compte de stockage ;
- `Cosmos DB Built-in Data Contributor` sur le plan de données Cosmos.

**Déploiement continu (`deploy.yml`, membre 4)**

Le job `functions` publie ce dossier quand la variable GitHub `FUNCTIONAPP_NAME` (`func-churn`) et le secret `AZURE_FUNCTIONAPP_PUBLISH_PROFILE` sont renseignés.

Pour une Function App **Flex Consumption**, l'action `Azure/functions-action` doit recevoir `sku: flexconsumption`. Voir la remarque dans la PR d'intégration.

**Intégration continue (`ci.yml`)** : le job `functions` lance `ruff check functions` et `pytest functions/tests`. Les tests n'ont besoin d'aucun paquet Azure.

## 8. Observabilité du pipeline (pour le membre 4)

Métriques personnalisées (table `customMetrics`, via `azure-monitor-opentelemetry`) :

| Métrique | Type |
|---|---|
| `pipeline.inferences` | Compteur de fichiers traités |
| `pipeline.customers_scored` | Compteur de clients évalués |
| `pipeline.model_latency_ms` | Latence de `/predict` |
| `pipeline.processing_ms` | Durée totale du traitement d'un fichier |
| `pipeline.errors` | Erreurs, par `error_type` |
| `pipeline.rejected` | Fichiers rejetés, par `reason` |

Requêtes KQL (onglet *Logs* d'Application Insights) :

```kusto
// 1. Inférences par heure
customMetrics
| where name == "pipeline.inferences"
| summarize inferences = sum(valueSum) by bin(timestamp, 1h)
| render columnchart

// 2. Les 5 fichiers les plus lents
traces
| where message startswith "INFERENCE_DONE"
| extend d = parse_json(extract(@"(\{.*\})", 1, message))
| project timestamp, fichier = tostring(d.fileName), clients = toint(d.nCustomers),
          dureeMs = todouble(d.durationMs), latenceApiMs = todouble(d.apiLatencyMs)
| top 5 by dureeMs desc

// 3. Répartition des erreurs et des rejets
traces
| where message startswith "WORKER_FAILED" or message startswith "DISPATCH_REJECTED"
| extend d = parse_json(extract(@"(\{.*\})", 1, message))
| extend type = iff(message startswith "WORKER_FAILED", strcat("worker: ", tostring(d.errorType)),
                                                        strcat("rejet: ", tostring(d.reasonCode)))
| summarize nb = count() by type
| render piechart
```

**Alerte suggérée** : une alerte de journal toutes les 15 min sur `traces | where message startswith "WORKER_FAILED"` avec un seuil supérieur à 0, ou une alerte sur le taux d'échec des exécutions de la fonction `worker`. Pour la déclencher volontairement : `bash infra/pipeline/03_demo.sh api-coupee`.

## 9. Restrictions Azure for Students (à compléter au fil du projet)

| Restriction rencontrée | Message | Solution appliquée |
|---|---|---|
| Cosmos DB : le portail ne propose que des régions EUAP | – | Création en CLI dans `$LOC` (`infra/pipeline/01_pipeline.sh`, étape 1) |
| Création de la Function App limitée | `429 ... throttled` | Attendre, puis relancer le script (il est ré-exécutable) |
| Event Grid créé avant le code | Échec de validation du point de terminaison | `02_eventgrid.sh` lancé après le premier déploiement |
| Le propriétaire n'a pas de rôle de données sur le stockage | `AuthorizationPermissionMismatch` | Clé récupérée par ARM dans le script, jamais écrite ; rôles de données attribués à l'identité de la Function App |
| Membres « Contributor » sur `rg-churn` seulement | `AuthorizationFailed ... Microsoft.EventGrid/register/action` ; attributions de rôles refusées | Ressources créées par le membre 3 (`01_pipeline.sh`) ; fournisseur Event Grid et rôles Blob/Queue confiés au propriétaire (`00_owner.sh`). Le rôle de données Cosmos DB, lui, passe avec Contributor |
| Application Insights non créé par `az functionapp create` | `Error while trying to create and configure an Application Insights` | Normal : `appi-churn` est créé et branché par `infra/monitoring.sh` (membre 4) |
| ... | ... | ... |

## État du déploiement (28/11/2026)

| Ressource | État |
|---|---|
| Cosmos DB `cosmos-churn` (Free Tier), base `pipeline`, conteneur `inferences` (`/day`) | créé |
| Stockage `stchurnpipe` : `input`, `output`, `rejected`, `models`, files `inference-requests` (+ `-poison`), site `$web` | créé |
| Function App `func-churn` (Flex Consumption, Python 3.12) : `dispatcher`, `worker`, `recent` | déployée ; `GET /api/recent` répond 200 depuis Cosmos DB |
| Rôle de données Cosmos DB de l'identité | attribué |
| Rôles Blob/Queue de l'identité, fournisseur `Microsoft.EventGrid`, abonnement Event Grid | **en attente du propriétaire** : `bash infra/pipeline/00_owner.sh` |

## 10. Preuves « terminé » (section 4.3 du sujet) et captures à faire

- [ ] Dépôt valide : `output/` et Cosmos DB remplis en moins d'une minute (`infra/pipeline/03_demo.sh valide` affiche le délai)
- [ ] Dépôt invalide : `rejected/image.png` avec la métadonnée `reason=bad_extension`
- [ ] Message empoisonné : capture de la file `inference-requests-poison` et des 5 lignes `WORKER_FAILED`
- [ ] Abonnement Event Grid : capture du filtre `BlobCreated` et du préfixe `/blobServices/default/containers/input/`
- [ ] Cosmos DB : Data Explorer, conteneur `inferences`, un document
- [ ] Function App : les 3 fonctions listées, avec l'identité managée activée
- [ ] `curl https://func-churn.azurewebsites.net/api/recent`

## 11. Questions possibles à la soutenance

**Pourquoi une file entre le dispatcher et le worker ?**
Elle découple la détection du traitement. Si l'API est lente ou coupée, aucun fichier n'est perdu : les tickets attendent dans la file, et le système bénéficie des reprises et de la file poison.

**Pourquoi l'ETag dans l'identifiant ?**
Deux dépôts du même nom sont deux versions distinctes, alors qu'un même événement rejoué donne le même identifiant : on obtient donc l'upsert sans doublon.

**Pourquoi 5 tentatives toutes les 10 s ?**
Cela couvre un démarrage à froid de l'API (de 0 à 1 réplica) ou une coupure courte, et un message impossible finit malgré tout isolé en moins d'une minute environ.

**Pourquoi aucun secret ?**
Les URI sont des paramètres d'application et l'identité managée obtient les jetons. `local.settings.json` est exclu par `.gitignore`.
