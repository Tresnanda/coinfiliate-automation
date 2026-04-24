# Coinfiliate Automation — Design Spec

**Date:** 2026-04-24
**Status:** approved for planning
**Source-of-truth tutorial:** `Cookie Affiliate_ENG.docx` (extracted screenshots in `docs/tutorial-images/`)

## 1. Goal

Replace the manual 9-step Coinfiliate Partner Shop cookie-configuration workflow with a fully automated Python pipeline that:

1. Syncs Partner Shops + per-shop affiliate links from the Coinfiliate admin UI.
2. For each unconfigured shop, opens its affiliate URL in an isolated browser context, captures cookies + redirect chain + tracker domains, and decides the tracking cookie + domains via a heuristic → LLM decision pipeline.
3. Drives the Coinfiliate "Edit Selected Partner Shop Links" modal to write back the decision (Primary Tracking Cookie Name, Tracking Cookie Names, Checkout Domains, Tracking Cookie Domains), enables the Published toggle, and saves.

The system runs unattended on a local Windows machine and is designed so future networks (Awin, Impact, CJ, etc.) and higher volumes (hundreds of shops per run) slot in without redesign.

## 2. Non-goals

- Live simulated checkout (add-to-cart → checkout flow). Default behavior uses the eTLD+1 of the landed page for both `Checkout Domains` and `Tracking Cookie Domains`. If the LLM fallback identifies a distinct checkout subdomain, those win.
- Browser extension / MV3. Headless Playwright only.
- Multi-tenant / cloud deployment. Local Windows, local SQLite. Future work.
- Real-time / webhook-driven operation. Batch-oriented; cron or manual trigger.

## 3. Assumptions & constraints

- The Coinfiliate admin DOM is stable enough to bind to text/role selectors (e.g. `button:has-text("Sync Partner Shop")`) rather than fragile `input[name="..."]` paths.
- All ~62 affiliate links inside a single shop share the same tracking cookie config (confirmed by the "Select All → Edit" bulk-edit flow in the tutorial). We only need to open ONE affiliate URL per shop to decide cookies.
- Shops may use network-native cookies (FlexOffers `fobs_*`, Impact `IR_*`, Awin `awc`, etc.), first-party tracking IDs (Klaviyo `__kla_id`, Shopify `_shopify_y`, GA `_ga`), or both. The decision pipeline must handle both classes.
- One run processes at most `runner.max_shops_per_batch` shops (default 50) to cap blast radius.
- LLM usage is rate-limited and costed per shop; heuristics resolve the majority of cases without an LLM call.

## 4. Operating mode

Default: **fully autonomous**. `harvest.review_threshold` defaults to `0.0`, which means any harvest result with a non-null `primary_cookie_name` advances to `status='harvested'` and is written back without gating. Flip to `0.5` to route low-confidence results into `status='needs_review'` instead, which the writeback phase will skip.

HITL is supported but not the default: run `coinfiliate harvest` then `coinfiliate review` (interactive CLI or CSV export) before `coinfiliate writeback`.

## 5. Architecture

Staged pipeline. SQLite as job queue + audit store. Three primary sub-commands plus helpers:

```
coinfiliate sync        pull Partner Shops + affiliate links into SQLite
coinfiliate harvest     for each pending shop: browser → decide → store decision
coinfiliate writeback   for each harvested shop: drive Edit modal → save → verify
coinfiliate run         = sync && harvest && writeback
coinfiliate doctor      selector validation against a live throwaway shop
coinfiliate review      HITL queue for needs_review shops
```

Exit codes: `0` all done, `1` some shops failed (batch continued), `2` setup/auth/infra failure (batch aborted).

## 6. Data model

SQLite single file at `./state.db`. Schema bootstrapped from `schema.sql` on first run. No migration tooling in v1.

```sql
CREATE TABLE shop (
    id                   INTEGER PRIMARY KEY,
    coinfiliate_id       TEXT UNIQUE NOT NULL,
    name                 TEXT NOT NULL,
    network              TEXT NOT NULL,
    advertiser_id        TEXT,
    website_url          TEXT,
    edit_url             TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending',
                         -- pending | harvested | writeback_done | needs_review | failed
    last_error           TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE affiliate_link (
    id                   INTEGER PRIMARY KEY,
    shop_id              INTEGER NOT NULL REFERENCES shop(id) ON DELETE CASCADE,
    link_id              TEXT NOT NULL,
    name                 TEXT,
    affiliate_url        TEXT NOT NULL,
    is_harvest_source    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(shop_id, link_id)
);

CREATE TABLE harvest (
    id                           INTEGER PRIMARY KEY,
    shop_id                      INTEGER NOT NULL REFERENCES shop(id) ON DELETE CASCADE,
    attempted_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    final_url                    TEXT,
    final_etld1                  TEXT,
    cookies_json                 TEXT NOT NULL,
    redirect_chain_json          TEXT NOT NULL,
    tracker_domains_json         TEXT NOT NULL,
    primary_cookie_name          TEXT,
    tracking_cookie_names_json   TEXT,
    checkout_domains_json        TEXT,
    tracking_cookie_domains_json TEXT,
    decision_source              TEXT NOT NULL,   -- 'heuristic' | 'llm' | 'manual'
    confidence                   REAL,
    llm_rationale                TEXT,
    ok                           INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_shop_status   ON shop(status);
CREATE INDEX idx_harvest_shop  ON harvest(shop_id, attempted_at DESC);
```

Status transitions are one-way and validated in code:

```
pending ──► harvested ──► writeback_done
   │            │
   └─► failed   └─► needs_review ──► harvested (on re-run) ──► writeback_done
```

## 7. Sync phase

Sub-command: `coinfiliate sync`.

1. Login to `coinfiliate.com/login` using `.env` credentials. Persist session in `user_data_dir=./.playwright/coinfiliate` so reruns the same day skip login.
2. For each network in `config.networks` (default `["flexoffers"]`):
   a. Navigate to `/admin/partner-shop`.
   b. Click `Sync Partner Shop`; in the modal select Network, Page=1, PageSize=100, tick all selectable fields, click `Sync Now`. Wait for the modal to close / spinner to clear (max 60s).
3. Scrape the Partner Shop table rows: name, network, status badge, `⋮ → Edit` URL. Upsert into `shop` by `coinfiliate_id`. New rows get `status='pending'`; existing rows keep their current status.
4. For each shop with `status='pending'`:
   a. Navigate to `shop.edit_url`.
   b. Click the `Affiliate Links` tab, then `Sync Affiliate Link`; fill inner modal identically (Network, Page=1, PageSize=100, all fields, Sync Now).
   c. Expand the resulting list; scrape each row for `link_id`, `name`, `affiliate_url`. Upsert into `affiliate_link`.
   d. Pick the first link (by DOM position) and set `is_harvest_source=1`; unset the flag on the others for this shop.
5. Commit. Shops remain `pending` until harvest runs.

Idempotent: rerunning `sync` never duplicates rows and never moves a non-`pending` shop back to `pending`.

## 8. Harvest phase

Sub-command: `coinfiliate harvest`. Processes `shop.status='pending'` rows.

### 8.1 Per-shop pipeline

```
1. Create a FRESH BrowserContext (no cookies leaking across shops; "incognito" semantics)
2. Attach recorders before navigation:
     - context.on("response")   → URLs + domains + status codes
     - page.on("framenavigated")→ redirect chain
     - page.on("request")       → 3p tracker requests
3. page.goto(harvest_source_affiliate_url, wait_until="domcontentloaded")
   page.wait_for_load_state("networkidle", timeout=15s)
4. Auto-accept consent banner; try in order:
     ["Accept", "Allow All", "Accept All", "I Accept", "Agree", "Got it",
      "Alle akzeptieren", "Accepter", "Aceptar", "同意", "同意する"]
   Click first visible match; wait 2s for cookies to write.
5. Harvest signals:
     cookies         = await context.cookies()
     final_url       = page.url
     final_etld1     = tldextract(final_url).registered_domain
     tracker_domains = set of response hosts != final_etld1 matching tracker regex
     redirect_chain  = response.url where status in 301..308
6. Decide (see 8.2).
7. Derive domain fields (see 8.3).
8. Insert `harvest` row with ALL raw signals + decision.
9. Update shop.status per 8.4.
10. context.close()
```

### 8.2 Decision pipeline

Three-step cascade:

- **Strict heuristic** — cookie name matches one of:
  `["pjnclick", "irclick", "ir_", "awc", "fobs_", "_ck_", "_wg_", "cj_source", "cje", "impact", "partnerize_", "rakuten_", "click_id"]`
  → `confidence=1.0`, `decision_source='heuristic'`.

- **Loose heuristic** — cookie name matches first-party tracking patterns:
  `["__kla_id", "_shopify_y", "_ga", "ajs_anonymous_id", "_fbp", "_gcl_aw"]`
  → `confidence=0.6`, `decision_source='heuristic'`.

- **LLM fallback** — triggered when both heuristics miss OR when strict returns multiple candidates:
  Input: `{shop_name, network, final_url, cookies, tracker_domains, redirect_chain}`.
  Output schema: `{primary_cookie_name, tracking_cookie_names[], checkout_domains[], tracking_cookie_domains[], confidence (0..1), rationale}`.
  `decision_source='llm'`, `confidence = llm.confidence`.

### 8.3 Domain derivation

- `checkout_domains = [final_etld1]`
- `tracking_cookie_domains = [final_etld1]`

If the LLM fallback path runs and produces domain lists, prefer LLM values (it may spot a distinct tracker subdomain).

### 8.4 Shop status update

`ok=1` is set on the `harvest` row iff the decision pipeline returned a non-null `primary_cookie_name` (any source: strict heuristic, loose heuristic, or LLM). `ok=0` means no candidate cookie was identified at all.

- `ok=1 AND confidence >= harvest.review_threshold` → `shop.status='harvested'`
- `ok=1 AND confidence <  harvest.review_threshold` → `shop.status='needs_review'`
- `ok=0`                                             → `shop.status='needs_review'` (manual decision required)
- Exception at any step                              → `shop.status='failed'`, `last_error=str(e)`

### 8.5 Concurrency

`asyncio.Semaphore(max_concurrency)` (default 4). One shared Playwright browser; one fresh `BrowserContext` per shop. Shops processed in ascending `shop.id` order.

### 8.6 LLM provider abstraction

```python
class CookieAnalyzer(Protocol):
    async def analyze(self, ctx: HarvestContext) -> HarvestDecision: ...
```

Implementations under `coinfiliate/llm/`: `openai_client.py`, `gemini_client.py`. Config selects one by name:

```yaml
llm:
  provider: "openai"         # or "gemini"
  model: "gpt-4o-mini"       # or "gemini-2.5-flash"
  max_retries: 3
  timeout_seconds: 30
```

Prompt (stored in `coinfiliate/llm/prompt.py` so it's reviewable and version-controlled):

> You are identifying the primary affiliate-tracking cookie on `{shop_name}` ({network}). The browser landed on `{final_url}`. Cookies that were set: `{cookies_json}`. Third-party tracker domains seen in the redirect chain: `{tracker_domains}`. Redirect chain: `{redirect_chain}`.
>
> Return strict JSON: `{"primary_cookie_name": string, "tracking_cookie_names": string[], "checkout_domains": string[], "tracking_cookie_domains": string[], "confidence": number (0..1), "rationale": string}`.
>
> Prefer cookies that carry a per-click unique value over cookies that look like session IDs. Prefer network-native cookies over first-party trackers when both are present.

### 8.7 Anti-bot and stability

- Realistic User-Agent + `viewport={width:1280,height:800}`.
- Randomized 500–2000 ms jitter between shops (configurable).
- Coinfiliate login context persisted in `user_data_dir`; harvest contexts always fresh.
- Screenshot of each landing page saved to `logs/harvest/{shop_id}_{timestamp}.png` for audit.

## 9. Writeback phase

Sub-command: `coinfiliate writeback`. Processes `shop.status='harvested'` rows.

### 9.1 Per-shop flow

```
 1. Load latest harvest row for shop.
 2. Navigate to shop.edit_url.
 3. Scroll to Affiliate Links tab.
 4. If affiliate-links list is empty (session stale), rerun inner sync here (defensive).
 5. Click "Select All"; verify header reads "Selected N items".
 6. Click "Selected Data (N)" dropdown → "Edit".
    Wait for modal titled "Edit Selected Partner Shop Links".
 7. Inside modal:
     a. Flip Published toggle ON (idempotent — read state first).
     b. Fill "Primary Tracking Cookie Name" with harvest.primary_cookie_name.
     c. For each name in harvest.tracking_cookie_names:
          click "+ Add" next to "Tracking Cookie Names"
          type name into newly-appeared input.
     d. For each domain in harvest.checkout_domains:
          click "+ Add" next to "Checkout Domains"; type into new input.
     e. For each domain in harvest.tracking_cookie_domains:
          click "+ Add" next to "Tracking Cookie Domains"; type into new input.
 8. Click "Save Changes"; wait for modal to close.
 9. On main Edit page, scroll to bottom.
10. Click the "Published" button (bottom-right).
11. Click "Update" button; wait for success toast / redirect.
12. Verify: reload shop edit page, assert "Primary Tracking Cookie Name" field
    value equals harvest.primary_cookie_name. On mismatch → status='failed'.
13. Mark shop.status='writeback_done'.
```

### 9.2 Selectors

All selectors centralized in `coinfiliate/selectors.py` keyed by semantic name. Text/role selectors preferred over DOM-structure selectors:

```python
SELECTORS = {
    "login.email":                      'input[type="email"]',
    "login.password":                   'input[type="password"]',
    "login.submit":                     'button[type="submit"]',
    "shoplist.sync_btn":                'button:has-text("Sync Partner Shop")',
    "shoplist.row":                     'table tbody tr',
    "shoplist.edit_action":             'text=Edit',
    "editshop.tab_affiliate_links":     'button:has-text("Affiliate Links")',
    "editshop.sync_affiliate_btn":      'button:has-text("Sync Affiliate Link")',
    "editshop.select_all":              'label:has-text("Select All") input[type="checkbox"]',
    "editshop.selected_data_dd":        'button:has-text("Selected Data")',
    "editshop.edit_selected":           'div[role="menu"] >> text=Edit',
    "modal.root":                       'div[role="dialog"]:has-text("Edit Selected Partner Shop Links")',
    "modal.published_toggle":           'role=switch[name="Published"]',
    "modal.primary_cookie_name":        'label:has-text("Primary Tracking Cookie Name") + * input',
    "modal.checkout_domains_add":       'div:has-text("Checkout Domains") >> button:has-text("Add")',
    "modal.checkout_domain_input_last": 'div:has-text("Checkout Domains") >> input >> nth=-1',
    "modal.tracking_names_add":         'div:has-text("Tracking Cookie Names") >> button:has-text("Add")',
    "modal.tracking_names_input_last":  'div:has-text("Tracking Cookie Names") >> input >> nth=-1',
    "modal.tracking_domains_add":       'div:has-text("Tracking Cookie Domains") >> button:has-text("Add")',
    "modal.tracking_domains_input_last":'div:has-text("Tracking Cookie Domains") >> input >> nth=-1',
    "modal.save_changes":               'button:has-text("Save Changes")',
    "editshop.published_btn":           'button:has-text("Published"):not([aria-expanded])',
    "editshop.update_btn":              'button:has-text("Update")',
}
```

### 9.3 Dry-run mode

`coinfiliate writeback --dry-run` walks the UI and fills all fields but clicks Cancel in place of Save Changes/Update. Used for `doctor` and smoke tests.

## 10. Top-level orchestration

`coinfiliate run` = `sync` → `harvest` → `writeback`. Continues on per-shop failures; aborts on setup failures (login, SQLite init, missing LLM key).

Always writes a summary to stdout and `logs/run_{timestamp}.json`:

```
Synced 47 shops, harvested 42, writeback_done 40, needs_review 2, failed 5
```

## 11. Error handling and retries

| Class | Examples | Retry policy |
|---|---|---|
| Transient network | timeout, reset, 5xx | Exponential backoff, max 3 per shop |
| Selector miss | element not found / not visible for 10s | No retry — fail shop, keep batch going |
| Auth | 401, redirect to `/login` mid-run | Re-login once, retry once; second failure aborts batch (exit 2) |
| LLM | rate-limit, 429, 5xx, malformed JSON | Backoff, max 3; then mark `needs_review` |
| Verification mismatch (writeback 9.1 step 12) | cookie didn't stick | No retry — `status='failed'`, screenshot |
| Unknown | any uncaught exception | Catch at shop-loop boundary, record, continue |

## 12. Logging and observability

- `structlog` with JSON output to `logs/run_{timestamp}.log`.
- Per-shop fields: `shop_id`, `shop_name`, `phase`, `step`, `duration_ms`, `outcome`.
- Screenshots under `logs/{phase}/{shop_id}_{step}_{timestamp}.png` on error + once per landing page.
- Final stdout summary as in §10.

**Not logged at INFO level:** email, password, Coinfiliate session cookie values, raw cookie values, full LLM prompts. These go to `logs/debug_{timestamp}.log` with 7-day rotation.

## 13. Configuration surface

`config.yaml` (checked into repo with defaults):

```yaml
networks: ["flexoffers"]

sync:
  page: 1
  page_size: 100
  selectable_fields: "all"

runner:
  max_shops_per_batch: 50
  max_concurrency: 4
  inter_shop_jitter_ms: [500, 2000]

harvest:
  networkidle_timeout_seconds: 15
  consent_wait_ms: 2000
  review_threshold: 0.0   # 0.0 = fully autonomous; set to 0.5 to gate low-confidence into needs_review

writeback:
  verify_after_save: true

llm:
  provider: "openai"      # or "gemini"
  model: "gpt-4o-mini"
  max_retries: 3
  timeout_seconds: 30

logging:
  level: "INFO"
  debug_log_retention_days: 7
```

`.env` (gitignored):
```
COINFILIATE_EMAIL=...
COINFILIATE_PASS=...
OPENAI_API_KEY=...         # or
GEMINI_API_KEY=...
```

## 14. Testing plan

**Unit (fast, no browser)** — `tests/unit/`:
- `test_heuristic.py` — strict + loose cookie-name matching edge cases.
- `test_domain.py` — eTLD+1 extraction from real affiliate URLs (`track.flexlinkspro.com/g.ashx?...`).
- `test_decision.py` — given fixture `cookies_json` + `redirect_chain_json`, verify the pipeline picks the right candidate without a real LLM call (LLM mocked).
- `test_llm_client.py` — mocked LLM response → correct `HarvestDecision`; malformed JSON handled.
- `test_store.py` — SQLite upserts idempotent; status transitions enforced.

**Integration (slow, Playwright against local fixture server)** — `tests/integration/`:
- `test_harvest_e2e.py` — local `aiohttp` server serves a fake merchant that sets `__kla_id` + has a consent banner. Run full harvest. Assert `harvest` row matches expected.
- `test_writeback_selectors.py` — local server serves saved HTML fixture of the real Coinfiliate edit page. Run writeback against it; assert each selector resolves.

**Smoke (manual, live Coinfiliate)**:
- `coinfiliate doctor` — validates every selector against a single throwaway shop.
- `coinfiliate run --limit 1 --dry-run` — full pipeline against one shop, Cancel instead of Save.

Coverage target: 80%+ on non-Playwright code; integration tests use local fixtures so CI is deterministic.

## 15. Repo layout

```
coinfiliate-automation/
├── main.py                          # thin entry: from coinfiliate.cli import main; main()
├── coinfiliate/
│   ├── __init__.py
│   ├── cli.py                       # Typer: sync | harvest | writeback | run | doctor | review
│   ├── config.py                    # pydantic-settings: config.yaml + .env
│   ├── store.py                     # SQLite ops, schema bootstrap
│   ├── selectors.py                 # single source of truth for DOM selectors
│   ├── browser.py                   # Playwright session + context factory
│   ├── sync.py                      # sync phase
│   ├── harvest.py                   # harvest phase
│   ├── writeback.py                 # writeback phase
│   ├── decision.py                  # heuristic pipeline + LLM dispatch
│   ├── llm/
│   │   ├── base.py                  # CookieAnalyzer Protocol
│   │   ├── prompt.py
│   │   ├── openai_client.py
│   │   └── gemini_client.py
│   └── logging_setup.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/                    # saved HTML + JSON fixtures
├── docs/
│   ├── superpowers/specs/           # this doc
│   └── tutorial-images/             # extracted from the source DOCX
├── schema.sql
├── config.yaml
├── .env.example
├── requirements.txt
└── README.md
```

`main.py` at the root stays as a thin shim so `python main.py` continues to work.

## 16. Dependencies

```
playwright
pydantic
pydantic-settings
typer
structlog
tldextract
aiosqlite
openai
google-generativeai
pyyaml
python-dotenv
pytest
pytest-asyncio
aiohttp          # test fixture server only
```

## 17. Out of scope for v1 (explicit)

- Checkout-flow simulation (add-to-cart → checkout walk). Default to eTLD+1; revisit if real-world data shows frequent domain divergence.
- Networks beyond FlexOffers. Config-driven so adding Awin/Impact/CJ is mechanical.
- Multi-machine / distributed workers. SQLite + local only.
- UI / dashboard. CLI + JSON summary only.
- Scheduled runs. User invokes manually or via Windows Task Scheduler.

## 18. Success criteria

- `coinfiliate run --limit 5 --dry-run` completes end-to-end against the live dashboard with no errors.
- `coinfiliate doctor` reports all selectors green.
- Against a batch of 20 real pending shops: `>=90%` reach `writeback_done`; the rest land in `needs_review` or `failed` with a captured screenshot and `last_error`.
- Unit test suite: `>=80%` coverage on non-Playwright code.
- Running the pipeline does not leak cookies between shops (verified by fixture test).
