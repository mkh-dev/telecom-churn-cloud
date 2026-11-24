# Variables communes — à sourcer :  source infra/pipeline/env.sh
# Noms alignés sur le reste de l'équipe (infra/monitoring.sh, README §2 : France Central).
# Chaque valeur peut être surchargée par une variable d'environnement.
export LOC="${LOC:-francecentral}"     # seule région retenue (README, section 2)
export RG="${RG:-rg-churn}"
export ST="${ST:-stchurnpipe}"         # compte de stockage : unique au monde, minuscules, sans tiret
export COSMOS="${COSMOS:-cosmos-churn}" # unique au monde
export FUNC="${FUNC:-func-churn}"       # unique au monde (<nom>.azurewebsites.net)
export AI="${AI:-appi-churn}"           # créé par infra/monitoring.sh (membre 4)
export LAW="${LAW:-law-churn}"          # créé par infra/monitoring.sh (membre 4)
export API_APP="${API_APP:-api-churn}"  # Container App de l'API (membre 2)
export EQ="${EQ:-churn}"                # étiquette equipe=<nom> du groupe de ressources
export QUEUE="inference-requests"
