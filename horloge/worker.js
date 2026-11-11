// lucarne-horloge — déclencheur fiable des calculs « Prévisions Lucarne » (Cloudflare Worker, cron)
//
// Pourquoi : le planificateur de GitHub Actions saute la plupart des passages programmés (≈ 1 sur 10 observé du
// 7 au 11/11/2026). Les déclencheurs « cron » de Cloudflare, eux, sont fiables : ce Worker demande simplement
// à GitHub de lancer chaque calcul à l'heure voulue (API « workflow_dispatch »).
//
// Secret requis (Réglages du Worker › Variables et secrets) : GITHUB_TOKEN — jeton GitHub « fine-grained » limité
// au dépôt rocourtois94/lucarne-previsions, autorisation « Actions : Read and write » uniquement.
//
// Déclencheurs (Réglages › Déclencheurs › Événements cron) :
//   47 * * * *          → vigilance.yml  (Météo-France : vigilance, observations, AROME-PI, satellite)
//   40 * * * *          → meteoswiss.yml (ICON-CH1 / CH2)
//   20 4,10,16,22 * * * → previsions.yml (AROME, ECMWF, seeing…)

const REPO = "rocourtois94/lucarne-previsions";
const PLAN = {
  "47 * * * *": ["vigilance.yml"],
  "40 * * * *": ["meteoswiss.yml"],
  "20 4,10,16,22 * * *": ["previsions.yml"],
};

async function dispatch(env, workflow) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "lucarne-horloge",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  // 204 = demandé ; 401/403 = jeton absent, expiré ou sans le droit « Actions »
  console.log(workflow, r.status, r.status === 204 ? "lancé" : await r.text());
  return r.status;
}

export default {
  async scheduled(event, env, ctx) {
    if (!env.GITHUB_TOKEN) { console.log("GITHUB_TOKEN manquant : rien n'est lancé"); return; }
    const workflows = PLAN[event.cron] || [];
    for (const w of workflows) ctx.waitUntil(dispatch(env, w));
  },
  // Page de contrôle (aucun secret affiché) : indique seulement si le jeton est en place
  async fetch(request, env) {
    return new Response(
      `Lucarne : déclencheur horaire des calculs.\nJeton GitHub : ${env.GITHUB_TOKEN ? "en place" : "manquant"}\n`,
      { headers: { "Content-Type": "text/plain; charset=utf-8" } });
  },
};
