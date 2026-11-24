# Déploiement de l'API sur Azure Container Apps

**Déployée le 24/11/2026** par le membre 2 avec [`infra/api/deploy_api.sh`](../../infra/api/deploy_api.sh) :
groupe `rg-churn`, environnement `cae-churn`, application `api-churn`, France Central.

URL publique : https://api-churn.salmonsmoke-322316a5.francecentral.azurecontainerapps.io

```bash
bash infra/api/deploy_api.sh                      # crée ou met à jour (idempotent)
IMAGE_TAG=sha-<7 car.> bash infra/api/deploy_api.sh # déploie un commit précis (GET /version renvoie son SHA)
```

Le script applique les paramètres ci-dessous. Les commandes détaillées restent ici pour référence ;
remplacer `<rg>` par `rg-churn`, `<app>` par `api-churn` et `<version>` par une étiquette de l'image.

## Paramètres

| Paramètre | Valeur |
|---|---|
| Image | `ghcr.io/mkh-dev/telecom-churn-cloud/api:<version>` (ex. `0.3.0`, ou `sha-<7 car.>`) |
| Plateforme de l'image | `linux/amd64` |
| Ressources | 0,25 vCPU / 0,5 Gi |
| Réplicas | min 0, max 2 |
| Ingress | externe ou interne selon le worker, **port cible 8000**, HTTP |
| Liveness | `GET /health` port 8000 |
| Readiness | `GET /health` port 8000 (503 tant que le modèle n'est pas chargé) |

Avec `min-replicas 0`, le premier appel après une période d'inactivité subit un démarrage à froid
(tirage de l'image + chargement du modèle, quelques secondes) : prévoir un délai d'expiration HTTP
d'au moins 30 s et une nouvelle tentative côté worker.

## Variables d'environnement

| Variable | Valeur |
|---|---|
| `MODEL_PATH` | `/app/models/model-1.0.0.joblib` (modèle livré dans l'image) |
| `MODEL_VERSION` | `1.0.0` (facultatif : déduite du nom du fichier) |
| `RISK_THRESHOLD` | `0.5` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | `secretref:appinsights-cs` (**secret**, jamais en clair dans le dépôt) |

## Commandes

Si le paquet GHCR reste privé, déclarer le registre (PAT GitHub avec `read:packages`, passé en variable
d'environnement locale, jamais commité) :

```bash
az containerapp registry set -g <rg> -n <app> \
  --server ghcr.io --username <utilisateur-github> --password "$GHCR_PAT"
```

Secret Application Insights :

```bash
az containerapp secret set -g <rg> -n <app> \
  --secrets appinsights-cs="$(az monitor app-insights component show -g <rg> --app <appinsights> \
    --query connectionString -o tsv)"
```

Mise à jour de l'image, des ressources, de l'échelle et des variables :

```bash
az containerapp update -g <rg> -n <app> \
  --image ghcr.io/mkh-dev/telecom-churn-cloud/api:<version> \
  --cpu 0.25 --memory 0.5Gi \
  --min-replicas 0 --max-replicas 2 \
  --set-env-vars MODEL_PATH=/app/models/model-1.0.0.joblib MODEL_VERSION=1.0.0 RISK_THRESHOLD=0.5 \
    APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appinsights-cs
```

Le port d'ingress (une seule fois, s'il n'est pas déjà à 8000) :

```bash
az containerapp ingress update -g <rg> -n <app> --target-port 8000
```

## Sondes

`az containerapp update` n'a pas d'option pour les sondes : les définir par YAML. Exporter la
configuration, ajouter le bloc `probes` sous le conteneur, puis réappliquer :

```bash
az containerapp show -g <rg> -n <app> -o yaml > app.yaml
# éditer app.yaml : properties.template.containers[0].probes (bloc ci-dessous)
az containerapp update -g <rg> -n <app> --yaml app.yaml
```

```yaml
probes:
  - type: Liveness
    httpGet: {path: /health, port: 8000}
    initialDelaySeconds: 10
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3
  - type: Readiness
    httpGet: {path: /health, port: 8000}
    initialDelaySeconds: 3
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 3
```

## Vérification

```bash
URL=https://$(az containerapp show -g <rg> -n <app> --query properties.configuration.ingress.fqdn -o tsv)
curl -s $URL/health
curl -s $URL/version
curl -s -X POST $URL/predict -H 'Content-Type: application/json' -d @api/dev/sample_request.json
curl -s $URL/metrics
```

## Bonus A/B

Le code ne change pas : `model-1.0.0` (régression logistique) et `model-2.0.0` (Random Forest) sont tous
les deux dans l'image (`/app/models/`), seule la révision change. Testé en local : les deux conteneurs sont
`healthy` et répondent en moins de 0,1 s pour 1 000 clients.

```bash
az containerapp revision set-mode -g <rg> -n <app> --mode multiple
az containerapp update -g <rg> -n <app> --revision-suffix modelb \
  --set-env-vars MODEL_PATH=/app/models/model-2.0.0.joblib MODEL_VERSION=2.0.0
az containerapp ingress traffic set -g <rg> -n <app> \
  --revision-weight <app>--<suffixe-révision-A>=80 <app>--modelb=20
```

Comparer ensuite les révisions dans Application Insights avec la dimension `model_version` des
métriques `churn_customers_scored` et `churn_model_latency_ms`, ou avec `model_version` dans les réponses.
