# Coinfiliate Automation

Automated cookie harvester for the Coinfiliate Partner Shop dashboard. Logs in, syncs Partner Shops + their affiliate links, opens each affiliate URL in an isolated browser context to capture tracking cookies, decides the primary cookie via heuristics + LLM fallback, and writes the result back into the Edit Selected Partner Shop Links modal.

Built on Playwright (async) + SQLite + Typer. LLM is pluggable: OpenAI or Gemini.

**Status:** Live-DOM calibrated against the production Coinfiliate dashboard (Clerk auth + Radix UI). End-to-end verified Draft → Published on a real account.

## Architecture

Three-phase pipeline; each phase reads/writes SQLite (`state.db`):

```
sync       Pull Partner Shops + affiliate links into SQLite (status='pending')
harvest    For each pending shop: open affiliate URL, capture cookies + redirect
           chain, run heuristic -> LLM decision, write harvest row
           (status='harvested' or 'needs_review' or 'failed')
writeback  For each harvested shop: drive the Edit modal, save, verify
           (status='writeback_done')
run        sync && harvest && writeback
doctor     Print all DOM selectors used (sanity check)
review     List 'needs_review' shops for manual decision
```

See `docs/superpowers/specs/2026-04-24-coinfiliate-automation-design.md` for the full design.

## Setup

```bash
python -m venv venv
source venv/bin/activate           # macOS/Linux
venv\Scripts\Activate.ps1          # Windows PowerShell

pip install -r requirements.txt
playwright install chromium
cp .env.example .env               # then edit:
#   COINFILIATE_EMAIL=...
#   COINFILIATE_PASS=...
#   OPENAI_API_KEY=...   (or GEMINI_API_KEY=... if you set llm.provider: gemini)
```

## Usage

```bash
python main.py run                  # full pipeline
python main.py sync                 # phase 1 only
python main.py harvest              # phase 2 only
python main.py writeback            # phase 3 only
python main.py writeback --dry-run  # walk modal but Cancel instead of Save
python main.py doctor               # print selectors
python main.py review               # list needs_review shops

python main.py run --limit 5        # cap to 5 shops per batch
```

## Configuration

`config.yaml` (checked in with sensible defaults):

```yaml
networks: ["flexoffers"]            # extend with awin/impact/cj when ready
sync:
  target_status: "Draft"            # only persist shops whose status badge contains this; "" = any
  max_pages: 80                     # cap on Partner Shop list pages walked per network
  page_size: 100                    # bulk-sync page size
runner:
  max_shops_per_batch: 50
  max_concurrency: 4
harvest:
  review_threshold: 0.0             # 0.0 = fully autonomous; raise to gate low-confidence
writeback:
  verify_after_save: true
llm:
  provider: "openai"                # or "gemini"
  model: "gpt-4o-mini"
```

The Partner Shop list paginates client-side at 10 rows/page. `sync.max_pages` caps how many pages we walk per network; `sync.target_status` lets us scope to `Draft` so shops already `Published` are left alone — that's where the automation must NOT touch.

## Tests

```bash
pytest                              # everything (unit + integration)
pytest -m unit                      # fast tests only (no browser)
pytest -m integration               # browser-driven tests against local fixture servers
```

## Project layout

```
coinfiliate/
  cli.py              # Typer commands
  config.py           # pydantic-settings loader (yaml + env)
  store.py            # SQLite schema + CRUD
  selectors.py        # all DOM selectors centralized
  browser.py          # Playwright session + context factory
  sync.py             # phase 1: login, partner-shop sync, affiliate-link sync
  harvest.py          # phase 2: signal collection, decision, store
  writeback.py        # phase 3: drive Edit modal, save, verify
  decision.py         # heuristic matchers + decide() orchestrator
  models.py           # HarvestContext / HarvestDecision dataclasses
  llm/
    base.py           # CookieAnalyzer Protocol
    prompt.py         # system prompt + user prompt builder
    openai_client.py
    gemini_client.py  # uses google-genai SDK
  logging_setup.py    # structlog config
schema.sql            # SQLite DDL
config.yaml           # runtime defaults
tests/
  unit/               # fast, no browser
  integration/        # Playwright + local aiohttp fixture servers
  fixtures/           # fake servers + saved HTML
docs/
  superpowers/specs/  # design spec
  superpowers/plans/  # implementation plan
  tutorial-images/    # extracted from the source DOCX
```

## Status & known limitations

- Selectors are mapped to the **live** Coinfiliate dashboard (Clerk auth + Radix UI) and verified end-to-end Draft → Published on a real account. Any future drift will require updates in `coinfiliate/selectors.py` and the relevant phase module. Run `python main.py doctor` to inspect the current selector set.
- The 3 fake-server integration tests for the sync phase are currently `skip`ped — their fixtures predate the Radix-shaped DOM and need to be rebuilt. The writeback fixture test still runs. For new live behavior, smoke-test with `python main.py run --limit 1 --dry-run` before a full batch.
- **Coinfiliate quirk — bulk-edit modal does not persist `Tracking Cookie Names` list field.** The modal Save commits `Primary Tracking Cookie Name`, `Checkout Domains`, and `Tracking Cookie Domains` correctly, but the per-cookie name list is dropped on reload. Verified to be a backend quirk, not a Playwright/selector issue. Acceptable for our use case since the Primary field is what drives tracking.
- Step 8 of the original tutorial ("simulate checkout") is not automated — we use the eTLD+1 of the landed page as both `Checkout Domains` and `Tracking Cookie Domains`. The LLM fallback can override this when it spots distinct tracker subdomains.
- Login uses a persistent `user_data_dir` at `.playwright/coinfiliate/` so reruns within the day skip re-login. Delete that directory to force re-auth.

## License

Private / internal automation. Do not distribute credentials.
