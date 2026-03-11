const input = document.getElementById('input');
const analyzeBtn = document.getElementById('analyze');
const resultsView = document.getElementById('resultsView');
const inputView = document.getElementById('inputView');
const results = document.getElementById('results');

input.addEventListener('input', () => {
  analyzeBtn.disabled = (input.value.trim().length === 0);
});

document.getElementById('newAnalysis').addEventListener('click', () => {
  resultsView.classList.add('hidden');
  inputView.classList.remove('hidden');
});

document.getElementById('loadExample').addEventListener('click', async () => {
  const resp = await fetch('/static/example.json');
  input.value = await resp.text();
  analyzeBtn.disabled = false;
});

analyzeBtn.addEventListener('click', async () => {
  analyzeBtn.disabled = true;
  analyzeBtn.textContent = 'Analyzing…';
  try {
    const resp = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: input.value })
    });
    const data = await resp.json();
    results.textContent = JSON.stringify(data, null, 2);
    inputView.classList.add('hidden');
    resultsView.classList.remove('hidden');
  } finally {
    analyzeBtn.textContent = 'Analyze →';
    analyzeBtn.disabled = false;
  }
});
