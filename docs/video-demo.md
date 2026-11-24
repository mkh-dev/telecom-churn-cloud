# Vidéo de démonstration (≈ 5 min) — script (membre 4)

Enregistrement : OBS Studio ou l'outil Capture de Windows (Win + Alt + R), 1080p, micro activé.
Avant de commencer : pipeline déployé, tableau de bord ouvert, onglets du portail prêts, CSV de démo sur le bureau
(`model/artifacts/sample-clients.csv` renommé `clients-demo.csv`, et `clients-invalide.csv` sans colonne `tenure`).

| Temps | Écran | À dire / à faire |
|---|---|---|
| 0:00-0:30 | Schéma d'architecture (README, section 2) | Problème : repérer les clients télécom qui vont résilier. Chaîne Blob → Event Grid → Function → Queue → FastAPI → Blob + Cosmos DB → tableau de bord, en `francecentral`. |
| 0:30-1:15 | Résultats du modèle (README, section 1) | Jeu IBM Telco, 2 modèles comparés à une référence naïve ; v1 régression logistique, ROC-AUC 0,841, rappel 78 %. Pourquoi l'accuracy seule est trompeuse. |
| 1:15-2:30 | Portail : conteneur `input/` | **Déposer `clients-demo.csv`.** Montrer le message dans la file `inference-requests`, puis le fichier dans `output/` et le document dans Cosmos DB (Explorateur de données). |
| 2:30-3:30 | Tableau de bord | Actualiser : le nouveau fichier apparaît en haut. Commenter les indicateurs, le graphique de risques, filtrer « Élevé », rechercher un client, export CSV. Durée de traitement sous l'objectif de 2 s. |
| 3:30-4:00 | Portail : `input/` puis `rejected/` | **Déposer `clients-invalide.csv`** : il part dans `rejected/`, pas d'appel à l'API. |
| 4:00-4:40 | Application Insights | Carte d'application, requête KQL 1 (temps de réponse), requête 3 (santé du pipeline), liste des alertes ; montrer un e-mail d'alerte reçu. |
| 4:40-5:00 | GitHub Actions | Un run vert de `CI` (4 jobs) et de `Deploy`. Conclusion : coût (rapport de coûts), limites, pistes (A/B v2). |

Conseils : couper les temps d'attente du pipeline au montage ; zoomer le navigateur à 125 % ; ne jamais montrer
de clé, chaîne de connexion ou adresse e-mail à l'écran.
