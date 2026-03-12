const el = (id) => document.getElementById(id);

// ─── Base path helpers (Tailscale / reverse proxy support) ─────────────────
// window.BASE_PATH is injected by the server (e.g. "/cachelens"). Empty string
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
  return `$${n.toFixed(4)}`;
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
  a.download = `cachelens-analysis.json`;
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

let tokenChart = null;
let dashboardInitialized = false;
let currentChartRange = 30;

function initDashboard() {
  if (dashboardInitialized) return;
  dashboardInitialized = true;
  loadKPIs();
  loadTokenChart(currentChartRange);
  loadProviderBreakdown();
  loadSourceBreakdown();
  loadCacheTrend();
  loadSessions();
  loadBudgetStatus();
  backfillLiveFeed();
  connectLiveFeed();
  setInterval(backfillLiveFeed, 10000);

  document.querySelectorAll('#chartToggle .tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#chartToggle .tab').forEach(b => b.classList.remove('tab--active'));
      btn.classList.add('tab--active');
      currentChartRange = parseInt(btn.dataset.range, 10);
      loadTokenChart(currentChartRange);
    });
  });
}

async function loadKPIs() {
  const periods = [
    { days: 1, label: 'Today' },
    { days: 7, label: 'Last 7 days' },
    { days: 30, label: 'Last 30 days' },
    { days: 365, label: 'Last 365 days' },
  ];
  for (const p of periods) {
    try {
      const r = await fetch(apiUrl(`/api/usage/kpi?days=${p.days}`));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const kpiEl = el(`kpi-${p.days}`);
      if (kpiEl) kpiEl.textContent = fmtCost(data.total_cost_usd);
      const savEl = el(`kpi-savings-${p.days}`);
      if (savEl && typeof data.savings_usd === 'number' && data.savings_usd > 0) {
        savEl.textContent = `saved ${fmtCost(data.savings_usd)}`;
      }
    } catch {
      const kpiEl = el(`kpi-${p.days}`);
      if (kpiEl) kpiEl.textContent = '—';
    }
  }
}

async function loadTokenChart(days) {
  try {
    const r = await fetch(apiUrl(`/api/usage/daily?days=${days}`));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const { rows } = await r.json();
    renderTokenChart(rows);
  } catch {
    // silently leave chart empty
  }
}

function renderTokenChart(rows) {
  const canvas = el('tokenChart');
  if (!canvas) return;

  // Aggregate by date (multiple provider/model/source rows per date)
  const byDate = {};
  for (const row of rows) {
    const d = row.date;
    if (!byDate[d]) byDate[d] = { input_tokens: 0, cache_read_tokens: 0, output_tokens: 0 };
    byDate[d].input_tokens += row.input_tokens || 0;
    byDate[d].cache_read_tokens += row.cache_read_tokens || 0;
    byDate[d].output_tokens += row.output_tokens || 0;
  }
  const dates = Object.keys(byDate).sort();
  const labels = dates;
  const inputData = dates.map(d => byDate[d].input_tokens);
  const cacheData = dates.map(d => byDate[d].cache_read_tokens);
  const outputData = dates.map(d => byDate[d].output_tokens);

  if (tokenChart) {
    tokenChart.destroy();
    tokenChart = null;
  }

  tokenChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Input',
          data: inputData,
          backgroundColor: 'rgba(124,92,255,0.65)',
          borderRadius: 4,
        },
        {
          label: 'Cache Read',
          data: cacheData,
          backgroundColor: 'rgba(46,233,166,0.65)',
          borderRadius: 4,
        },
        {
          label: 'Output',
          data: outputData,
          backgroundColor: 'rgba(255,176,32,0.65)',
          borderRadius: 4,
        },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: { color: 'rgba(231,237,246,0.85)', font: { size: 12 } }
        }
      },
      scales: {
        x: {
          stacked: false,
          ticks: { color: 'rgba(154,167,184,0.9)', maxRotation: 45 },
          grid: { color: 'rgba(255,255,255,0.06)' }
        },
        y: {
          ticks: { color: 'rgba(154,167,184,0.9)' },
          grid: { color: 'rgba(255,255,255,0.06)' }
        }
      }
    }
  });
}

async function loadProviderBreakdown() {
  const tbody = el('providerBody');
  if (!tbody) return;
  try {
    const r = await fetch(apiUrl('/api/usage/daily?days=365'));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const { rows } = await r.json();

    const byProvider = {};
    for (const row of rows) {
      const prov = row.provider || 'unknown';
      if (!byProvider[prov]) byProvider[prov] = { calls: 0, cost: 0 };
      byProvider[prov].calls += row.call_count || 0;
      byProvider[prov].cost += row.cost_usd || 0;
    }

    const sorted = Object.entries(byProvider).sort((a, b) => b[1].cost - a[1].cost);
    if (sorted.length === 0) {
      tbody.innerHTML = '<tr><td colspan="3" class="table-empty">No data.</td></tr>';
      return;
    }
    tbody.innerHTML = sorted.map(([prov, d]) =>
      `<tr><td>${escapeHtml(prov)}</td><td>${fmtInt(d.calls)}</td><td>${fmtCost(d.cost)}</td></tr>`
    ).join('');
  } catch {
    tbody.innerHTML = '<tr><td colspan="3" class="table-empty">Failed to load.</td></tr>';
  }
}

async function loadSourceBreakdown() {
  const tbody = el('sourceBody');
  if (!tbody) return;
  try {
    const r = await fetch(apiUrl('/api/usage/daily?days=365'));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const { rows } = await r.json();

    const bySource = {};
    for (const row of rows) {
      const src = row.source || 'unknown';
      if (!bySource[src]) bySource[src] = { calls: 0, cost: 0 };
      bySource[src].calls += row.call_count || 0;
      bySource[src].cost += row.cost_usd || 0;
    }

    const sorted = Object.entries(bySource).sort((a, b) => b[1].cost - a[1].cost);
    if (sorted.length === 0) {
      tbody.innerHTML = '<tr><td colspan="3" class="table-empty">No data.</td></tr>';
      return;
    }
    tbody.innerHTML = sorted.map(([src, d]) =>
      `<tr><td>${escapeHtml(src)}</td><td>${fmtInt(d.calls)}</td><td>${fmtCost(d.cost)}</td></tr>`
    ).join('');
  } catch {
    tbody.innerHTML = '<tr><td colspan="3" class="table-empty">Failed to load.</td></tr>';
  }
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
  const d = el('liveFeedDebug');
  if (!d) return;
  d.style.display = 'block';
  d.textContent = msg;
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
        tbody.innerHTML = `<tr><td colspan="8" class="table-empty">No API calls recorded yet. Route traffic through CacheLens to see activity here.</td></tr>`;
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
      const inputTok = row.input_tokens || 0;
      const denom = cacheRead + inputTok;
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
    el('deepDiveBody').innerHTML = `<tr><td colspan="10" class="table-empty">Failed: ${escapeHtml(err.message)}</td></tr>`;
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
    tbody.innerHTML = '<tr><td colspan="10" class="table-empty">No records match the filters.</td></tr>';
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
    tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No Anthropic rows in current filter.</td></tr>';
    return;
  }

  tbody.innerHTML = anthropicRows.map(row => {
    const hitPct = row.cache_hit_pct != null ? row.cache_hit_pct.toFixed(1) + '%' : '—';
    const hitClass = row.cache_hit_pct >= 50 ? 'hit-good' : row.cache_hit_pct >= 20 ? 'hit-ok' : 'hit-poor';
    return `
      <tr>
        <td>${escapeHtml(row.date || '—')}</td>
        <td class="mono-sm">${escapeHtml(row.model || '—')}</td>
        <td>${escapeHtml(row.source || '—')}</td>
        <td>${fmtInt(row.input_tokens)}</td>
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
          borderColor: 'rgba(46,233,166,0.85)',
          backgroundColor: 'rgba(46,233,166,0.15)',
          fill: true,
          tension: 0.3,
          pointRadius: 3,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: 'rgba(231,237,246,0.85)' } }
        },
        scales: {
          x: { ticks: { color: 'rgba(154,167,184,0.9)', maxRotation: 45 }, grid: { color: 'rgba(255,255,255,0.06)' } },
          y: { min: 0, max: 100, ticks: { color: 'rgba(154,167,184,0.9)', callback: v => v + '%' }, grid: { color: 'rgba(255,255,255,0.06)' } }
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

// Populate model dropdowns when Deep Dive page loads
const origLoadDeepDive = loadDeepDive;
loadDeepDive = async function() {
  await origLoadDeepDive();
  populateModelDropdowns();
};

// Start on dashboard — must be last so all let/const variables are initialized
switchPage('dashboard');
