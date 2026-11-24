# Vidéo de démonstration (5 min maximum) — membre 4

Texte prêt à lire (environ 680 mots, soit 4 min 40 à un débit normal) et ce qu'il faut montrer à l'écran.
Les passages entre crochets sont des actions, pas du texte à lire.

## 1. Préparer (30 min avant)

**Outils**, sans rien installer : *Outil Capture d'écran* de Windows 11 (`Win + Maj + R`, mode vidéo) pour
enregistrer, puis **Clipchamp** (fourni avec Windows) pour couper les temps d'attente et exporter en MP4 1080p.
OBS Studio convient aussi.

**Réchauffer le système**, sinon l'API démarre à froid (9 à 12 s quand elle sort de 0 réplica) :

```bash
source infra/pipeline/env.sh
curl -s https://<api>/health                 # réveille l'API
bash infra/pipeline/03_demo.sh valide        # 2 ou 3 fois : remplit le tableau de bord et les KQL
bash infra/declencher-alerte.sh              # au moins 15 min avant : l'alerte et l'e-mail seront prêts
```

**Onglets ouverts dans cet ordre**, zoom du navigateur à 125 % :

1. README sur GitHub, section 2 (schéma d'architecture) ;
2. README, section 1 (tableau des résultats du modèle) ;
3. portail Azure > compte de stockage `stchurnpipe` > *Conteneurs* ;
4. le tableau de bord en ligne (URL dans `docs/captures/preuves/dashboard-url.txt`) ;
5. Application Insights `appi-churn` > *Journaux*, avec les 3 requêtes de `docs/kql/` dans 3 onglets de requête ;
6. Azure Monitor > *Alertes*, et la boîte mail avec l'e-mail d'alerte ;
7. GitHub > *Actions*, le dernier run vert de `Deploy`.

**Fichiers sur le bureau** : `functions/samples/clients_valide.csv` renommé `clients-demo.csv`, et
`functions/samples/clients_colonnes_manquantes.csv` renommé `clients-invalide.csv`.

**À ne jamais montrer** : clés, chaînes de connexion, identifiants de souscription, adresse e-mail (masquer
l'en-tête de l'e-mail ou flouter au montage).

## 2. Texte et écrans

### 0:00 – 0:35 · Le problème et l'architecture — *onglet 1*

> Bonjour. Notre projet prédit l'attrition des clients d'un opérateur télécom, c'est-à-dire les clients qui
> risquent de résilier, et il le fait dans un pipeline entièrement automatique sur Microsoft Azure.
> Un fichier CSV de clients est déposé dans Blob Storage. Event Grid prévient une Azure Function, le dispatcher,
> qui valide le fichier et dépose un message dans une file d'attente. Une deuxième fonction, le worker, envoie
> les clients à notre API de machine learning, une API FastAPI dans un conteneur Docker sur Container Apps.
> Les résultats sont enregistrés dans Blob Storage et dans Cosmos DB, puis affichés dans un tableau de bord.
> Tout est en France Central.

### 0:35 – 1:15 · Le modèle — *onglet 2*

> Le modèle est entraîné sur le jeu IBM Telco Customer Churn : environ 7 000 clients, dont 26,5 % sont partis.
> Nous avons comparé une régression logistique et une forêt aléatoire à une référence naïve qui prédit toujours
> « le client reste ». Cette référence atteint 73 % d'exactitude sans détecter un seul départ : c'est pour ça
> que nous regardons surtout la ROC-AUC et le rappel. La régression logistique obtient une ROC-AUC de 0,84 et
> repère 78 % des clients qui partent. Nous l'avons retenue : elle est aussi bonne, plus légère et explicable.

### 1:15 – 2:25 · Un dépôt de bout en bout — *onglet 3, puis 4*

> Je dépose maintenant un fichier de vingt clients dans le conteneur input.

[Téléverser `clients-demo.csv` dans `input/`. Couper l'attente au montage.]

> Quelques secondes plus tard, le résultat apparaît dans le conteneur output : une prédiction par client.
> Le résumé du fichier est aussi enregistré dans Cosmos DB.

[Ouvrir `output/`, montrer le fichier JSON. Passer à l'onglet 4, cliquer sur « Actualiser ».]

> Le tableau de bord interroge la fonction recent toutes les trente secondes. Notre fichier est en haut de
> la liste. On voit le nombre de clients scorés, les clients à risque élevé, le taux de churn prédit et la durée
> de traitement, bien en dessous de notre objectif de deux secondes. Ici, la répartition entre risque élevé,
> moyen et faible. Et en bas, les clients les plus à risque du dernier fichier, que le service client peut
> appeler en priorité.

[Survoler le graphique des risques, puis le tableau des clients.]

### 2:25 – 3:00 · La robustesse — *onglet 3*

> Que se passe-t-il avec un mauvais fichier ? Je dépose un CSV auquel il manque des colonnes.

[Téléverser `clients-invalide.csv` dans `input/`, puis ouvrir `rejected/`.]

> Le dispatcher le refuse avant tout appel à l'API : il est déplacé dans rejected, avec un fichier qui explique
> le motif. Si l'API était en panne, le message serait retenté cinq fois puis placé dans une file poison :
> aucun fichier n'est perdu.

### 3:00 – 4:05 · La supervision — *onglets 5 et 6*

> Toutes les briques envoient leur télémétrie dans Application Insights, avec OpenTelemetry et deux métriques
> personnalisées : le nombre d'inférences et la latence du modèle.
> Première requête KQL : le nombre d'inférences par heure.

[Exécuter `1-inferences-par-heure.kql`.]

> Deuxième requête : les cinq fichiers les plus lents, avec le temps passé dans l'API et dans le modèle.

[Exécuter `2-fichiers-les-plus-lents.kql`.]

> Troisième requête : la répartition des codes HTTP de l'API et des fonctions.

[Exécuter `3-codes-http.kql`. Passer à l'onglet 6.]

> Nous avons aussi des alertes. Celle-ci s'est déclenchée quand nous avons déposé un fichier invalide : l'équipe
> reçoit un e-mail. D'autres alertes surveillent les échecs du worker, les erreurs serveur et un temps de réponse
> supérieur à deux secondes.

### 4:05 – 4:45 · Le déploiement continu — *onglet 7*

> Enfin, l'automatisation. À chaque pull request, la CI vérifie le code avec Ruff, lance les tests et construit
> l'image Docker. Quand une modification arrive sur main, le workflow Deploy se connecte à Azure par OIDC, sans
> aucun mot de passe ni clé stockés dans GitHub, grâce à une identité managée. Il met à jour l'API, les fonctions
> et le tableau de bord, puis vérifie que la route version renvoie bien le commit qui vient d'être déployé.

[Ouvrir le résumé du run : la ligne `GET /version` avec le commit.]

### 4:45 – 5:00 · Conclusion

> Pour le coût, l'API descend à zéro réplica au repos et Cosmos DB est en offre gratuite, avec un budget et des
> alertes à 23 euros par mois. Merci pour votre attention.

## 3. Montage et rendu

- Couper chaque attente de plus de 3 s, sans accélérer la voix ; durée finale **inférieure à 5 min**.
- Exporter en MP4 1080p. Déposer la vidéo sur YouTube en « non répertoriée » ou sur le OneDrive de l'école, et
  mettre le lien dans le README (le fichier vidéo ne va pas dans le dépôt).
- Revoir la vidéo en entier pour vérifier qu'aucune donnée personnelle ou secrète n'apparaît.
