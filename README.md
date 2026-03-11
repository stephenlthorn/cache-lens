# CacheLens

Local-first CLI + web UI to analyze AI prompts, prompt chains, and agent traces for token waste and cacheability.

- **Runs locally** (no backend)
- **Deterministic** (rules-based; no LLM calls)
- **Actionable output** (waste breakdown + restructuring suggestions)

See: [`PRODUCT_SPEC.md`](./PRODUCT_SPEC.md)

## Quickstart (dev)

```bash
cd CacheLens
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# CLI
cachelens analyze examples/agent-loop.json
cachelens analyze examples/agent-loop.json --format json | jq .cacheability_score

# Web UI
cachelens ui
```

## CLI

```bash
cachelens analyze <file|-> [--format human|json] [--suggestions] [--score-only] [--sponsor-reminder]
cachelens ui [--port 8420] [--no-open]
```

## Inputs

CacheLens auto-detects:

- Raw prompt text
- `{ "messages": [ {"role": "system|user|assistant", "content": "..."} ] }`
- `{ "calls": [ { "messages": [...], "model": "...", "usage": {...} } ] }`

## Sponsorship

If CacheLens helps you ship faster or cut token spend, consider sponsoring.

To enable the post-run CLI reminder:

- One-shot: `cachelens analyze … --sponsor-reminder`
- Or via env var: `export CACHELENS_SPONSOR_REMINDER=1`

https://github.com/sponsors/stephenlthorn

### Sponsor tiers (suggested)

- **$5/month — supporter**
- **$25/month — power user**
- **$200/month — company sponsor**

### Sponsors

Thanks to these companies supporting CacheLens:

- *(your name here)*

## Status

This repo is early-stage. MVP is under active development.
