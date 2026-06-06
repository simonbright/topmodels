# Top Models — MotoMetrics 2.x Data Pipeline

Standalone, re-runnable pipeline that ranks used-car models by buyer demand and content opportunity, then enriches top models with **sourced** facts for the growth content backlog and app profile priorities.

**Phase 1 (current):** free sources only — NHTSA, Google Trends (pytrends), first-party telemetry, curated industry reports.

## Quick start

```bash
cd topmodels
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Optional: point FIRSTPARTY_EXPORT_PATH at a MotoMetrics telemetry export JSON

topmodels run --phase 1
```

Outputs land in `out/`:

- `top_models.csv` / `top_models.json` — ranked models with per-signal score breakdown
- `backlog.md` — content briefs with sourced facts (human review required before publish)

## CLI

```bash
topmodels run --phase 1          # Free MVP (default)
topmodels run --phase 1 --refresh   # Bypass HTTP/trends cache
topmodels run --phase 1 --top-n 10
topmodels run --phase 1 --dry-run     # Score only, no writes
```

## Configuration

| File | Purpose |
|------|---------|
| `config.yaml` | Weights, `top_n`, source toggles, budget caps (committed) |
| `.env` | API keys — never commit |

### Scoring weights (`config.yaml`)

```
priority = w_search * norm(search)
         + w_listings * norm(listings)
         + w_community * norm(community)
         + w_first_party * norm(first_party_scans)
         + w_problems * norm(problem_volume)
```

Missing signals → **0** (never fabricated). Each rank includes an explanation string.

## Sources (Phase 1)

| Source | Signal | Cost |
|--------|--------|------|
| NHTSA vPIC | Taxonomy / alias resolution | Free |
| NHTSA recalls, complaints, investigations | Problem volume + enrichment | Free |
| Google Trends (pytrends) | Relative search interest | Free (unofficial) |
| First-party telemetry export | Scan counts per model | Free |
| Curated published reports | Sanity-check ranking | Free (attributed) |

**Phase 2:** Reddit (PRAW), Google Keyword Planner — stubs included, disabled in config.

**Phase 3:** MarketCheck (paid) — gated by `budget.max_paid_calls` + API key.

## First-party telemetry

Export from MotoMetrics (`telemetry.exportAll()` or `/api/telemetry-export`) to JSON and set:

```bash
FIRSTPARTY_EXPORT_PATH=/path/to/export.json
```

Demo/simulator runs are excluded by default (`firstparty.exclude_demo: true`).

## Guardrails

- Official APIs and published findings only — **no marketplace scraping**
- Raw responses cached under `cache/` for reproducibility
- Every fact carries a `source`; enrichment fields are tagged
- LLM-generated copy is **not** auto-published — backlog items note `needs_review`
- Paid connectors require explicit config + budget cap

## Tests

```bash
pytest
```

## Repo layout

```
topmodels/
  config.yaml
  data/curated_reports.json
  topmodels/
    taxonomy.py
    connectors/{nhtsa,trends,firstparty,reports,...}.py
    normalize.py
    score.py
    enrich.py
    pipeline.py
    cli.py
  cache/          # gitignored raw responses
  out/            # gitignored outputs
  tests/
```
