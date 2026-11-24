# Requêtes KQL (membre 4)

Une requête par fichier : à coller dans *Application Insights `appi-churn` > Journaux*, ou à exécuter en ligne
de commande avec [`infra/preuves.sh`](../../infra/preuves.sh), qui enregistre les résultats dans
`docs/captures/preuves/`.

| Fichier | Exigence du sujet | Capture |
|---|---|---|
| [`1-inferences-par-heure.kql`](1-inferences-par-heure.kql) | Nombre d'inférences par heure | `07-kql-1-inferences-heure.png` |
| [`2-fichiers-les-plus-lents.kql`](2-fichiers-les-plus-lents.kql) | Les 5 fichiers les plus lents | `08-kql-2-fichiers-lents.png` |
| [`3-codes-http.kql`](3-codes-http.kql) | Répartition des codes HTTP | `09-kql-3-codes-http.png` |
| [`3b-erreurs-pipeline.kql`](3b-erreurs-pipeline.kql) | Répartition des erreurs (complément) | `09b-kql-3b-erreurs.png` |
| [`bonus-latence-ab.kql`](bonus-latence-ab.kql) | Bonus A/B : latence par version du modèle | facultatif |

Sources de télémétrie :

- `customMetrics` `pipeline.inferences`, `pipeline.customers_scored`, `pipeline.model_latency_ms` : métriques
  OpenTelemetry personnalisées du worker (`functions/function_app.py`, classe `_Metrics`) ;
- `traces` `INFERENCE_DONE {json}` (une ligne par fichier traité), `DISPATCH_REJECTED`, `WORKER_FAILED` ;
- `requests` : requêtes HTTP de l'API (Container Apps) et des Functions, avec leur code de retour.

Les tables ne se remplissent qu'après de vrais dépôts de fichiers : lancer d'abord
`bash infra/pipeline/03_demo.sh valide` (plusieurs fois) et `bash infra/declencher-alerte.sh`.
