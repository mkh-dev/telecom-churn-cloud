# Variables communes — à sourcer :  source infra/pipeline/env.sh
# Remplacer <equipe> et <region> (région validée avec : az functionapp list-flexconsumption-locations)
export EQ="${EQ:-<equipe>}"            # minuscules, sans tiret (ex. dscc7)
export LOC="${LOC:-<region>}"          # ex. francecentral, swedencentral
export RG="rg-projet-$EQ"
export ST="st${EQ}pipe"                # compte de stockage (unique au monde)
export COSMOS="cosmos-$EQ"
export FUNC="func-$EQ"
export AI="appi-$EQ"
export LAW="log-$EQ"
export API_APP="api-$EQ"               # Container App du membre 2
export QUEUE="inference-requests"
