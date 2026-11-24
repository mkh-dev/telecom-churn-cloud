/* Tableau de bord du churn télécom (membre 4).
 * Lit GET {API_BASE_URL}/api/recent (contrat : docs/contrats.md, section 5) et affiche
 * indicateurs, graphiques Chart.js et tableaux. Sans API configurée, ou avec ?demo=1,
 * utilise les données fictives de demo/recent.json.
 */
(() => {
  "use strict";

  const CFG = Object.assign(
    { API_BASE_URL: "", RECENT_LIMIT: 20, REFRESH_SECONDS: 30, LATENCY_SLO_MS: 2000 },
    window.DASHBOARD_CONFIG || {},
  );
  const params = new URLSearchParams(location.search);
  const API_BASE = (params.get("api") || CFG.API_BASE_URL || "").replace(/\/+$/, "");
  const FORCE_DEMO = params.has("demo") || !API_BASE;
  const RISKS = ["élevé", "moyen", "faible"];

  const $ = (id) => document.getElementById(id);
  const nf = new Intl.NumberFormat("fr-FR");
  const pf = new Intl.NumberFormat("fr-FR", { style: "percent", maximumFractionDigits: 1 });
  const df = new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "short" });
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmtMs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(2).replace(".", ",")} s` : `${Math.round(ms)} ms`);
  const shortName = (f) => String(f || "").replace(/\.csv$/i, "").replace(/^clients-/, "");

  // Mêmes seuils que model/churn_model.py (contrat, section 2)
  const riskLevel = (p) => (p >= 0.7 ? "élevé" : p >= 0.4 ? "moyen" : "faible");

  const state = { data: null, charts: {}, timer: null, risk: "", search: "" };

  // ---------- Thème ----------
  const THEME_KEY = "churn-dashboard-theme";
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved) document.documentElement.dataset.theme = saved;
  } catch { /* stockage indisponible : thème du système */ }
  $("theme").addEventListener("click", () => {
    const dark = getComputedStyle(document.documentElement).colorScheme === "dark";
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch { /* ignoré */ }
    if (state.data) renderCharts(state.data);
  });

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ---------- Données ----------
  /** Accepte {items, latest} (contrat), {inferences}, ou un tableau brut de documents Cosmos. */
  function normalize(raw) {
    const items = Array.isArray(raw) ? raw : raw.items || raw.inferences || [];
    const docs = items
      .filter((d) => d && d.fileName)
      .map((d) => ({
        ...d,
        nCustomers: Number(d.nCustomers) || 0,
        nHighRisk: Number(d.nHighRisk) || 0,
        nMediumRisk: d.nMediumRisk == null ? null : Number(d.nMediumRisk) || 0,
        churnRate: Number(d.churnRate) || 0,
        durationMs: Number(d.durationMs) || 0,
        status: d.status || "ok",
        ts: new Date(d.timestamp),
      }))
      .sort((a, b) => b.ts - a.ts);
    let latest = !Array.isArray(raw) && raw.latest && Array.isArray(raw.latest.predictions) ? raw.latest : null;
    if (!latest) {
      // Format réel de la fonction recent (membre 3) : pas de détail complet, mais topRisk
      // (clients les plus à risque) dans chaque document. On affiche ceux du dernier fichier traité.
      const last = docs.find((d) => d.status === "ok" && Array.isArray(d.topRisk) && d.topRisk.length);
      if (last) {
        latest = {
          fileName: last.fileName,
          modelVersion: last.modelVersion,
          partial: true,
          predictions: last.topRisk.map((p) => {
            const prob = Number(p.churn_probability) || 0;
            return {
              customerID: p.customerID,
              churn_probability: prob,
              churn_prediction: p.churn_prediction ?? (prob >= 0.5 ? 1 : 0),
              risk_level: p.risk_level || riskLevel(prob),
            };
          }),
        };
      }
    }
    return { docs, latest, demo: Boolean(raw.demo), generatedAt: raw.generatedAt };
  }

  async function fetchJson(url) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 15000);
    try {
      const res = await fetch(url, { signal: ctrl.signal, headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } finally {
      clearTimeout(t);
    }
  }

  async function load() {
    const btn = $("refresh");
    btn.disabled = true;
    let source = "live";
    let error = null;
    let raw;
    try {
      if (FORCE_DEMO) {
        source = "demo";
        raw = await fetchJson("demo/recent.json");
      } else {
        try {
          raw = await fetchJson(`${API_BASE}/api/recent?limit=${encodeURIComponent(CFG.RECENT_LIMIT)}`);
        } catch (e) {
          error = e;
          source = "fallback";
          raw = await fetchJson("demo/recent.json");
        }
      }
      state.data = normalize(raw);
      render(state.data);
      setSource(source, error);
    } catch (e) {
      setSource("error", e);
    } finally {
      btn.disabled = false;
      $("updated").textContent = `Mis à jour à ${new Date().toLocaleTimeString("fr-FR")}`;
    }
  }

  function setSource(kind, error) {
    const pill = $("source");
    const banner = $("banner");
    banner.hidden = true;
    banner.className = "banner";
    if (kind === "live") {
      pill.className = "pill live";
      pill.textContent = "En direct";
      pill.title = `${API_BASE}/api/recent`;
    } else if (kind === "demo") {
      pill.className = "pill demo";
      pill.textContent = "Mode démo";
      banner.hidden = false;
      banner.textContent = "Données fictives (demo/recent.json). Renseigner API_BASE_URL dans config.js ou ajouter ?api=https://<function-app> à l'URL pour afficher les vrais résultats.";
    } else if (kind === "fallback") {
      pill.className = "pill error";
      pill.textContent = "API injoignable";
      banner.hidden = false;
      banner.className = "banner error";
      banner.textContent = `Impossible de joindre ${API_BASE}/api/recent (${error && error.message}). Affichage des données fictives en attendant. Vérifier la Function App et son CORS.`;
    } else {
      pill.className = "pill error";
      pill.textContent = "Erreur";
      banner.hidden = false;
      banner.className = "banner error";
      banner.textContent = `Chargement impossible : ${error && error.message}`;
    }
  }

  // ---------- Rendu ----------
  function render(data) {
    renderKpis(data);
    renderCharts(data);
    renderFiles(data);
    renderClients();
  }

  function renderKpis({ docs, latest }) {
    const ok = docs.filter((d) => d.status === "ok");
    const customers = ok.reduce((s, d) => s + d.nCustomers, 0);
    const high = ok.reduce((s, d) => s + d.nHighRisk, 0);
    const churned = ok.reduce((s, d) => s + d.churnRate * d.nCustomers, 0);
    const durations = ok.map((d) => d.durationMs).filter((x) => x > 0);
    const avgDur = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0;
    const overSlo = durations.filter((x) => x > CFG.LATENCY_SLO_MS).length;
    const errors = docs.length - ok.length;
    const versions = [...new Set(ok.map((d) => d.modelVersion).filter(Boolean))];

    $("k-files").textContent = nf.format(docs.length);
    $("k-files-sub").textContent = errors ? `${errors} en erreur` : "aucune erreur";
    $("k-customers").textContent = nf.format(customers);
    $("k-customers-sub").textContent = docs[0] ? `dernier : ${df.format(docs[0].ts)}` : "";
    $("k-high").textContent = nf.format(high);
    $("k-high-sub").textContent = customers ? `${pf.format(high / customers)} des clients` : "";
    $("k-churn").textContent = customers ? pf.format(churned / customers) : "–";
    $("k-duration").textContent = durations.length ? fmtMs(avgDur) : "–";
    $("k-duration-sub").textContent = durations.length
      ? `moyenne · ${overSlo} fichier(s) > ${fmtMs(CFG.LATENCY_SLO_MS)}`
      : "";
    $("k-model").textContent = latest?.modelVersion || ok[0]?.modelVersion || "–";
    $("k-model-sub").textContent = versions.length > 1 ? `versions actives : ${versions.join(", ")}` : "version du dernier fichier";
  }

  function baseOptions() {
    const text = css("--muted");
    const grid = css("--border");
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      plugins: {
        legend: { labels: { color: text, boxWidth: 12, usePointStyle: true } },
        tooltip: { mode: "index", intersect: false },
      },
      scales: {
        x: { ticks: { color: text, maxRotation: 0, autoSkip: true }, grid: { display: false } },
        y: { ticks: { color: text }, grid: { color: grid }, beginAtZero: true },
      },
    };
  }

  function chart(id, config) {
    if (state.charts[id]) state.charts[id].destroy();
    state.charts[id] = new Chart($(id), config);
  }

  function renderCharts({ docs, latest }) {
    if (!window.Chart) return; // CDN indisponible : les tableaux restent affichés
    const chrono = docs.filter((d) => d.status === "ok").slice().reverse();
    const labels = chrono.map((d) => shortName(d.fileName));
    const primary = css("--primary");
    const high = css("--high");
    const medium = css("--medium");
    const low = css("--low");
    const muted = css("--muted");

    // 1. Taux de churn par fichier
    const o1 = baseOptions();
    o1.scales.y.ticks.callback = (v) => pf.format(v);
    o1.scales.y.suggestedMax = 0.5;
    o1.plugins.tooltip.callbacks = { label: (c) => ` ${c.dataset.label} : ${pf.format(c.parsed.y)}` };
    chart("c-churn", {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "Churn prédit", data: chrono.map((d) => d.churnRate), borderColor: primary, backgroundColor: primary + "22", fill: true, tension: 0.3, pointRadius: 3 },
          { label: "Part à risque élevé", data: chrono.map((d) => (d.nCustomers ? d.nHighRisk / d.nCustomers : 0)), borderColor: high, borderDash: [5, 4], pointRadius: 2, tension: 0.3 },
        ],
      },
      options: o1,
    });

    // 2. Niveaux de risque (détail du dernier fichier, sinon agrégat Cosmos)
    let riskLabels, riskData, riskColors;
    if (latest && !latest.partial && latest.predictions.length) {
      const counts = Object.fromEntries(RISKS.map((r) => [r, 0]));
      latest.predictions.forEach((p) => { if (p.risk_level in counts) counts[p.risk_level]++; });
      riskLabels = ["Élevé (≥ 70 %)", "Moyen (40-70 %)", "Faible (< 40 %)"];
      riskData = RISKS.map((r) => counts[r]);
      riskColors = [high, medium, low];
      $("risk-scope").textContent = shortName(latest.fileName);
    } else {
      const tot = chrono.reduce((s, d) => s + d.nCustomers, 0);
      const hi = chrono.reduce((s, d) => s + d.nHighRisk, 0);
      if (chrono.length && chrono.every((d) => d.nMediumRisk != null)) {
        const mid = chrono.reduce((s, d) => s + d.nMediumRisk, 0);
        riskLabels = ["Élevé (≥ 70 %)", "Moyen (40-70 %)", "Faible (< 40 %)"];
        riskData = [hi, mid, Math.max(tot - hi - mid, 0)];
        riskColors = [high, medium, low];
      } else {
        riskLabels = ["Élevé", "Moyen + faible"];
        riskData = [hi, tot - hi];
        riskColors = [high, low];
      }
      $("risk-scope").textContent = "tous les fichiers";
    }
    chart("c-risk", {
      type: "doughnut",
      data: { labels: riskLabels, datasets: [{ data: riskData, backgroundColor: riskColors, borderColor: css("--surface"), borderWidth: 2 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: {
          legend: { position: "bottom", labels: { color: muted, boxWidth: 12, usePointStyle: true } },
          tooltip: {
            callbacks: {
              label: (c) => {
                const sum = c.dataset.data.reduce((a, b) => a + b, 0) || 1;
                return ` ${nf.format(c.parsed)} clients (${pf.format(c.parsed / sum)})`;
              },
            },
          },
        },
      },
    });

    // 3. Volume par fichier
    const o3 = baseOptions();
    o3.scales.x.stacked = true;
    o3.scales.y.stacked = true;
    chart("c-volume", {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Risque élevé", data: chrono.map((d) => d.nHighRisk), backgroundColor: high, borderRadius: 3 },
          { label: "Autres clients", data: chrono.map((d) => d.nCustomers - d.nHighRisk), backgroundColor: primary + "99", borderRadius: 3 },
        ],
      },
      options: o3,
    });

    // 4. Durée de traitement vs objectif
    const o4 = baseOptions();
    o4.scales.y.ticks.callback = (v) => fmtMs(v);
    o4.plugins.tooltip.callbacks = { label: (c) => ` ${c.dataset.label} : ${fmtMs(c.parsed.y)}` };
    $("slo-label").textContent = `objectif < ${fmtMs(CFG.LATENCY_SLO_MS)}`;
    chart("c-duration", {
      type: "bar",
      data: {
        labels,
        datasets: [
          { type: "bar", label: "Durée", data: chrono.map((d) => d.durationMs), backgroundColor: chrono.map((d) => (d.durationMs > CFG.LATENCY_SLO_MS ? high : low)), borderRadius: 3 },
          { type: "line", label: "Objectif", data: chrono.map(() => CFG.LATENCY_SLO_MS), borderColor: muted, borderDash: [6, 4], pointRadius: 0, borderWidth: 1.5 },
        ],
      },
      options: o4,
    });
  }

  function renderFiles({ docs }) {
    const tbody = $("t-files").querySelector("tbody");
    if (!docs.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty">Aucun fichier traité pour l'instant. Déposer un CSV dans <code>input/</code>.</td></tr>`;
      return;
    }
    tbody.innerHTML = docs
      .map((d) => `<tr>
        <td>${esc(isNaN(d.ts) ? d.timestamp : df.format(d.ts))}</td>
        <td>${esc(d.fileName)}</td>
        <td class="num">${nf.format(d.nCustomers)}</td>
        <td class="num">${nf.format(d.nHighRisk)}</td>
        <td class="num">${d.status === "ok" ? pf.format(d.churnRate) : "–"}</td>
        <td>${esc(d.modelVersion || "–")}</td>
        <td class="num">${d.durationMs ? fmtMs(d.durationMs) : "–"}</td>
        <td><span class="badge ${d.status === "ok" ? "ok" : "error"}">${esc(d.status)}</span></td>
      </tr>`)
      .join("");
  }

  function filteredClients() {
    const latest = state.data?.latest;
    if (!latest) return [];
    const q = state.search.trim().toLowerCase();
    return latest.predictions
      .filter((p) => !state.risk || p.risk_level === state.risk)
      .filter((p) => !q || String(p.customerID).toLowerCase().includes(q))
      .sort((a, b) => b.churn_probability - a.churn_probability);
  }

  function renderClients() {
    const latest = state.data?.latest;
    const card = $("clients-card");
    card.hidden = !latest;
    if (!latest) return;
    $("clients-title").textContent = latest.partial ? "Clients les plus à risque du dernier fichier" : "Clients du dernier fichier";
    $("clients-file").textContent = `${latest.fileName}${latest.modelVersion ? ` · modèle ${latest.modelVersion}` : ""}`;
    const colors = { "élevé": css("--high"), moyen: css("--medium"), faible: css("--low") };
    const rows = filteredClients();
    const shown = rows.slice(0, 300);
    $("t-clients").querySelector("tbody").innerHTML = shown.length
      ? shown.map((p) => `<tr>
          <td><code>${esc(p.customerID)}</code></td>
          <td><div class="bar"><span class="track"><span class="fill" style="width:${(p.churn_probability * 100).toFixed(1)}%;background:${colors[p.risk_level] || css("--primary")}"></span></span><b>${pf.format(p.churn_probability)}</b></div></td>
          <td>${p.churn_prediction ? "Départ probable" : "Reste"}</td>
          <td><span class="badge ${esc(p.risk_level)}">${esc(p.risk_level)}</span></td>
        </tr>`).join("")
      : `<tr><td colspan="4" class="empty">Aucun client ne correspond au filtre.</td></tr>`;
    $("clients-count").textContent = `${nf.format(rows.length)} client(s) sur ${nf.format(latest.predictions.length)}${rows.length > shown.length ? ` · 300 premiers affichés` : ""}`;
  }

  // ---------- Interactions ----------
  document.querySelectorAll(".seg button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x === b));
      state.risk = b.dataset.risk;
      renderClients();
    }),
  );
  $("search").addEventListener("input", (e) => { state.search = e.target.value; renderClients(); });

  $("export").addEventListener("click", () => {
    const rows = filteredClients();
    const csv = ["customerID,churn_probability,churn_prediction,risk_level"]
      .concat(rows.map((p) => [p.customerID, p.churn_probability, p.churn_prediction, p.risk_level].join(",")))
      .join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
    a.download = `predictions-${shortName(state.data.latest.fileName)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  $("refresh").addEventListener("click", load);

  function schedule() {
    clearInterval(state.timer);
    if ($("auto").checked && CFG.REFRESH_SECONDS > 0) state.timer = setInterval(load, CFG.REFRESH_SECONDS * 1000);
  }
  $("auto").addEventListener("change", schedule);

  window.addEventListener("load", () => { load(); schedule(); });
})();
