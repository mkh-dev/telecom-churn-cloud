# Fiche de soutenance : modèle de Machine Learning (Malek Khelil, membre 1)

## En une phrase

Le modèle prédit, pour chaque client télécom, la probabilité qu'il résilie son abonnement ; la v1 (régression logistique)
repère 78 % des clients qui partent, avec une ROC-AUC de 0,841, contre 0,5 pour une référence naïve.

## 1. Données

- **IBM Telco Customer Churn** : 7 043 clients, 21 colonnes, moins de 1 Mo, licence Apache-2.0 (dépôt GitHub officiel d'IBM).
- Cas d'usage proposé dans le sujet (« Attrition télécom », même jeu que le TP 6).
- Cible : `Churn` (Yes / No). **26,5 % de départs** : classes déséquilibrées.
- Variables : 4 numériques (`SeniorCitizen`, `tenure`, `MonthlyCharges`, `TotalCharges`) et 15 catégorielles
  (contrat, type d'internet, moyen de paiement, services…). `customerID` sert seulement à identifier la prédiction.

## 2. Préparation (dans un seul `Pipeline` scikit-learn)

| Étape | Pourquoi |
|---|---|
| `TotalCharges` converti en nombre ; 11 valeurs vides imputées par la médiane | Clients arrivés ce mois-ci (`tenure` = 0) : pas encore de total facturé |
| Normalisation des numériques (`StandardScaler`) | La régression logistique est sensible aux échelles |
| One-hot des catégorielles, `handle_unknown="ignore"` | Une catégorie inconnue en production ne fait pas planter l'API |
| Tout dans un seul pipeline exporté | L'API reçoit les données brutes : même préparation à l'entraînement et en production |

Protocole reproductible : jeu de test **stratifié** de 20 % (1 409 clients), graine 42, versions figées.

## 3. Résultats sur le jeu de test

| Modèle | ROC-AUC | F1 churn | Précision | Rappel | Accuracy |
|---|---|---|---|---|---|
| Référence naïve (toujours « Non ») | 0,500 | 0,000 | 0,000 | 0,000 | 0,735 |
| **v1.0.0 : régression logistique** | **0,841** | 0,614 | 0,504 | 0,783 | 0,738 |
| v2.0.0 : Random Forest | 0,840 | 0,632 | 0,535 | 0,773 | 0,761 |

Matrice de confusion de la v1 (374 clients partis dans le jeu de test) :

|  | Prédit « reste » | Prédit « part » |
|---|---|---|
| **Est resté** | 747 | 288 (fausses alertes) |
| **Est parti** | 81 (ratés) | 293 (détectés) |

## 4. Les métriques, en mots simples

- **Accuracy** : part de bonnes réponses. **Trompeuse ici** : dire « personne ne part » donne 73,5 % sans trouver un seul départ.
- **Rappel** : sur les clients réellement partis, combien le modèle en trouve. v1 : 293 / 374 = **78 %**.
- **Précision** : quand le modèle dit « part », a-t-il raison ? v1 : 293 / 581 = **50 %**.
- **F1** : moyenne harmonique de la précision et du rappel ; résume la qualité sur la classe « part ».
- **ROC-AUC** : probabilité que le modèle donne un score plus élevé à un client qui part qu'à un client qui reste.
  0,5 = hasard, 1 = parfait. Elle ne dépend pas du seuil de décision.

**Pourquoi privilégier le rappel ?** Pour un opérateur, rater un client qui part coûte plus cher qu'une offre de fidélité
envoyée pour rien. `class_weight="balanced"` pousse le modèle à mieux repérer la classe minoritaire.

## 5. Pourquoi la régression logistique en v1 ?

- ROC-AUC équivalente à la Random Forest (0,841 contre 0,840).
- **1 700 fois plus légère** : 2,5 Ko contre 4,2 Mo.
- **5 fois plus rapide** : environ 16 ms contre 89 ms pour 1 000 clients (limite du sujet : 1 s).
- **Explicable** : ses coefficients montrent ce qui pèse sur le risque.
  - Augmentent le risque : internet par fibre (+0,71), contrat mensuel (+0,66), paiement par chèque électronique (+0,24).
  - Diminuent le risque : ancienneté `tenure` (−1,12, le facteur le plus fort), contrat de deux ans (−0,78), internet DSL (−0,62).
  - Lecture métier : un client récent, sans engagement et en fibre est le profil le plus à risque.

La Random Forest devient la **v2** pour le bonus A/B (80 % du trafic sur v1, 20 % sur v2, comparaison en KQL).

## 6. Du score à la décision

- `churn_prediction` = 1 si la probabilité est ≥ 0,5.
- `risk_level` : **élevé** si ≥ 0,7 ; **moyen** si ≥ 0,4 ; **faible** sinon. C'est ce qu'affiche le dashboard.

## 7. Contraintes du sujet respectées

| Exigence | Valeur |
|---|---|
| Jeu de données ≤ 50 Mo, licence citée | < 1 Mo, Apache-2.0 |
| Au moins 2 métriques, comparées à une référence naïve | 5 métriques + référence « toujours Non » |
| Modèle versionné, < 100 Mo | `model-1.0.0.joblib`, 2,5 Ko |
| Inférence < 1 s sur CPU | 16 ms pour 1 000 clients |
| Script reproductible | graine 42, test séparé et stratifié, `requirements.txt` figé |

## 8. Questions probables du jury

- **« Pourquoi pas l'accuracy ? »** Classes déséquilibrées : la référence naïve atteint 73,5 % sans rien détecter.
- **« Comment évitez-vous les fuites de données ? »** Le jeu de test est séparé avant tout entraînement, et la préparation
  (médiane, normalisation, one-hot) est apprise uniquement sur l'entraînement, à l'intérieur du pipeline.
- **« Que se passe-t-il si une colonne manque dans le CSV ? »** `predict_batch()` lève une erreur claire ; le `dispatcher`
  (membre 3) rejette d'abord le fichier dans `rejected/` grâce au contrôle du schéma.
- **« Et une valeur jamais vue, comme un nouveau type de contrat ? »** Le one-hot l'ignore : la prédiction reste possible.
- **« Comment améliorer le modèle ? »** Ajuster le seuil selon le coût métier, essayer un gradient boosting (LightGBM),
  ajouter des variables (historique de réclamations), valider par validation croisée.
- **« Comment le modèle arrive-t-il en production ? »** Il est copié dans l'image Docker de l'API, construite par GitHub
  Actions et déployée sur Azure Container Apps ; `GET /version` affiche la version du modèle servie.
