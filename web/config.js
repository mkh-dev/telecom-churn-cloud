// Configuration du tableau de bord (membre 4).
// En production, ce fichier est RÉÉCRIT par le workflow deploy.yml avec la variable GitHub API_BASE_URL.
// - API_BASE_URL : URL de la Function App, sans "/" final (ex. https://func-churn.azurewebsites.net).
//   Vide => mode démo (données fictives de demo/recent.json).
// - REFRESH_SECONDS : intervalle de rafraîchissement automatique.
window.DASHBOARD_CONFIG = {
  API_BASE_URL: "",
  RECENT_LIMIT: 20,
  REFRESH_SECONDS: 30,
  LATENCY_SLO_MS: 2000,
};
