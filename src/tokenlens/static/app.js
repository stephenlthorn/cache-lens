const el = (id) => document.getElementById(id);

// ─── Base path helpers (Tailscale / reverse proxy support) ─────────────────
// window.BASE_PATH is injected by the server (e.g. "/tokenlens"). Empty string
// when served directly at root.
const BASE = (window.BASE_PATH || '').replace(/\/$/, '');
const apiUrl  = (path) => BASE + path;
const wsUrl   = (path) => {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}${BASE}${path}`;
};

// ─── Page navigation ───────────────────────────────────────────────────────

const pageTabs = document.querySelectorAll('.page-tab');
const analyzeActions = el('analyzeActions');

function switchPage(pageId) {
  document.querySelectorAll('.page-content').forEach(p => p.classList.add('hidden'));
  pageTabs.forEach(t => t.classList.toggle('page-tab--active', t.dataset.page === pageId));
  const target = el(`page-${pageId}`);
  if (target) target.classList.remove('hidden');

  analyzeActions.style.display = (pageId === 'analyze') ? '' : 'none';

  if (pageId === 'dashboard') initDashboard();
  if (pageId === 'recommendations') loadRecommendations();
}

pageTabs.forEach(tab => {
  tab.addEventListener('click', () => switchPage(tab.dataset.page));
});

// ─── Analyze page elements ─────────────────────────────────────────────────

const input = el('input');
const analyzeBtn = el('analyze');
const loadExampleBtn = el('loadExample');
const exportBtn = el('exportJson');
const newBtn = el('newAnalysis');
const fileInput = el('file');
const drop = el('drop');

const inputView = el('inputView');
const resultsView = el('resultsView');

const inputMeta = el('inputMeta');

const scoreLabel = el('scoreLabel');
const scoreGauge = el('scoreGauge');
const totalInput = el('totalInput');
const wastePct = el('wastePct');
const topDriver = el('topDriver');
const breakdown = el('breakdown');

const wasteSources = el('wasteSources');
const wasteMeta = el('wasteMeta');

const sdMeta = el('sdMeta');
const barStatic = el('barStatic');
const barDynamic = el('barDynamic');
const staticPct = el('staticPct');
const dynamicPct = el('dynamicPct');

const suggestions = el('suggestions');
const sugMeta = el('sugMeta');

const optimizedOut = el('optimizedOut');
const copyOptimized = el('copyOptimized');

const repeats = el('repeats');
const errorBox = el('error');

let lastResult = null;
let activeTab = 'optimized';

function fmtInt(n) {
  if (typeof n !== 'number') return '—';
  return n.toLocaleString();
}

function fmtCost(n) {
  if (typeof n !== 'number') return '—';
  return `$${n.toFixed(2)}`;
}

function fmtCompact(n) {
  if (typeof n !== 'number') return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return n.toLocaleString();
}

function clamp(n, a, b) { return Math.min(b, Math.max(a, n)); }

function setLoading(loading) {
  analyzeBtn.disabled = loading || input.value.trim().length === 0;
  analyzeBtn.classList.toggle('is-loading', loading);
  analyzeBtn.querySelector('.btn__spinner').style.display = loading ? 'inline-block' : 'none';
  analyzeBtn.querySelector('.btn__label').textContent = loading ? 'Analyzing' : 'Analyze';
}

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.remove('hidden');
}

function clearError() {
  errorBox.classList.add('hidden');
  errorBox.textContent = '';
}

function updateInputMeta() {
  const chars = input.value.length;
  inputMeta.textContent = `${chars.toLocaleString()} characters`;
}

function gaugeSvg(score) {
  const s = clamp(score, 0, 100);
  const r = 46;
  const c = 2 * Math.PI * r;
  const dash = (s / 100) * c;
  const hue = s < 40 ? 350 : (s < 70 ? 38 : 155);
  const color = `hsl(${hue} 90% 60%)`;

  return `
  <svg viewBox="0 0 120 120" width="118" height="118" role="img" aria-label="Cacheability score ${s} out of 100">
    <defs>
      <linearGradient id="g" x1="0" x2="1" y1="0" y2="1">
        <stop offset="0" stop-color="${color}" stop-opacity="1" />
        <stop offset="1" stop-color="rgba(124,92,255,.75)" stop-opacity="1" />
      </linearGradient>
      <filter id="glow" x="-40%" y="-40%" width="180%" height="180%">
        <feGaussianBlur stdDeviation="2.6" result="blur"/>
        <feMerge>
          <feMergeNode in="blur"/>
          <feMergeNode in="SourceGraphic"/>
        </feMerge>
      </filter>
    </defs>

    <circle cx="60" cy="60" r="46" fill="none" stroke="rgba(255,255,255,.10)" stroke-width="10" />
    <circle cx="60" cy="60" r="46" fill="none" stroke="url(#g)" stroke-width="10" stroke-linecap="round"
      stroke-dasharray="${dash} ${c}" transform="rotate(-90 60 60)" filter="url(#glow)" />

    <text x="60" y="62" text-anchor="middle" font-size="28" font-family="ui-sans-serif,system-ui" fill="rgba(231,237,246,.95)" font-weight="700">${s}</text>
    <text x="60" y="82" text-anchor="middle" font-size="11" fill="rgba(154,167,184,.95)">/ 100</text>
  </svg>`;
}

function pill(label, value, tone) {
  const cls = tone === 'warm' ? 'pill pill--warm' : 'pill';
  return `<span class="${cls}">${label}: <strong>${value}</strong></span>`;
}

function renderBreakdown(bd) {
  breakdown.innerHTML = '';
  if (!bd) return;
  const parts = [
    ['Static prefix', bd.static_prefix_penalty],
    ['Repetition', bd.repetition_penalty],
    ['Interleaving', bd.interleave_penalty],
    ['No prefix', bd.no_prefix_penalty],
    ['Fragmentation', bd.fragmentation_penalty]
  ];
  for (const [k, v] of parts) {
    const val = (typeof v === 'number') ? `${v}` : '0';
    const t = document.createElement('span');
    t.className = 'pill';
    t.textContent = `${k}: ${val}`;
    breakdown.appendChild(t);
  }
}

function renderWasteSources(ws) {
  wasteSources.innerHTML = '';
  if (!ws || ws.length === 0) {
    wasteSources.innerHTML = '<li>No significant waste detected.</li>';
    return;
  }
  ws.slice(0, 5).forEach((s) => {
    const li = document.createElement('li');
    li.innerHTML = `<div><strong>${s.type}</strong> — ${s.waste_tokens.toLocaleString()} tokens</div>` +
      `<span class="sub">${s.description} · ${s.percentage_of_total.toFixed(1)}% of total</span>`;
    wasteSources.appendChild(li);
  });
}

function mkAccordion(title, meta, bodyHtml) {
  const wrap = document.createElement('div');
  wrap.className = 'acc';
  wrap.innerHTML = `
    <div class="acc__top" role="button" tabindex="0" aria-expanded="false">
      <div>
        <div class="acc__title">${title}</div>
        <div class="acc__meta">${meta}</div>
      </div>
      <div class="kbd">Enter</div>
    </div>
    <div class="acc__body">${bodyHtml}</div>
  `;

  const top = wrap.querySelector('.acc__top');
  function toggle() {
    const open = wrap.classList.toggle('acc--open');
    top.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  top.addEventListener('click', toggle);
  top.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  });

  return wrap;
}

function renderSuggestions(list) {
  suggestions.innerHTML = '';
  if (!list || list.length === 0) {
    suggestions.innerHTML = '<div class="hint">No suggestions — looks pretty good.</div>';
    return;
  }

  list.forEach((sug, idx) => {
    const meta = `${sug.priority.toUpperCase()} · est. save ~${fmtInt(sug.estimated_savings_tokens)} tokens`;

    const before = sug.before_snippet ? `<div class="hint" style="margin:10px 0 6px 0">Before</div><pre class="code">${escapeHtml(sug.before_snippet)}</pre>` : '';
    const after = sug.after_snippet ? `<div class="hint" style="margin:10px 0 6px 0">After</div><pre class="code">${escapeHtml(sug.after_snippet)}</pre>` : '';

    const body = `
      <div class="hint">${escapeHtml(sug.description || '')}</div>
      ${before}
      ${after}
    `;

    suggestions.appendChild(mkAccordion(`${idx + 1}. ${escapeHtml(sug.title)}`, meta, body));
  });
}

function renderRepeats(list) {
  repeats.innerHTML = '';
  if (!list || list.length === 0) {
    repeats.innerHTML = '<div class="hint">No repeated blocks found above the threshold.</div>';
    return;
  }

  list.slice(0, 5).forEach((rb, idx) => {
    const meta = `${rb.occurrences}× · ${fmtInt(rb.tokens_per_occurrence)} tokens each · waste ${fmtInt(rb.total_waste_tokens)}`;
    const body = `<pre class="code">${escapeHtml(rb.content_full)}</pre>`;
    repeats.appendChild(mkAccordion(`${idx + 1}. ${escapeHtml(rb.content_preview)}`, meta, body));
  });
}

function setStaticDynamic(sd) {
  if (!sd) {
    sdMeta.textContent = '—';
    barStatic.style.width = '0%';
    barDynamic.style.width = '100%';
    staticPct.textContent = '—';
    dynamicPct.textContent = '—';
    return;
  }

  const sp = (typeof sd.static_percentage === 'number') ? sd.static_percentage : 0;
  const dp = Math.max(0, 100 - sp);

  sdMeta.textContent = `${fmtInt(sd.total_static_tokens)} static · ${fmtInt(sd.total_dynamic_tokens)} dynamic`;
  barStatic.style.width = `${clamp(sp, 0, 100)}%`;
  barDynamic.style.width = `${clamp(dp, 0, 100)}%`;

  staticPct.textContent = `${sp.toFixed ? sp.toFixed(1) : sp}%`;
  dynamicPct.textContent = `${dp.toFixed(1)}%`;
}

function renderResult(res) {
  lastResult = res;
  exportBtn.disabled = false;
  newBtn.classList.remove('hidden');

  scoreLabel.textContent = res.cacheability_label || '—';
  scoreGauge.innerHTML = gaugeSvg(res.cacheability_score ?? 0);

  totalInput.textContent = `${fmtInt(res.input_summary?.total_input_tokens)} tokens`;
  wastePct.textContent = `${fmtInt(res.waste_summary?.total_waste_tokens)} tokens (${(res.waste_summary?.waste_percentage ?? 0).toFixed(1)}%)`;

  const top = (res.waste_summary?.sources && res.waste_summary.sources[0]) ? res.waste_summary.sources[0].type : '—';
  topDriver.textContent = top;

  renderBreakdown(res.score_breakdown);

  wasteMeta.textContent = `${res.waste_summary?.sources?.length ?? 0} sources`;
  renderWasteSources(res.waste_summary?.sources);

  setStaticDynamic(res.static_dynamic_breakdown);

  sugMeta.textContent = `${res.suggestions?.length ?? 0} suggestion(s)`;
  renderSuggestions(res.suggestions);

  setOptimizedOutput();
  renderRepeats(res.repeated_blocks);

  resultsView.classList.remove('hidden');
  inputView.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

function setOptimizedOutput() {
  if (!lastResult) return;

  const tabButtons = document.querySelectorAll('.tab');
  tabButtons.forEach(b => b.classList.toggle('tab--active', b.dataset.tab === activeTab));

  if (activeTab === 'raw') {
    optimizedOut.textContent = lastResult.raw_content ? lastResult.raw_content : (lastResult && lastResult.input_type ? '(raw input not available)' : '');
    optimizedOut.textContent = input.value;
    copyOptimized.textContent = 'Copy';
    return;
  }

  const opt = lastResult.optimized_structure;
  if (!opt) {
    optimizedOut.textContent = 'No optimized structure produced.';
    return;
  }

  optimizedOut.textContent = JSON.stringify(opt, null, 2);
  copyOptimized.textContent = 'Copy';
}

function escapeHtml(str) {
  return String(str)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

async function analyzeNow() {
  clearError();
  setLoading(true);
  try {
    const resp = await fetch(apiUrl('/api/analyze'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: input.value, min_tokens: 50 })
    });
    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(txt || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    renderResult(data);
  } catch (e) {
    showError(`Analysis failed: ${e.message || e}`);
  } finally {
    setLoading(false);
  }
}

function exportJson() {
  if (!lastResult) return;
  const blob = new Blob([JSON.stringify(lastResult, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `tokenlens-analysis.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function loadExample() {
  const resp = await fetch(apiUrl('/static/example.json'));
  input.value = await resp.text();
  updateInputMeta();
  analyzeBtn.disabled = input.value.trim().length === 0;
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result || ''));
    r.onerror = () => reject(r.error || new Error('read failed'));
    r.readAsText(file);
  });
}

async function handleFiles(files) {
  if (!files || files.length === 0) return;
  const txt = await readFile(files[0]);
  input.value = txt;
  updateInputMeta();
  analyzeBtn.disabled = input.value.trim().length === 0;
}

// Events — Analyze page
input.addEventListener('input', () => {
  updateInputMeta();
  analyzeBtn.disabled = input.value.trim().length === 0;
});

analyzeBtn.addEventListener('click', analyzeNow);
loadExampleBtn.addEventListener('click', loadExample);
exportBtn.addEventListener('click', exportJson);

newBtn.addEventListener('click', () => {
  resultsView.classList.add('hidden');
  lastResult = null;
  exportBtn.disabled = true;
  newBtn.classList.add('hidden');
  clearError();
});

fileInput.addEventListener('change', async (e) => {
  await handleFiles(e.target.files);
  fileInput.value = '';
});

['dragenter','dragover'].forEach(evt => {
  drop.addEventListener(evt, (e) => {
    e.preventDefault();
    e.stopPropagation();
    drop.classList.add('is-drag');
  });
});
['dragleave','drop'].forEach(evt => {
  drop.addEventListener(evt, (e) => {
    e.preventDefault();
    e.stopPropagation();
    drop.classList.remove('is-drag');
  });
});

drop.addEventListener('drop', async (e) => {
  const dt = e.dataTransfer;
  if (dt && dt.files && dt.files.length) {
    await handleFiles(dt.files);
  }
});

document.querySelectorAll('.tab').forEach((b) => {
  b.addEventListener('click', () => {
    activeTab = b.dataset.tab;
    setOptimizedOutput();
  });
});

copyOptimized.addEventListener('click', async () => {
  const txt = optimizedOut.textContent || '';
  if (!txt.trim()) return;
  await navigator.clipboard.writeText(txt);
  copyOptimized.textContent = 'Copied';
  setTimeout(() => copyOptimized.textContent = 'Copy', 900);
});

// init analyze page state
updateInputMeta();
setLoading(false);

// ─── Dashboard ─────────────────────────────────────────────────────────────

let costChart = null;
let dashboardInitialized = false;
let currentCostRange = 30;
let currentCostMode = 'model';
let currentBreakdownDays = 30;

// Color palette: index 0-2 reserved for model colors, 3+ for sources
const COST_PALETTE = ['#7c5cff','#64a0ff','#2ee9a6','#ffb020','#ff6b6b','#e879f9','#38bdf8','#a3e635'];

function modelColor(modelName) {
  const m = (modelName || '').toLowerCase();
  if (m.includes('opus'))   return COST_PALETTE[0];
  if (m.includes('sonnet')) return COST_PALETTE[1];
  if (m.includes('haiku'))  return COST_PALETTE[2];
  return null; // caller assigns from overflow
}

function assignColors(keys, mode) {
  const result = {};
  let overflowIdx = mode === 'model' ? 3 : 3;
  for (const key of keys) {
    if (mode === 'model') {
      const fixed = modelColor(key);
      if (fixed) { result[key] = fixed; continue; }
    }
    result[key] = COST_PALETTE[overflowIdx % COST_PALETTE.length];
    overflowIdx++;
  }
  return result;
}

function refreshDashboard() {
  loadKPIs();
  loadCostChart(currentCostRange, currentCostMode);
  loadSpendBreakdown(currentBreakdownDays);
  loadCacheTrend();
  loadSessions();
  loadBudgetStatus();
  loadForecastKPI();
  loadCacheAccordionSummary();
}

function initDashboard() {
  if (dashboardInitialized) return;
  dashboardInitialized = true;
  refreshDashboard();
  loadTokenAnatomy(7);
  backfillLiveFeed();
  connectLiveFeed();
  setInterval(backfillLiveFeed, 10000);
  setInterval(refreshDashboard, 60000);

  // Cost chart range tabs
  document.querySelectorAll('#costChartRange .tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#costChartRange .tab').forEach(b => b.classList.remove('tab--active'));
      btn.classList.add('tab--active');
      currentCostRange = parseInt(btn.dataset.range, 10);
      loadCostChart(currentCostRange, currentCostMode);
    });
  });

  // Cost chart mode toggle (model / source)
  document.querySelectorAll('#costChartMode .tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#costChartMode .tab').forEach(b => b.classList.remove('tab--active'));
      btn.classList.add('tab--active');
      currentCostMode = btn.dataset.mode;
      loadCostChart(currentCostRange, currentCostMode);
    });
  });

  // Spend breakdown period tabs
  document.querySelectorAll('#breakdownPeriodTabs .tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#breakdownPeriodTabs .tab').forEach(b => b.classList.remove('tab--active'));
      btn.classList.add('tab--active');
      currentBreakdownDays = parseInt(btn.dataset.days, 10);
      loadSpendBreakdown(currentBreakdownDays);
    });
  });

  // Token anatomy period tabs (inside accordion)
  document.querySelectorAll('#taPeriodTabs .tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#taPeriodTabs .tab').forEach(b => b.classList.remove('tab--active'));
      btn.classList.add('tab--active');
      loadTokenAnatomy(parseInt(btn.dataset.days, 10));
    });
  });

  // Cache accordion toggle
  const accordionToggle = el('cacheAccordionToggle');
  const accordionContent = el('cacheAccordionContent');
  if (accordionToggle && accordionContent) {
    accordionToggle.addEventListener('click', () => {
      const isOpen = !accordionContent.classList.contains('hidden');
      accordionContent.classList.toggle('hidden', isOpen);
      accordionToggle.setAttribute('aria-expanded', String(!isOpen));
      accordionToggle.querySelector('.ca-chevron').textContent = isOpen ? '▼' : '▲';
      if (!isOpen) {
        loadTokenAnatomy(7);
        loadCacheTrend();
      }
    });
  }
}

async function loadKPIs() {
  const periods = [1, 7, 30, 365];
  for (const days of periods) {
    try {
      const r = await fetch(apiUrl(`/api/usage/kpi?days=${days}`));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const costEl = el(`kpi-cost-${days}`);
      if (costEl) costEl.textContent = fmtCost(data.total_cost_usd || 0);
      // populate saved card from 30d
      if (days === 30) {
        const savedEl = el('kpi-saved-30');
        if (savedEl) savedEl.textContent = fmtCost(data.savings_usd || 0);
        // also populate cache accordion summary savings
        const accSaved = el('cacheAccordionSaved');
        if (accSaved) accSaved.textContent = fmtCost(data.savings_usd || 0) + ' saved';
      }
    } catch {
      const costEl = el(`kpi-cost-${days}`);
      if (costEl) costEl.textContent = '—';
    }
  }
}

async function loadForecastKPI() {
  const valEl = el('kpi-forecast-val');
  const subEl = el('kpi-forecast-sub');
  if (!valEl) return;
  try {
    const r = await fetch(apiUrl('/api/usage/forecast'));
    if (!r.ok) { valEl.textContent = '—'; return; }
    const data = await r.json();
    valEl.textContent = fmtCost(data.projected_monthly_usd);
    if (subEl) subEl.textContent = `$${(data.daily_avg_usd || 0).toFixed(2)}/day`;
  } catch {
    valEl.textContent = '—';
  }
}

async function loadCacheAccordionSummary() {
  const hitEl = el('cacheAccordionHitRate');
  if (!hitEl) return;
  try {
    const r = await fetch(apiUrl('/api/usage/cache-trend?days=30'));
    if (!r.ok) return;
    const { data } = await r.json();
    if (data && data.length > 0) {
      const avg = data.reduce((s, d) => s + (d.cache_hit_pct || 0), 0) / data.length;
      hitEl.textContent = avg.toFixed(0) + '% hit rate';
    }
  } catch { /* silent */ }
}

async function loadTokenAnatomy(days = 7) {
  try {
    const r = await fetch(apiUrl(`/api/usage/kpi?days=${days}`));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();

    const fresh = d.input_tokens || 0;
    const cacheWrite = d.cache_write_tokens || 0;
    const cacheRead = d.cache_read_tokens || 0;
    const output = d.output_tokens || 0;
    const total = fresh + cacheWrite + cacheRead + output;
    const contextTotal = fresh + cacheWrite + cacheRead;

    const setBox = (type, tokens) => {
      const pct = total > 0 ? (tokens / total * 100) : 0;
      const tokEl = el(`ta-${type}-tokens`);
      const pctEl = el(`ta-${type}-pct`);
      if (tokEl) tokEl.textContent = fmtCompact(tokens);
      if (pctEl) pctEl.textContent = total > 0 ? pct.toFixed(1) + '% of total' : '';
    };

    setBox('fresh', fresh);
    setBox('write', cacheWrite);
    setBox('read', cacheRead);
    setBox('out', output);

    const totalEl = el('ta-total');
    if (totalEl) totalEl.textContent = fmtCompact(total);

    const callsEl = el('ta-calls');
    if (callsEl) callsEl.textContent = d.call_count ? `${fmtInt(d.call_count)} calls` : '';

    const costEl = el('ta-cost');
    if (costEl) costEl.textContent = fmtCost(d.total_cost_usd);

    const savEl = el('ta-savings');
    if (savEl) savEl.textContent = fmtCost(d.savings_usd || 0);

    const cachePctEl = el('ta-cache-pct');
    if (cachePctEl) {
      const readPct = contextTotal > 0 ? (cacheRead / contextTotal * 100) : 0;
      cachePctEl.textContent = readPct.toFixed(1) + '%';
    }
  } catch {
    ['fresh','write','read','out'].forEach(t => {
      const e = el(`ta-${t}-tokens`); if (e) e.textContent = '—';
    });
  }
}

async function loadCostChart(days, mode) {
  try {
    const r = await fetch(apiUrl(`/api/usage/daily?days=${days}`));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const { rows } = await r.json();
    renderCostChart(rows, mode);
  } catch {
    // silently leave chart empty
  }
}

function renderCostChart(rows, mode) {
  const canvas = el('costChart');
  if (!canvas) return;

  const groupKey = mode === 'source' ? 'source' : 'model';

  // Collect all dates and group keys
  const dateSet = new Set();
  const groupSet = new Set();
  for (const row of rows) {
    dateSet.add(row.date);
    groupSet.add(row[groupKey] || 'unknown');
  }
  const dates = Array.from(dateSet).sort();
  const groups = Array.from(groupSet);

  // Aggregate cost by date+group
  const byDateGroup = {};
  for (const row of rows) {
    const d = row.date;
    const g = row[groupKey] || 'unknown';
    if (!byDateGroup[d]) byDateGroup[d] = {};
    byDateGroup[d][g] = (byDateGroup[d][g] || 0) + (row.cost_usd || 0);
  }

  // Sort groups by total cost desc
  const groupTotals = {};
  for (const g of groups) {
    groupTotals[g] = dates.reduce((s, d) => s + (byDateGroup[d]?.[g] || 0), 0);
  }
  groups.sort((a, b) => groupTotals[b] - groupTotals[a]);

  const colors = assignColors(groups, mode);

  const datasets = groups.map(g => ({
    label: g,
    data: dates.map(d => parseFloat((byDateGroup[d]?.[g] || 0).toFixed(4))),
    backgroundColor: colors[g] + 'cc',
    borderColor: colors[g],
    borderWidth: 0,
    borderRadius: 2,
    borderSkipped: false,
  }));

  if (costChart) { costChart.destroy(); costChart = null; }

  costChart = new Chart(canvas, {
    type: 'bar',
    data: { labels: dates, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: $${ctx.parsed.y.toFixed(3)}`
          }
        }
      },
      scales: {
        x: {
          stacked: true,
          ticks: { color: '#64748b', maxRotation: 45, font: { size: 10 } },
          grid: { color: 'rgba(255,255,255,0.03)' }
        },
        y: {
          stacked: true,
          ticks: { color: '#64748b', font: { size: 10 }, callback: v => '$' + v.toFixed(2) },
          grid: { color: 'rgba(255,255,255,0.04)' }
        }
      }
    }
  });

  // Render custom legend
  const legendEl = el('costChartLegend');
  if (legendEl) {
    legendEl.innerHTML = groups.map(g =>
      `<span class="cost-legend-item"><span class="cost-legend-dot" style="background:${colors[g]}"></span>${escapeHtml(g)}</span>`
    ).join('');
  }
}

async function loadSpendBreakdown(days) {
  const modelEl = el('modelBreakdownBars');
  const sourceEl = el('sourceBreakdownBars');
  try {
    const r = await fetch(apiUrl(`/api/usage/daily?days=${days}`));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const { rows } = await r.json();

    // Aggregate by model
    const byModel = {};
    const bySource = {};
    for (const row of rows) {
      const m = row.model || 'unknown';
      const s = row.source || 'unknown';
      byModel[m] = (byModel[m] || 0) + (row.cost_usd || 0);
      bySource[s] = (bySource[s] || 0) + (row.cost_usd || 0);
    }

    const modelColors = assignColors(Object.keys(byModel), 'model');
    const sourceColors = assignColors(Object.keys(bySource), 'source');

    if (modelEl) modelEl.innerHTML = renderBreakdownBars(byModel, modelColors);
    if (sourceEl) sourceEl.innerHTML = renderBreakdownBars(bySource, sourceColors);
  } catch {
    if (modelEl) modelEl.innerHTML = '<div class="muted">Failed to load.</div>';
    if (sourceEl) sourceEl.innerHTML = '<div class="muted">Failed to load.</div>';
  }
}

function renderBreakdownBars(totals, colors) {
  const sorted = Object.entries(totals).sort((a, b) => b[1] - a[1]);
  if (sorted.length === 0) return '<div class="muted">No data.</div>';
  const max = sorted[0][1];
  const grandTotal = sorted.reduce((s, [, v]) => s + v, 0);
  return sorted.map(([key, cost]) => {
    const pct = grandTotal > 0 ? (cost / grandTotal * 100) : 0;
    const barPct = max > 0 ? (cost / max * 100) : 0;
    const color = colors[key] || COST_PALETTE[3];
    return `<div class="breakdown-row">
      <div class="breakdown-row__label">${escapeHtml(key)}</div>
      <div class="breakdown-row__bar-wrap">
        <div class="breakdown-row__bar" style="width:${barPct.toFixed(1)}%;background:${color}"></div>
      </div>
      <div class="breakdown-row__cost">${fmtCost(cost)}</div>
      <div class="breakdown-row__pct">${pct.toFixed(0)}%</div>
    </div>`;
  }).join('');
}

// ─── Live Feed ──────────────────────────────────────────────────────────────

const LIVE_FEED_MAX_ROWS = 50;

// Tracks the unix timestamp (seconds) of the most recent call displayed.
// Used to deduplicate between the initial backfill, polling, and WebSocket events.
let liveFeedLastTs = 0;

function _callTs(call) {
  // WS events use 'ts' (unix seconds); /recent responses use 'timestamp' (ISO string)
  if (typeof call.ts === 'number') return call.ts;
  if (call.timestamp) return Math.floor(new Date(call.timestamp).getTime() / 1000);
  return 0;
}

function _liveFeedDebug(msg) {
  // debug logging removed in v8 UI cleanup
}

async function backfillLiveFeed() {
  const tbody = el('liveFeedBody');
  try {
    const url = apiUrl('/api/usage/recent?limit=50');
    _liveFeedDebug(`[1] fetching ${url}`);
    const r = await fetch(url);
    _liveFeedDebug(`[2] HTTP ${r.status}`);
    if (!r.ok) {
      if (tbody && liveFeedLastTs === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="table-empty">Backfill failed: HTTP ${r.status} (${url})</td></tr>`;
      }
      return;
    }
    const data = await r.json();
    const calls = data.calls || [];
    _liveFeedDebug(`[3] ${calls.length} calls, liveFeedLastTs=${liveFeedLastTs}, tbody=${tbody ? 'ok' : 'null'}`);
    if (calls.length === 0) {
      if (tbody && liveFeedLastTs === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="table-empty">No API calls recorded yet. Route traffic through TokenLens to see activity here.</td></tr>`;
      }
      return;
    }
    const newCalls = calls.filter(c => _callTs(c) > liveFeedLastTs);
    _liveFeedDebug(`[4] ${newCalls.length} new, first_callTs=${calls[0] ? _callTs(calls[0]) : 'n/a'}`);
    if (newCalls.length === 0) {
      if (tbody && liveFeedLastTs === 0) {
        // All returned calls have ts=0 — show them anyway
        for (const call of [...calls].reverse()) {
          addLiveFeedRow(call);
        }
      }
      return;
    }
    for (const call of [...newCalls].reverse()) {
      liveFeedLastTs = Math.max(liveFeedLastTs, _callTs(call));
      addLiveFeedRow(call);
    }
    _liveFeedDebug('');  // clear on success
  } catch (err) {
    _liveFeedDebug(`ERROR: ${String(err)}`);
    if (tbody && liveFeedLastTs === 0) {
      tbody.innerHTML = `<tr><td colspan="8" class="table-empty">Backfill error: ${escapeHtml(String(err))}</td></tr>`;
    }
  }
}
let liveFeedEmpty = true;

function connectLiveFeed() {
  const statusEl = el('liveFeedStatus');
  let reconnectDelay = 1000;
  let ws = null;

  function connect() {
    if (ws) {
      ws.onclose = null;
      ws.onerror = null;
      ws.close();
    }

    ws = new WebSocket(wsUrl('/api/live'));

    if (statusEl) statusEl.textContent = 'Connecting…';

    ws.onopen = () => {
      if (statusEl) statusEl.textContent = 'Live';
      reconnectDelay = 1000;
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // Update tracker so polling won't re-add this WS-delivered call
        liveFeedLastTs = Math.max(liveFeedLastTs, _callTs(data));
        addLiveFeedRow(data);
        reconnectDelay = 1000;
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      if (statusEl) statusEl.textContent = `Reconnecting in ${Math.round(reconnectDelay / 1000)}s…`;
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 30000);
    };

    ws.onerror = () => ws.close();
  }

  connect();
}

function addLiveFeedRow(data) {
  const tbody = el('liveFeedBody');
  if (!tbody) return;

  if (liveFeedEmpty) {
    tbody.innerHTML = '';
    liveFeedEmpty = false;
  }

  const time = data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();
  const tr = document.createElement('tr');
  tr.className = 'live-feed-row-new';
  tr.innerHTML = `
    <td>${escapeHtml(time)}</td>
    <td>${escapeHtml(data.provider || '—')}</td>
    <td class="mono-sm">${escapeHtml(data.model || '—')}</td>
    <td>${escapeHtml(data.source || '—')}</td>
    <td>${fmtInt(data.input_tokens)}</td>
    <td>${fmtInt(data.cache_write_tokens)}</td>
    <td>${fmtInt(data.cache_read_tokens)}</td>
    <td>${fmtInt(data.output_tokens)}</td>
    <td>${fmtCost(data.cost_usd)}</td>
  `;
  tbody.insertBefore(tr, tbody.firstChild);

  // Remove old rows beyond limit
  while (tbody.rows.length > LIVE_FEED_MAX_ROWS) {
    tbody.removeChild(tbody.lastChild);
  }

  // Fade-in animation cleanup
  setTimeout(() => tr.classList.remove('live-feed-row-new'), 600);
}

// ─── Deep Dive ─────────────────────────────────────────────────────────────

let deepDiveData = [];
let deepDiveSortCol = 'date';
let deepDiveSortAsc = true;

el('applyFilters').addEventListener('click', loadDeepDive);

async function loadDeepDive() {
  const from = el('filterFrom').value;
  const to = el('filterTo').value;
  const provider = el('filterProvider').value;
  const model = el('filterModel').value.trim().toLowerCase();
  const source = el('filterSource').value.trim().toLowerCase();

  const meta = el('deepDiveMeta');
  if (meta) meta.textContent = 'Loading…';

  try {
    const params = new URLSearchParams({ days: 365 });
    const r = await fetch(apiUrl(`/api/usage/daily?${params}`));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    let { rows } = await r.json();

    // Client-side filtering
    rows = rows.filter(row => {
      if (from && row.date < from) return false;
      if (to && row.date > to) return false;
      if (provider && row.provider !== provider) return false;
      if (model && !(row.model || '').toLowerCase().includes(model)) return false;
      if (source && !(row.source || '').toLowerCase().includes(source)) return false;
      return true;
    });

    deepDiveData = rows.map(row => {
      const cacheRead = row.cache_read_tokens || 0;
      const cacheWrite = row.cache_write_tokens || 0;
      const inputTok = row.input_tokens || 0;
      const denom = cacheRead + cacheWrite + inputTok;
      return {
        ...row,
        cache_hit_pct: denom > 0 ? (cacheRead / denom) * 100 : 0,
      };
    });

    if (meta) meta.textContent = `${deepDiveData.length} row(s)`;
    renderDeepDiveTable();
    renderCacheEfficiency();

    // Populate provider dropdown from actual data
    populateProviderFilter(rows);
  } catch (err) {
    if (meta) meta.textContent = 'Error loading data';
    el('deepDiveBody').innerHTML = `<tr><td colspan="11" class="table-empty">Failed: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function populateProviderFilter(rows) {
  const sel = el('filterProvider');
  if (!sel) return;
  const existing = new Set(Array.from(sel.options).map(o => o.value).filter(Boolean));
  const providers = [...new Set(rows.map(r => r.provider).filter(Boolean))];
  for (const prov of providers) {
    if (!existing.has(prov)) {
      const opt = document.createElement('option');
      opt.value = prov;
      opt.textContent = prov;
      sel.appendChild(opt);
    }
  }
}

function renderDeepDiveTable() {
  const tbody = el('deepDiveBody');
  if (!tbody) return;

  const sorted = [...deepDiveData].sort((a, b) => {
    const va = a[deepDiveSortCol] ?? '';
    const vb = b[deepDiveSortCol] ?? '';
    const result = va < vb ? -1 : va > vb ? 1 : 0;
    return deepDiveSortAsc ? result : -result;
  });

  if (sorted.length === 0) {
    tbody.innerHTML = '<tr><td colspan="11" class="table-empty">No records match the filters.</td></tr>';
    return;
  }

  tbody.innerHTML = sorted.map(row => `
    <tr>
      <td>${escapeHtml(row.date || '—')}</td>
      <td>${escapeHtml(row.provider || '—')}</td>
      <td class="mono-sm">${escapeHtml(row.model || '—')}</td>
      <td>${escapeHtml(row.source || '—')}</td>
      <td>${fmtInt(row.call_count)}</td>
      <td>${fmtInt(row.input_tokens)}</td>
      <td>${fmtInt(row.cache_write_tokens)}</td>
      <td>${fmtInt(row.cache_read_tokens)}</td>
      <td>${row.cache_hit_pct != null ? row.cache_hit_pct.toFixed(1) + '%' : '—'}</td>
      <td>${fmtInt(row.output_tokens)}</td>
      <td>${fmtCost(row.cost_usd)}</td>
    </tr>
  `).join('');
}

function renderCacheEfficiency() {
  const tbody = el('cacheEfficiencyBody');
  if (!tbody) return;

  const anthropicRows = deepDiveData.filter(r => (r.provider || '').toLowerCase() === 'anthropic');
  if (anthropicRows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="table-empty">No Anthropic rows in current filter.</td></tr>';
    return;
  }

  tbody.innerHTML = anthropicRows.map(row => {
    const hitPct = row.cache_hit_pct != null ? row.cache_hit_pct.toFixed(1) + '%' : '—';
    const hitClass = row.cache_hit_pct >= 80 ? 'hit-good' : row.cache_hit_pct >= 40 ? 'hit-ok' : 'hit-poor';
    return `
      <tr>
        <td>${escapeHtml(row.date || '—')}</td>
        <td class="mono-sm">${escapeHtml(row.model || '—')}</td>
        <td>${escapeHtml(row.source || '—')}</td>
        <td>${fmtInt(row.input_tokens)}</td>
        <td>${fmtInt(row.cache_write_tokens)}</td>
        <td>${fmtInt(row.cache_read_tokens)}</td>
        <td><span class="hit-badge ${hitClass}">${hitPct}</span></td>
      </tr>
    `;
  }).join('');
}

// Sortable column headers
document.querySelectorAll('#deepDiveTable th.sortable').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.col;
    if (deepDiveSortCol === col) {
      deepDiveSortAsc = !deepDiveSortAsc;
    } else {
      deepDiveSortCol = col;
      deepDiveSortAsc = true;
    }

    document.querySelectorAll('#deepDiveTable th.sortable').forEach(h => {
      const icon = h.querySelector('.sort-icon');
      if (!icon) return;
      if (h.dataset.col === col) {
        icon.textContent = deepDiveSortAsc ? '↑' : '↓';
      } else {
        icon.textContent = '↕';
      }
    });

    renderDeepDiveTable();
  });
});

// ─── Recommendations ───────────────────────────────────────────────────────

async function loadRecommendations() {
  const container = el('recommendationsContent');
  if (!container) return;
  container.innerHTML = '<div class="table-empty">Loading…</div>';

  try {
    const r = await fetch(apiUrl('/api/usage/recommendations'));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const recs = (await r.json()).recommendations || [];

    if (!recs || recs.length === 0) {
      container.innerHTML = '<div class="rec-empty">No recommendations — your usage looks optimal!</div>';
      return;
    }

    container.innerHTML = recs.map((rec, i) => {
      const impactClass = rec.estimated_impact === 'high' ? 'impact-high'
        : rec.estimated_impact === 'medium' ? 'impact-medium'
        : 'impact-low';
      const impactLabel = rec.estimated_impact || 'low';

      const deepDiveLink = rec.deep_dive_link
        ? `<a href="#" class="rec-link" data-filter="${escapeHtml(JSON.stringify(rec.deep_dive_link))}">View in Deep Dive →</a>`
        : '';

      return `
        <div class="rec-card">
          <div class="rec-card__header">
            <div class="rec-rank">#${i + 1}</div>
            <div class="rec-title">${escapeHtml(rec.title || 'Recommendation')}</div>
            <span class="impact-badge ${impactClass}">${escapeHtml(impactLabel)}</span>
          </div>
          <div class="rec-desc">${escapeHtml(rec.description || '')}</div>
          ${deepDiveLink}
        </div>
      `;
    }).join('');

    // Wire up deep-dive links
    container.querySelectorAll('.rec-link').forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        try {
          const filters = JSON.parse(link.dataset.filter);
          applyDeepDiveFilters(filters);
          switchPage('deepdive');
        } catch {
          switchPage('deepdive');
        }
      });
    });
  } catch (err) {
    container.innerHTML = `<div class="table-empty">Failed to load recommendations: ${escapeHtml(err.message)}</div>`;
  }
}

function applyDeepDiveFilters(filters) {
  if (filters.provider) el('filterProvider').value = filters.provider;
  if (filters.model) el('filterModel').value = filters.model;
  if (filters.source) el('filterSource').value = filters.source;
  if (filters.from) el('filterFrom').value = filters.from;
  if (filters.to) el('filterTo').value = filters.to;
  loadDeepDive();
}

// ─── Cache Hit Rate Trend (Phase 3) ─────────────────────────────────────────

let cacheTrendChart = null;

async function loadCacheTrend() {
  try {
    const r = await fetch(apiUrl('/api/usage/cache-trend?days=30'));
    if (!r.ok) return;
    const { trend, data } = await r.json();

    const meta = el('cacheTrendMeta');
    if (meta) {
      const arrows = { improving: 'Improving', degrading: 'Degrading', stable: 'Stable', insufficient_data: 'Not enough data' };
      meta.textContent = arrows[trend] || trend;
    }

    if (!data || data.length === 0) return;

    const canvas = el('cacheTrendChart');
    if (!canvas) return;
    if (cacheTrendChart) { cacheTrendChart.destroy(); cacheTrendChart = null; }

    cacheTrendChart = new Chart(canvas, {
      type: 'line',
      data: {
        labels: data.map(d => d.date),
        datasets: [{
          label: 'Cache Hit %',
          data: data.map(d => d.cache_hit_pct),
          borderColor: 'rgba(0,255,136,0.8)',
          backgroundColor: 'rgba(0,255,136,0.08)',
          fill: true,
          tension: 0.4,
          pointRadius: 2,
          pointBackgroundColor: 'rgba(0,255,136,0.9)',
          borderWidth: 2,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#94a3b8', font: { size: 11 } } }
        },
        scales: {
          x: { ticks: { color: '#64748b', maxRotation: 45, font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.03)' } },
          y: { min: 0, max: 100, ticks: { color: '#64748b', font: { size: 10 }, callback: v => v + '%' }, grid: { color: 'rgba(255,255,255,0.04)' } }
        }
      }
    });
  } catch { /* silently fail */ }
}

// ─── Sessions (Phase 5) ─────────────────────────────────────────────────────

async function loadSessions() {
  const tbody = el('sessionsBody');
  if (!tbody) return;
  try {
    const r = await fetch(apiUrl('/api/usage/sessions?days=1'));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const { sessions } = await r.json();
    if (!sessions || sessions.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No sessions detected (raw calls only, 24h window).</td></tr>';
      return;
    }
    tbody.innerHTML = sessions.map(s => {
      const start = s.start_time ? new Date(s.start_time).toLocaleString() : '—';
      const dur = s.duration_minutes != null ? s.duration_minutes + ' min' : '—';
      return `<tr>
        <td>${escapeHtml(s.source || '—')}</td>
        <td>${escapeHtml(start)}</td>
        <td>${dur}</td>
        <td>${fmtInt(s.call_count)}</td>
        <td class="mono-sm">${escapeHtml((s.models || []).join(', '))}</td>
        <td>${fmtCost(s.total_cost_usd)}</td>
      </tr>`;
    }).join('');
  } catch {
    tbody.innerHTML = '<tr><td colspan="6" class="table-empty">Failed to load sessions.</td></tr>';
  }
}

// ─── Budget Status (Phase 7) ────────────────────────────────────────────────

async function loadBudgetStatus() {
  try {
    const r = await fetch(apiUrl('/api/usage/budget-status'));
    if (!r.ok) return;
    const data = await r.json();
    const bar = el('budgetStatusBar');
    if (!bar) return;

    if (!data.enabled) { bar.classList.add('hidden'); return; }
    bar.classList.remove('hidden');

    const dailyFill = el('budgetDailyFill');
    const dailyText = el('budgetDailyText');
    const monthlyFill = el('budgetMonthlyFill');
    const monthlyText = el('budgetMonthlyText');

    if (data.daily_limit_usd) {
      const pct = Math.min(100, (data.daily_spend_usd / data.daily_limit_usd) * 100);
      if (dailyFill) { dailyFill.style.width = pct + '%'; dailyFill.className = 'budget-bar__fill' + (pct >= 90 ? ' over' : ''); }
      if (dailyText) dailyText.textContent = `$${data.daily_spend_usd.toFixed(2)} / $${data.daily_limit_usd.toFixed(2)}`;
    } else {
      if (dailyText) dailyText.textContent = 'No limit set';
    }

    if (data.monthly_limit_usd) {
      const pct = Math.min(100, (data.monthly_spend_usd / data.monthly_limit_usd) * 100);
      if (monthlyFill) { monthlyFill.style.width = pct + '%'; monthlyFill.className = 'budget-bar__fill' + (pct >= 90 ? ' over' : ''); }
      if (monthlyText) monthlyText.textContent = `$${data.monthly_spend_usd.toFixed(2)} / $${data.monthly_limit_usd.toFixed(2)}`;
    } else {
      if (monthlyText) monthlyText.textContent = 'No limit set';
    }
  } catch { /* silently fail */ }
}

// ─── Spend Forecast ─────────────────────────────────────────────────────────

async function loadForecast() {
  const container = el('forecast-content');
  if (!container) return;
  try {
    const r = await fetch(apiUrl('/api/usage/forecast'));
    if (!r.ok) { container.innerHTML = '<span class="muted">Unavailable</span>'; return; }
    const data = await r.json();

    const trendArrow = data.trend === 'increasing' ? '\u2191'
      : data.trend === 'decreasing' ? '\u2193'
      : '\u2192';
    const trendColor = data.trend === 'increasing' ? '#ef4444'
      : data.trend === 'decreasing' ? '#00ff88'
      : '#64748b';
    const confColor = data.confidence === 'high' ? '#00ff88'
      : data.confidence === 'medium' ? '#f59e0b'
      : '#64748b';

    container.innerHTML = `
      <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap">
        <span class="neon-value" style="font-size:1.8rem">
          $${data.projected_monthly_usd.toFixed(2)}
        </span>
        <span style="font-size:1rem;color:${trendColor};font-weight:600;font-family:var(--mono)" title="Trend: ${data.trend}">
          ${trendArrow} ${data.trend}
        </span>
        <span class="pill" style="background:${confColor}15;color:${confColor};border:1px solid ${confColor}30;font-family:var(--mono)">
          ${data.confidence}
        </span>
      </div>
      <div style="margin-top:10px;color:#64748b;font-size:12px;font-family:var(--mono)">
        $${data.daily_avg_usd.toFixed(2)}/day &middot; ${data.days_remaining}d remaining
      </div>
    `;
  } catch {
    container.innerHTML = '<span class="muted">Failed to load forecast</span>';
  }
}

// ─── Settings Panel ─────────────────────────────────────────────────────────

const settingsToggle = el('settingsToggle');
const settingsPanel = el('settingsPanel');
const settingsClose = el('settingsClose');

if (settingsToggle && settingsPanel) {
  settingsToggle.addEventListener('click', async () => {
    settingsPanel.classList.toggle('hidden');
    if (!settingsPanel.classList.contains('hidden')) {
      await loadAlertSettings();
      await loadBudgetSettings();
      await loadPricingSettings();
      await loadQuotas();
    }
  });
}
if (settingsClose && settingsPanel) {
  settingsClose.addEventListener('click', () => settingsPanel.classList.add('hidden'));
}

async function loadAlertSettings() {
  try {
    const r = await fetch(apiUrl('/api/settings/alerts'));
    if (!r.ok) return;
    const data = await r.json();
    const enabled = el('alertsEnabled');
    const threshold = el('alertThreshold');
    if (enabled) enabled.checked = !!data.alerts_enabled;
    if (threshold && data.daily_cost_threshold != null) threshold.value = data.daily_cost_threshold;
  } catch { /* ignore */ }
}

async function loadBudgetSettings() {
  try {
    const r = await fetch(apiUrl('/api/settings/budget'));
    if (!r.ok) return;
    const data = await r.json();
    const enabled = el('budgetEnabled');
    const daily = el('budgetDaily');
    const monthly = el('budgetMonthly');
    if (enabled) enabled.checked = !!data.enabled;
    if (daily && data.daily_limit_usd != null) daily.value = data.daily_limit_usd;
    if (monthly && data.monthly_limit_usd != null) monthly.value = data.monthly_limit_usd;
  } catch { /* ignore */ }
}

const saveAlertsBtn = el('saveAlerts');
if (saveAlertsBtn) {
  saveAlertsBtn.addEventListener('click', async () => {
    const enabled = el('alertsEnabled')?.checked || false;
    const threshold = parseFloat(el('alertThreshold')?.value) || null;
    await fetch(apiUrl('/api/settings/alerts'), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ alerts_enabled: enabled, daily_cost_threshold: threshold }),
    });
    saveAlertsBtn.textContent = 'Saved!';
    setTimeout(() => saveAlertsBtn.textContent = 'Save Alerts', 1200);
  });
}

const saveBudgetBtn = el('saveBudget');
if (saveBudgetBtn) {
  saveBudgetBtn.addEventListener('click', async () => {
    const enabled = el('budgetEnabled')?.checked || false;
    const daily = parseFloat(el('budgetDaily')?.value) || null;
    const monthly = parseFloat(el('budgetMonthly')?.value) || null;
    await fetch(apiUrl('/api/settings/budget'), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled, daily_limit_usd: daily, monthly_limit_usd: monthly }),
    });
    saveBudgetBtn.textContent = 'Saved!';
    setTimeout(() => saveBudgetBtn.textContent = 'Save Budget', 1200);
    loadBudgetStatus();
  });
}

// ─── Custom Pricing Settings ────────────────────────────────────────────────

async function loadPricingSettings() {
  try {
    const r = await fetch(apiUrl('/api/settings/pricing'));
    if (!r.ok) return;
    const data = await r.json();
    const tbody = el('pricingBody');
    if (!tbody) return;

    const models = data.models || {};
    const defaultModels = ['anthropic/default', 'openai/default', 'google/default'];
    const modelNames = Object.keys(models)
      .filter(m => !defaultModels.includes(m))
      .sort();

    if (modelNames.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No models found.</td></tr>';
      return;
    }

    tbody.innerHTML = modelNames.map(name => {
      const rates = models[name];
      return `<tr data-model="${name}">
        <td><code>${name}</code></td>
        <td><input type="number" class="filter-input pricing-input" data-field="input" step="0.01" min="0" value="${rates.input}" /></td>
        <td><input type="number" class="filter-input pricing-input" data-field="output" step="0.01" min="0" value="${rates.output}" /></td>
        <td><input type="number" class="filter-input pricing-input" data-field="cache_read" step="0.01" min="0" value="${rates.cache_read}" /></td>
        <td><input type="number" class="filter-input pricing-input" data-field="cache_write" step="0.01" min="0" value="${rates.cache_write}" /></td>
      </tr>`;
    }).join('');
  } catch { /* ignore */ }
}

const savePricingBtn = el('savePricing');
if (savePricingBtn) {
  savePricingBtn.addEventListener('click', async () => {
    const tbody = el('pricingBody');
    if (!tbody) return;
    const overrides = {};
    tbody.querySelectorAll('tr[data-model]').forEach(row => {
      const model = row.dataset.model;
      const inputs = row.querySelectorAll('.pricing-input');
      const rates = {};
      inputs.forEach(inp => {
        const val = parseFloat(inp.value);
        if (!isNaN(val)) rates[inp.dataset.field] = val;
      });
      if (Object.keys(rates).length > 0) overrides[model] = rates;
    });
    await fetch(apiUrl('/api/settings/pricing'), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ overrides }),
    });
    savePricingBtn.textContent = 'Saved!';
    setTimeout(() => savePricingBtn.textContent = 'Save Pricing', 1200);
  });
}

// ─── Cost Alert Banner ──────────────────────────────────────────────────────

const dismissAlertBtn = el('dismissAlert');
if (dismissAlertBtn) {
  dismissAlertBtn.addEventListener('click', () => {
    const banner = el('costAlertBanner');
    if (banner) banner.classList.add('hidden');
  });
}

// ─── CSV Export (Phase 2) ───────────────────────────────────────────────────

const exportCsvBtn = el('exportCsv');
if (exportCsvBtn) {
  exportCsvBtn.addEventListener('click', () => {
    window.open(apiUrl('/api/export/csv?days=30'), '_blank');
  });
}

// ─── Model Comparison (Phase 4) ─────────────────────────────────────────────

async function populateModelDropdowns() {
  try {
    const r = await fetch(apiUrl('/api/usage/daily?days=365'));
    if (!r.ok) return;
    const { rows } = await r.json();
    const models = [...new Set(rows.map(r => r.model).filter(Boolean))].sort();
    const fromSel = el('compareFrom');
    const toSel = el('compareTo');
    if (!fromSel || !toSel) return;
    for (const m of models) {
      fromSel.appendChild(Object.assign(document.createElement('option'), { value: m, textContent: m }));
      toSel.appendChild(Object.assign(document.createElement('option'), { value: m, textContent: m }));
    }
  } catch { /* ignore */ }
}

const compareBtn = el('compareModels');
if (compareBtn) {
  compareBtn.addEventListener('click', async () => {
    const from = el('compareFrom')?.value;
    const to = el('compareTo')?.value;
    const resultDiv = el('compareResult');
    if (!from || !to || !resultDiv) return;

    resultDiv.classList.remove('hidden');
    resultDiv.innerHTML = '<div class="meta">Comparing…</div>';

    try {
      const r = await fetch(apiUrl(`/api/usage/compare?from_model=${encodeURIComponent(from)}&to_model=${encodeURIComponent(to)}&days=30`));
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        resultDiv.innerHTML = `<div class="meta" style="color:var(--danger)">${escapeHtml(err.error || 'Error')}</div>`;
        return;
      }
      const data = await r.json();
      const savingsColor = data.savings_usd > 0 ? 'var(--accent2)' : 'var(--danger)';
      resultDiv.innerHTML = `
        <div class="compare-grid">
          <div><span class="meta">Actual cost (${escapeHtml(from)})</span><br><strong>${fmtCost(data.actual_cost_usd)}</strong></div>
          <div><span class="meta">Hypothetical (${escapeHtml(to)})</span><br><strong>${fmtCost(data.hypothetical_cost_usd)}</strong></div>
          <div><span class="meta">Savings</span><br><strong style="color:${savingsColor}">${fmtCost(data.savings_usd)} (${data.savings_pct.toFixed(1)}%)</strong></div>
          <div><span class="meta">Calls</span><br><strong>${fmtInt(data.call_count)}</strong></div>
        </div>
      `;
    } catch (err) {
      resultDiv.innerHTML = `<div class="meta" style="color:var(--danger)">Failed: ${escapeHtml(err.message)}</div>`;
    }
  });
}

// --- Cost Allocation Tags ---

async function loadTagBreakdown() {
  const tbody = el('tag-tbody');
  if (!tbody) return;
  try {
    const r = await fetch(apiUrl('/api/usage/by-tag?days=30'));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (!data || data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No tag data available.</td></tr>';
      return;
    }
    tbody.innerHTML = data.map(row => `
      <tr>
        <td>${escapeHtml(row.source || '—')}</td>
        <td>${fmtInt(row.call_count)}</td>
        <td>${fmtInt(row.input_tokens)}</td>
        <td>${fmtInt(row.output_tokens)}</td>
        <td>${fmtCost(row.cost_usd)}</td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="table-empty">Failed: ${escapeHtml(err.message)}</td></tr>`;
  }
}

// ─── Token Cost Breakdown ─────────────────────────────────────────────────

let tokenCostChartInstance = null;

async function loadTokenCostBreakdown() {
  try {
    const r = await fetch(apiUrl('/api/usage/token-breakdown?days=30'));
    if (!r.ok) return;
    const { data } = await r.json();
    if (!data || data.length === 0) return;

    const labels = data.map(d => d.date);
    const inputCosts = data.map(d => d.input_cost);
    const outputCosts = data.map(d => d.output_cost);
    const cacheReadCosts = data.map(d => d.cache_read_cost);
    const cacheWriteCosts = data.map(d => d.cache_write_cost);

    const canvas = el('tokenCostChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    if (tokenCostChartInstance) tokenCostChartInstance.destroy();

    tokenCostChartInstance = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Input',
            data: inputCosts,
            backgroundColor: 'rgba(139,92,246,0.7)',
          },
          {
            label: 'Output',
            data: outputCosts,
            backgroundColor: 'rgba(0,255,136,0.5)',
          },
          {
            label: 'Cache Read',
            data: cacheReadCosts,
            backgroundColor: 'rgba(245,158,11,0.5)',
          },
          {
            label: 'Cache Write',
            data: cacheWriteCosts,
            backgroundColor: 'rgba(239,68,68,0.5)',
          },
        ],
      },
      options: {
        responsive: true,
        scales: {
          x: { stacked: true, ticks: { color: '#64748b', font: { size: 10 } }, grid: { display: false } },
          y: { stacked: true, ticks: { color: '#64748b', font: { size: 10 }, callback: v => '$' + v.toFixed(2) }, grid: { color: 'rgba(255,255,255,0.03)' } },
        },
        plugins: {
          legend: { labels: { color: '#94a3b8', font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: ctx => `${ctx.dataset.label}: $${ctx.parsed.y.toFixed(4)}`,
            },
          },
        },
      },
    });
  } catch (err) {
    // silently fail — chart is supplementary
  }
}

// Populate model dropdowns + tag breakdown + token cost breakdown when Deep Dive page loads
const origLoadDeepDive = loadDeepDive;
loadDeepDive = async function() {
  await origLoadDeepDive();
  populateModelDropdowns();
  loadTagBreakdown();
  loadTokenCostBreakdown();
};

async function loadWasteSummary() {
  try {
    const r = await fetch(apiUrl('/api/usage/waste-summary?days=30'));
    const d = await r.json();
    const wtEl = el('kpi-waste-tokens');
    const wsEl = el('kpi-waste-savings');
    if (wtEl) wtEl.textContent = d.total_waste_tokens
      ? `${d.total_waste_tokens.toLocaleString()} tok`
      : '—';
    if (wsEl) wsEl.textContent = d.total_savings_usd
      ? `Saved: $${d.total_savings_usd.toFixed(2)}`
      : 'No waste found';
  } catch (e) {
    // silent fail
  }
}
loadWasteSummary();

// --- Quota Management ---
let _quotaConfig = { source_limits: {}, model_limits: {}, kill_switches: [] };

async function loadQuotas() {
    try {
        const resp = await fetch(`${BASE}/api/config/quotas`);
        _quotaConfig = await resp.json();
        renderQuotaSources();
        renderQuotaModels();
        renderKillSwitches();
    } catch (e) { console.error("Failed to load quotas", e); }
}

function renderQuotaSources() {
    const container = document.getElementById("quota-source-list");
    container.innerHTML = "";
    for (const [source, limits] of Object.entries(_quotaConfig.source_limits || {})) {
        const row = document.createElement("div");
        row.className = "flex gap-2 mb-2 items-center";
        row.innerHTML = `
            <input type="text" value="${source}" class="input-sm quota-src-name" placeholder="source" style="width:120px">
            <label class="muted">$/day</label>
            <input type="number" value="${limits.daily_limit_usd ?? ''}" class="input-sm quota-src-daily" style="width:80px" step="0.01">
            <label class="muted">$/mo</label>
            <input type="number" value="${limits.monthly_limit_usd ?? ''}" class="input-sm quota-src-monthly" style="width:80px" step="0.01">
            <button class="btn btn-sm btn-ghost" onclick="this.parentElement.remove()">✕</button>
        `;
        container.appendChild(row);
    }
}

function addQuotaSource() {
    const container = document.getElementById("quota-source-list");
    const row = document.createElement("div");
    row.className = "flex gap-2 mb-2 items-center";
    row.innerHTML = `
        <input type="text" class="input-sm quota-src-name" placeholder="source" style="width:120px">
        <label class="muted">$/day</label>
        <input type="number" class="input-sm quota-src-daily" style="width:80px" step="0.01">
        <label class="muted">$/mo</label>
        <input type="number" class="input-sm quota-src-monthly" style="width:80px" step="0.01">
        <button class="btn btn-sm btn-ghost" onclick="this.parentElement.remove()">✕</button>
    `;
    container.appendChild(row);
}

function renderQuotaModels() {
    const container = document.getElementById("quota-model-list");
    container.innerHTML = "";
    for (const [model, limits] of Object.entries(_quotaConfig.model_limits || {})) {
        const row = document.createElement("div");
        row.className = "flex gap-2 mb-2 items-center";
        row.innerHTML = `
            <input type="text" value="${model}" class="input-sm quota-model-name" placeholder="model" style="width:180px">
            <label class="muted">calls/day</label>
            <input type="number" value="${limits.daily_call_limit ?? ''}" class="input-sm quota-model-daily" style="width:80px">
            <button class="btn btn-sm btn-ghost" onclick="this.parentElement.remove()">✕</button>
        `;
        container.appendChild(row);
    }
}

function addQuotaModel() {
    const container = document.getElementById("quota-model-list");
    const row = document.createElement("div");
    row.className = "flex gap-2 mb-2 items-center";
    row.innerHTML = `
        <input type="text" class="input-sm quota-model-name" placeholder="model" style="width:180px">
        <label class="muted">calls/day</label>
        <input type="number" class="input-sm quota-model-daily" style="width:80px">
        <button class="btn btn-sm btn-ghost" onclick="this.parentElement.remove()">✕</button>
    `;
    container.appendChild(row);
}

function renderKillSwitches() {
    const container = document.getElementById("quota-kill-list");
    container.innerHTML = "";
    for (const source of (_quotaConfig.kill_switches || [])) {
        const row = document.createElement("div");
        row.className = "flex gap-2 mb-2 items-center";
        row.innerHTML = `
            <input type="text" value="${source}" class="input-sm kill-switch-name" placeholder="source" style="width:160px">
            <button class="btn btn-sm btn-ghost" onclick="this.parentElement.remove()">✕</button>
        `;
        container.appendChild(row);
    }
}

function addKillSwitch() {
    const container = document.getElementById("quota-kill-list");
    const row = document.createElement("div");
    row.className = "flex gap-2 mb-2 items-center";
    row.innerHTML = `
        <input type="text" class="input-sm kill-switch-name" placeholder="source" style="width:160px">
        <button class="btn btn-sm btn-ghost" onclick="this.parentElement.remove()">✕</button>
    `;
    container.appendChild(row);
}

async function saveQuotas() {
    const source_limits = {};
    document.querySelectorAll("#quota-source-list > div").forEach(row => {
        const name = row.querySelector(".quota-src-name").value.trim();
        if (!name) return;
        const daily = row.querySelector(".quota-src-daily").value;
        const monthly = row.querySelector(".quota-src-monthly").value;
        const limits = {};
        if (daily) limits.daily_limit_usd = parseFloat(daily);
        if (monthly) limits.monthly_limit_usd = parseFloat(monthly);
        source_limits[name] = limits;
    });

    const model_limits = {};
    document.querySelectorAll("#quota-model-list > div").forEach(row => {
        const name = row.querySelector(".quota-model-name").value.trim();
        if (!name) return;
        const daily = row.querySelector(".quota-model-daily").value;
        if (daily) model_limits[name] = { daily_call_limit: parseInt(daily) };
    });

    const kill_switches = [];
    document.querySelectorAll("#quota-kill-list > div").forEach(row => {
        const name = row.querySelector(".kill-switch-name").value.trim();
        if (name) kill_switches.push(name);
    });

    const resp = await fetch(`${BASE}/api/config/quotas`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_limits, model_limits, kill_switches }),
    });
    const status = document.getElementById("quota-save-status");
    status.textContent = resp.ok ? "Saved!" : "Error";
    setTimeout(() => { status.textContent = ""; }, 2000);
}

// Start on dashboard — must be last so all let/const variables are initialized
switchPage('dashboard');
