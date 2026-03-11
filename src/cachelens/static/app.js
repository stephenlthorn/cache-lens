const el = (id) => document.getElementById(id);

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

  // top score card
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

  // optimized structure tabs
  setOptimizedOutput();

  renderRepeats(res.repeated_blocks);

  // show results
  resultsView.classList.remove('hidden');
  inputView.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

function setOptimizedOutput() {
  if (!lastResult) return;

  const tabButtons = document.querySelectorAll('.tab');
  tabButtons.forEach(b => b.classList.toggle('tab--active', b.dataset.tab === activeTab));

  if (activeTab === 'raw') {
    optimizedOut.textContent = lastResult.raw_content ? lastResult.raw_content : (lastResult && lastResult.input_type ? '(raw input not available)' : '');
    // In our API we do not return raw_content; fall back to input box.
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
    const resp = await fetch('/api/analyze', {
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
  const resp = await fetch('/static/example.json');
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

// Events
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

// Drag and drop
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

// Tabs
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

// init
updateInputMeta();
setLoading(false);
