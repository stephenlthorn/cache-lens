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

// Start on dashboard
switchPage('dashboard');

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
      body: JSON.stringify({ input: input.value })
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
  connectLiveFeed();

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
    const rows = await r.json();
    renderTokenChart(rows);
  } catch {
    // silently leave chart empty
  }
}

function renderTokenChart(rows) {
  const canvas = el('tokenChart');
  if (!canvas) return;

  const labels = rows.map(r => r.date || r.day || '');
  const inputData = rows.map(r => r.input_tokens || 0);
  const cacheData = rows.map(r => r.cache_read_tokens || 0);
  const outputData = rows.map(r => r.output_tokens || 0);

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
    const rows = await r.json();

    const byProvider = {};
    for (const row of rows) {
      const prov = row.provider || 'unknown';
      if (!byProvider[prov]) byProvider[prov] = { calls: 0, cost: 0 };
      byProvider[prov].calls += row.calls || 0;
      byProvider[prov].cost += row.total_cost_usd || 0;
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
    const rows = await r.json();

    const bySource = {};
    for (const row of rows) {
      const src = row.source || 'unknown';
      if (!bySource[src]) bySource[src] = { calls: 0, cost: 0 };
      bySource[src].calls += row.calls || 0;
      bySource[src].cost += row.total_cost_usd || 0;
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
    let rows = await r.json();

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
      <td>${fmtInt(row.calls)}</td>
      <td>${fmtInt(row.input_tokens)}</td>
      <td>${fmtInt(row.cache_read_tokens)}</td>
      <td>${row.cache_hit_pct != null ? row.cache_hit_pct.toFixed(1) + '%' : '—'}</td>
      <td>${fmtInt(row.output_tokens)}</td>
      <td>${fmtCost(row.total_cost_usd)}</td>
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
    const recs = await r.json();

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
