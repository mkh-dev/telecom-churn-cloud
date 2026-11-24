# Rapport de coûts (membre 2)

Abonnement **Azure for Students** (crédit de 100 $, facturé en euros), région **France Central**,
toutes les ressources du projet dans le groupe **`rg-churn`**, étiquetées `projet=dscc` et `equipe=churn`.

## 1. Budget et alertes

| Paramètre | Valeur |
|---|---|
| Nom | `budget-churn` (portée : tout l'abonnement) |
| Montant | **23 € par mois**, soit environ **25 $** (Azure exprime le budget dans la devise de facturation, ici l'euro) |
| Période | mensuelle, du 1er novembre 2026 au 30 juin 2027 |
| Alertes par e-mail | coût réel > 50 %, > 80 % et > 100 % ; coût **prévu** > 100 % (prévient avant le dépassement) |
| Destinataire | adresse ECE du membre 2 (passée en variable au lancement, jamais commitée) |
| Création | 24/11/2026 avec [`infra/budget.sh`](../infra/budget.sh) (idempotent) |

`az consumption budget create` ne sait pas créer d'alertes : le script appelle directement l'API REST
`Microsoft.Consumption/budgets`.

Un budget **ne bloque pas** les dépenses : il envoie seulement des alertes. La protection réelle vient du
crédit Azure for Students (l'abonnement est désactivé quand il est épuisé) et des choix ci-dessous.

## 2. Choix qui limitent les coûts

| Service | Choix | Effet |
|---|---|---|
| Container Apps (API) | plan Consommation, 0,25 vCPU / 0,5 Gi, **`min-replicas 0`** | Rien n'est facturé quand l'API est au repos ; l'octroi gratuit mensuel couvre la démo |
| Azure Functions | plan Flex Consumption, 512 Mo | Facturé à l'exécution, avec un octroi gratuit mensuel |
| Cosmos DB | **Free Tier** (1 000 RU/s et 25 Go gratuits), base à 400 RU/s | 0 € dans la limite gratuite |
| Log Analytics / App Insights | rétention 30 jours, **plafond 0,1 Go/jour** | Ingestion bornée, dans la part gratuite mensuelle |
| Stockage | Standard LRS, quelques Mo | Quelques centimes au plus |
| Event Grid, file Storage | volumes de démo (quelques centaines d'opérations) | Dans la part gratuite |
| Une seule région | tout en France Central | Pas de trafic facturé entre régions |
| Image Docker | GHCR (GitHub), pas d'Azure Container Registry | Évite le coût fixe journalier d'un ACR Basic |

## 3. Estimation

Pour 8 jours de projet avec quelques dizaines de fichiers de démo, l'estimation est **inférieure à 1 €**,
très loin du budget. Les tarifs et les octrois gratuits sont ceux de la grille Azure publique : à
revérifier sur la [calculatrice de prix](https://azure.microsoft.com/fr-fr/pricing/calculator/) au moment du rendu.

Le poste le plus exposé est Cosmos DB si le Free Tier n'a pas pu être activé à la création : 400 RU/s
provisionnées coûtent alors environ 0,77 $ (0,70 €) par jour. À vérifier dans le portail (Cosmos DB > Fonctionnalités).

## 4. Relevés des coûts réels

Relevé avec [`infra/cost_report.sh`](../infra/cost_report.sh) (API Cost Management, lecture seule,
avec 8 à 24 h de retard). Copier ici le tableau produit à chaque relevé.

| Date du relevé | Période | Coût cumulé de `rg-churn` | Remarque |
|---|---|---|---|
| 24/11/2026 | 01/11 → 24/11 | 0,00 € | Jour du déploiement de l'API : coûts pas encore remontés |
| … | | | |
| Jour de la suppression | début du projet → fin | | Relevé final, fait automatiquement par `infra/teardown.sh` |

## 5. Suppression finale

Après la soutenance, [`infra/teardown.sh`](../infra/teardown.sh) :

1. fait le relevé final des coûts (à copier dans le tableau ci-dessus) ;
2. supprime définitivement l'espace Log Analytics (sinon il reste 14 jours en suppression réversible) ;
3. supprime le groupe `rg-churn` et tout son contenu, après avoir fait retaper son nom ;
4. vérifie qu'aucune ressource étiquetée `projet=dscc` ne subsiste ;
5. supprime le budget si `DELETE_BUDGET=1`.

À faire en plus, à la main : supprimer les secrets GitHub Actions (`AZURE_*`) et les variables
`API_BASE_URL` / `FUNCTIONAPP_NAME`, puis noter la date de suppression et le coût final ici.

| Suppression | Date | Vérification |
|---|---|---|
| Groupe `rg-churn` | à compléter | `az group exists -n rg-churn` → `false` |
| Budget `budget-churn` | à compléter | |
