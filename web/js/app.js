/* =====================================================================
   FinSight front-end - vanilla JS + GSAP + Chart.js
   ===================================================================== */
(() => {
  "use strict";

  // ------------------------------------------------------------ helpers --
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (v) => {
    if (v == null || isNaN(v)) return "—";
    const a = Math.abs(v), s = v < 0 ? "-" : "";
    if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(1)}K`;
    return `${s}$${a.toFixed(0)}`;
  };
  const usd = (v, d = 4) => (v == null || isNaN(v) ? "—" : `$${Number(v).toFixed(d)}`);
  const num = (v) => (v == null ? "—" : Number(v).toLocaleString());
  const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
  const PALETTE = ["#818cf8", "#c084fc", "#22d3ee", "#34d399", "#f472b6", "#fbbf24", "#60a5fa", "#fb7185"];

  async function api(path, opts = {}) {
    const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    return r.json();
  }

  const state = { page: "dashboard", mode: "enforce", loaded: {}, charts: {}, budget: null, config: null, running: false };

  // ------------------------------------------------------------- theme --
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function applyChartTheme() {
    if (!window.Chart) return;
    Chart.defaults.color = cssVar("--muted-solid");
    Chart.defaults.borderColor = cssVar("--grid-line");
    Chart.defaults.font.family = "Inter, system-ui, sans-serif";
    Chart.defaults.font.size = 10.5;
    Chart.defaults.plugins.legend.labels.boxWidth = 8;
    Chart.defaults.plugins.legend.labels.boxHeight = 8;
    Chart.defaults.plugins.legend.labels.padding = 8;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.layout.padding = 2;
  }
  function setTheme(t) {
    document.documentElement.dataset.theme = t;
    localStorage.setItem("finsight-theme", t);
    applyChartTheme();
    // re-render charts with new colours
    Object.keys(state.loaded).forEach((k) => { state.loaded[k] = false; });
    loadPage(state.page, true);
  }
  $("#themeToggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    gsap.to("body", { opacity: 0.6, duration: 0.15, yoyo: true, repeat: 1 });
    setTheme(next);
  });

  // ------------------------------------------------------------ charts --
  function chart(id, cfg) {
    if (state.charts[id]) state.charts[id].destroy();
    const el = document.getElementById(id);
    if (!el) return null;
    state.charts[id] = new Chart(el, cfg);
    return state.charts[id];
  }
  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  const fade = (hex) => (c) => {
    const { ctx, chartArea } = c.chart;
    if (!chartArea) return hexA(hex, 0.3);
    const g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
    g.addColorStop(0, hexA(hex, 0.45));
    g.addColorStop(1, hexA(hex, 0.0));
    return g;
  };
  const moneyAxis = { ticks: { callback: (v) => money(v) } };

  // --------------------------------------------------------------- nav --
  function moveIndicator(btn, instant) {
    const ind = $("#navIndicator");
    const nav = $("#nav").getBoundingClientRect();
    const b = btn.getBoundingClientRect();
    gsap.to(ind, { x: b.left - nav.left - 3, width: b.width, duration: instant ? 0 : 0.45, ease: "power3.out" });
    ind.style.left = "3px";
  }
  function goto(page) {
    if (page === state.page) return;
    const cur = $(`#page-${state.page}`), next = $(`#page-${page}`);
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
    moveIndicator($(`.nav-btn[data-page="${page}"]`));
    gsap.to(cur, {
      opacity: 0, y: 12, duration: 0.2, onComplete: () => {
        cur.classList.remove("active");
        next.classList.add("active");
        gsap.fromTo(next, { opacity: 0, y: 16 }, { opacity: 1, y: 0, duration: 0.4, ease: "power2.out" });
        state.page = page;
        if (page === "agent") modeThumb(true);
        if (page === "pitch") enterPitch();
        syncPresenting();
        annRefresh();
        loadPage(page);
      },
    });
  }
  $$(".nav-btn").forEach((b) => b.addEventListener("click", () => goto(b.dataset.page)));
  document.addEventListener("click", (e) => {
    const g = e.target.closest("[data-goto]");
    if (g) goto(g.dataset.goto);
  });
  window.addEventListener("resize", () => moveIndicator($(".nav-btn.active"), true));

  function revealCards(root) {
    gsap.fromTo($$(".reveal, .kpi", root), { opacity: 0, y: 24, scale: 0.98 },
      { opacity: 1, y: 0, scale: 1, duration: 0.6, stagger: 0.05, ease: "power3.out" });
  }

  async function loadPage(page, force) {
    if (state.loaded[page] && !force) return;
    state.loaded[page] = true;
    try {
      if (page === "dashboard") await loadDashboard();
      if (page === "analytics") await loadAnalytics();
      if (page === "agent") await loadAgent();
      if (page === "governance") await loadGovernance();
      if (page === "evals") await loadEvals();
      if (page === "proof") await loadProof();
      if (page === "data") await loadData();
    } catch (err) {
      console.error(err);
      state.loaded[page] = false;
    }
  }

  // ----------------------------------------------------------- status --
  async function loadStatus() {
    try {
      const h = await api("/api/health");
      const online = h.mcp_servers.filter((s) => s.status === "online").length;
      $("#statusPills").innerHTML = [
        [`Control plane`, h.control_plane],
        [`MCP ${online}/${h.mcp_servers.length} · ${h.tools} tools`, online === h.mcp_servers.length && h.tools > 0],
        [`Azure OpenAI`, h.aoai_configured],
      ].map(([l, ok]) => `<span class="pill ${ok ? "ok" : "bad"}"><i></i><span>${esc(l)}</span></span>`).join("");
    } catch (_) {
      $("#statusPills").innerHTML = `<span class="pill bad"><i></i><span>API offline</span></span>`;
    }
  }

  // --------------------------------------------------------- dashboard --
  const PROMPTS = [
    "Give me a board-ready summary of the last 12 months by business line and region, with the top 3 risks.",
    "Which regions and business lines drive net income growth, and where is the cost/income ratio deteriorating?",
    "Summarise our AML exposure: high-risk open alerts, SAR filings and whether we meet our AML escalation policy.",
    "How healthy is the loan book? Show delinquency, watchlist names and expected loss by rating.",
    "Which wealth portfolios underperform their benchmark and where is our concentration risk?",
    "What are customers telling us? Combine NPS, CRM sentiment and churn risk with recommended actions.",
  ];

  function fmtKpi(k, v) {
    if (k.unit === "$") return money(v);
    if (k.unit === "%") return `${v.toFixed(1)}%`;
    return Math.round(v).toLocaleString();
  }

  async function loadDashboard() {
    const d = await api("/api/dashboard");
    $("#asOf").textContent = `as of ${d.as_of}`;
    $("#kpiGrid").innerHTML = d.kpis.map((k) => {
      const good = k.delta == null ? null : (k.invert ? k.delta < 0 : k.delta >= 0);
      const unit = k.unit === "%" || k.id === "nps" ? " pts" : "%";
      const delta = k.delta == null ? "" : `${k.delta >= 0 ? "▲" : "▼"} ${Math.abs(k.delta).toFixed(1)}${unit} YoY`;
      return `<div class="kpi glass"><span class="kpi-label">${esc(k.label)}</span>
        <div class="kpi-main"><span class="kpi-value" data-v="${k.value}" data-id="${esc(k.id)}">0</span>
        <span class="kpi-delta ${good == null ? "" : good ? "good-t" : "bad-t"}">${esc(delta)}</span></div></div>`;
    }).join("");
    $$("#kpiGrid .kpi-value").forEach((el, i) => {
      const k = d.kpis[i], o = { v: 0 };
      gsap.to(o, { v: k.value, duration: 1.6, ease: "power3.out", delay: i * 0.05, onUpdate: () => { el.textContent = fmtKpi(k, o.v); } });
    });

    chart("chTrend", {
      type: "line",
      data: {
        labels: d.trend.map((r) => r.month),
        datasets: [
          { label: "Revenue", data: d.trend.map((r) => r.revenue), borderColor: PALETTE[0], backgroundColor: fade(PALETTE[0]), fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2.5 },
          { label: "Net income", data: d.trend.map((r) => r.net_income), borderColor: PALETTE[2], backgroundColor: fade(PALETTE[2]), fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2.5 },
          { label: "Operating expense", data: d.trend.map((r) => r.opex), borderColor: PALETTE[4], borderDash: [5, 4], tension: 0.35, pointRadius: 0, borderWidth: 1.5 },
        ],
      },
      options: { interaction: { mode: "index", intersect: false }, scales: { y: moneyAxis, x: { ticks: { maxTicksLimit: 12 } } },
        plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money(c.raw)}` } } } },
    });
    chart("chMix", {
      type: "doughnut",
      data: { labels: d.by_business_line.map((r) => r.business_line), datasets: [{ data: d.by_business_line.map((r) => r.revenue), backgroundColor: PALETTE, borderWidth: 0, hoverOffset: 10 }] },
      options: { cutout: "68%", plugins: { legend: { position: "right" }, tooltip: { callbacks: { label: (c) => `${c.label}: ${money(c.raw)}` } } } },
    });
    chart("chRegion", {
      type: "bar",
      data: {
        labels: d.by_region.map((r) => r.region),
        datasets: [
          { label: "Revenue", data: d.by_region.map((r) => r.revenue), backgroundColor: hexA(PALETTE[0], 0.8), borderRadius: 8 },
          { label: "Net income", data: d.by_region.map((r) => r.net_income), backgroundColor: hexA(PALETTE[2], 0.8), borderRadius: 8 },
        ],
      },
      options: { scales: { y: moneyAxis }, plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money(c.raw)}` } } } },
    });

    const r = d.risk;
    $("#riskTiles").innerHTML = [
      ["Open AML alerts", num(r.aml_open), ""], ["High-risk open (≥80)", num(r.aml_high_open), "bad-t"],
      ["SARs filed", num(r.sar_filed), "warn-t"], ["NPL ratio", `${r.npl_ratio}%`, r.npl_ratio > 2 ? "bad-t" : "good-t"],
      ["Expected loss", money(r.expected_loss), ""], ["Credit watchlist", num(r.watchlist), "warn-t"],
      ["Open findings", num(r.findings_open), ""], ["Critical / overdue", `${r.findings_critical} / ${r.findings_overdue}`, "bad-t"],
    ].map(([l, v, c]) => `<div class="tile"><small>${esc(l)}</small><span class="${c}">${esc(v)}</span></div>`).join("");

    if (!$("#railMessages").children.length) railReset();
    await loadAiTiles();
    revealCards($("#page-dashboard"));
  }

  const chipHtml = (p) => `<button class="chip" data-prompt="${esc(p)}" title="${esc(p)}" type="button"><span>${esc(p)}</span></button>`;

  function railReset() {
    $("#railMessages").innerHTML = `<div class="rail-intro"><small>Suggested questions</small><div class="chips stack">${PROMPTS.map(chipHtml).join("")}</div></div>`;
    $("#railPinChips").innerHTML = PROMPTS.map(chipHtml).join("");
    $("#railPins").hidden = true;
    $("#railTrace").innerHTML = "";
    state.railCost = 0;
    meter(0, state.budget);
  }
  $("#railClear").addEventListener("click", () => { if (!state.running) railReset(); });

  function meter(cost, cap) {
    const bar = $("#railMeterBar");
    const pct = cap ? cost / cap : 0;
    bar.classList.toggle("warn", pct > 0.8 && pct <= 1);
    bar.classList.toggle("bad", pct > 1);
    gsap.to(bar, { width: `${Math.min(pct, 1) * 100}%`, duration: 0.5, ease: "power2.out" });
    $("#railMeterLbl").textContent = cap
      ? `${usd(cost)} of ${usd(cap, 3)} per-run budget · ${(pct * 100).toFixed(0)}%`
      : `${usd(cost)} · per-run budget unknown`;
  }

  async function loadAiTiles() {
    try {
      const s = await api("/api/tokenops/summary");
      state.budget = s.kpis.run_budget_usd;
      $("#aiTiles").innerHTML = [
        ["Budget per run", usd(s.kpis.run_budget_usd, 3), ""], ["Governed runs", num(s.kpis.total_runs), ""],
        ["AI spend (all runs)", usd(s.kpis.total_cost_usd), ""], ["Avg cost / run", usd(s.kpis.avg_cost_usd), "good-t"],
        ["Runs halted by policy", num(s.kpis.halted), s.kpis.halted ? "warn-t" : "good-t"], ["Active policies", num(s.policies.length), ""],
      ].map(([l, v, c]) => `<div class="tile"><small>${esc(l)}</small><span class="${c}">${esc(v)}</span></div>`).join("");
      if (!state.running) meter(state.railCost || 0, state.budget);
    } catch (e) {
      $("#aiTiles").innerHTML = `<div class="tile"><small>TokenOps</small><span class="bad-t">control plane offline</span></div>`;
    }
  }

  // Suggested-question chips run in whichever panel they live in (rail or AI Analyst page).
  document.addEventListener("click", (e) => {
    const c = e.target.closest("[data-prompt]");
    if (!c || state.running) return;
    const v = c.closest("#askRail") ? VIEWS.rail : VIEWS.agent;
    $(v.input).value = c.dataset.prompt;
    $(v.form).requestSubmit();
  });

  // --------------------------------------------------------- analytics --
  async function loadAnalytics() {
    const a = await api("/api/analytics");
    const bls = Object.keys(a.revenue_by_bl);
    chart("chBL", {
      type: "line",
      data: { labels: a.months, datasets: bls.map((b, i) => ({ label: b, data: a.revenue_by_bl[b], borderColor: PALETTE[i], backgroundColor: hexA(PALETTE[i], 0.25), fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2 })) },
      options: { interaction: { mode: "index", intersect: false }, scales: { y: { stacked: true, ...moneyAxis }, x: { ticks: { maxTicksLimit: 12 } } },
        plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money(c.raw)}` } } } },
    });
    const chans = Object.keys(a.digital_users);
    chart("chDigital", {
      type: "line",
      data: { labels: a.digital_months, datasets: chans.map((c, i) => ({ label: c, data: a.digital_users[c], borderColor: PALETTE[(i + 2) % 8], backgroundColor: fade(PALETTE[(i + 2) % 8]), fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2 })) },
      options: { interaction: { mode: "index", intersect: false }, scales: { x: { ticks: { maxTicksLimit: 12 } } } },
    });
    chart("chChannels", {
      type: "polarArea",
      data: { labels: a.channel_mix.map((r) => r.channel), datasets: [{ data: a.channel_mix.map((r) => r.amount), backgroundColor: PALETTE.map((p) => hexA(p, 0.6)), borderWidth: 0 }] },
      options: { scales: { r: { ticks: { display: false } } }, plugins: { legend: { position: "right" }, tooltip: { callbacks: { label: (c) => `${c.label}: ${money(c.raw)}` } } } },
    });
    const statusColor = { "Current": PALETTE[3], "30-89 DPD": PALETTE[5], "90+ DPD": "#fb923c", "Default": PALETTE[7] };
    chart("chLoans", {
      type: "bar",
      data: { labels: a.loan_status.map((r) => r.status), datasets: [{ label: "Outstanding", data: a.loan_status.map((r) => r.outstanding), backgroundColor: a.loan_status.map((r) => hexA(statusColor[r.status] || PALETTE[0], 0.8)), borderRadius: 8 }] },
      options: { indexAxis: "y", scales: { x: { type: "logarithmic", ...moneyAxis } }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => money(c.raw) } } } },
    });
    chart("chRatings", {
      type: "bar",
      data: { labels: a.ratings.map((r) => r.internal_rating), datasets: [
        { label: "EAD", data: a.ratings.map((r) => r.ead), backgroundColor: hexA(PALETTE[0], 0.8), borderRadius: 6, yAxisID: "y" },
        { label: "Expected loss", data: a.ratings.map((r) => r.el), type: "line", borderColor: PALETTE[7], backgroundColor: PALETTE[7], tension: 0.3, yAxisID: "y1" },
      ] },
      options: { scales: { y: moneyAxis, y1: { position: "right", grid: { display: false }, ...moneyAxis } }, plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money(c.raw)}` } } } },
    });
    chart("chAml", {
      type: "bar",
      data: { labels: a.aml_scenarios.map((r) => r.scenario), datasets: [{ label: "Alerts", data: a.aml_scenarios.map((r) => r.n), backgroundColor: a.aml_scenarios.map((_, i) => hexA(PALETTE[i % 8], 0.75)), borderRadius: 8 }] },
      options: { indexAxis: "y", plugins: { legend: { display: false } } },
    });
    chart("chStrat", {
      type: "bar",
      data: { labels: a.strategies.map((r) => r.strategy), datasets: [
        { label: "YTD return %", data: a.strategies.map((r) => r.ytd), backgroundColor: hexA(PALETTE[1], 0.8), borderRadius: 6 },
        { label: "Benchmark %", data: a.strategies.map((r) => r.bench), backgroundColor: hexA(PALETTE[2], 0.6), borderRadius: 6 },
      ] },
      options: { plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${Number(c.raw).toFixed(2)}%` } } } },
    });
    const sChans = Object.keys(a.crm_sentiment);
    const sColor = { Positive: PALETTE[3], Neutral: PALETTE[0], Negative: PALETTE[7] };
    chart("chSent", {
      type: "bar",
      data: { labels: sChans, datasets: ["Positive", "Neutral", "Negative"].map((s) => ({ label: s, data: sChans.map((c) => a.crm_sentiment[c][s] || 0), backgroundColor: hexA(sColor[s], 0.8), borderRadius: 4 })) },
      options: { scales: { x: { stacked: true }, y: { stacked: true } } },
    });
    chart("chMarket", {
      type: "bar",
      data: { labels: a.market.map((m) => m.symbol), datasets: [{ label: "1Y return %", data: a.market.map((m) => m.return_1y), backgroundColor: a.market.map((m) => hexA(m.return_1y >= 0 ? PALETTE[3] : PALETTE[7], 0.8)), borderRadius: 6 }] },
      options: { plugins: { legend: { display: false }, tooltip: { callbacks: { title: (c) => a.market[c[0].dataIndex].name, label: (c) => `${c.raw}%` } } } },
    });
    chart("chSeg", {
      type: "bar",
      data: { labels: a.segments.map((s) => s.segment), datasets: [
        { label: "Customers", data: a.segments.map((s) => s.n), backgroundColor: hexA(PALETTE[1], 0.8), borderRadius: 8, yAxisID: "y" },
        { label: "Avg income", data: a.segments.map((s) => s.income), type: "line", borderColor: PALETTE[2], backgroundColor: PALETTE[2], tension: 0.3, yAxisID: "y1" },
      ] },
      options: { scales: { y1: { position: "right", grid: { display: false }, ...moneyAxis } } },
    });
    revealCards($("#page-analytics"));
  }

  // ------------------------------------------------------------- agent --
  function modeThumb(instant) {
    const act = $("#modeToggle button.active");
    gsap.to("#modeToggle .seg-thumb", { x: act.offsetLeft - 3, width: act.offsetWidth, duration: instant ? 0 : 0.35, ease: "power3.out" });
    $("#modeToggle .seg-thumb").style.left = "3px";
  }
  function setMode(m) {
    state.mode = m;
    $$("#modeToggle button").forEach((x) => x.classList.toggle("active", x.dataset.mode === m));
    if (state.page === "agent") modeThumb();
    const rm = $("#railMode");
    rm.classList.toggle("preview", m === "preview");
    rm.querySelector("span").textContent = m === "preview" ? "Preview" : "Enforce";
  }
  $$("#modeToggle button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  $("#railMode").addEventListener("click", () => setMode(state.mode === "enforce" ? "preview" : "enforce"));

  function gauge(cost, budget) {
    const cap = budget || 0;
    const used = Math.min(cost, cap || cost);
    const over = cap && cost > cap;
    const pct = cap ? cost / cap : 0;
    const col = over || pct > 0.95 ? PALETTE[7] : pct > 0.8 ? PALETTE[5] : PALETTE[0];
    const c = state.charts.chGauge;
    const data = [used, Math.max(cap - used, 0) || (cap ? 0 : 1)];
    if (c) {
      c.data.datasets[0].data = data;
      c.data.datasets[0].backgroundColor = [col, hexA("#94a3b8", 0.15)];
      c.update();
    } else {
      chart("chGauge", {
        type: "doughnut",
        data: { datasets: [{ data, backgroundColor: [col, hexA("#94a3b8", 0.15)], borderWidth: 0 }] },
        options: { rotation: -90, circumference: 180, cutout: "78%", plugins: { tooltip: { enabled: false }, legend: { display: false } }, animation: { duration: 500 } },
      });
    }
    $("#gaugeVal").textContent = usd(cost);
    $("#gaugeVal").className = `gauge-val ${over ? "bad-t" : ""}`;
    $("#gaugeCap").textContent = cap ? `of ${usd(cap, 3)} per run · ${(pct * 100).toFixed(0)}%` : "budget unknown";
  }

  async function loadAgent() {
    $("#agentPrompts").innerHTML = PROMPTS.map(chipHtml).join("");
    if (state.budget == null) {
      try { state.budget = (await api("/api/tokenops/summary")).kpis.run_budget_usd; } catch (_) { /* offline */ }
    }
    gauge(0, state.budget);
    modeThumb(true);
  }

  // Two chat surfaces share one streaming implementation: the full AI Analyst
  // page and the compact "Ask FinSight" rail on the executive dashboard.
  const DEPT = { CEO: "Executive Office", CFO: "Finance", CRO: "Risk", COO: "Operations", CMO: "Marketing", "Head of Wealth": "Wealth" };
  const VIEWS = {
    agent: { full: true, form: "#composer", input: "#question", send: "#sendBtn", messages: "#messages", trace: "#timeline",
      persona: () => $("#persona").value, department: () => $("#department").value },
    rail: { full: false, form: "#railComposer", input: "#railQuestion", send: "#railSend", messages: "#railMessages", trace: "#railTrace",
      persona: () => $("#railPersona").value, department: () => DEPT[$("#railPersona").value] || "Executive Office" },
  };

  function addMsg(view, role, html, cls = "") {
    const box = $(view.messages);
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.innerHTML = `<div class="bubble ${cls}">${html}</div>`;
    box.appendChild(div);
    gsap.from(div, { opacity: 0, y: 14, duration: 0.35, ease: "power2.out" });
    box.scrollTop = box.scrollHeight;
    return div.querySelector(".bubble");
  }

  function tl(view, cls, title, sub) {
    const box = $(view.trace);
    const li = document.createElement("li");
    li.className = cls;
    li.innerHTML = `<span class="t-title">${esc(title)}</span>${sub ? `<span class="t-sub">${esc(sub)}</span>` : ""}`;
    box.appendChild(li);
    gsap.from(li, { opacity: 0, x: 20, duration: 0.3 });
    box.scrollTop = box.scrollHeight;
    return li;
  }

  function showCost(view, cost) {
    if (view.full) gauge(cost, state.budget);
    else { state.railCost = cost; meter(cost, state.budget); }
  }

  function renderMd(md) {
    return DOMPurify.sanitize(marked.parse(md || ""));
  }

  Object.values(VIEWS).forEach((v) => {
    $(v.form).addEventListener("submit", (e) => { e.preventDefault(); runAgent(v); });
    $(v.input).addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $(v.form).requestSubmit(); }
    });
  });

  async function runAgent(view) {
    const q = $(view.input).value.trim();
    if (!q || state.running) return;
    state.running = true;
    Object.values(VIEWS).forEach((v) => { $(v.send).disabled = true; });
    $(view.input).value = "";
    $(view.messages).querySelector(".rail-intro")?.remove();
    if (!view.full) $("#railPins").hidden = false;
    addMsg(view, "user", `<p>${esc(q)}</p>`);
    const bubble = addMsg(view, "assistant", `<span class="thinking"><i></i><i></i><i></i></span> <small>discovering tools &amp; planning…</small>`);
    $(view.trace).innerHTML = "";
    const t = { llm: 0, tools: 0, in: 0, out: 0, cost: 0 };
    const toolItems = {};
    showCost(view, 0);

    const body = { question: q, persona: view.persona(), department: view.department(), mode: state.mode };
    try {
      const resp = await fetch("/api/agent/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!resp.ok) {
        let msg = resp.statusText;
        try { msg = (await resp.json()).detail || msg; } catch (_) { /* ignore */ }
        throw new Error(msg);
      }
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          handle(JSON.parse(line.slice(6)));
        }
      }
    } catch (err) {
      bubble.classList.add("halt");
      bubble.innerHTML = `<p><strong class="bad-t">Error:</strong> <span>${esc(err.message)}</span></p>`;
    } finally {
      state.running = false;
      Object.values(VIEWS).forEach((v) => { $(v.send).disabled = false; });
      state.loaded.governance = false;
      state.loaded.evals = false;
      loadAiTiles();
    }

    function stats() {
      if (!view.full) return;
      $("#tLlm").textContent = t.llm;
      $("#tTools").textContent = t.tools;
      $("#tTok").textContent = `${num(t.in)} / ${num(t.out)}`;
    }
    function setModel(m) { if (view.full) $("#tModel").textContent = m; }

    function handle(ev) {
      switch (ev.type) {
        case "run_start":
          if (view.full) $("#runIdLbl").textContent = `${ev.run_id} · ${ev.mode}`;
          setModel(ev.model);
          tl(view, "", `Run started · ${ev.mode.toUpperCase()}`, `${ev.tools_available} MCP tools discovered and bound to the model`);
          break;
        case "tool_start":
          toolItems[ev.id] = tl(view, "tool", `MCP → ${ev.server} · ${ev.tool}`, JSON.stringify(ev.args));
          bubble.innerHTML = `<span class="thinking"><i></i><i></i><i></i></span> <small>calling ${esc(ev.tool)} on ${esc(ev.server)}…</small>`;
          break;
        case "tool_end": {
          t.tools += 1; stats();
          const li = toolItems[ev.id];
          if (li) {
            const s = document.createElement("span");
            s.className = "t-sub";
            s.textContent = `✓ ${ev.latency_ms} ms · ${num(ev.chars)} chars${ev.replaced_by_governance ? " · result replaced by TokenOps tool_fix" : ""}`;
            li.appendChild(s);
            li.classList.add("done");
          }
          break;
        }
        case "llm":
          t.llm += 1; t.in += ev.input_tokens; t.out += ev.output_tokens; t.cost = ev.cost_usd; stats();
          setModel(ev.model);
          showCost(view, ev.cost_usd);
          tl(view, "llm", `LLM · ${ev.model} · ${usd(ev.call_cost_usd, 5)}`,
            `${num(ev.input_tokens)} in / ${num(ev.output_tokens)} out · ${ev.latency_ms} ms${ev.tool_calls.length ? ` · requested ${ev.tool_calls.join(", ")}` : " · final answer"}`);
          bubble.innerHTML = `<span class="thinking"><i></i><i></i><i></i></span> <small>${ev.tool_calls.length ? "gathering data…" : "writing answer…"}</small>`;
          break;
        case "governance":
          tl(view, "gov", `TokenOps ${String(ev.kind).toUpperCase()} · ${ev.policy && ev.policy !== "—" ? ev.policy : "policy"}`, ev.reason);
          break;
        case "model_switch":
          setModel(ev.to);
          tl(view, "gov", `Model downgraded · ${ev.from} → ${ev.to}`, ev.reason);
          break;
        case "halt":
          showCost(view, ev.cost_usd);
          tl(view, "halt", "Run HALTED by TokenOps", ev.reason);
          break;
        case "final": {
          if (ev.budget_usd) state.budget = ev.budget_usd;
          showCost(view, ev.cost_usd);
          tl(view, ev.status === "completed" ? "done" : "halt", `Run ${ev.status}`, `${usd(ev.cost_usd)} · ${ev.llm_calls} LLM calls · ${ev.tool_calls} tool calls · ${ev.elapsed_s}s`);
          if (ev.status !== "completed") bubble.classList.add("halt");
          const events = ev.governance_events.length;
          bubble.innerHTML = renderMd(ev.answer) + `<div class="meta">
            <span class="badge ${esc(ev.status)}"><i></i>${esc(ev.status)}</span>
            <span class="badge"><i></i>${esc(usd(ev.cost_usd))} of ${esc(usd(ev.budget_usd, 3))}</span>
            <span class="badge"><i></i>${esc(ev.model_final)}</span>
            <span class="badge"><i></i>${ev.tool_calls} MCP calls</span>
            <span class="badge"><i></i>${events} governance action${events === 1 ? "" : "s"}</span>
            <span class="badge"><i></i>${esc(ev.mode)}</span></div>`;
          gsap.from(bubble.children, { opacity: 0, y: 8, stagger: 0.03, duration: 0.3 });
          $(view.messages).scrollTop = $(view.messages).scrollHeight;
          break;
        }
        case "error":
          bubble.classList.add("halt");
          bubble.innerHTML = `<p><strong class="bad-t">Agent error</strong></p><p>${esc(ev.message)}</p>`;
          tl(view, "halt", "Error", ev.message);
          break;
        default:
          break;
      }
    }
  }

  // -------------------------------------------------------- governance --
  const POLICY_INFO = {
    cost_budget: "Halts the run when the per-run LLM budget is exhausted.",
    pre_call_worst_case: "Bounds max output tokens so the worst-case call fits the remaining budget.",
    cost_guard: "Downgrades to the cheaper deployment under budget pressure.",
    step_cap: "Caps total LLM + tool steps per run.",
    concurrency_cap: "Limits concurrent in-flight runs.",
    tool_fix: "Repairs calls to tools not discovered from MCP.",
    tool_output_cap: "Flags oversized tool outputs.",
    progress_guard: "Detects loops / no-progress and steers or halts.",
    context_compaction: "Guards context growth.",
    output_runaway: "Stops repetitive runaway output.",
  };

  async function loadGovernance() {
    const s = await api("/api/tokenops/summary");
    state.budget = s.kpis.run_budget_usd;
    $("#planeLink").href = s.plane_url + "/";
    const k = s.kpis;
    $("#govKpis").innerHTML = [
      ["Governed runs", num(k.total_runs), ""], ["Completed", num(k.completed), "good-t"], ["Halted by policy", num(k.halted), k.halted ? "warn-t" : ""],
      ["Total AI spend", usd(k.total_cost_usd), ""], ["Avg cost / run", usd(k.avg_cost_usd), ""], ["Max run cost", usd(k.max_cost_usd), ""],
    ].map(([l, v, c]) => `<div class="kpi glass"><span class="kpi-label">${esc(l)}</span><span class="kpi-value ${c}">${esc(v)}</span></div>`).join("");

    $("#budgetNow").textContent = usd(k.run_budget_usd, 3);
    if (k.run_budget_usd) { $("#budgetInput").value = k.run_budget_usd; $("#budgetRange").value = Math.min(1, k.run_budget_usd); }

    $("#policyGrid").innerHTML = s.policies.map((p) => {
      const params = Object.entries(p.params || {}).filter(([, v]) => !Array.isArray(v) || v.length < 6)
        .map(([key, v]) => `${key}=${Array.isArray(v) ? `[${v.length}]` : v}`).join(" · ");
      const reg = Array.isArray((p.params || {}).registry) ? ` · registry: ${p.params.registry.length} MCP tools` : "";
      return `<div class="policy"><b>${esc(p.template)}</b>
        <small>${esc(POLICY_INFO[p.template] || "")}</small>
        <small>${esc(params)}${esc(reg)}${p.budget_id ? ` · budget=${esc(p.budget_id)}` : ""}</small>
        <span class="badge ${p.enabled ? "completed" : "halted"}"><i></i>${p.enabled ? "enabled" : "disabled"}</span></div>`;
    }).join("");

    const personas = Object.keys(s.cost_by_persona);
    chart("chPersona", {
      type: "bar",
      data: { labels: personas, datasets: [{ label: "USD", data: personas.map((p) => s.cost_by_persona[p].cost_micros / 1e6), backgroundColor: personas.map((_, i) => hexA(PALETTE[i % 8], 0.8)), borderRadius: 8 }] },
      options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${usd(c.raw)} · ${s.cost_by_persona[c.label].runs} runs` } } } },
    });
    const evk = Object.keys(s.events_by_policy);
    chart("chEvents", {
      type: "doughnut",
      data: { labels: evk.length ? evk : ["no actions yet"], datasets: [{ data: evk.length ? evk.map((e) => s.events_by_policy[e]) : [1], backgroundColor: evk.length ? PALETTE : [hexA("#94a3b8", 0.2)], borderWidth: 0 }] },
      options: { cutout: "65%", plugins: { legend: { position: "right" } } },
    });

    $("#runsTable tbody").innerHTML = s.runs.length ? s.runs.map((r) => `
      <tr data-run="${esc(r.run_id)}">
        <td><code>${esc(r.run_id.slice(0, 14))}</code></td>
        <td>${esc((r.dims || {}).persona || "—")}</td>
        <td class="q"><span>${esc((r.task || "").slice(0, 110))}</span></td>
        <td><span class="badge ${esc(r.status)}"><i></i>${esc(r.status)}</span></td>
        <td>${esc(usd(r.cost_micros / 1e6))}</td>
        <td>${esc(r.steps)}</td>
        <td>${qualityCell(r)}</td>
        <td>${esc((r.governance_events || []).length)}${r.halt_reason ? ` · <small>${esc(r.halt_reason.slice(0, 60))}</small>` : ""}</td>
        <td><small>${esc(when(r.started_at))}</small></td>
      </tr>`).join("") : `<tr><td colspan="9"><span>No governed runs yet - ask the AI Analyst a question.</span></td></tr>`;
    revealCards($("#page-governance"));
  }

  $("#budgetRange").addEventListener("input", (e) => { $("#budgetInput").value = e.target.value; });
  $("#budgetApply").addEventListener("click", async () => {
    const v = parseFloat($("#budgetInput").value);
    if (!(v > 0)) return;
    await api("/api/tokenops/budget", { method: "PUT", body: JSON.stringify({ limit_usd: v }) });
    state.budget = v;
    gsap.fromTo("#budgetNow", { scale: 1.15 }, { scale: 1, duration: 0.5, ease: "back.out(3)" });
    await loadGovernance();
  });
  $("#budgetReset").addEventListener("click", async () => {
    await api("/api/tokenops/reseed", { method: "POST" });
    await loadGovernance();
  });
  $("#refreshGov").addEventListener("click", () => loadGovernance());

  // Two-step confirm: first click arms the button, second click (within 4s) clears.
  let clearTimer = null;
  $("#clearHistory").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const label = btn.querySelector("span");
    if (!btn.classList.contains("armed")) {
      btn.classList.add("armed");
      label.textContent = "Click again to delete all runs";
      clearTimer = setTimeout(() => { btn.classList.remove("armed"); label.textContent = "Clear run history"; }, 4000);
      return;
    }
    clearTimeout(clearTimer);
    btn.disabled = true;
    label.textContent = "Clearing...";
    try {
      await api("/api/tokenops/clear-history", { method: "POST" });
      label.textContent = "Cleared";
      await loadGovernance();
    } catch (err) {
      label.textContent = String(err.message || err).slice(0, 60);
    } finally {
      setTimeout(() => { btn.disabled = false; btn.classList.remove("armed"); label.textContent = "Clear run history"; }, 2500);
    }
  });

  // --------------------------------------------------------- run trace --
  const TR = { runId: null, data: null, open: new Set(), rootOpen: true, tabs: {}, timer: null, polls: 0, copy: [] };
  const CHK = { pass: "good", warn: "warn", fail: "bad", info: "info" };
  const KTAG = { llm: "LLM", tool: "MCP", governance: "GOV", eval: "EVAL" };
  const cap = (s) => String(s).charAt(0).toUpperCase() + String(s).slice(1).replace(/_/g, " ");
  const dur = (v) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`);
  const scoreCls = (v) => (v >= 4 ? "good" : v >= 3 ? "warn" : "bad");

  function qualityCell(r) {
    const q = typeof r.quality === "number" ? `<span class="q-pill ${scoreCls(r.quality)}" title="LLM-as-judge overall score (1-5)">${r.quality.toFixed(1)}</span>` : "<small>—</small>";
    return q + (r.safety_flags ? ` <span class="q-pill bad" title="Safety flags raised">⚠ ${esc(r.safety_flags)}</span>` : "");
  }

  // Syntax-highlights JSON after escaping it (only escaped text ever reaches innerHTML).
  function jsonHtml(v) {
    const s = esc(JSON.stringify(v, null, 2) ?? "null");
    return s.replace(/(&quot;(?:\\.|[^&\\]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
      (m, str, colon, lit, n) => (str ? (colon ? `<span class="j-k">${str}</span>${colon}` : `<span class="j-s">${str}</span>`)
        : lit ? `<span class="j-l">${lit}</span>` : `<span class="j-n">${n}</span>`));
  }
  function tryJson(s) {
    if (typeof s !== "string") return s;
    if (!/^\s*[[{]/.test(s)) return null;
    try { return JSON.parse(s); } catch (_) { return null; }
  }
  const copyBtn = (value, title = "Copy") =>
    `<button class="icon-btn tiny" type="button" data-copy="${TR.copy.push(typeof value === "string" ? value : JSON.stringify(value, null, 2)) - 1}" title="${esc(title)}">⧉</button>`;
  function block(label, value) {
    const parsed = tryJson(value);
    const body = parsed && typeof parsed === "object" ? `<pre class="json">${jsonHtml(parsed)}</pre>` : `<pre class="txt">${esc(value ?? "")}</pre>`;
    return `<div class="payload"><div class="payload-head"><span>${esc(label)}</span>${copyBtn(value ?? "")}</div>${body}</div>`;
  }
  function msgHtml(m) {
    const parsed = m.role === "tool" ? tryJson(m.content) : null;
    const body = parsed && typeof parsed === "object" ? `<pre class="json">${jsonHtml(parsed)}</pre>` : m.content ? `<pre class="txt">${esc(m.content)}</pre>` : "";
    const calls = m.tool_calls && m.tool_calls.length ? `<pre class="json">${jsonHtml(m.tool_calls)}</pre>` : "";
    return `<div class="m role-${esc(m.role)}"><div class="m-head"><b>${esc(m.role)}</b>${m.name ? `<small>${esc(m.name)}</small>` : ""}${m.tool_call_id ? `<small>${esc(m.tool_call_id)}</small>` : ""}${m.truncated ? `<small class="warn-t">truncated</small>` : ""}</div>${body}${calls}</div>`;
  }
  function messagesHtml(inp) {
    const msgs = inp.messages || [];
    const k = Math.min(inp.carried || 0, msgs.length);
    const old = msgs.slice(0, k);
    return `<div class="msgs-list">${old.length ? `<details class="carried"><summary><span>${old.length} earlier message${old.length === 1 ? "" : "s"} carried in context</span> <small>(system prompt, question, prior turns)</small></summary>${old.map(msgHtml).join("")}</details>` : ""}${msgs.slice(k).map(msgHtml).join("")}</div>`;
  }

  function ioHtml(s) {
    const inp = s.input || {}, out = s.output || {};
    let input, output;
    if (s.kind === "llm" || s.kind === "eval") {
      input = (inp.params ? block("Request parameters", { ...inp.params, tools_bound: inp.tools_bound }) : "")
        + `<div class="payload"><div class="payload-head"><span>Messages sent · ${(inp.messages || []).length}</span>${copyBtn(inp.messages || [])}</div><div class="payload-body">${messagesHtml(inp)}</div></div>`;
      if (!out.content && !(out.tool_calls || []).length) {
        output = `<div class="err-box"><span>${esc(s.error || "No response - the call did not reach the model.")}</span></div>`;
      } else {
        output = (out.content ? (s.final
          ? `<div class="payload"><div class="payload-head"><span>Final answer (rendered markdown)</span>${copyBtn(out.content)}</div><div class="bubble md-out">${renderMd(out.content)}</div></div>`
          : block(s.kind === "eval" ? "Judge verdict" : "Assistant content", out.content)) : "")
          + ((out.tool_calls || []).length ? block(`Tool calls requested · ${out.tool_calls.length}`, out.tool_calls) : "")
          + (s.kind === "llm" ? block("Response metadata", { finish_reason: out.finish_reason, model_version: out.model_version, system_fingerprint: out.system_fingerprint, usage: s.tokens }) : "");
      }
    } else if (s.kind === "tool") {
      input = block(`Arguments · ${s.server || "mcp"} › ${s.name}`, inp.args || {});
      const flags = [`${num(out.chars || 0)} chars`, out.stored_truncated ? "stored truncated" : "", out.sent_to_model_truncated ? "truncated before reaching the model" : "", s.replaced_by_governance ? "replaced by governance" : ""].filter(Boolean).join(" · ");
      output = (s.error ? `<div class="err-box"><span>${esc(s.error)}</span></div>` : "") + block(`Result · ${flags}`, out.content ?? "");
    } else {
      input = `<p class="hint"><small>Emitted by the TokenOps SDK while governing the parent call.</small></p>`;
      output = block("TokenOps action", out);
    }
    return `<div class="io-grid"><div><h5><span>Request / input</span></h5>${input}</div><div><h5><span>Response / output</span></h5>${output}</div></div>`;
  }

  function checksHtml(s, t) {
    const checks = [...(s.checks || [])];
    const ev = t.evaluation || {};
    if (s.final && ev.status === "done") {
      Object.entries(ev.quality || {}).forEach(([k, q]) => checks.push({ name: `${cap(k)} · judge`, value: q.score ? `${q.score}/5 - ${q.reason || ""}` : "n/a", status: q.score ? { good: "pass", warn: "warn", bad: "fail" }[scoreCls(q.score)] : "info" }));
    } else if (s.final && ev.status === "pending") {
      checks.push({ name: "Quality evaluation", value: "LLM-as-judge is running...", status: "info" });
    }
    if (!checks.length) return `<p class="hint"><small>No checks recorded for this span.</small></p>`;
    return `<div class="chk-list">${checks.map((c) => `<div class="chk-item"><i class="dot ${CHK[c.status] || "info"}"></i><div><b>${esc(c.name)}</b><span>${esc(c.value)}</span></div></div>`).join("")}</div>`;
  }

  function metaHtml(s) {
    const tk = s.tokens || {};
    const rows = [
      ["Span id", s.id], ["Parent", s.parent || "run"], ["Kind", s.kind], ["Status", s.status],
      [s.kind === "tool" ? "MCP server" : "Model", s.server || s.model || "—"],
      ["Start", `+${dur(s.start_ms)}`], ["End", `+${dur(s.end_ms)}`], ["Latency", dur(s.latency_ms)],
    ];
    if (s.llm_latency_ms != null) rows.push(["Model latency", dur(s.llm_latency_ms)], ["SDK overhead", dur(s.overhead_ms)]);
    if (s.tokens) rows.push(["Input tokens", num(tk.input)], ["Output tokens", num(tk.output)], ["Total tokens", num(tk.total)], ["Cached tokens", num(tk.cached || 0)], ["Reasoning tokens", num(tk.reasoning || 0)]);
    if (s.cost_usd != null) rows.push(["Cost", usd(s.cost_usd, 6)]);
    if (s.billing) rows.push(["Billing", s.billing]);
    return `<div class="meta-grid">${rows.map(([k, v]) => `<div><small>${esc(k)}</small><span title="${esc(v)}">${esc(v)}</span></div>`).join("")}</div>`
      + (s.safety ? block("Azure content filter / Prompt Shields (raw, normalized)", s.safety) : "")
      + (s.injection && s.injection.length ? block("Prompt-injection heuristic matches", s.injection) : "");
  }

  function detailHtml(s, t) {
    const tab = TR.tabs[s.id] || "io";
    const tabs = [["io", "Request / Response"], ["eval", "Evaluation"], ["meta", "Metadata"]];
    const panes = { io: ioHtml(s), eval: checksHtml(s, t), meta: metaHtml(s) };
    return `<div class="tr-tabs">${tabs.map(([k, l]) => `<button class="tr-tab${k === tab ? " active" : ""}" type="button" data-tab="${k}" data-sid="${esc(s.id)}">${esc(l)}</button>`).join("")}</div>`
      + tabs.map(([k]) => `<div class="tr-pane${k === tab ? " active" : ""}" data-pane="${k}">${panes[k]}</div>`).join("");
  }

  function chkSummary(checks) {
    if (!checks || !checks.length) return "<small>—</small>";
    const fail = checks.filter((c) => c.status === "fail").length, warn = checks.filter((c) => c.status === "warn").length;
    if (fail) return `<span class="chk bad">✕ ${fail}</span>`;
    if (warn) return `<span class="chk warn">! ${warn}</span>`;
    return `<span class="chk good">✓ ${checks.length}</span>`;
  }

  function treeHtml(t) {
    const spans = t.spans || [], x = t.totals || {}, ev = t.evaluation || {};
    const total = Math.max(t.duration_ms || 0, ...spans.map((s) => s.end_ms || 0), 1);
    const depth = {};
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => `<small>${f ? dur(total * f) : "0"}</small>`).join("");
    const head = `<div class="tr-cols"><small>Span</small><div class="ticks">${ticks}</div><small class="num-c">Latency</small><small class="num-c">Tokens in / out</small><small class="num-c">Cost</small><small class="num-c">Checks</small></div>`;
    const quality = typeof ev.overall === "number" ? `<span class="q-pill ${scoreCls(ev.overall)}">${ev.overall.toFixed(1)}/5</span>` : ev.status === "pending" ? `<small class="pending">judging…</small>` : "<small>—</small>";
    const root = `<div class="tr-row root${TR.rootOpen ? " open" : ""}" data-root="1">
      <div class="tr-name"><span class="chev">›</span><span class="ktag tag-run">RUN</span><code class="nm">${esc(t.run_id)}</code><span class="badge ${esc(t.status)}"><i></i>${esc(t.status)}</span></div>
      <div class="wf"><i class="bar-run" style="left:0;width:${((t.duration_ms || total) / total) * 100}%"></i></div>
      <div class="num-c"><span>${dur(t.duration_ms)}</span></div>
      <div class="num-c"><span>${num(x.input_tokens || 0)} / ${num(x.output_tokens || 0)}</span></div>
      <div class="num-c"><span>${usd(x.cost_usd || 0, 5)}</span></div>
      <div class="num-c">${quality}</div></div>`;
    if (!TR.rootOpen) return head + root;
    const rows = spans.map((s) => {
      depth[s.id] = s.parent && depth[s.parent] ? depth[s.parent] + 1 : 1;
      const left = ((s.start_ms || 0) / total) * 100, width = Math.max((((s.end_ms ?? s.start_ms) - s.start_ms) / total) * 100, 0.4);
      const bad = ["error", "blocked", "halted", "halt"].includes(s.status);
      const bar = s.kind === "governance" ? `<b class="mark${bad ? " st-bad" : ""}" style="left:${left}%" title="${esc(s.name)}"></b>`
        : `<i class="bar-${esc(s.kind)}${bad ? " st-bad" : ""}" style="left:${left}%;width:${width}%" title="${esc(dur(s.latency_ms))}"></i>`;
      const open = TR.open.has(s.id);
      return `<div class="tr-row${open ? " open" : ""}${bad ? " st-bad" : ""}" data-span="${esc(s.id)}">
        <div class="tr-name" style="padding-left:${depth[s.id] * 16}px"><span class="chev">›</span><span class="ktag tag-${esc(s.kind)}">${esc(KTAG[s.kind] || s.kind)}</span><span class="nm" title="${esc(s.name)}">${esc(s.name)}</span>${s.server ? `<small>${esc(s.server)}</small>` : ""}${s.final ? `<small class="good-t">final</small>` : ""}</div>
        <div class="wf">${bar}</div>
        <div class="num-c"><span>${s.kind === "governance" ? "—" : dur(s.latency_ms)}</span></div>
        <div class="num-c"><span>${s.tokens ? `${num(s.tokens.input)} / ${num(s.tokens.output)}` : "—"}</span></div>
        <div class="num-c"><span>${s.cost_usd != null ? usd(s.cost_usd, 5) : "—"}</span></div>
        <div class="num-c">${chkSummary(s.checks)}</div></div>
        ${open ? `<div class="tr-detail" data-detail="${esc(s.id)}">${detailHtml(s, t)}</div>` : ""}`;
    }).join("");
    return head + root + rows;
  }

  function qualityPanel(ev) {
    const head = `<h4><span>Quality</span> <small>LLM-as-judge${ev.judge_model ? ` · ${esc(ev.judge_model)}` : ""}</small></h4>`;
    if (ev.status === "pending") return head + `<p class="hint pending"><small>Scoring the answer against the MCP evidence...</small></p>`;
    if (ev.status !== "done") return head + `<p class="hint"><small>${esc(ev.reason || "Not evaluated.")}</small></p>`;
    const rows = Object.entries(ev.quality || {}).map(([k, q]) => `
      <div class="score-row" title="${esc(q.reason || "")}"><span>${esc(cap(k))}</span><div class="score-bar"><i class="${q.score ? scoreCls(q.score) : ""}" style="width:${(q.score || 0) * 20}%"></i></div><b>${q.score ? `${q.score}/5` : "n/a"}</b></div>`).join("");
    const claims = (ev.unsupported_claims || []).length
      ? `<details class="claims"><summary><span class="warn-t">${ev.unsupported_claims.length} unsupported claim(s)</span></summary><ul>${ev.unsupported_claims.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></details>` : "";
    const foot = `<p class="hint"><small>Judge cost ${usd(ev.cost_usd || 0, 5)} · ${dur(ev.latency_ms)} · not charged to the run budget</small></p>`;
    return head + rows + (ev.summary ? `<p class="ev-sum"><span>${esc(ev.summary)}</span></p>` : "") + claims + foot;
  }

  function safetyPanel(ev) {
    const s = ev.safety || {}, hc = s.harmful_content || {}, jb = s.jailbreak || {}, pi = s.prompt_injection || {}, pii = s.pii_leak;
    const sev = (v) => (v === "safe" ? "good" : v === "low" ? "warn" : v ? "bad" : "info");
    const rows = [
      ["Harmful content", sev(hc.severity), hc.severity || "n/a",
        [hc.source, Object.entries(hc.categories || {}).map(([k, v]) => `${k}: ${v}`).join(", "), hc.judge ? `judge: ${hc.judge.severity}` : ""].filter(Boolean).join(" · ")],
      ["Jailbreak", jb.detected ? "bad" : "good", jb.detected ? "detected" : "not detected",
        [`Prompt Shields: ${jb.prompt_shields == null ? "n/a" : jb.prompt_shields ? "detected" : "clear"}`, `heuristic: ${(jb.heuristic || []).length ? jb.heuristic.join("; ") : "clear"}`, jb.judge ? `judge: ${jb.judge.detected ? "detected" : "clear"}` : ""].filter(Boolean).join(" · ")],
      ["Prompt injection", pi.detected ? "bad" : "good", pi.detected ? `${(pi.flagged || []).length} tool output(s) flagged` : "none in tool outputs",
        (pi.flagged || []).map((f) => `${f.tool}: ${f.matches.join("; ")}`).join(" · ") || `indirect attack (Prompt Shields): ${pi.indirect_attack == null ? "n/a" : pi.indirect_attack ? "detected" : "clear"}`],
      ["PII exposure", pii ? (pii.detected ? "warn" : "good") : "info", pii ? (pii.detected ? "detected" : "none") : ev.status === "pending" ? "evaluating..." : "n/a", (pii && pii.reason) || "judge-assessed"],
    ];
    return `<h4><span>Safety</span> <small>content filter · Prompt Shields · heuristics · judge</small></h4>`
      + rows.map(([l, c, v, d]) => `<div class="safety-row" title="${esc(d)}"><i class="dot ${c}"></i><span>${esc(l)}</span><div><b class="${c === "bad" ? "bad-t" : c === "warn" ? "warn-t" : ""}">${esc(v)}</b><small>${esc(d)}</small></div></div>`).join("");
  }

  function breakdownPanel(t) {
    const x = t.totals || {};
    const other = Math.max((t.duration_ms || 0) - (x.llm_latency_ms || 0) - (x.tool_latency_ms || 0), 0);
    const time = [["llm", x.llm_latency_ms || 0, "LLM"], ["tool", x.tool_latency_ms || 0, "MCP tools"], ["other", other, "orchestration"]];
    const fresh = Math.max((x.input_tokens || 0) - (x.cached_tokens || 0), 0);
    const toks = [["llm", fresh, "input"], ["other", x.cached_tokens || 0, "cached"], ["tool", x.output_tokens || 0, "output"]];
    const bar = (parts, fmt) => `<div class="tr-split">${parts.map(([k, v]) => `<i class="bar-${k}" style="flex:${v}"></i>`).join("")}</div>
      <div class="tr-legend">${parts.map(([k, v, l]) => `<small><i class="sw bar-${k}"></i>${esc(l)} ${fmt(v)}</small>`).join("")}</div>`;
    return `<h4><span>Breakdown</span> <small>where time and tokens went</small></h4>
      <p class="split-l"><small>Wall time · ${dur(t.duration_ms)}</small></p>${bar(time, dur)}
      <p class="split-l"><small>Tokens · ${num((x.input_tokens || 0) + (x.output_tokens || 0))}</small></p>${bar(toks, num)}
      <p class="split-l"><small>Model ${esc(t.model_initial || "—")}${t.model_final && t.model_final !== t.model_initial ? ` → ${esc(t.model_final)} (cost guard)` : ""} · tools available ${num((t.tools_available || []).length)}</small></p>`;
  }

  function renderTrace(r) {
    const t = r.trace, x = t.totals || {}, ev = t.evaluation || {};
    TR.copy = [];
    const pct = t.budget_usd ? `${(((x.cost_usd || 0) / t.budget_usd) * 100).toFixed(0)}% of ${usd(t.budget_usd, 3)} budget` : "governed spend";
    const kpis = [
      ["Duration", dur(t.duration_ms), `LLM ${dur(x.llm_latency_ms)} · tools ${dur(x.tool_latency_ms)}`],
      ["Run cost", usd(x.cost_usd || 0, 5), pct],
      ["LLM calls", num(x.llm_calls || 0), x.llm_blocked ? `${x.llm_blocked} blocked` : "model requests"],
      ["MCP tool calls", num(x.tool_calls || 0), x.tool_errors ? `${x.tool_errors} error(s)` : "no errors"],
      ["Input tokens", num(x.input_tokens || 0), x.cached_tokens ? `${num(x.cached_tokens)} cached` : "prompt"],
      ["Output tokens", num(x.output_tokens || 0), x.reasoning_tokens ? `${num(x.reasoning_tokens)} reasoning` : "completion"],
      ["Governance", num(x.governance_actions || 0), "TokenOps actions"],
      ["Quality", typeof ev.overall === "number" ? `${ev.overall.toFixed(1)} / 5` : ev.status === "pending" ? "…" : "—", ev.status === "done" ? "LLM-as-judge" : ev.status || ""],
    ];
    const notes = (t.error ? `<div class="err-box"><span>${esc(t.error)}</span></div>` : "")
      + (t.halt ? `<div class="err-box"><span>Halted by TokenOps${t.halt.stage ? ` (${esc(t.halt.stage)})` : ""}: ${esc(t.halt.reason || "")}</span></div>` : "");
    $("#modalBody").innerHTML = `
      <div class="tr-head">
        <div class="tr-id"><small class="tr-eyebrow">Run trace</small>
          <div class="tr-title"><code>${esc(t.run_id)}</code>${copyBtn(t.run_id, "Copy run id")}</div>
          <p class="tr-q"><span>${esc(t.question)}</span></p></div>
        <div class="tr-badges">
          <span class="badge ${esc(t.status)}"><i></i>${esc(t.status)}</span>
          <span class="badge"><i></i>${esc(t.mode || "—")}</span>
          <span class="badge"><i></i>${esc(t.persona || "—")} · ${esc(t.department || "—")}</span>
          <span class="badge"><i></i>${esc(t.model_initial || "—")}</span>
          <span class="badge"><i></i>${esc(when(t.started_at))}</span></div>
      </div>${notes}
      <div class="tr-kpis">${kpis.map(([l, v, s]) => `<div class="tr-kpi"><small>${esc(l)}</small><b>${esc(v)}</b><em>${esc(s)}</em></div>`).join("")}</div>
      <div class="tr-evals"><div class="tr-panel">${qualityPanel(ev)}</div><div class="tr-panel">${safetyPanel(ev)}</div><div class="tr-panel">${breakdownPanel(t)}</div></div>
      <div class="tr-section-head"><h4><span>Trace</span></h4><small>${(t.spans || []).length} spans · click a span for its request / response, evaluation and metadata</small>
        <div class="head-actions"><button class="btn small" id="trExpand" type="button"><span>Expand all</span></button><button class="btn small" id="trCollapse" type="button"><span>Collapse all</span></button></div></div>
      <div class="tr-tree" id="trTree">${treeHtml(t)}</div>`;
  }
  const renderTree = () => { $("#trTree").innerHTML = treeHtml(TR.data.trace); };

  function renderLegacy(r) {
    const evs = r.governance_events || [];
    $("#modalBody").innerHTML = `
      <div class="tr-head"><div class="tr-id"><small class="tr-eyebrow">Run summary</small><h3>Run ${esc(r.run_id)}</h3></div></div>
      <div class="err-box info"><span>Trace not available - this run predates tracing (or history was cleared). Run a new question to capture a full trace.</span></div>
      <p><small>${esc(r.task || "")}</small></p>
      <div class="kv">
        <div class="tile"><small>Status</small><span class="${r.status === "completed" ? "good-t" : "bad-t"}">${esc(r.status)}</span></div>
        <div class="tile"><small>Cost</small><span>${esc(usd(r.cost_micros / 1e6))}</span></div>
        <div class="tile"><small>Steps</small><span>${esc(r.steps)}</span></div>
        <div class="tile"><small>Persona</small><span>${esc((r.dims || {}).persona || "—")}</span></div>
        <div class="tile"><small>Detector</small><span>${esc(r.detector || "—")}</span></div>
        <div class="tile"><small>Duration</small><span>${r.ended_at && r.started_at ? esc((r.ended_at - r.started_at).toFixed(1)) + "s" : "—"}</span></div>
      </div>
      ${r.halt_reason ? `<p><strong class="bad-t">Halt reason:</strong> <span>${esc(r.halt_reason)}</span></p>` : ""}
      <h4 style="margin:14px 0 8px">Governance actions (${evs.length})</h4>
      <ul class="ev-list">${evs.length ? evs.map((ev) => `<li><b>${esc(String(ev.kind).toUpperCase())}</b> <small>· ${esc(ev.policy)}</small><br><span>${esc(ev.reason)}</span></li>`).join("") : "<li><span>No interventions - run stayed within policy.</span></li>"}</ul>`;
  }

  async function refreshRun(first) {
    const id = TR.runId;
    let r;
    try {
      r = await api(`/api/tokenops/runs/${encodeURIComponent(id)}`);
    } catch (err) {
      if (first && TR.runId === id) $("#modalBody").innerHTML = `<div class="err-box"><span>${esc(err.message || err)}</span></div>`;
      return;
    }
    if (TR.runId !== id || $("#modal").hidden) return;
    const wasPending = TR.data && TR.data.trace && TR.data.trace.evaluation && TR.data.trace.evaluation.status === "pending";
    TR.data = r;
    const body = $("#modalBody"), top = body.scrollTop;
    if (r.trace) renderTrace(r); else renderLegacy(r);
    body.scrollTop = top;
    if (first) gsap.from(body.querySelectorAll(".tr-kpi, .tr-panel, .tr-row"), { opacity: 0, y: 8, stagger: 0.012, duration: 0.3, ease: "power2.out", clearProps: "all" });
    const status = r.trace && r.trace.evaluation ? r.trace.evaluation.status : null;
    if (status === "pending" && TR.polls++ < 40) TR.timer = setTimeout(() => refreshRun(false), 2500);
    else if (wasPending && state.page === "governance") loadGovernance();
  }

  async function openRun(runId) {
    clearTimeout(TR.timer);
    Object.assign(TR, { runId, data: null, open: new Set(), rootOpen: true, tabs: {}, polls: 0 });
    $("#modalBody").innerHTML = `<p class="hint pending"><small>Loading trace...</small></p>`;
    $("#modal").hidden = false;
    $("#modalBody").scrollTop = 0;
    gsap.fromTo(".modal-card", { opacity: 0, y: 30, scale: 0.97 }, { opacity: 1, y: 0, scale: 1, duration: 0.35, ease: "power3.out" });
    await refreshRun(true);
  }

  $("#runsTable").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-run]");
    if (tr) openRun(tr.dataset.run);
  });

  $("#modalBody").addEventListener("click", (e) => {
    const copy = e.target.closest("[data-copy]");
    if (copy) {
      const text = TR.copy[+copy.dataset.copy] ?? "";
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => {
        copy.textContent = "✓";
        setTimeout(() => { copy.textContent = "⧉"; }, 1200);
      }).catch(() => {});
      return;
    }
    const tab = e.target.closest(".tr-tab");
    if (tab) {
      TR.tabs[tab.dataset.sid] = tab.dataset.tab;
      const det = tab.closest(".tr-detail");
      $$(".tr-tab", det).forEach((b) => b.classList.toggle("active", b === tab));
      $$(".tr-pane", det).forEach((p) => p.classList.toggle("active", p.dataset.pane === tab.dataset.tab));
      return;
    }
    if (!TR.data || !TR.data.trace) return;
    if (e.target.closest("#trExpand")) { TR.rootOpen = true; TR.open = new Set(TR.data.trace.spans.map((s) => s.id)); renderTree(); return; }
    if (e.target.closest("#trCollapse")) { TR.open.clear(); renderTree(); return; }
    const row = e.target.closest(".tr-row");
    if (!row) return;
    if (row.dataset.root) {
      TR.rootOpen = !TR.rootOpen;
      renderTree();
      if (TR.rootOpen) gsap.from($$("#trTree .tr-row:not(.root)"), { opacity: 0, x: -8, stagger: 0.015, duration: 0.25, clearProps: "all" });
      return;
    }
    const sid = row.dataset.span;
    if (TR.open.has(sid)) TR.open.delete(sid); else TR.open.add(sid);
    renderTree();
    const det = $(`#trTree [data-detail="${CSS.escape(sid)}"]`);
    if (det) gsap.from(det, { opacity: 0, y: -6, duration: 0.25, ease: "power2.out", clearProps: "all" });
  });

  const closeModal = () => {
    clearTimeout(TR.timer);
    TR.runId = null;
    $("#modal").hidden = true;
  };
  $("#modalClose").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  // ---------------------------------------------------- evaluation lab --
  // Compares the custom LLM-as-judge (1-5, shown as score / 5) with LangSmith online evaluation (0-1) on the
  // same production runs. Pass threshold is 0.8 on the shared scale (judge 4/5 <-> LangSmith 0.80).
  const EV = { data: null, rows: [], feedback: null, sel: null, filter: "all", timer: null, polls: 0, sig: "" };
  const EV_PAIRS = [
    ["Groundedness", "groundedness", "groundedness"],
    ["Relevance", "relevance", "answer_relevance"],
    ["Figures / accuracy", "accuracy", "numeric_grounding"],
    ["Completeness · helpfulness", "completeness", "helpfulness"],
    ["Coherence", "coherence", null],
    ["MCP tool health", null, "tool_health"],
    ["Budget adherence", null, "budget_adherence"],
  ];
  const PASS = 0.8;
  const cls01 = (v) => (v >= PASS ? "good" : v >= 0.5 ? "warn" : "bad");
  const mean = (a) => (a.length ? a.reduce((p, c) => p + c, 0) / a.length : null);
  const total = (a) => a.reduce((p, c) => p + (c || 0), 0);
  const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
  const jScore = (x, k) => { const q = x.j.status === "done" && (x.j.quality || {})[k]; return q && typeof q.score === "number" ? q.score / 5 : null; };
  const lScore = (x, k) => { const m = x.l.status === "done" && x.m[k]; return m && typeof m.score === "number" ? m.score : null; };

  function evRow(r) {
    const j = r.judge || {}, l = r.langsmith || {}, m = l.metrics || {}, s = r.safety || {};
    const jn = j.status === "done" && typeof j.overall === "number" ? j.overall / 5 : null;
    const ln = l.status === "done" && typeof l.overall === "number" ? l.overall : null;
    const hj = ((s.harmful_content || {}).judge || {}).severity;
    const harmJ = !!hj && hj !== "safe", piiJ = !!(s.pii_leak || {}).detected, jbJ = !!((s.jailbreak || {}).judge || {}).detected;
    const toxL = !!(m.toxicity || {}).detected, piiL = !!(m.pii_leakage || {}).detected;
    const both = jn != null && ln != null;
    const agree = both ? (jn >= PASS) === (ln >= PASS) : null;
    const safetyAgree = both ? harmJ === toxL && piiJ === piiL : null;
    return {
      r, j, l, m, jn, ln, both, agree, safetyAgree, harmJ, piiJ, jbJ, toxL, piiL,
      delta: both ? ln - jn : null, disagree: both && (!agree || !safetyAgree),
      jFlags: [harmJ && "harmful", jbJ && "jailbreak", piiJ && "PII"].filter(Boolean),
      lFlags: [toxL && "toxicity", piiL && "PII"].filter(Boolean),
      cost: (j.cost_usd || 0) + (l.cost_usd || 0),
    };
  }

  function evStatus(ev, html) {
    if (ev.status === "done") return html;
    if (ev.status === "pending") return `<small class="pending">evaluating…</small>`;
    return `<small title="${esc(ev.reason || "")}">${esc(ev.status || "—")}</small>`;
  }

  async function loadEvals(first = true) {
    clearTimeout(EV.timer);
    if (first) EV.polls = 0;
    const d = await api("/api/evaluations");
    const rows = (d.runs || []).map(evRow);
    const sig = JSON.stringify(rows.map((x) => [x.r.run_id, x.j.status, x.l.status]));
    EV.data = d;
    EV.rows = rows;
    if (first || sig !== EV.sig) { EV.sig = sig; renderEvals(); }
    if (first) revealCards($("#page-evals"));
    const pending = rows.some((x) => x.j.status === "pending" || x.l.status === "pending");
    if (pending && state.page === "evals" && EV.polls++ < 60) EV.timer = setTimeout(() => loadEvals(false).catch(console.error), 4000);
    else if (pending) state.loaded.evals = false;
  }

  function renderEvals() {
    const d = EV.data, rows = EV.rows;
    const judged = rows.filter((x) => x.jn != null), lsd = rows.filter((x) => x.ln != null), both = rows.filter((x) => x.both);
    const agree = both.length ? both.filter((x) => x.agree).length / both.length : null;
    const safetyAgree = both.length ? both.filter((x) => x.safetyAgree).length / both.length : null;
    const gap = mean(both.map((x) => Math.abs(x.delta)));
    const avgJ = mean(judged.map((x) => x.j.overall)), avgL = mean(lsd.map((x) => x.ln));
    const pctCls = (v) => (v == null ? "" : v >= 0.8 ? "good-t" : v >= 0.5 ? "warn-t" : "bad-t");

    $("#evalBanner").innerHTML = d.langsmith_configured ? "" : `<div class="err-box info"><span>LangSmith online evaluation is not active - set LANGSMITH_API_KEY in .env (and keep LANGSMITH_EVAL_ENABLED=true), then restart. The custom LLM-as-judge keeps running; the LangSmith column shows why each run was skipped.</span></div>`;
    $("#evalSync").disabled = !d.langsmith_configured;
    const proj = rows.map((x) => safeUrl(x.l.project_url)).find(Boolean);
    $("#lsProjectLink").style.display = proj ? "" : "none";
    if (proj) $("#lsProjectLink").href = proj;

    $("#evalKpis").innerHTML = [
      ["Judge-evaluated", `${judged.length} / ${rows.length}`, "", "Runs scored by the custom LLM-as-judge"],
      ["Avg judge quality", avgJ == null ? "—" : `${avgJ.toFixed(2)} / 5`, avgJ == null ? "" : `${cls01(avgJ / 5)}-t`, "Mean of groundedness, relevance, coherence, accuracy, completeness"],
      ["LangSmith-evaluated", `${lsd.length} / ${rows.length}`, "", "Production runs scored by LangSmith online evaluators"],
      ["Avg LangSmith quality", avgL == null ? "—" : avgL.toFixed(2), avgL == null ? "" : `${cls01(avgL)}-t`, "Mean of groundedness, helpfulness, answer relevance, numeric grounding (0-1)"],
      ["Verdict agreement", agree == null ? "—" : `${Math.round(agree * 100)}%`, pctCls(agree), "Share of runs where both evaluators give the same pass / fail verdict"],
      ["Mean score gap", gap == null ? "—" : gap.toFixed(2), gap == null ? "" : gap <= 0.1 ? "good-t" : gap <= 0.2 ? "warn-t" : "bad-t", "Mean |LangSmith - judge/5| on runs evaluated by both"],
    ].map(([l, v, c, t]) => `<div class="kpi glass" title="${esc(t)}"><span class="kpi-label">${esc(l)}</span><span class="kpi-value ${c}">${esc(v)}</span></div>`).join("");

    const pt = (x) => ({ x: +x.jn.toFixed(3), y: +x.ln.toFixed(3), id: x.r.run_id, q: (x.r.question || "").slice(0, 70) });
    chart("chEvalScatter", {
      type: "scatter",
      data: { datasets: [
        { label: "Agree", data: both.filter((x) => !x.disagree).map(pt), backgroundColor: hexA(PALETTE[3], 0.85), pointRadius: 6, pointHoverRadius: 8, clip: false },
        { label: "Disagree", data: both.filter((x) => x.disagree).map(pt), backgroundColor: hexA(PALETTE[7], 0.9), pointRadius: 6, pointHoverRadius: 8, clip: false },
        { label: "Perfect agreement", type: "line", data: [{ x: 0, y: 0 }, { x: 1, y: 1 }], borderColor: hexA("#94a3b8", 0.6), borderDash: [5, 4], borderWidth: 1, pointRadius: 0, pointHoverRadius: 0 },
      ] },
      options: {
        scales: { x: { min: 0, max: 1, title: { display: true, text: "Custom judge (score ÷ 5)" } }, y: { min: 0, max: 1, title: { display: true, text: "LangSmith overall" } } },
        plugins: { tooltip: { filter: (i) => !!i.raw.id, callbacks: { title: (c) => c[0].raw.id, label: (c) => [`judge ${c.raw.x.toFixed(2)} · LangSmith ${c.raw.y.toFixed(2)}`, c.raw.q] } } },
        onClick: (_, els, ch) => { const p = els[0] && ch.data.datasets[els[0].datasetIndex].data[els[0].index]; if (p && p.id) selectEval(p.id); },
      },
    });

    const avgOf = (fn) => { const v = rows.map(fn).filter((n) => typeof n === "number"); return v.length ? +mean(v).toFixed(3) : null; };
    chart("chEvalMetrics", {
      type: "bar",
      data: { labels: EV_PAIRS.map((p) => p[0]), datasets: [
        { label: "Custom LLM-as-judge", data: EV_PAIRS.map(([, jk]) => (jk ? avgOf((x) => jScore(x, jk)) : null)), backgroundColor: hexA(PALETTE[1], 0.8), borderRadius: 5 },
        { label: "LangSmith online", data: EV_PAIRS.map(([, , lk]) => (lk ? avgOf((x) => lScore(x, lk)) : null)), backgroundColor: hexA(PALETTE[2], 0.8), borderRadius: 5 },
      ] },
      options: { indexAxis: "y", scales: { x: { min: 0, max: 1 } },
        plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.raw == null ? "not measured" : c.raw.toFixed(2)}` } } } },
    });

    $("#evalTiles").innerHTML = [
      ["Judge eval spend", usd(total(rows.map((x) => x.j.cost_usd)), 5), ""],
      ["LangSmith eval spend", usd(total(rows.map((x) => x.l.cost_usd)), 5), ""],
      ["Avg judge latency", dur(mean(judged.map((x) => x.j.latency_ms || 0))), ""],
      ["Avg LangSmith latency", dur(mean(lsd.map((x) => x.l.latency_ms || 0))), ""],
      ["Harmful · judge / toxicity · LS", `${judged.filter((x) => x.harmJ).length} / ${lsd.filter((x) => x.toxL).length}`, judged.some((x) => x.harmJ) || lsd.some((x) => x.toxL) ? "bad-t" : "good-t"],
      ["PII · judge / LangSmith", `${judged.filter((x) => x.piiJ).length} / ${lsd.filter((x) => x.piiL).length}`, judged.some((x) => x.piiJ) || lsd.some((x) => x.piiL) ? "warn-t" : "good-t"],
      ["Jailbreak · judge only", num(judged.filter((x) => x.jbJ).length), judged.some((x) => x.jbJ) ? "bad-t" : "good-t"],
      ["Safety agreement", safetyAgree == null ? "—" : `${Math.round(safetyAgree * 100)}%`, pctCls(safetyAgree)],
      ["Feedback on LangSmith runs", num(total(rows.map((x) => x.l.feedback_recorded))), ""],
      ["LangSmith sampling", `${Math.round((d.sampling_rate ?? 1) * 100)}%`, ""],
      ["Judge model", d.judge_model || "—", ""],
      ["LangSmith project", d.project || "—", ""],
    ].map(([l, v, c]) => `<div class="tile" title="${esc(v)}"><small>${esc(l)}</small><span class="${c}">${esc(v)}</span></div>`).join("");

    renderEvalTable();
    renderEvalDetail();
  }

  function renderEvalTable() {
    const rows = EV.rows.filter((x) => EV.filter === "all" || (EV.filter === "both" ? x.both : x.disagree));
    const flags = (a) => (a.length ? `<span class="warn-t">${esc(a.join(", "))}</span>` : `<span class="good-t">clear</span>`);
    $("#evalTable tbody").innerHTML = rows.length ? rows.map((x) => {
      const jv = evStatus(x.j, x.jn != null ? `<span class="q-pill ${cls01(x.jn)}" title="${esc(x.j.summary || "")}">${x.j.overall.toFixed(1)}/5</span>` : "<small>n/a</small>");
      const lv = evStatus(x.l, x.ln != null ? `<span class="q-pill ${cls01(x.ln)}">${x.ln.toFixed(2)}</span>` : "<small>n/a</small>");
      const ad = x.delta == null ? 0 : Math.abs(x.delta);
      const dl = x.delta == null ? "<small>—</small>" : `<span class="delta ${ad > 0.2 ? "bad-t" : ad > 0.1 ? "warn-t" : "good-t"}">${x.delta >= 0 ? "+" : ""}${x.delta.toFixed(2)}</span>`;
      const why = [x.agree === false && "pass / fail verdict differs", x.safetyAgree === false && "safety flags differ"].filter(Boolean).join(" · ");
      const vd = !x.both ? "<small>—</small>" : x.disagree ? `<span class="badge halted" title="${esc(why)}"><i></i>disagree</span>` : `<span class="badge completed"><i></i>agree</span>`;
      const sf = `<small>judge</small> ${x.j.status === "done" ? flags(x.jFlags) : "<small>—</small>"} <small>· LS</small> ${x.l.status === "done" ? flags(x.lFlags) : "<small>—</small>"}`;
      return `<tr data-eval="${esc(x.r.run_id)}" class="${x.r.run_id === EV.sel ? "sel" : ""}">
        <td><code>${esc(String(x.r.run_id).slice(0, 14))}</code></td>
        <td class="q"><span>${esc((x.r.question || "").slice(0, 110))}</span></td>
        <td>${jv}</td><td>${lv}</td><td>${dl}</td><td>${vd}</td><td>${sf}</td>
        <td>${esc(usd(x.cost, 5))}</td><td><small>${esc(when(x.r.started_at))}</small></td></tr>`;
    }).join("") : `<tr><td colspan="9"><span>${EV.rows.length ? "No runs match this filter." : "No traced runs yet - ask the AI Analyst a question; both evaluations run in the background after the answer."}</span></td></tr>`;
  }

  function renderEvalDetail() {
    const x = EV.rows.find((y) => y.r.run_id === EV.sel) || EV.rows[0];
    if (!x) {
      $("#evalSelLbl").textContent = "select a run";
      $("#evalDetail").innerHTML = `<p class="hint"><small>No traced runs yet.</small></p>`;
      return;
    }
    EV.sel = x.r.run_id;
    $("#evalSelLbl").textContent = `${x.r.persona || "—"} · ${when(x.r.started_at)}`;
    const jd = x.j.status === "done", ld = x.l.status === "done";
    const na = (ev) => `<small class="evd-na">${ev.status === "pending" ? "evaluating…" : ev.status === "done" ? "n/a" : esc(ev.status || "—")}</small>`;
    const cell = (v, measured, ev, tip) => (!measured ? `<small class="evd-na">not measured</small>`
      : v == null ? na(ev)
      : `<div class="evd-bar" title="${esc(tip || "")}"><div class="score-bar"><i class="${cls01(v)}" style="width:${(v * 100).toFixed(0)}%"></i></div><b>${v.toFixed(2)}</b></div>`);
    const flag = (done, ev, hit) => (!done ? na(ev)
      : `<div class="evd-flag"><i class="dot ${hit ? "bad" : "good"}"></i><span class="${hit ? "bad-t" : ""}">${hit ? "detected" : "clear"}</span></div>`);
    const cols = `<div class="evd-cols"><small>Metric</small><small>Custom judge</small><small>LangSmith</small></div>`;

    const quality = EV_PAIRS.map(([label, jk, lk]) => {
      const jq = (jk && (x.j.quality || {})[jk]) || {}, lm = (lk && x.m[lk]) || {};
      return `<div class="evd-row"><span>${esc(label)}</span>${cell(jk ? jScore(x, jk) : null, !!jk, x.j, jq.reason)}${cell(lk ? lScore(x, lk) : null, !!lk, x.l, lm.comment)}</div>`;
    }).join("");
    const safety = [
      ["Harmful · toxicity", flag(jd, x.j, x.harmJ), flag(ld, x.l, x.toxL)],
      ["PII exposure", flag(jd, x.j, x.piiJ), flag(ld, x.l, x.piiL)],
      ["Jailbreak attempt", flag(jd, x.j, x.jbJ), `<small class="evd-na">not measured</small>`],
    ].map(([l, a, b]) => `<div class="evd-row"><span>${esc(l)}</span>${a}${b}</div>`).join("");

    const ad = x.delta == null ? null : Math.abs(x.delta);
    const verdict = [
      ["Custom judge", x.jn != null ? `${x.j.overall.toFixed(1)} / 5` : x.j.status || "—", x.jn != null ? `${cls01(x.jn)}-t` : ""],
      ["LangSmith", x.ln != null ? x.ln.toFixed(2) : x.l.status || "—", x.ln != null ? `${cls01(x.ln)}-t` : ""],
      ["Score gap Δ", x.delta == null ? "—" : `${x.delta >= 0 ? "+" : ""}${x.delta.toFixed(2)}`, ad == null ? "" : ad > 0.2 ? "bad-t" : ad > 0.1 ? "warn-t" : "good-t"],
      ["Verdict", !x.both ? "—" : x.disagree ? "disagree" : "agree", !x.both ? "" : x.disagree ? "bad-t" : "good-t"],
    ].map(([l, v, c]) => `<div class="tile"><small>${esc(l)}</small><span class="${c}">${esc(v)}</span></div>`).join("");

    const notes = [["Custom judge", x.j], ["LangSmith", x.l]].filter(([, ev]) => ev.status !== "done" && ev.reason)
      .map(([n, ev]) => `<div class="err-box info"><span>${esc(n)} ${esc(ev.status)}: ${esc(ev.reason)}</span></div>`).join("");

    const claims = x.j.unsupported_claims || [];
    const judgeSec = jd ? `<div class="evd-sec"><h5><span>Custom judge verdict</span></h5>
      ${x.j.summary ? `<p class="ev-sum"><span>${esc(x.j.summary)}</span></p>` : ""}
      ${claims.length ? `<details class="claims"><summary><span class="warn-t">${claims.length} unsupported claim(s)</span></summary><ul>${claims.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></details>` : `<p class="hint"><small>No unsupported claims reported.</small></p>`}</div>` : "";

    const lsItems = Object.entries(x.m).map(([k, mt]) => {
      const val = mt.detected != null ? (mt.detected ? "detected" : "clear") : typeof mt.score === "number" ? mt.score.toFixed(2) : "n/a";
      const c = mt.detected != null ? (mt.detected ? "bad" : "good") : typeof mt.score === "number" ? cls01(mt.score) : "";
      return `<li><b>${esc(cap(k))}</b> <small>${esc(mt.type || "")} · ${esc(mt.group || "")}</small> <span class="q-pill ${c}">${esc(val)}</span>${mt.comment ? `<br><small>${esc(mt.comment)}</small>` : ""}</li>`;
    }).join("");
    const lsSec = ld ? `<div class="evd-sec"><h5><span>LangSmith evaluator comments</span></h5><details class="claims"><summary><span>${Object.keys(x.m).length} evaluator result(s)</span></summary><ul class="evd-list">${lsItems}</ul></details></div>` : "";

    const fb = EV.feedback ? EV.feedback[x.r.run_id] || [] : null;
    const fbBody = fb == null
      ? `<p class="hint"><small>Click <b>Sync LangSmith feedback</b> to pull every feedback item on this run from LangSmith, including online-evaluator rules and human annotations.</small></p>`
      : !fb.length ? `<p class="hint"><small>No feedback found on this run in LangSmith.</small></p>`
      : `<ul class="evd-list">${fb.map((f) => {
        const v = typeof f.score === "number" ? f.score.toFixed(2) : f.value != null ? String(f.value) : "—";
        return `<li><b>${esc(f.key)}</b> <span class="q-pill">${esc(v)}</span> <small>${esc(f.ours ? "FinSight evaluator" : `${f.source || "external"} (rule / human)`)}</small>${f.comment ? `<br><small>${esc(f.comment)}</small>` : ""}</li>`;
      }).join("")}</ul>`;

    const runUrl = safeUrl(x.l.run_url);
    const foot = `<p class="hint evd-foot"><small>Judge: ${esc(x.j.judge_model || "—")} · ${usd(x.j.cost_usd || 0, 5)} · ${dur(x.j.latency_ms)}</small><br>
      <small>LangSmith: ${esc(x.l.judge_model || "—")} · ${usd(x.l.cost_usd || 0, 5)} · ${dur(x.l.latency_ms)}${x.l.ingest_ms != null ? ` (incl. ${dur(x.l.ingest_ms)} trace ingest)` : ""}${x.l.judge_calls != null ? ` · ${num(x.l.judge_calls)} judge calls` : ""}${x.l.traced_tool_runs != null ? ` · ${num(x.l.traced_tool_runs)} traced tool runs` : ""}</small></p>`;

    $("#evalDetail").innerHTML = `
      <div class="evd-head"><div><code>${esc(x.r.run_id)}</code> <span class="badge ${esc(x.r.status)}"><i></i>${esc(x.r.status || "—")}</span>
        <p class="evd-q"><span>${esc(x.r.question || "")}</span></p></div>
        <div class="head-actions"><button class="btn small" type="button" data-open-run="${esc(x.r.run_id)}"><span>Trace</span></button>${runUrl ? `<a class="btn small" href="${esc(runUrl)}" target="_blank" rel="noopener"><span>LangSmith ↗</span></a>` : ""}</div></div>
      <div class="evd-verdict">${verdict}</div>${notes}
      <div class="evd-sec"><h5><span>Quality · shared 0-1 scale</span></h5>${cols}${quality}</div>
      <div class="evd-sec"><h5><span>Safety</span></h5>${cols}${safety}</div>
      ${judgeSec}${lsSec}
      <div class="evd-sec"><h5><span>LangSmith feedback (synced)</span></h5>${fbBody}</div>${foot}`;
  }

  function selectEval(id) {
    EV.sel = id;
    $$("#evalTable tbody tr[data-eval]").forEach((t) => t.classList.toggle("sel", t.dataset.eval === id));
    renderEvalDetail();
    $("#evalDetail").scrollTop = 0;
    gsap.from("#evalDetail > *", { opacity: 0, y: 6, stagger: 0.02, duration: 0.25, clearProps: "all" });
  }

  $("#evalTable").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-eval]");
    if (tr) selectEval(tr.dataset.eval);
  });
  $("#evalDetail").addEventListener("click", (e) => {
    const b = e.target.closest("[data-open-run]");
    if (b) openRun(b.dataset.openRun);
  });
  $("#evalFilter").addEventListener("change", (e) => { EV.filter = e.target.value; renderEvalTable(); });
  $("#evalRefresh").addEventListener("click", () => loadEvals().catch(console.error));
  $("#evalSync").addEventListener("click", async (e) => {
    const btn = e.currentTarget, lbl = btn.querySelector("span");
    btn.disabled = true;
    lbl.textContent = "Syncing…";
    try {
      const r = await api("/api/evaluations/langsmith-feedback");
      EV.feedback = r.feedback || {};
      lbl.textContent = `Synced · ${total(Object.values(EV.feedback).map((v) => v.length))} feedback`;
      if (EV.data) renderEvalDetail();
    } catch (err) {
      lbl.textContent = "Sync failed";
      btn.title = String(err.message || err);
    } finally {
      setTimeout(() => { btn.disabled = !(EV.data && EV.data.langsmith_configured); lbl.textContent = "Sync LangSmith feedback"; }, 2500);
    }
  });

  // ------------------------------------------------------- budget proof --
  const BP = { running: false, meta: null, tiers: [], restored: null, error: null };
  const BP_DEPT = { CEO: "Executive Office", CFO: "Finance", CRO: "Risk", COO: "Operations", CMO: "Marketing", "Head of Wealth": "Wealth" };
  const bpUsd = (v) => {
    if (v == null || isNaN(v)) return "—";
    const a = Math.abs(v);
    return `${v < 0 ? "-" : ""}$${a >= 1 ? a.toFixed(2) : a.toFixed(4)}`;
  };
  const bpCap = (b) => `$${b >= 1 ? b.toFixed(2) : +b.toFixed(4)}`;
  const rateLbl = (p) => (p == null ? "$?" : `$${Number.isInteger(p) ? p : p.toFixed(2)}`);
  // typeset maths as plain spans so every glyph keeps the gradient text fill
  const mS = (t, cls) => `<span${cls ? ` class="${cls}"` : ""}>${t}</span>`;
  const mOp = (t) => mS(t, "op");
  const mSub = (t) => mS(t, "sb");
  const mVar = (name, sub) => mS(name, "v") + (sub ? mSub(sub) : "");
  const mFrac = (n, d) => `<span class="frac"><span>${n}</span><span>${d}</span></span>`;
  const mRes = (t, tone) => `<b class="res ${tone}-t">${t}</b>`;
  const mx = (parts, sm) => `<div class="mx${sm ? " sm" : ""}">${parts.join("")}</div>`;
  const PER_M = "10⁶";

  function parseBudgets() {
    return $("#bpBudgets").value.split(/[\s,;]+/).map((s) => parseFloat(s.replace("$", "")))
      .filter((v) => isFinite(v) && v > 0 && v <= 100).slice(0, 6);
  }
  const newTier = (budget) => ({ budget, state: "waiting", checks: [], log: [], cost: 0, llm: 0, tools: 0, tokens: 0 });
  const bpStatus = (msg, tone) => { $("#bpStatus").innerHTML = `<span${tone ? ` class="${tone}-t"` : ""}>${esc(msg)}</span>`; };

  async function loadProof() {
    if (!$("#bpQuestion").value) $("#bpQuestion").value = PROMPTS[0];
    try {
      BP.meta = await api("/api/tokenops/proof-meta");
      if (!BP.running && !BP.tiers.some((t) => t.state !== "waiting")) $("#bpBanner").innerHTML = "";
    } catch (err) {
      $("#bpBanner").innerHTML = `<div class="err-box"><span>Control plane unavailable: ${esc(err.message)}</span></div>`;
    }
    if (!BP.running && !BP.tiers.some((t) => t.state !== "waiting")) BP.tiers = parseBudgets().map(newTier);
    renderRules();
    renderProof();
    revealCards($("#page-proof"));
  }

  function renderRules() {
    const m = BP.meta || {};
    const [ci, co] = (m.rates || {})[m.chat_model] || [];
    const maxOut = m.max_output ?? 4000, thr = m.guard_threshold ?? 0.8;
    const head = (n, name, sub) => `<header><span class="bp-n">${n}</span><b>${name}</b><small>${sub}</small></header>`;
    $("#bpRules").innerHTML = `
      <div class="glass bp-rule reveal">${head(1, "pre_call_worst_case", "prevention · before every LLM call")}
        ${mx([mVar("worst"), mOp("="), mVar("tokens", "in"), mOp("×"), mFrac(rateLbl(ci), PER_M), mOp("+"), mS(num(maxOut)), mSub("out"), mOp("×"), mFrac(rateLbl(co), PER_M)])}
        ${mx([mVar("worst"), mOp("≥"), mVar("budget"), mOp("−"), mVar("spent"), mOp("⇒"), mRes("BLOCK", "bad")], true)}
        <small>output is capped at ${num(maxOut)} tokens on the call, so the worst case is a hard upper bound</small></div>
      <div class="glass bp-rule r-guard reveal">${head(2, "cost_guard", "degradation · after every LLM call")}
        ${mx([mVar("spent"), mOp("≥"), mS(String(thr)), mOp("×"), mVar("budget"), mOp("⇒"), mRes("SWITCH", "warn")])}
        ${mx([mS(esc(m.chat_model || "chat model")), mOp("→"), mS(esc(m.mini_model || "mini model")), mOp("("), mFrac(rateLbl(((m.rates || {})[m.mini_model] || [])[1]), PER_M), mS("out)")], true)}
        <small>keeps the answer coming on a cheaper model instead of failing</small></div>
      <div class="glass bp-rule r-budget reveal">${head(3, "cost_budget", "hard cap · on actual ledger spend")}
        ${mx([mVar("spent"), mOp("≥"), mVar("budget"), mOp("⇒"), mRes("HALT", "bad")])}
        ${mx([mVar("headroom"), mOp("="), mVar("budget"), mOp("−"), mVar("spent")], true)}
        <small>budget <code>run_llm_cap</code> · per run · current ${m.budget_usd != null ? bpCap(m.budget_usd) : "—"}</small></div>`;
  }

  function renderProof() {
    const grid = $("#bpGrid");
    grid.style.setProperty("--n", Math.max(1, BP.tiers.length));
    if (!BP.tiers.length) {
      grid.innerHTML = `<div class="card glass bp-empty"><p>Enter one or more per-run budgets (USD), e.g. <b>0.005, 0.25, 1, 10</b>, then run the proof.</p></div>`;
      return;
    }
    grid.innerHTML = BP.tiers.map((t, i) => `<div class="card glass bp-tier is-${t.state}" data-tier="${i}">${tierHtml(t)}</div>`).join("");
  }
  function renderTier(i) {
    const el = $(`#bpGrid [data-tier="${i}"]`);
    const t = BP.tiers[i];
    if (!el || !t) return;
    const open = el.querySelector("details.bp-ans")?.open;
    el.className = `card glass bp-tier is-${t.state}`;
    el.innerHTML = tierHtml(t);
    if (open) el.querySelector("details.bp-ans")?.setAttribute("open", "");
  }

  function prePolicy(t) {
    const cs = t.checks;
    if (!cs.length) {
      return { tone: "idle", chip: t.state === "running" ? "checking…" : t.state === "waiting" ? "waiting" : "no call",
        body: `<span class="bp-note">Prices the worst case of each LLM call before it is sent.</span>` };
    }
    const blk = cs.find((c) => c.decision === "block");
    const known = cs.filter((c) => c.worst_usd != null && c.left_usd != null);
    const c = blk || (known.length ? known.reduce((a, x) => (x.left_usd - x.worst_usd < a.left_usd - a.worst_usd ? x : a)) : cs[0]);
    const label = blk ? `Call #${c.call} · ${esc(c.model)}`
      : cs.length > 1 ? `Tightest of ${cs.length} calls: #${c.call} · ${esc(c.model)}` : `Call #${c.call} · ${esc(c.model)}`;
    if (c.worst_usd == null) {
      return { tone: "bad", chip: "BLOCKED", body: `<span class="bp-note">${label}: no price for this model - TokenOps fails closed.</span>` };
    }
    let body = `<span class="bp-note">${label}</span>` + mx([
      mS(num(c.est_input)), mSub("in"), mOp("×"), mFrac(rateLbl(c.in_price), PER_M), mOp("+"),
      mS(num(c.max_output)), mSub("out"), mOp("×"), mFrac(rateLbl(c.out_price), PER_M), mOp("≈"),
      mS(bpUsd(c.input_usd)), mOp("+"), mS(bpUsd(c.output_usd)), mOp("="), `<b>${bpUsd(c.worst_usd)}</b>`]);
    if (c.left_usd != null) {
      body += mx([mVar("worst"), `<b>${bpUsd(c.worst_usd)}</b>`, mOp(blk ? "≥" : "<"), mS(bpUsd(c.left_usd)), mSub("left"),
        mOp("⇒"), blk ? mRes("BLOCK", "bad") : mRes("ALLOW", "good")]);
    }
    if (blk) body += `<span class="bp-note">${c.call === 1 ? "The very first call can't fit in the budget - nothing is sent to the model." : `Spent ${bpUsd(c.spent_usd)} so far; the next call could overshoot, so it is never sent.`}</span>`;
    return { tone: blk ? "bad" : "good", chip: blk ? `BLOCKED #${c.call}` : `ALLOWED ${cs.length}/${cs.length}`, body };
  }

  function guardPolicy(t) {
    const m = BP.meta || {};
    const thr = m.guard_threshold ?? 0.8, trig = thr * t.budget;
    const chat = esc(m.chat_model || "chat"), mini = esc(m.mini_model || "mini");
    const line1 = mx([mS(String(thr)), mOp("×"), mS(bpCap(t.budget)), mOp("="), `<b>${bpUsd(trig)}</b>`, mSub("trigger")], true);
    if (t.state === "waiting") return { tone: "idle", chip: "waiting", body: line1 };
    const switched = t.switched || (m.mini_model && t.model === m.mini_model);
    if (switched) {
      const at = t.guardAt ?? t.cost;
      return { tone: "warn", chip: "DOWNGRADED", body: line1 + mx([mVar("spent"), mS(bpUsd(at)), mOp("≥"), mS(bpUsd(trig)), mOp("⇒"), mS(chat), mOp("→"), mRes(mini, "warn")], true) };
    }
    const noSpend = !t.cost && t.checks.some((c) => c.decision === "block");
    if (noSpend) return { tone: "idle", chip: "NOT REACHED", body: line1 + `<span class="bp-note">No LLM spend, so there is nothing to downgrade.</span>` };
    return { tone: "good", chip: t.state === "running" ? "watching" : "NOT TRIGGERED",
      body: line1 + mx([mVar("spent"), mS(bpUsd(t.cost)), mOp("<"), mS(bpUsd(trig)), mOp("⇒"), mS("stay on"), mRes(chat, "good")], true) };
  }

  function capPolicy(t) {
    if (t.state === "waiting") return { tone: "idle", chip: "waiting", body: mx([mVar("spent"), mOp("<"), mS(bpCap(t.budget))], true) };
    const reason = (t.halt && t.halt.reason) || "";
    if (/exhausted/.test(reason)) {
      return { tone: "bad", chip: "TRIPPED", body: mx([mVar("spent"), mS(bpUsd(t.cost)), mOp("≥"), mS(bpCap(t.budget)), mOp("⇒"), mRes("HALT", "bad")], true) };
    }
    const head = t.budget - (t.cost || 0);
    let body = mx([mVar("spent"), mS(bpUsd(t.cost)), mOp("<"), mS(bpCap(t.budget)), mOp("⇒"), mVar("headroom"), mRes(bpUsd(head), "good"),
      mS(`(${(head / t.budget * 100).toFixed(1)}%)`, "op")], true);
    if (t.checks.some((c) => c.decision === "block")) body += `<span class="bp-note">Never breached - pre_call_worst_case stopped the run first, so spend can't overshoot the cap.</span>`;
    return { tone: "good", chip: t.state === "running" ? "watching" : "WITHIN CAP", body };
  }

  function outcome(t) {
    const blk = t.checks.find((c) => c.decision === "block");
    if (t.state === "waiting") return `<span>Queued - runs after the previous budget.</span>`;
    if (t.state === "running") return `<span>Running… spent <b>${bpUsd(t.cost)}</b> so far.</span>`;
    if (t.state === "error") return `<span class="bad-t">${esc(t.error || "error")}</span>`;
    if (blk && blk.call === 1) return `<b class="bad-t">Blocked before the first LLM call</b> <span>- ${bpUsd(t.cost)} spent. One worst-case call (${bpUsd(blk.worst_usd)}) is bigger than the whole budget.</span>`;
    if (blk) return `<b class="bad-t">Stopped before LLM call #${blk.call}</b> <span>- ${bpUsd(t.cost)} spent, the next call (${bpUsd(blk.worst_usd)}) would not fit in ${bpUsd(blk.left_usd)}.</span>`;
    if (t.state === "completed") {
      const sw = t.switched || (BP.meta && t.model === BP.meta.mini_model);
      return `<b class="good-t">Answered within budget</b> <span>- spent ${bpUsd(t.cost)} of ${bpCap(t.budget)} (${(t.cost / t.budget * 100).toFixed(1)}%)${sw ? `, finished on ${esc(t.model)} after cost_guard` : ""}.</span>`;
    }
    return `<b class="bad-t">${esc(t.state)}</b> <span>- ${esc((t.halt && t.halt.reason) || "")}</span>`;
  }

  function tierHtml(t) {
    const pct = Math.min(100, ((t.cost || 0) / t.budget) * 100);
    const badge = { waiting: ["", "waiting"], running: ["running", "running"], completed: ["completed", "completed"],
      halted: ["halted", "halted"], throttled: ["throttled", "throttled"], error: ["error", "error"] }[t.state] || ["", t.state];
    const pols = [["pre_call_worst_case", "before each call", prePolicy(t)], ["cost_guard", "after each call", guardPolicy(t)],
      ["cost_budget", "on actual spend", capPolicy(t)]];
    const live = t.state === "running" && t.log.length
      ? `<ol class="bp-live">${t.log.slice(-6).reverse().map((l) => `<li><span>${esc(l)}</span></li>`).join("")}</ol>` : "";
    const ans = t.answer && t.state !== "running"
      ? `<details class="bp-ans"><summary><span>Answer</span></summary><div class="bubble md-out">${renderMd(t.answer)}</div></details>` : "";
    const foot = t.state === "waiting" ? "" : `<div class="bp-foot">
        <small>${t.llm} LLM · ${t.tools} tools · ${num(t.tokens)} tok${t.model ? ` · ${esc(t.model)}` : ""}${t.elapsed != null ? ` · ${t.elapsed}s` : ""}</small>
        ${t.runId && t.state !== "running" ? `<button class="btn small" type="button" data-run="${esc(t.runId)}"><span>Trace</span></button>` : ""}</div>`;
    return `
      <div class="bp-head"><div><small>Per-run budget</small><span class="bp-budget">${bpCap(t.budget)}</span></div>
        <span class="badge ${badge[0]}"><i></i>${esc(badge[1])}</span></div>
      <div class="bp-out">${outcome(t)}</div>
      <div class="bp-meter"><div class="meter"><i class="${pct >= 100 ? "bad" : pct >= 80 ? "warn" : ""}" style="width:${pct.toFixed(1)}%"></i></div>
        <small>${bpUsd(t.cost)} spent of ${bpCap(t.budget)} · ${pct.toFixed(1)}%</small></div>
      <div class="bp-body">
        ${pols.map(([name, when, p], k) => `<div class="bp-pol ${p.tone}">
          <div class="bp-pol-h"><b><span class="bp-n">${k + 1}</span><span>${name}</span></b><span class="chk ${p.tone === "idle" ? "" : p.tone}">${esc(p.chip)}</span></div>
          <small>${when}</small>${p.body}</div>`).join("")}
        ${live}${ans}
      </div>${foot}`;
  }

  function renderProofBanner() {
    const done = BP.tiers.filter((t) => ["completed", "halted", "throttled"].includes(t.state));
    if (BP.error) {
      $("#bpBanner").innerHTML = `<div class="err-box"><span>${esc(BP.error)}</span></div>`;
      return;
    }
    if (!done.length) { $("#bpBanner").innerHTML = ""; return; }
    const blocked = done.filter((t) => t.state !== "completed").length;
    const within = done.every((t) => (t.cost || 0) <= t.budget + 1e-9);
    const restored = BP.restored != null ? ` · per-run budget restored to ${bpCap(BP.restored)}` : "";
    $("#bpBanner").innerHTML = `<div class="err-box good"><span><b>Same question, ${done.length} budget${done.length > 1 ? "s" : ""}:</b>
      ${blocked} stopped by policy, ${done.length - blocked} answered · ${within ? "every run stayed within its cap" : "a run exceeded its cap - check the ledger"}${restored}.</span></div>`;
  }

  async function streamPost(path, body, onEvent) {
    const resp = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!resp.ok) {
      let msg = resp.statusText;
      try {
        const d = (await resp.json()).detail;
        msg = Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || msg;
      } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, idx).split("\n").find((l) => l.startsWith("data: "));
        buf = buf.slice(idx + 2);
        if (line) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }

  function onProofEvent(ev) {
    if (ev.type === "proof_start") {
      BP.meta = { ...(BP.meta || {}), ...ev, budget_usd: ev.original_budget_usd };
      renderRules();
      return;
    }
    if (ev.type === "proof_done") { BP.restored = ev.restored_usd; return; }
    if (ev.type === "error") { BP.error = ev.message; return; }
    const t = BP.tiers[ev.tier];
    if (!t) return;
    const log = (s) => { t.log.push(s); if (t.log.length > 40) t.log.shift(); };
    switch (ev.type) {
      case "tier_start":
        t.state = "running";
        bpStatus(`Running budget ${ev.tier + 1} of ${BP.tiers.length}: ${bpCap(t.budget)}…`);
        break;
      case "run_start": t.runId = ev.run_id; t.model = ev.model; log(`run ${ev.run_id} started on ${ev.model}`); break;
      case "budget_check": t.checks.push(ev); break;
      case "llm":
        t.llm += 1; t.cost = ev.cost_usd; t.model = ev.model;
        t.tokens += (ev.input_tokens || 0) + (ev.output_tokens || 0);
        log(`LLM #${t.llm} · ${ev.model} · ${num(ev.input_tokens)}→${num(ev.output_tokens)} tok · ${bpUsd(ev.call_cost_usd)}`);
        break;
      case "tool_start": t.tools += 1; log(`tool ${ev.tool} (${ev.server})`); break;
      case "governance":
        if (ev.policy === "cost_guard" && t.guardAt == null) t.guardAt = ev.cost_usd;
        if (!(ev.kind === "mutate" && ev.policy === "pre_call_worst_case")) log(`${String(ev.kind || "").toUpperCase()} · ${ev.policy || "policy"}`);
        break;
      case "model_switch": t.switched = { from: ev.from, to: ev.to }; t.model = ev.to; log(`model ${ev.from} → ${ev.to}`); break;
      case "halt": t.halt = { reason: ev.reason }; log(`HALT · ${ev.reason}`); break;
      case "tier_done":
        Object.assign(t, {
          state: ev.status === "completed" ? "completed" : ev.status === "throttled" ? "throttled" : "halted",
          cost: ev.cost_usd || 0, checks: ev.budget_checks || t.checks, runId: ev.run_id, halt: ev.halt,
          model: ev.model_final, llm: ev.llm_calls, tools: ev.tool_calls,
          tokens: (ev.input_tokens || 0) + (ev.output_tokens || 0), elapsed: ev.elapsed_s, answer: ev.answer,
        });
        break;
      case "tier_error": t.state = "error"; t.error = ev.message; break;
      default: return;
    }
    renderTier(ev.tier);
  }

  async function runProof(e) {
    e.preventDefault();
    if (BP.running) return;
    const question = $("#bpQuestion").value.trim();
    const budgets = parseBudgets();
    if (question.length < 3) { bpStatus("Enter a question (3+ characters).", "bad"); return; }
    if (!budgets.length) { bpStatus("Enter 1-6 budgets between $0.001 and $100, e.g. 0.005, 0.25, 1, 10", "bad"); return; }
    const persona = $("#bpPersona").value;
    BP.running = true;
    BP.restored = null;
    BP.error = null;
    BP.tiers = budgets.map(newTier);
    $("#bpRun").disabled = true;
    $("#bpBanner").innerHTML = "";
    renderProof();
    bpStatus(`Running ${budgets.length} governed run${budgets.length > 1 ? "s" : ""} one after another…`);
    try {
      await streamPost("/api/tokenops/budget-proof", { question, persona, department: BP_DEPT[persona] || "Finance", budgets }, onProofEvent);
    } catch (err) {
      BP.error = err.message;
    } finally {
      BP.running = false;
      $("#bpRun").disabled = false;
      BP.tiers.forEach((t, i) => {
        if (t.state === "running" || (t.state === "waiting" && BP.error)) { t.state = "error"; t.error = t.error || "not run"; renderTier(i); }
      });
      bpStatus(BP.error ? "Proof stopped - see the message above." : "Done. Open any Trace to see the governance spans.", BP.error ? "bad" : "");
      renderProofBanner();
      state.loaded.governance = false;
      state.loaded.evals = false;
      api("/api/tokenops/proof-meta").then((m) => { BP.meta = m; renderRules(); }).catch(() => {});
    }
  }

  $("#bpForm").addEventListener("submit", runProof);
  $("#bpBudgets").addEventListener("input", () => {
    if (BP.running || BP.tiers.some((t) => t.state !== "waiting")) return;
    BP.tiers = parseBudgets().map(newTier);
    renderProof();
  });
  $("#bpGrid").addEventListener("click", (e) => {
    const b = e.target.closest("[data-run]");
    if (b) openRun(b.dataset.run);
  });

  // -------------------------------------------------------- pitch deck --
  const deck = {
    i: Math.max(0, Number(sessionStorage.getItem("finsight-slide")) || 0),
    slides: $$("#deckStage .p-slide"),
  };
  $("#deckDots").innerHTML = deck.slides.map((s, i) =>
    `<button type="button" data-i="${i}" title="${esc(s.dataset.title)}" aria-label="Slide ${i + 1}: ${esc(s.dataset.title)}"></button>`).join("");

  function fitDeck() {
    const v = $("#deckView");
    const w = v.clientWidth, h = v.clientHeight;
    if (!w || !h) return;
    $("#deckStage").style.setProperty("--s", Math.min(w / 1600, h / 900).toFixed(4));
  }
  new ResizeObserver(fitDeck).observe($("#deckView"));

  function showSlide(n, instant) {
    const total = deck.slides.length;
    n = Math.max(0, Math.min(total - 1, n));
    const dir = n >= deck.i ? 1 : -1;
    const s = deck.slides[n];
    const changed = !s.classList.contains("active");
    deck.i = n;
    deck.slides.forEach((el, i) => el.classList.toggle("active", i === n));
    const parts = $$("[data-a]", s);
    gsap.killTweensOf([s, ...parts]);
    if (changed && !instant) {
      gsap.fromTo(s, { opacity: 0, x: 36 * dir }, { opacity: 1, x: 0, duration: 0.5, ease: "power3.out" });
      gsap.fromTo(parts, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.5, stagger: 0.06, delay: 0.08, ease: "power2.out" });
    } else {
      gsap.set([s, ...parts], { clearProps: "opacity,transform" });
    }
    const pad = (x) => String(x).padStart(2, "0");
    $("#sfNum").textContent = `${pad(n + 1)} / ${pad(total)}`;
    $("#sfSection").textContent = s.dataset.title || "";
    $("#deckCount").textContent = `${n + 1} / ${total}`;
    $("#deckProgress").style.width = `${((n + 1) / total) * 100}%`;
    $$("#deckDots button").forEach((b, i) => b.classList.toggle("on", i === n));
    $("#deckPrev").disabled = n === 0;
    $("#deckNext").disabled = n === total - 1;
    sessionStorage.setItem("finsight-slide", String(n));
    annRefresh();
  }
  function enterPitch() {
    fitDeck();
    deck.slides[deck.i]?.classList.remove("active");
    showSlide(deck.i);
  }

  function togglePresent() {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else document.documentElement.requestFullscreen?.().catch(() => {});
  }
  function syncPresenting() {
    const on = !!document.fullscreenElement && state.page === "pitch";
    document.body.classList.toggle("presenting", on);
    $("#deckPresent span").textContent = document.fullscreenElement ? "✕ Exit" : "⛶ Present";
    requestAnimationFrame(() => {
      fitDeck();
      if (!on) moveIndicator($(".nav-btn.active"), true);
    });
  }
  document.addEventListener("fullscreenchange", syncPresenting);

  $("#deckPrev").addEventListener("click", () => showSlide(deck.i - 1));
  $("#deckNext").addEventListener("click", () => showSlide(deck.i + 1));
  $("#deckPresent").addEventListener("click", togglePresent);
  $("#deckDots").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (b) showSlide(Number(b.dataset.i));
  });

  // ---------------------------------------------------------- annotate --
  // Ink is stored per screen (each slide has its own layer) in viewport-
  // normalised coordinates, so it survives resizes and full-screen toggles.
  const annCv = $("#annotCanvas");
  const annCtx = annCv.getContext("2d");
  const annot = { on: false, tool: "pen", color: "#fbbf24", store: new Map(), cur: null, raf: 0 };
  const annKey = () => (state.page === "pitch" ? `pitch:${deck.i}` : state.page);
  function annList() {
    const k = annKey();
    if (!annot.store.has(k)) annot.store.set(k, []);
    return annot.store.get(k);
  }
  function annResize() {
    const dpr = window.devicePixelRatio || 1;
    annCv.width = Math.round(innerWidth * dpr);
    annCv.height = Math.round(innerHeight * dpr);
    annCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    annDraw();
  }
  function annStroke(s, w, h) {
    const p = s.pts.map(([x, y]) => [x * w, y * h]);
    const c = annCtx;
    c.save();
    c.lineCap = "round";
    c.lineJoin = "round";
    c.strokeStyle = s.color;
    if (s.tool === "hl") {
      c.globalAlpha = 0.32;
      c.lineWidth = 22;
    } else {
      c.lineWidth = 3.5;
      c.shadowColor = s.color;
      c.shadowBlur = 6;
    }
    c.beginPath();
    c.moveTo(p[0][0], p[0][1]);
    if (p.length === 1) c.lineTo(p[0][0] + 0.1, p[0][1] + 0.1);
    for (let i = 1; i < p.length - 1; i++) {
      c.quadraticCurveTo(p[i][0], p[i][1], (p[i][0] + p[i + 1][0]) / 2, (p[i][1] + p[i + 1][1]) / 2);
    }
    if (p.length > 1) c.lineTo(p[p.length - 1][0], p[p.length - 1][1]);
    c.stroke();
    c.restore();
  }
  function annDraw() {
    annot.raf = 0;
    const w = innerWidth, h = innerHeight;
    annCtx.clearRect(0, 0, w, h);
    (annot.store.get(annKey()) || []).forEach((s) => annStroke(s, w, h));
  }
  const annSchedule = () => { if (!annot.raf) annot.raf = requestAnimationFrame(annDraw); };
  function annUi() {
    $("#annotBar").classList.toggle("pitch", state.page === "pitch");
  }
  function annRefresh() {
    annot.cur = null;
    annUi();
    annDraw();
  }
  function annSet(on) {
    annot.on = on;
    annot.cur = null;
    document.body.classList.toggle("annotating", on);
    $$("[data-annot-toggle]").forEach((b) => b.setAttribute("aria-pressed", String(on)));
    annUi();
  }
  function annTool(t) {
    annot.tool = t;
    $$("#annotBar [data-tool]").forEach((b) => b.classList.toggle("on", b.dataset.tool === t));
  }
  function annColor(c) {
    annot.color = c;
    $$("#annotBar [data-color]").forEach((b) => b.classList.toggle("on", b.dataset.color === c));
  }
  function annUndo() {
    annList().pop();
    annDraw();
  }
  function annClear() {
    annot.store.delete(annKey());
    annot.cur = null;
    annDraw();
  }
  const annPt = (e) => [e.clientX / innerWidth, e.clientY / innerHeight];
  annCv.addEventListener("pointerdown", (e) => {
    if (!annot.on || e.button > 0) return;
    e.preventDefault();
    annCv.setPointerCapture(e.pointerId);
    annot.cur = { tool: annot.tool, color: annot.color, pts: [annPt(e)] };
    annList().push(annot.cur);
    annSchedule();
  });
  annCv.addEventListener("pointermove", (e) => {
    if (!annot.cur) return;
    const evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [];
    (evs.length ? evs : [e]).forEach((ev) => annot.cur.pts.push(annPt(ev)));
    annSchedule();
  });
  const annEnd = () => { annot.cur = null; };
  annCv.addEventListener("pointerup", annEnd);
  annCv.addEventListener("pointercancel", annEnd);
  window.addEventListener("resize", annResize);
  annResize();

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-annot-toggle], [data-annot-clear], [data-annot-undo], [data-tool], [data-color], [data-deck]");
    if (!el) return;
    if (el.hasAttribute("data-annot-toggle")) annSet(!annot.on);
    else if (el.hasAttribute("data-annot-clear")) annClear();
    else if (el.hasAttribute("data-annot-undo")) annUndo();
    else if (el.dataset.tool) annTool(el.dataset.tool);
    else if (el.dataset.color) annColor(el.dataset.color);
    else if (el.dataset.deck) showSlide(deck.i + Number(el.dataset.deck));
  });

  // Shortcuts: A annotate · C clear · Ctrl+Z undo · P/H tools · Esc stop;
  // on the Pitch page: ←/→ PgUp/PgDn Space Home End to navigate, F present.
  document.addEventListener("keydown", (e) => {
    if (e.target.closest?.("input, textarea, select, [contenteditable]")) return;
    if (e.altKey || e.metaKey) return;
    const k = e.key;
    if (e.ctrlKey) {
      if (annot.on && (k === "z" || k === "Z")) { e.preventDefault(); annUndo(); }
      return;
    }
    if (k === "a" || k === "A") { annSet(!annot.on); return; }
    if (annot.on) {
      if (k === "Escape") { annSet(false); return; }
      if (k === "c" || k === "C") { annClear(); return; }
      if (k === "p" || k === "P") { annTool("pen"); return; }
      if (k === "h" || k === "H") { annTool("hl"); return; }
    }
    if (state.page !== "pitch") return;
    if (k === "ArrowRight" || k === "PageDown" || k === " ") { e.preventDefault(); showSlide(deck.i + 1); }
    else if (k === "ArrowLeft" || k === "PageUp") { e.preventDefault(); showSlide(deck.i - 1); }
    else if (k === "Home") { e.preventDefault(); showSlide(0); }
    else if (k === "End") { e.preventDefault(); showSlide(deck.slides.length - 1); }
    else if (k === "f" || k === "F") togglePresent();
  });

  // -------------------------------------------------------------- data --
  async function loadData() {
    const [servers, ds] = await Promise.all([api("/api/mcp/tools"), api("/api/datasources")]);
    renderServers(servers);
    $("#dsList").innerHTML = ds.map((s) => `
      <div class="ds"><header><b>${esc(s.name)}</b><span class="badge ${s.exists ? "completed" : "halted"}"><i></i>${esc(s.type)}</span></header>
        <small>data/${esc(s.path)} · ${esc(s.refresh)}${s.mcp_server ? ` · served by MCP <b>${esc(s.mcp_server)}</b>` : ""}</small>
        <div class="ds-meta">${Object.entries(s.row_counts).map(([t, n]) => `<span>${esc(t)}: <b>${num(n)}</b></span>`).join("") || `<span>${esc(s.tables.join(", "))}</span>`}
          <small>${esc(s.size_kb)} KB</small></div></div>`).join("");
    revealCards($("#page-data"));
  }
  function renderServers(servers) {
    const total = servers.reduce((a, s) => a + s.tools.length, 0);
    $("#toolCount").textContent = `${total} tools discovered via tools/list`;
    $("#mcpList").innerHTML = servers.map((s) => `
      <div class="mcp-server"><header><b>${esc(s.title)}</b><span class="badge ${s.status === "online" ? "completed" : "halted"}"><i></i>${esc(s.status)}</span></header>
        <small>${esc(s.url)} · ${esc(s.description)}</small>
        <div class="tool-list">${s.tools.map((t) => `<span class="tool" title="${esc(t.description)}\nargs: ${esc(t.args.join(", "))}">${esc(t.name)}</span>`).join("")}</div></div>`).join("");
  }
  $("#rediscover").addEventListener("click", async () => {
    $("#toolCount").textContent = "re-discovering…";
    renderServers(await api("/api/mcp/refresh", { method: "POST" }));
    loadStatus();
  });

  // -------------------------------------------------------------- boot --
  function boot() {
    const saved = localStorage.getItem("finsight-theme");
    if (saved) document.documentElement.dataset.theme = saved;
    applyChartTheme();
    moveIndicator($(".nav-btn.active"), true);

    const tlBoot = gsap.timeline();
    tlBoot.from("#header", { y: -40, opacity: 0, duration: 0.7, ease: "power3.out" })
      .from(".brand-name, .brand-sub", { opacity: 0, x: -12, stagger: 0.08, duration: 0.4 }, "-=0.3")
      .from(".nav-btn", { opacity: 0, y: -8, stagger: 0.05, duration: 0.3 }, "-=0.2")
      .from(".footer", { y: 40, opacity: 0, duration: 0.5 }, "-=0.4")
      .from("#page-dashboard .hero", { opacity: 0, y: 16, duration: 0.5 }, "-=0.3");

    gsap.to(".b1", { x: "12vw", y: "8vh", duration: 18, repeat: -1, yoyo: true, ease: "sine.inOut" });
    gsap.to(".b2", { x: "-10vw", y: "-12vh", duration: 22, repeat: -1, yoyo: true, ease: "sine.inOut" });
    gsap.to(".b3", { x: "-14vw", y: "-6vh", duration: 26, repeat: -1, yoyo: true, ease: "sine.inOut" });
    gsap.to(".page-title, .brand-name", { backgroundPosition: "200% center", duration: 8, repeat: -1, ease: "none" });

    loadStatus();
    setInterval(loadStatus, 20000);
    api("/api/config").then((c) => { state.config = c; setMode(c.default_mode === "preview" ? "preview" : "enforce"); }).catch(() => {});
    loadPage("dashboard");
  }
  if (document.readyState === "complete") boot(); else window.addEventListener("load", boot);
})();
