# Harvest Active-Checkout + Per-Link Retry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the harvest phase actively navigate Landing → Product → Add-to-Cart → Checkout on every shop, capture cookies + checkout URL there, and retry through all of a shop's affiliate links until one succeeds.

**Architecture:** Replace the passive `collect_signals` flow inside `harvest_shop` with an LLM-driven active-navigation flow (`attempt_link`) that runs once per affiliate link. The orchestrator iterates a shop's links in priority order until one returns `AttemptSuccess` or all are exhausted. The decision pipeline gains two new context fields (`checkout_url`, `checkout_etld1`) and computes `tracking_cookie_domains` from the captured primary cookie's own `domain` attribute.

**Tech Stack:** Python 3.9+, asyncio, Playwright (Chromium), aiosqlite, OpenAI gpt-4o-mini (element finder), google-genai, pytest with `pytest-asyncio` and aiohttp fixture server.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `schema.sql` | Modify | Add 3 new columns on `harvest` (`checkout_url`, `checkout_etld1`, `attempted_link_id`). |
| `coinfiliate/store.py` | Modify | Idempotent migration in `init()`; extend `insert_harvest`; add `list_affiliate_links_ordered`. |
| `coinfiliate/models.py` | Modify | Extend `HarvestContext` with `checkout_url` and `checkout_etld1`. |
| `coinfiliate/decision.py` | Modify | Add `cookie_domain_etld1`; rewrite `decide` to use checkout_etld1 + cookie's own domain for the two new field assignments. |
| `coinfiliate/llm/base.py` | Modify | Add `ElementFinder` protocol (alongside `CookieAnalyzer`). |
| `coinfiliate/llm/prompt.py` | Modify | Mention `checkout_url`/`checkout_etld1` in user prompt. Add element-finder system prompt + builder. |
| `coinfiliate/llm/openai_client.py` | Modify | Add `find_element` method on the existing class so one instance satisfies both protocols. |
| `coinfiliate/llm/gemini_client.py` | Modify | Same. |
| `coinfiliate/harvest.py` | Rewrite | New `attempt_link`, `is_error_page`, `collect_clickable_candidates`, `AttemptSuccess`/`AttemptFailure` types; rewritten `harvest_shop` retry loop. |
| `tests/fixtures/fake_merchant_server.py` | Modify | Add `/pdp`, `/cart`, `/checkouts/cn/<id>` routes plus a `/404` route; happy-path fixture sets cookie at `/checkouts/...`. |
| `tests/unit/test_decision.py` | Modify | Cases for new field rules. |
| `tests/unit/test_cookie_domain.py` | Create | Unit tests for `cookie_domain_etld1`. |
| `tests/unit/test_attempt_result.py` | Create | Cases for `is_error_page` and result types. |
| `tests/integration/test_active_checkout.py` | Create | Happy-path active-flow + 404-retry tests with stub `ElementFinder`. |

---

## Tasks

### Task 1: Schema migration + harvest insert signature

Add 3 columns to `harvest` (idempotent), extend `Store.insert_harvest` to accept and persist them.

**Files:**
- Modify: `schema.sql`
- Modify: `coinfiliate/store.py:27-32` (init), `coinfiliate/store.py:111-134` (insert_harvest)
- Test: `tests/unit/test_store_migration.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_store_migration.py`:

```python
from __future__ import annotations

import pytest
from coinfiliate.store import Store


@pytest.mark.unit
async def test_init_adds_new_harvest_columns_to_existing_db(tmp_path):
    # Simulate a pre-migration DB: create harvest WITHOUT the new columns,
    # then run init() and assert the columns now exist.
    db = tmp_path / "old.db"
    import aiosqlite
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(
            """
            CREATE TABLE shop (id INTEGER PRIMARY KEY, coinfiliate_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, network TEXT NOT NULL, advertiser_id TEXT, website_url TEXT,
                edit_url TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE harvest (id INTEGER PRIMARY KEY,
                shop_id INTEGER NOT NULL REFERENCES shop(id) ON DELETE CASCADE,
                attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                final_url TEXT, final_etld1 TEXT,
                cookies_json TEXT NOT NULL, redirect_chain_json TEXT NOT NULL,
                tracker_domains_json TEXT NOT NULL, primary_cookie_name TEXT,
                tracking_cookie_names_json TEXT, checkout_domains_json TEXT,
                tracking_cookie_domains_json TEXT, decision_source TEXT NOT NULL,
                confidence REAL, llm_rationale TEXT, ok INTEGER NOT NULL DEFAULT 0);
            """
        )
        await conn.commit()

    store = Store(db)
    await store.init()
    try:
        cur = await store._conn.execute("PRAGMA table_info(harvest)")
        cols = {row["name"] for row in await cur.fetchall()}
        assert {"checkout_url", "checkout_etld1", "attempted_link_id"} <= cols
    finally:
        await store.close()


@pytest.mark.unit
async def test_init_is_idempotent_on_already_migrated_db(tmp_path):
    store = Store(tmp_path / "fresh.db")
    await store.init()
    await store.close()
    # Running init() a second time on a fresh DB must not raise (column already exists).
    store2 = Store(tmp_path / "fresh.db")
    await store2.init()
    await store2.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_store_migration.py -v`
Expected: FAIL — columns don't exist.

- [ ] **Step 3: Update `schema.sql`**

Replace the `harvest` CREATE TABLE block with:

```sql
CREATE TABLE IF NOT EXISTS harvest (
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
    decision_source              TEXT NOT NULL,
    confidence                   REAL,
    llm_rationale                TEXT,
    ok                           INTEGER NOT NULL DEFAULT 0,
    checkout_url                 TEXT,
    checkout_etld1               TEXT,
    attempted_link_id            TEXT
);
```

The `IF NOT EXISTS` already protects fresh DBs. Pre-migration DBs need the ALTER pass below.

- [ ] **Step 4: Add migration helper in `Store`**

In `coinfiliate/store.py`, modify the `init` method to call a new `_migrate` helper after `executescript`:

```python
async def init(self) -> None:
    self._conn = await aiosqlite.connect(self.db_path)
    self._conn.row_factory = aiosqlite.Row
    await self._conn.execute("PRAGMA foreign_keys = ON")
    await self._conn.executescript(SCHEMA_PATH.read_text())
    await self._migrate()
    await self._conn.commit()

async def _migrate(self) -> None:
    """Apply additive schema changes for DBs created before columns were added.

    PRAGMA-guarded ALTERs so this stays idempotent regardless of SQLite version.
    """
    cur = await self._conn.execute("PRAGMA table_info(harvest)")
    cols = {row["name"] for row in await cur.fetchall()}
    additions = [
        ("checkout_url", "TEXT"),
        ("checkout_etld1", "TEXT"),
        ("attempted_link_id", "TEXT"),
    ]
    for col, typ in additions:
        if col not in cols:
            await self._conn.execute(f"ALTER TABLE harvest ADD COLUMN {col} {typ}")
```

- [ ] **Step 5: Extend `insert_harvest` signature**

Replace the `insert_harvest` method body in `coinfiliate/store.py` with:

```python
async def insert_harvest(self, *, shop_id: int, final_url: Optional[str], final_etld1: Optional[str],
                         cookies: Sequence[dict], redirect_chain: Sequence[str],
                         tracker_domains: Sequence[str], primary_cookie_name: Optional[str],
                         tracking_cookie_names: Optional[Sequence[str]],
                         checkout_domains: Optional[Sequence[str]],
                         tracking_cookie_domains: Optional[Sequence[str]],
                         decision_source: str, confidence: Optional[float],
                         llm_rationale: Optional[str], ok: bool,
                         checkout_url: Optional[str] = None,
                         checkout_etld1: Optional[str] = None,
                         attempted_link_id: Optional[str] = None) -> int:
    cur = await self._conn.execute(
        """INSERT INTO harvest
             (shop_id, final_url, final_etld1, cookies_json, redirect_chain_json, tracker_domains_json,
              primary_cookie_name, tracking_cookie_names_json, checkout_domains_json,
              tracking_cookie_domains_json, decision_source, confidence, llm_rationale, ok,
              checkout_url, checkout_etld1, attempted_link_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (shop_id, final_url, final_etld1,
         json.dumps(list(cookies)), json.dumps(list(redirect_chain)), json.dumps(list(tracker_domains)),
         primary_cookie_name,
         json.dumps(list(tracking_cookie_names)) if tracking_cookie_names is not None else None,
         json.dumps(list(checkout_domains)) if checkout_domains is not None else None,
         json.dumps(list(tracking_cookie_domains)) if tracking_cookie_domains is not None else None,
         decision_source, confidence, llm_rationale, 1 if ok else 0,
         checkout_url, checkout_etld1, attempted_link_id),
    )
    await self._conn.commit()
    return cur.lastrowid
```

- [ ] **Step 6: Run tests, confirm pass**

Run: `pytest tests/unit/test_store_migration.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add schema.sql coinfiliate/store.py tests/unit/test_store_migration.py
git commit -m "feat(store): add checkout_url/checkout_etld1/attempted_link_id columns

PRAGMA-guarded migration in Store.init() keeps reapply-on-startup idempotent
regardless of SQLite version. insert_harvest now accepts the three new
fields as keyword args (default None for back-compat with callers that
don't have checkout data yet)."
```

---

### Task 2: `Store.list_affiliate_links_ordered`

Helper that returns a shop's affiliate links with the current `is_harvest_source` first, then by `id` ascending — the iteration order for retries.

**Files:**
- Modify: `coinfiliate/store.py` (add new method)
- Test: `tests/unit/test_store_migration.py` (add test there to keep store tests in one file)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_store_migration.py`:

```python
@pytest.mark.unit
async def test_list_affiliate_links_ordered_puts_harvest_source_first(tmp_path):
    store = Store(tmp_path / "t.db")
    await store.init()
    sid = await store.upsert_shop(coinfiliate_id="x", name="S", network="n",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    await store.upsert_affiliate_link(sid, link_id="L1", name="a", affiliate_url="u1")
    await store.upsert_affiliate_link(sid, link_id="L2", name="b", affiliate_url="u2")
    await store.upsert_affiliate_link(sid, link_id="L3", name="c", affiliate_url="u3")
    await store.mark_harvest_source(sid, "L2")

    rows = await store.list_affiliate_links_ordered(sid)
    assert [r["link_id"] for r in rows] == ["L2", "L1", "L3"]
    await store.close()


@pytest.mark.unit
async def test_list_affiliate_links_ordered_empty_shop(tmp_path):
    store = Store(tmp_path / "t.db")
    await store.init()
    sid = await store.upsert_shop(coinfiliate_id="x", name="S", network="n",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    rows = await store.list_affiliate_links_ordered(sid)
    assert rows == []
    await store.close()
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/unit/test_store_migration.py::test_list_affiliate_links_ordered_puts_harvest_source_first -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'list_affiliate_links_ordered'`.

- [ ] **Step 3: Implement**

Add to `coinfiliate/store.py` after `list_affiliate_links`:

```python
async def list_affiliate_links_ordered(self, shop_id: int) -> list:
    """Return affiliate links ordered for retry: is_harvest_source first, then by id."""
    cur = await self._conn.execute(
        "SELECT * FROM affiliate_link WHERE shop_id=? "
        "ORDER BY is_harvest_source DESC, id ASC",
        (shop_id,),
    )
    return [dict(r) for r in await cur.fetchall()]
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/unit/test_store_migration.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/store.py tests/unit/test_store_migration.py
git commit -m "feat(store): add list_affiliate_links_ordered for retry iteration"
```

---

### Task 3: `cookie_domain_etld1` helper

Pure function that extracts the eTLD+1 of a cookie's `domain` attribute, normalizing the leading dot.

**Files:**
- Modify: `coinfiliate/decision.py`
- Test: `tests/unit/test_cookie_domain.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cookie_domain.py`:

```python
from __future__ import annotations

import pytest
from coinfiliate.decision import cookie_domain_etld1


@pytest.mark.unit
def test_strips_leading_dot_and_extracts_etld1():
    assert cookie_domain_etld1({"domain": ".example.com"}) == "example.com"


@pytest.mark.unit
def test_handles_subdomain():
    assert cookie_domain_etld1({"domain": "checkout.shop.example.co.uk"}) == "example.co.uk"


@pytest.mark.unit
def test_returns_none_when_no_domain_key():
    assert cookie_domain_etld1({"name": "x", "value": "v"}) is None


@pytest.mark.unit
def test_returns_none_for_empty_domain():
    assert cookie_domain_etld1({"domain": ""}) is None


@pytest.mark.unit
def test_returns_none_for_localhost_or_ip():
    # tldextract returns suffix='' for these — we treat them as no eTLD+1.
    assert cookie_domain_etld1({"domain": "localhost"}) is None
    assert cookie_domain_etld1({"domain": "127.0.0.1"}) is None
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/unit/test_cookie_domain.py -v`
Expected: FAIL — `ImportError: cannot import name 'cookie_domain_etld1' from 'coinfiliate.decision'`.

- [ ] **Step 3: Implement**

Add to `coinfiliate/decision.py` directly after `extract_etld1`:

```python
def cookie_domain_etld1(cookie: dict) -> Optional[str]:
    """Return eTLD+1 of a cookie's `domain` attribute, or None if absent/non-domain.

    Strips a leading dot (Set-Cookie domains often start with '.', meaning the
    cookie applies to subdomains). Returns None for localhost/IP/missing —
    these aren't real eTLD+1 scopes.
    """
    raw = (cookie.get("domain") or "").lstrip(".").strip()
    if not raw:
        return None
    ext = tldextract.extract(raw)
    if not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/unit/test_cookie_domain.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/decision.py tests/unit/test_cookie_domain.py
git commit -m "feat(decision): add cookie_domain_etld1 helper"
```

---

### Task 4: Extend `HarvestContext` + rewrite `decide()` for new fields

`HarvestContext` gains `checkout_url` and `checkout_etld1`. `decide()` uses `checkout_etld1` for `checkout_domains`, and the primary cookie's own `domain` (with checkout fallback) for `tracking_cookie_domains`.

**Files:**
- Modify: `coinfiliate/models.py:7-15` (HarvestContext)
- Modify: `coinfiliate/decision.py:30-71` (decide)
- Modify: `coinfiliate/llm/prompt.py` (build_user_prompt — pass new fields)
- Test: `tests/unit/test_decision.py` (update + add cases)

- [ ] **Step 1: Update existing decision tests + add new ones**

Replace the `_ctx` helper at the top of `tests/unit/test_decision.py`:

```python
def _ctx(cookies, *, checkout_url="https://s.com/checkouts/cn/abc", checkout_etld1="s.com"):
    return HarvestContext(shop_name="S", network="flexoffers",
                          final_url="https://s.com/", final_etld1="s.com",
                          cookies=cookies, redirect_chain=[], tracker_domains=[],
                          checkout_url=checkout_url, checkout_etld1=checkout_etld1)
```

Append new cases at the end of `tests/unit/test_decision.py`:

```python
async def test_strict_match_uses_checkout_etld1_for_checkout_domains():
    llm = MagicMock(); llm.analyze = AsyncMock()
    ctx = _ctx(
        [{"name": "pjnclick", "value": "v", "domain": ".s.com"}],
        checkout_url="https://s.com/checkouts/cn/x", checkout_etld1="s.com",
    )
    d = await decide(ctx, llm=llm)
    assert d.checkout_domains == ["s.com"]


async def test_strict_match_uses_cookie_domain_for_tracking_cookie_domains():
    llm = MagicMock(); llm.analyze = AsyncMock()
    # Cookie scoped to .merchant.com, checkout on pay.shopify.com — different eTLD+1s.
    ctx = _ctx(
        [{"name": "pjnclick", "value": "v", "domain": ".merchant.com"}],
        checkout_url="https://pay.shopify.com/x", checkout_etld1="shopify.com",
    )
    d = await decide(ctx, llm=llm)
    assert "merchant.com" in d.tracking_cookie_domains
    assert "shopify.com" in d.tracking_cookie_domains


async def test_strict_match_falls_back_to_checkout_etld1_when_cookie_has_no_domain():
    llm = MagicMock(); llm.analyze = AsyncMock()
    ctx = _ctx(
        [{"name": "pjnclick", "value": "v"}],  # host-only cookie, no domain attr
        checkout_url="https://s.com/checkouts/cn/x", checkout_etld1="s.com",
    )
    d = await decide(ctx, llm=llm)
    assert d.tracking_cookie_domains == ["s.com"]
```

- [ ] **Step 2: Run tests, verify the new ones fail**

Run: `pytest tests/unit/test_decision.py -v`
Expected: New tests FAIL (`HarvestContext` doesn't accept `checkout_url` yet).

- [ ] **Step 3: Extend `HarvestContext`**

In `coinfiliate/models.py`, change the `HarvestContext` dataclass to:

```python
@dataclass(frozen=True)
class HarvestContext:
    """All signals collected from the browser; input to the decision pipeline."""
    shop_name: str
    network: str
    final_url: str
    final_etld1: str
    cookies: List[dict] = field(default_factory=list)
    redirect_chain: List[str] = field(default_factory=list)
    tracker_domains: List[str] = field(default_factory=list)
    # URL bar at the end of the active checkout flow, and its eTLD+1.
    # Same as final_url/final_etld1 if the active flow wasn't run (e.g. unit
    # tests passing a passive context).
    checkout_url: Optional[str] = None
    checkout_etld1: Optional[str] = None
```

(Add `Optional` to the imports if not already present: `from typing import List, Optional`.)

- [ ] **Step 4: Rewrite `decide()` to use the new fields**

Replace the `decide` body in `coinfiliate/decision.py`:

```python
def _checkout_domain(ctx: HarvestContext) -> str:
    """Return the eTLD+1 to use for checkout_domains.

    Falls back to landing eTLD+1 only when the active flow didn't run.
    """
    return ctx.checkout_etld1 or ctx.final_etld1


def _tracking_cookie_domains(primary_cookie: dict, ctx: HarvestContext) -> List[str]:
    """Compose tracking_cookie_domains from the cookie's own scope + checkout.

    Use the primary cookie's own `domain` attribute (eTLD+1-normalized). If it
    differs from the checkout eTLD+1, include both — the field is list-valued
    and Coinfiliate's matcher checks any-match-wins.
    """
    checkout = _checkout_domain(ctx)
    cookie_etld1 = cookie_domain_etld1(primary_cookie)
    if cookie_etld1 is None:
        return [checkout]
    if cookie_etld1 == checkout:
        return [checkout]
    return [cookie_etld1, checkout]


async def decide(ctx: HarvestContext, *, llm: CookieAnalyzer) -> HarvestDecision:
    # 1. strict heuristic — only network-native affiliate cookies match here.
    strict = strict_match(ctx.cookies)
    if strict:
        return HarvestDecision(
            primary_cookie_name=strict["name"],
            tracking_cookie_names=[strict["name"]],
            checkout_domains=[_checkout_domain(ctx)],
            tracking_cookie_domains=_tracking_cookie_domains(strict, ctx),
            decision_source="heuristic",
            confidence=1.0,
            rationale=None,
        )
    # 2. LLM fallback — generic analytics cookies (_ga, _fbp, __kla_id, …) are
    # not affiliate trackers, so we let the model decide instead of guessing.
    try:
        return await llm.analyze(ctx)
    except Exception as e:
        return HarvestDecision(
            primary_cookie_name=None,
            tracking_cookie_names=[],
            checkout_domains=[_checkout_domain(ctx)],
            tracking_cookie_domains=[_checkout_domain(ctx)],
            decision_source="llm",
            confidence=0.0,
            rationale=f"LLM failed: {e}",
        )
```

- [ ] **Step 5: Update LLM user prompt to mention the new fields**

In `coinfiliate/llm/prompt.py`, change `build_user_prompt`:

```python
def build_user_prompt(ctx: HarvestContext) -> str:
    return (
        f"Shop: {ctx.shop_name} (network={ctx.network})\n"
        f"Final landing URL: {ctx.final_url}\n"
        f"Landing eTLD+1: {ctx.final_etld1}\n"
        f"Checkout URL: {ctx.checkout_url or '(not captured)'}\n"
        f"Checkout eTLD+1: {ctx.checkout_etld1 or '(not captured)'}\n\n"
        f"Cookies set on the landing page and through checkout:\n{json.dumps(ctx.cookies, indent=2)}\n\n"
        f"Third-party tracker domains seen in the redirect chain:\n{json.dumps(ctx.tracker_domains, indent=2)}\n\n"
        f"Redirect chain:\n{json.dumps(ctx.redirect_chain, indent=2)}\n\n"
        f"Respond with strict JSON matching this schema:\n{json.dumps(SCHEMA, indent=2)}"
    )
```

- [ ] **Step 6: Run all decision tests, verify pass**

Run: `pytest tests/unit/test_decision.py tests/unit/test_heuristic.py -v`
Expected: ALL PASS (existing 4 + new 3 = 7 tests in test_decision.py, plus 3 in test_heuristic.py).

- [ ] **Step 7: Commit**

```bash
git add coinfiliate/models.py coinfiliate/decision.py coinfiliate/llm/prompt.py tests/unit/test_decision.py
git commit -m "feat(decision): use checkout eTLD+1 + cookie's own domain for new fields

HarvestContext gains checkout_url and checkout_etld1. checkout_domains
now reflects the actual checkout page; tracking_cookie_domains uses the
captured primary cookie's authoritative scope (its domain attribute),
adding the checkout eTLD+1 as a second entry only when they differ."
```

---

### Task 5: `is_error_page` helper

Cheap deterministic 404/error landing detection — runs before any LLM call.

**Files:**
- Modify: `coinfiliate/harvest.py` (add helper)
- Test: `tests/unit/test_attempt_result.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_attempt_result.py`:

```python
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from coinfiliate.harvest import is_error_page


def _page(*, status=200, title="OK", h1=""):
    page = MagicMock()
    page.evaluate = AsyncMock(return_value={"title": title, "h1": h1})
    return page, status


@pytest.mark.unit
async def test_is_error_page_when_status_404():
    page, status = _page(status=404, title="Page Not Found")
    assert await is_error_page(page, response_status=status) is True


@pytest.mark.unit
async def test_is_error_page_when_title_says_404():
    page, status = _page(status=200, title="404 - this product does not exist")
    assert await is_error_page(page, response_status=status) is True


@pytest.mark.unit
async def test_is_error_page_when_h1_says_not_found():
    page, status = _page(status=200, title="Cool Store", h1="Page not found")
    assert await is_error_page(page, response_status=status) is True


@pytest.mark.unit
async def test_is_not_error_page_on_normal_page():
    page, status = _page(status=200, title="Buy Roses | EnjoyFlowers", h1="Designer Bouquet")
    assert await is_error_page(page, response_status=status) is False
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/unit/test_attempt_result.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_error_page'`.

- [ ] **Step 3: Implement**

Add to `coinfiliate/harvest.py` (near the top, after the existing imports/constants):

```python
import re

_ERROR_TITLE_PATTERN = re.compile(r"\b(404|not found|error|page does not exist)\b", re.I)


async def is_error_page(page, *, response_status: int | None) -> bool:
    """Return True if the landing page looks like a 404 / error / 'no product'.

    Three signals checked, any of which fires:
      - HTTP status >= 400 on the navigation response
      - <title> matches a 404/not-found/error pattern
      - <h1> matches the same pattern
    """
    if response_status is not None and response_status >= 400:
        return True
    info = await page.evaluate(
        """() => ({
            title: document.title || '',
            h1: (document.querySelector('h1')?.innerText || '').slice(0, 200),
        })"""
    )
    if _ERROR_TITLE_PATTERN.search(info.get("title") or ""):
        return True
    if _ERROR_TITLE_PATTERN.search(info.get("h1") or ""):
        return True
    return False
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/unit/test_attempt_result.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/harvest.py tests/unit/test_attempt_result.py
git commit -m "feat(harvest): add is_error_page deterministic 404 check"
```

---

### Task 6: `collect_clickable_candidates` helper

DOM-walker that returns clickable elements with stable selectors. The element-finder LLM picks an index, we click via the pre-computed selector.

**Files:**
- Modify: `coinfiliate/harvest.py`
- Test: `tests/unit/test_attempt_result.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_attempt_result.py`:

```python
@pytest.mark.unit
async def test_collect_clickable_candidates_signature():
    """Smoke: function exists and is callable with a Page-like mock.

    Real DOM behavior is exercised in the integration test using the fake
    merchant server. Here we just ensure the function shape (it returns a
    list of dicts after evaluating page JS).
    """
    from coinfiliate.harvest import collect_clickable_candidates
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"idx": 0, "tag": "a", "text": "Shop now", "selector": "#hero a"},
        {"idx": 1, "tag": "button", "text": "Add to Cart", "selector": "form button"},
    ])
    out = await collect_clickable_candidates(page)
    assert len(out) == 2
    assert out[0]["selector"] == "#hero a"
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/unit/test_attempt_result.py::test_collect_clickable_candidates_signature -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

Add to `coinfiliate/harvest.py` (right after `is_error_page`):

```python
_CLICKABLE_JS = r"""
(() => {
    // Select clickable elements: links, buttons, role=link/button.
    // Compute a short, stable CSS selector for each by walking up to a
    // unique ancestor or using nth-of-type as a last resort.
    const NODES = Array.from(document.querySelectorAll(
        'a[href], button, [role="button"], [role="link"], input[type="submit"], input[type="button"]'
    )).filter(el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return false;          // hidden
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.display === 'none') return false;
        return true;
    });

    function selectorFor(el) {
        if (el.id) return '#' + CSS.escape(el.id);
        // Build a path with tag + nth-of-type, capped at 6 segments.
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && parts.length < 6) {
            let part = cur.tagName.toLowerCase();
            const parent = cur.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
                if (siblings.length > 1) {
                    const i = siblings.indexOf(cur) + 1;
                    part += `:nth-of-type(${i})`;
                }
            }
            parts.unshift(part);
            if (cur.id) { parts[0] = '#' + CSS.escape(cur.id); break; }
            cur = parent;
        }
        return parts.join(' > ');
    }

    return NODES.slice(0, 80).map((el, idx) => ({
        idx,
        tag: el.tagName.toLowerCase(),
        text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 120),
        href: el.getAttribute('href') || null,
        aria_label: el.getAttribute('aria-label') || null,
        selector: selectorFor(el),
    }));
})()
"""


async def collect_clickable_candidates(page) -> list[dict]:
    """Return up to 80 visible clickable elements with text + a CSS selector.

    The element-finder LLM picks an `idx`; the caller maps back to `selector`.
    Capped at 80 to keep the LLM prompt small.
    """
    return await page.evaluate(_CLICKABLE_JS)
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/unit/test_attempt_result.py -v`
Expected: PASS (5 tests in this file).

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/harvest.py tests/unit/test_attempt_result.py
git commit -m "feat(harvest): add collect_clickable_candidates DOM walker"
```

---

### Task 7: `ElementFinder` protocol + OpenAI implementation

Add a parallel protocol and extend `OpenAICookieAnalyzer` so one instance satisfies both `CookieAnalyzer` and `ElementFinder`.

**Files:**
- Modify: `coinfiliate/llm/base.py`
- Modify: `coinfiliate/llm/prompt.py` (add element-finder system + builder)
- Modify: `coinfiliate/llm/openai_client.py`
- Test: `tests/unit/test_element_finder.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_element_finder.py`:

```python
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from coinfiliate.llm.openai_client import OpenAICookieAnalyzer


def _fake_openai_response(content: dict):
    """Return a MagicMock shaped like an openai chat.completions response."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(content)
    return resp


@pytest.mark.unit
async def test_find_element_returns_idx_when_llm_picks_one():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response({"idx": 2, "confidence": 0.9})
    )
    finder = OpenAICookieAnalyzer(client=client, model="m", max_retries=1, timeout_seconds=10)

    candidates = [
        {"idx": 0, "tag": "a", "text": "Home", "selector": "nav a:nth-of-type(1)"},
        {"idx": 1, "tag": "a", "text": "About", "selector": "nav a:nth-of-type(2)"},
        {"idx": 2, "tag": "a", "text": "Roses Bouquet $30", "selector": "main a:nth-of-type(1)"},
    ]
    out = await finder.find_element(
        candidates=candidates, goal="navigate to a product detail page",
        url="https://shop.example.com/",
    )
    assert out == 2


@pytest.mark.unit
async def test_find_element_returns_none_when_idx_out_of_range():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response({"idx": 99, "confidence": 0.9})
    )
    finder = OpenAICookieAnalyzer(client=client, model="m", max_retries=1, timeout_seconds=10)
    out = await finder.find_element(
        candidates=[{"idx": 0, "tag": "a", "text": "x", "selector": "a"}],
        goal="g", url="https://x/",
    )
    assert out is None


@pytest.mark.unit
async def test_find_element_returns_none_when_idx_is_null():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response({"idx": None, "confidence": 0.0})
    )
    finder = OpenAICookieAnalyzer(client=client, model="m", max_retries=1, timeout_seconds=10)
    out = await finder.find_element(candidates=[], goal="g", url="https://x/")
    assert out is None
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/unit/test_element_finder.py -v`
Expected: FAIL — `find_element` doesn't exist on `OpenAICookieAnalyzer`.

- [ ] **Step 3: Add `ElementFinder` protocol**

Replace the contents of `coinfiliate/llm/base.py` with:

```python
from __future__ import annotations

from typing import List, Optional, Protocol
from coinfiliate.models import HarvestContext, HarvestDecision


class CookieAnalyzer(Protocol):
    async def analyze(self, ctx: HarvestContext) -> HarvestDecision: ...


class ElementFinder(Protocol):
    async def find_element(
        self, *,
        candidates: List[dict],
        goal: str,
        url: str,
    ) -> Optional[int]:
        """Return idx of the candidate that best satisfies goal, or None."""
        ...
```

- [ ] **Step 4: Add element-finder prompts**

Append to `coinfiliate/llm/prompt.py`:

```python
ELEMENT_FINDER_SYSTEM = (
    "You help drive a browser through an e-commerce checkout flow. "
    "You receive a list of clickable elements (each with idx, tag, visible text, "
    "and href). Pick the SINGLE element that best satisfies the goal. "
    "If no element clearly satisfies the goal, return idx=null. "
    "Output strict JSON only: {\"idx\": <int or null>, \"confidence\": 0..1}. "
    "No prose, no markdown fences."
)


def build_element_finder_prompt(*, candidates: list, goal: str, url: str) -> str:
    return (
        f"Goal: {goal}\n"
        f"Current URL: {url}\n\n"
        f"Clickable elements (pick one idx):\n"
        f"{json.dumps(candidates, indent=2)}\n\n"
        f"Respond with strict JSON: {{\"idx\": <int or null>, \"confidence\": 0..1}}"
    )
```

- [ ] **Step 5: Add `find_element` method to OpenAI client**

In `coinfiliate/llm/openai_client.py`, add this method to `OpenAICookieAnalyzer` (don't touch the existing `analyze`):

```python
async def find_element(
    self, *, candidates: list, goal: str, url: str,
) -> Optional[int]:
    """Ask the LLM which candidate idx satisfies goal. None = no match.

    No retries: a single LLM miss falls through to the caller's one-shot
    re-snapshot retry, not a transport-level retry. Errors return None
    rather than raising — element-finding is best-effort.
    """
    from coinfiliate.llm.prompt import ELEMENT_FINDER_SYSTEM, build_element_finder_prompt
    try:
        resp = await self._client.chat.completions.create(
            model=self._model,
            timeout=self._timeout,
            messages=[
                {"role": "system", "content": ELEMENT_FINDER_SYSTEM},
                {"role": "user", "content": build_element_finder_prompt(
                    candidates=candidates, goal=goal, url=url,
                )},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        idx = data.get("idx")
        if idx is None:
            return None
        idx = int(idx)
        if not (0 <= idx < len(candidates)):
            return None
        return idx
    except Exception:
        return None
```

Also add at the top of `coinfiliate/llm/openai_client.py`:

```python
from typing import Optional
```

- [ ] **Step 6: Run, verify pass**

Run: `pytest tests/unit/test_element_finder.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add coinfiliate/llm/base.py coinfiliate/llm/prompt.py coinfiliate/llm/openai_client.py tests/unit/test_element_finder.py
git commit -m "feat(llm): add ElementFinder protocol + OpenAI find_element

OpenAICookieAnalyzer now satisfies both CookieAnalyzer and ElementFinder;
the harvest pipeline can pass one instance and use both. Errors return
None — element-finding is best-effort, the caller retries with a
re-snapshot before giving up."
```

---

### Task 8: Gemini `find_element` implementation

Same surface as OpenAI, on `GeminiCookieAnalyzer`.

**Files:**
- Modify: `coinfiliate/llm/gemini_client.py`
- Test: `tests/unit/test_element_finder.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_element_finder.py`:

```python
@pytest.mark.unit
async def test_gemini_find_element_returns_idx():
    from coinfiliate.llm.gemini_client import GeminiCookieAnalyzer

    fake_resp = MagicMock()
    fake_resp.text = '{"idx": 1, "confidence": 0.8}'
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=fake_resp)

    finder = GeminiCookieAnalyzer(client=client, model="m", max_retries=1)
    out = await finder.find_element(
        candidates=[
            {"idx": 0, "tag": "a", "text": "x", "selector": "a"},
            {"idx": 1, "tag": "button", "text": "Add to Cart", "selector": "form button"},
        ],
        goal="add to cart", url="https://x/",
    )
    assert out == 1


@pytest.mark.unit
async def test_gemini_find_element_handles_fenced_json():
    """Gemini sometimes wraps JSON in ```json ... ``` fences; the impl must extract."""
    from coinfiliate.llm.gemini_client import GeminiCookieAnalyzer

    fake_resp = MagicMock()
    fake_resp.text = '```json\n{"idx": 0, "confidence": 0.9}\n```'
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=fake_resp)
    finder = GeminiCookieAnalyzer(client=client, model="m", max_retries=1)
    out = await finder.find_element(
        candidates=[{"idx": 0, "tag": "a", "text": "x", "selector": "a"}],
        goal="g", url="https://x/",
    )
    assert out == 0
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/unit/test_element_finder.py -v`
Expected: 2 new tests FAIL — `find_element` doesn't exist on `GeminiCookieAnalyzer`.

- [ ] **Step 3: Implement on Gemini client**

Add to `coinfiliate/llm/gemini_client.py`, both at top imports:

```python
from typing import Optional
```

And as a new method on `GeminiCookieAnalyzer`:

```python
async def find_element(
    self, *, candidates: list, goal: str, url: str,
) -> Optional[int]:
    """Ask Gemini which candidate idx satisfies goal. None = no match.

    Mirrors OpenAICookieAnalyzer.find_element. Single-shot, errors → None.
    """
    from coinfiliate.llm.prompt import ELEMENT_FINDER_SYSTEM, build_element_finder_prompt
    prompt = ELEMENT_FINDER_SYSTEM + "\n\n" + build_element_finder_prompt(
        candidates=candidates, goal=goal, url=url,
    )
    try:
        resp = await self._client.aio.models.generate_content(
            model=self._model, contents=prompt,
        )
        text = resp.text.strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0) if m else text)
        idx = data.get("idx")
        if idx is None:
            return None
        idx = int(idx)
        if not (0 <= idx < len(candidates)):
            return None
        return idx
    except Exception:
        return None
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/unit/test_element_finder.py -v`
Expected: PASS (5 tests total).

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/llm/gemini_client.py tests/unit/test_element_finder.py
git commit -m "feat(llm): add Gemini find_element parallel to OpenAI"
```

---

### Task 9: `AttemptResult` types + `attempt_link` core

The active-navigation flow per affiliate link. Returns `AttemptSuccess` or `AttemptFailure(kind=...)`.

**Files:**
- Modify: `coinfiliate/harvest.py` (add types + `attempt_link`)

- [ ] **Step 1: Add the types and helper signatures (no test yet — exercised in integration)**

Add to `coinfiliate/harvest.py` directly above the existing `collect_signals` function:

```python
from dataclasses import dataclass
from typing import List, Optional, Union


@dataclass(frozen=True)
class AttemptSuccess:
    landing_url: str           # URL after consent + redirects (before product click)
    checkout_url: str          # URL bar at the end of the active flow
    cookies: List[dict]
    redirect_chain: List[str]
    tracker_domains: List[str]


@dataclass(frozen=True)
class AttemptFailure:
    kind: str                  # "Error404" | "NoProduct" | "NoCart" | "NoCheckout" | "Error"
    detail: Optional[str] = None


AttemptResult = Union[AttemptSuccess, AttemptFailure]
```

- [ ] **Step 2: Implement `attempt_link`**

Add to `coinfiliate/harvest.py` after the `AttemptResult` definitions:

```python
async def _click_one(page, candidates: list, *, idx: Optional[int],
                     post_click_wait_ms: int = 1500) -> bool:
    """Click candidates[idx]; return True on apparent success."""
    if idx is None or not (0 <= idx < len(candidates)):
        return False
    selector = candidates[idx]["selector"]
    try:
        loc = page.locator(selector).first
        await loc.click(timeout=10_000)
        await page.wait_for_timeout(post_click_wait_ms)
        return True
    except Exception:
        return False


async def _drive_step(page, *, finder, goal: str, url: str) -> bool:
    """One element-finder step with one re-snapshot retry on click failure."""
    candidates = await collect_clickable_candidates(page)
    idx = await finder.find_element(candidates=candidates, goal=goal, url=url)
    if await _click_one(page, candidates, idx=idx):
        return True
    # One re-snapshot retry: the page may have updated since the snapshot.
    candidates = await collect_clickable_candidates(page)
    idx = await finder.find_element(candidates=candidates, goal=goal, url=page.url)
    return await _click_one(page, candidates, idx=idx)


async def attempt_link(
    page, context, affiliate_url: str, *,
    finder,
    consent_texts: Optional[List[str]] = None,
    consent_wait_ms: int = 2000,
    networkidle_timeout_s: int = 15,
) -> AttemptResult:
    """Drive Landing → Product → Add-to-Cart → Checkout for one affiliate link.

    Args:
      page: a fresh Playwright Page.
      context: the BrowserContext that owns `page` (we attach a response listener).
      affiliate_url: the link to follow.
      finder: an ElementFinder.

    Returns AttemptSuccess on full traversal, or AttemptFailure with `kind`
    set to one of "Error404"/"NoProduct"/"NoCart"/"NoCheckout"/"Error".
    """
    consent_texts = consent_texts or DEFAULT_CONSENT_TEXTS
    response_urls: List[str] = []
    redirect_chain: List[str] = []

    def _on_response(resp):
        response_urls.append(resp.url)
        if 300 <= resp.status < 400:
            redirect_chain.append(resp.url)

    context.on("response", _on_response)

    try:
        nav = await page.goto(affiliate_url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle",
                                           timeout=networkidle_timeout_s * 1000)
        except Exception:
            pass

        for text in consent_texts:
            try:
                btn = page.locator(f"button:has-text('{text}'), a:has-text('{text}')").first
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    await page.wait_for_timeout(consent_wait_ms)
                    break
            except Exception:
                continue

        landing_url = page.url

        if await is_error_page(page, response_status=nav.status if nav else None):
            return AttemptFailure(kind="Error404", detail=f"landing={landing_url}")

        if not await _drive_step(page, finder=finder,
                                 goal="navigate to a product detail page", url=page.url):
            return AttemptFailure(kind="NoProduct", detail=f"landing={landing_url}")

        if not await _drive_step(page, finder=finder,
                                 goal="add the current product to cart", url=page.url):
            return AttemptFailure(kind="NoCart", detail=f"after_pdp={page.url}")

        if not await _drive_step(page, finder=finder,
                                 goal="proceed to checkout", url=page.url):
            return AttemptFailure(kind="NoCheckout", detail=f"after_cart={page.url}")

        # Wait for the post-checkout-click navigation to settle so we capture
        # the final URL bar (Shopify checkouts are JS-routed).
        try:
            await page.wait_for_load_state("networkidle",
                                           timeout=networkidle_timeout_s * 1000)
        except Exception:
            pass

        cookies = await context.cookies()
        checkout_url = page.url

        tracker_domains = sorted({
            extract_etld1(u) for u in response_urls
            if extract_etld1(u) and extract_etld1(u) != extract_etld1(landing_url)
        })

        return AttemptSuccess(
            landing_url=landing_url,
            checkout_url=checkout_url,
            cookies=list(cookies),
            redirect_chain=list(redirect_chain),
            tracker_domains=tracker_domains,
        )
    except Exception as e:
        return AttemptFailure(kind="Error", detail=f"{type(e).__name__}: {e}")
    finally:
        try:
            context.remove_listener("response", _on_response)
        except Exception:
            pass
```

- [ ] **Step 3: Confirm imports**

At the top of `coinfiliate/harvest.py`, add `extract_etld1` to the existing `from coinfiliate.decision import ...` line. (Already imported — verify `extract_etld1` is in the import list, otherwise add it.)

- [ ] **Step 4: No unit tests at this level**

`attempt_link` is exercised end-to-end in the integration tests (Tasks 11 + 12). Skipping a unit test here — the function's value is real-browser integration.

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/harvest.py
git commit -m "feat(harvest): add attempt_link active-navigation flow

Drives one affiliate link through Landing → Product → Add-to-Cart →
Checkout via element-finder LLM. Each step gets one re-snapshot retry on
click failure. Returns AttemptSuccess with the captured checkout URL +
post-flow cookies, or a typed AttemptFailure for the orchestrator to
log and try the next link. Exercised end-to-end in integration tests."
```

---

### Task 10: `harvest_shop` rewrite — per-link retry orchestration

Iterates `list_affiliate_links_ordered`, calls `attempt_link`, persists harvest only when one link succeeds. Re-points `is_harvest_source` to the working link.

**Files:**
- Modify: `coinfiliate/harvest.py:71-139` (the existing `harvest_shop`)
- Test: extended in Tasks 11 + 12 (integration)

- [ ] **Step 1: Replace `harvest_shop`**

Replace the existing `harvest_shop` function in `coinfiliate/harvest.py` with:

```python
async def harvest_shop(store, *, shop_id: int, settings, llm, browser) -> None:
    """Per-shop harvest with retry across all affiliate links.

    For each link in order (current is_harvest_source first, then by id),
    open a fresh context, run attempt_link, and break on AttemptSuccess.
    If all links fail, mark the shop failed/needs_review based on the
    failure kinds seen.
    """
    shop = next(s for s in await store.list_shops() if s["id"] == shop_id)
    log.info("harvest.shop.start", shop=shop["name"], network=shop["network"], shop_id=shop_id)

    links = await store.list_affiliate_links_ordered(shop_id)
    if not links:
        log.info("harvest.shop.skipped", shop=shop["name"], shop_id=shop_id,
                 reason="no affiliate links")
        await store.update_shop_status(shop_id, "failed",
                                       last_error="no affiliate links")
        return

    failures: list[tuple[str, str]] = []
    success: AttemptSuccess | None = None
    chosen_link_id: str | None = None

    for link in links:
        try:
            async with fresh_context(browser) as ctx:
                page = await ctx.new_page()
                result = await attempt_link(
                    page, ctx, link["affiliate_url"],
                    finder=llm,
                    consent_wait_ms=settings.harvest.consent_wait_ms,
                    networkidle_timeout_s=settings.harvest.networkidle_timeout_seconds,
                )
        except Exception as e:
            failures.append((link["link_id"], f"Error:{type(e).__name__}"))
            log.warning("harvest.shop.link.exception",
                        shop=shop["name"], shop_id=shop_id,
                        link_id=link["link_id"], err=f"{type(e).__name__}: {e}")
            continue

        if isinstance(result, AttemptSuccess):
            success = result
            chosen_link_id = link["link_id"]
            log.info("harvest.shop.link.success",
                     shop=shop["name"], shop_id=shop_id,
                     link_id=link["link_id"], checkout_url=result.checkout_url)
            break

        failures.append((link["link_id"], result.kind))
        log.info("harvest.shop.link.failed",
                 shop=shop["name"], shop_id=shop_id,
                 link_id=link["link_id"], kind=result.kind, detail=result.detail)

    if success is None:
        kinds = {kind for _, kind in failures}
        # 404 / no-product → almost certainly a dead shop. NoCart/NoCheckout/Error
        # could be us misreading the UI on a real shop — kick to review.
        if kinds and kinds <= {"Error404", "NoProduct"}:
            status = "failed"
        else:
            status = "needs_review"
        err = "; ".join(f"{lid}:{k}" for lid, k in failures)[:500]
        log.error("harvest.shop.exhausted",
                  shop=shop["name"], shop_id=shop_id,
                  status=status, failures=failures)
        await store.update_shop_status(shop_id, status, last_error=err)
        return

    checkout_etld1 = extract_etld1(success.checkout_url)
    landing_etld1 = extract_etld1(success.landing_url)

    hctx = HarvestContext(
        shop_name=shop["name"], network=shop["network"],
        final_url=success.landing_url, final_etld1=landing_etld1,
        cookies=success.cookies, redirect_chain=success.redirect_chain,
        tracker_domains=success.tracker_domains,
        checkout_url=success.checkout_url, checkout_etld1=checkout_etld1,
    )
    decision = await decide(hctx, llm=llm)
    ok = decision.primary_cookie_name is not None

    await store.insert_harvest(
        shop_id=shop_id,
        final_url=success.landing_url, final_etld1=landing_etld1,
        cookies=success.cookies, redirect_chain=success.redirect_chain,
        tracker_domains=success.tracker_domains,
        primary_cookie_name=decision.primary_cookie_name,
        tracking_cookie_names=decision.tracking_cookie_names,
        checkout_domains=decision.checkout_domains,
        tracking_cookie_domains=decision.tracking_cookie_domains,
        decision_source=decision.decision_source,
        confidence=decision.confidence,
        llm_rationale=decision.rationale, ok=ok,
        checkout_url=success.checkout_url,
        checkout_etld1=checkout_etld1,
        attempted_link_id=chosen_link_id,
    )

    if chosen_link_id and chosen_link_id != links[0]["link_id"]:
        await store.mark_harvest_source(shop_id, chosen_link_id)

    log.info("harvest.shop.ok",
             shop=shop["name"], shop_id=shop_id,
             attempted_link_id=chosen_link_id,
             decision_source=decision.decision_source,
             confidence=decision.confidence,
             primary_cookie_name=decision.primary_cookie_name, ok=ok)

    if ok and decision.confidence >= settings.harvest.review_threshold:
        await store.update_shop_status(shop_id, "harvested")
    else:
        await store.update_shop_status(shop_id, "needs_review")
```

- [ ] **Step 2: Remove `collect_signals`**

The function is no longer called from `harvest_shop`. Search for other callers:

```bash
grep -rn "collect_signals" /Users/mymac/Documents/Work/coinfiliate-automation
```

If only `tests/integration/test_harvest_signals.py` references it, leave the function in place (the test will be updated/removed in Task 11's fixture update). Otherwise, leave it in place for now — it's still a usable passive helper and removing it is out of scope.

- [ ] **Step 3: Lint check**

```bash
python3 -c "import ast; ast.parse(open('coinfiliate/harvest.py').read())"
```

Expected: no output (parses cleanly).

- [ ] **Step 4: Commit**

```bash
git add coinfiliate/harvest.py
git commit -m "feat(harvest): rewrite harvest_shop for per-link retry

Iterates list_affiliate_links_ordered, calls attempt_link until one
returns AttemptSuccess. is_harvest_source is re-pointed to the working
link. All-failed shops go to needs_review unless every failure is
Error404/NoProduct (in which case the shop is genuinely dead → failed)."
```

---

### Task 11: Extend fake merchant server with PDP / cart / checkout / 404 routes

The integration tests need a deterministic site with a real Add-to-Cart → Checkout flow. We extend `tests/fixtures/fake_merchant_server.py` so the integration tests don't depend on any real merchant.

**Files:**
- Modify: `tests/fixtures/fake_merchant_server.py`
- Modify: `tests/integration/test_harvest_signals.py` (no longer relevant after the rewrite, but keep it green by pointing the existing assertion paths at the new routes if needed; otherwise mark `xfail` with a reason). Easiest: leave as-is — `/aff` still redirects through `/tracker → /merchant` and that path is preserved as the "landing".

- [ ] **Step 1: Extend the fake server**

Replace the contents of `tests/fixtures/fake_merchant_server.py` with:

```python
from __future__ import annotations

from aiohttp import web


# ---- Existing passive flow (preserved so test_harvest_signals.py still passes)

async def _aff_redirect(req):
    raise web.HTTPFound("/tracker")


async def _tracker(req):
    raise web.HTTPFound("/merchant")


async def _merchant(req):
    return web.Response(text="""
        <html><body>
          <div id="consent"><button id="accept">Accept</button></div>
          <script>document.getElementById("accept").onclick = () =>
            document.cookie = "__kla_id=clickid-abc; path=/";
          </script>
        </body></html>
    """, content_type="text/html")


# ---- Active flow: landing (catalog) → PDP → cart → checkout

async def _aff_active(req):
    """Affiliate hop that lands on the catalog page."""
    raise web.HTTPFound("/catalog")


async def _catalog(req):
    return web.Response(text="""
        <html><head><title>Shop Catalog | Fake Store</title></head><body>
          <h1>Shop Catalog</h1>
          <a href="/products/roses-bouquet" id="pdp-link">Roses Bouquet $30</a>
          <a href="/about">About</a>
        </body></html>
    """, content_type="text/html")


async def _pdp(req):
    return web.Response(text="""
        <html><head><title>Roses Bouquet | Fake Store</title></head><body>
          <h1>Roses Bouquet</h1>
          <p>$30</p>
          <form action="/cart/add" method="post">
            <button type="submit" id="add-to-cart">Add to Cart</button>
          </form>
        </body></html>
    """, content_type="text/html")


async def _cart_add(req):
    raise web.HTTPFound("/cart")


async def _cart(req):
    return web.Response(text="""
        <html><head><title>Cart | Fake Store</title></head><body>
          <h1>Your Cart</h1>
          <p>Roses Bouquet — $30</p>
          <a href="/checkouts/cn/abc123" id="checkout-link">Checkout</a>
        </body></html>
    """, content_type="text/html")


async def _checkout(req):
    # Set the affiliate cookie *only* at checkout — proves the cookie capture
    # is happening at the right point.
    resp = web.Response(text="""
        <html><head><title>Checkout | Fake Store</title></head><body>
          <h1>Checkout</h1>
          <p>Total: $30</p>
        </body></html>
    """, content_type="text/html")
    resp.set_cookie("pjnclick", "click-xyz", path="/", domain="127.0.0.1")
    return resp


# ---- Dead link variant (404 landing)

async def _aff_dead(req):
    raise web.HTTPFound("/dead")


async def _dead(req):
    return web.Response(status=404, text="""
        <html><head><title>404 — Page Not Found</title></head>
        <body><h1>Page not found</h1></body></html>
    """, content_type="text/html")


def make_app() -> web.Application:
    app = web.Application()
    # Passive flow (legacy test fixture)
    app.router.add_get("/aff", _aff_redirect)
    app.router.add_get("/tracker", _tracker)
    app.router.add_get("/merchant", _merchant)
    # Active flow
    app.router.add_get("/aff_active", _aff_active)
    app.router.add_get("/catalog", _catalog)
    app.router.add_get("/products/{slug}", _pdp)
    app.router.add_post("/cart/add", _cart_add)
    app.router.add_get("/cart", _cart)
    app.router.add_get("/checkouts/cn/{sid}", _checkout)
    # Dead-link flow
    app.router.add_get("/aff_dead", _aff_dead)
    app.router.add_get("/dead", _dead)
    return app
```

- [ ] **Step 2: Confirm legacy test still passes**

Run: `pytest tests/integration/test_harvest_signals.py -v`
Expected: PASS (the `/aff → /tracker → /merchant` path is unchanged).

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/fake_merchant_server.py
git commit -m "test(fixtures): add active checkout flow + 404 routes to fake merchant"
```

---

### Task 12: Integration test — happy-path active flow

End-to-end: shop with one good affiliate link → `harvest_shop` lands at `/checkouts/cn/abc123`, captures `pjnclick` cookie, persists harvest with `checkout_etld1` set.

**Files:**
- Test: `tests/integration/test_active_checkout.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_active_checkout.py`:

```python
from __future__ import annotations

import pytest
from aiohttp import web
from coinfiliate.browser import harvest_browser
from coinfiliate.store import Store
from coinfiliate.harvest import harvest_shop
from coinfiliate.config import (
    Settings, HarvestConfig, LLMConfig, SyncConfig, RunnerConfig,
    WritebackConfig, LoggingConfig,
)
from tests.fixtures.fake_merchant_server import make_app


def _settings():
    return Settings(
        coinfiliate_email="a@b.com", coinfiliate_pass="x", openai_api_key="k",
        networks=["flexoffers"], sync=SyncConfig(), runner=RunnerConfig(),
        harvest=HarvestConfig(networkidle_timeout_seconds=5, consent_wait_ms=300, review_threshold=0.0),
        writeback=WritebackConfig(), llm=LLMConfig(), logging=LoggingConfig(),
    )


class _StubFinder:
    """Picks the first candidate whose visible text matches a goal-keyword.

    Keeps the integration test free of any real LLM call while exercising
    the same protocol the production OpenAI/Gemini implementations satisfy.
    """
    def __init__(self):
        self._goal_keywords = {
            "navigate to a product detail page": ["roses", "bouquet", "product"],
            "add the current product to cart":   ["add to cart", "add"],
            "proceed to checkout":               ["checkout"],
        }

    async def find_element(self, *, candidates, goal, url):
        keywords = self._goal_keywords.get(goal, [])
        for c in candidates:
            text = (c.get("text") or "").lower()
            if any(k in text for k in keywords):
                return c["idx"]
        return None

    async def analyze(self, ctx):
        # Not exercised in this test — strict heuristic should match `pjnclick`.
        raise AssertionError("LLM cookie analyze should not be called when strict matches")


@pytest.mark.integration
async def test_harvest_shop_active_flow_captures_checkout_cookie(tmp_path):
    app = make_app()
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    store = Store(tmp_path / "t.db"); await store.init()
    sid = await store.upsert_shop(coinfiliate_id="x", name="Fake", network="flexoffers",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    await store.upsert_affiliate_link(sid, link_id="L1", name="L1",
                                      affiliate_url=f"{base}/aff_active")
    await store.mark_harvest_source(sid, "L1")

    finder = _StubFinder()

    async with harvest_browser(headless=True) as browser:
        await harvest_shop(store, shop_id=sid, settings=_settings(), llm=finder, browser=browser)

    shop = (await store.list_shops())[0]
    assert shop["status"] == "harvested", f"got status={shop['status']} err={shop['last_error']}"

    latest = await store.latest_harvest(sid)
    assert latest["primary_cookie_name"] == "pjnclick"
    assert latest["decision_source"] == "heuristic"
    assert "/checkouts/cn/abc123" in (latest["checkout_url"] or "")
    assert latest["attempted_link_id"] == "L1"
    # Checkout URL is on 127.0.0.1 — no real eTLD+1, so checkout_etld1 may be empty;
    # what we care about is that it's the SAME as final_etld1 when both are loopback.
    assert latest["checkout_etld1"] == latest["final_etld1"]

    await store.close()
    await runner.cleanup()
```

- [ ] **Step 2: Run, verify pass**

Run: `pytest tests/integration/test_active_checkout.py -v -s`
Expected: PASS.

If it fails because `extract_etld1` returns `""` for `127.0.0.1` (no suffix), the existing function already returns `""` — and the existing `test_harvest_shop_writes_row_and_updates_status` test asserts `final_etld1 == ""` works. So this should be fine. If not, lower the assertion to `latest["checkout_etld1"] == ""`.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_active_checkout.py
git commit -m "test(integration): happy-path active checkout flow

End-to-end through the fake merchant: catalog → PDP → cart → checkouts/cn/abc123.
Verifies the pjnclick cookie set by the checkout route is captured into
the harvest row alongside checkout_url and attempted_link_id."
```

---

### Task 13: Integration test — first link 404 → retry succeeds on link 2

Verifies the per-link retry: shop has two links, the first goes to `/aff_dead → 404`, the second to `/aff_active`. Final state: shop harvested, `is_harvest_source` re-pointed to L2.

**Files:**
- Test: `tests/integration/test_active_checkout.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_active_checkout.py`:

```python
@pytest.mark.integration
async def test_harvest_shop_retries_to_next_link_when_first_is_404(tmp_path):
    app = make_app()
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    store = Store(tmp_path / "t.db"); await store.init()
    sid = await store.upsert_shop(coinfiliate_id="x", name="Fake", network="flexoffers",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    # L1 is the dead link; L2 is the working one. mark_harvest_source(L1) makes
    # L1 the first-tried link.
    await store.upsert_affiliate_link(sid, link_id="L1", name="dead",
                                      affiliate_url=f"{base}/aff_dead")
    await store.upsert_affiliate_link(sid, link_id="L2", name="good",
                                      affiliate_url=f"{base}/aff_active")
    await store.mark_harvest_source(sid, "L1")

    async with harvest_browser(headless=True) as browser:
        await harvest_shop(store, shop_id=sid, settings=_settings(),
                           llm=_StubFinder(), browser=browser)

    shop = (await store.list_shops())[0]
    assert shop["status"] == "harvested", f"got {shop['status']} err={shop['last_error']}"

    latest = await store.latest_harvest(sid)
    assert latest["attempted_link_id"] == "L2"
    assert latest["primary_cookie_name"] == "pjnclick"

    # is_harvest_source must now point to L2.
    links = await store.list_affiliate_links(sid)
    sources = [l["link_id"] for l in links if l["is_harvest_source"]]
    assert sources == ["L2"]

    await store.close()
    await runner.cleanup()


@pytest.mark.integration
async def test_harvest_shop_marks_failed_when_all_links_404(tmp_path):
    app = make_app()
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    store = Store(tmp_path / "t.db"); await store.init()
    sid = await store.upsert_shop(coinfiliate_id="x", name="Fake", network="flexoffers",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    await store.upsert_affiliate_link(sid, link_id="L1", name="dead1",
                                      affiliate_url=f"{base}/aff_dead")
    await store.upsert_affiliate_link(sid, link_id="L2", name="dead2",
                                      affiliate_url=f"{base}/aff_dead")
    await store.mark_harvest_source(sid, "L1")

    async with harvest_browser(headless=True) as browser:
        await harvest_shop(store, shop_id=sid, settings=_settings(),
                           llm=_StubFinder(), browser=browser)

    shop = (await store.list_shops())[0]
    assert shop["status"] == "failed"
    assert shop["last_error"] is not None
    assert "Error404" in shop["last_error"]

    await store.close()
    await runner.cleanup()
```

- [ ] **Step 2: Run, verify pass**

Run: `pytest tests/integration/test_active_checkout.py -v -s`
Expected: PASS (3 tests in this file).

- [ ] **Step 3: Run the FULL test suite**

```bash
pytest tests -v
```

Expected: all tests pass. If `tests/integration/test_harvest_shop.py` (the legacy one using `collect_signals` semantics indirectly) fails, root cause: it set `mark_harvest_source(sid, "L1")` and uses the passive `/aff → /merchant` route which doesn't have an Add-to-Cart button — meaning the new active flow returns `NoCart` and the shop goes to `needs_review`. Update the legacy test to point at `/aff_active` (whose `pjnclick` cookie now also satisfies the strict heuristic since the post-checkout cookie set is what gets captured), or mark it `xfail` with a clear reason. Lean toward updating, since `xfail` rots.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_active_checkout.py tests/integration/test_harvest_shop.py
git commit -m "test(integration): per-link retry + all-links-fail cases

Verifies harvest_shop iterates through links in order and re-points
is_harvest_source on retry success, and marks shops failed (not
needs_review) when every link returns Error404."
```

---

## Self-review checklist

Run mentally against the spec at `docs/superpowers/specs/2026-05-09-harvest-active-checkout-design.md`:

- [x] **Schema additions** (Task 1) — checkout_url, checkout_etld1, attempted_link_id, idempotent migration.
- [x] **`list_affiliate_links_ordered`** (Task 2) — current source first, then by id.
- [x] **`cookie_domain_etld1` helper** (Task 3) — strip leading dot, eTLD+1.
- [x] **HarvestContext + decide() new fields** (Task 4) — checkout_etld1 and primary cookie's domain feed the two list fields.
- [x] **`is_error_page`** (Task 5) — status / title / h1 signals.
- [x] **`collect_clickable_candidates`** (Task 6) — DOM walker with stable selectors.
- [x] **`ElementFinder` protocol + OpenAI** (Task 7) — same instance satisfies both protocols.
- [x] **Gemini ElementFinder** (Task 8) — parallel.
- [x] **AttemptSuccess / AttemptFailure / `attempt_link`** (Task 9) — Landing → Product → Cart → Checkout, one re-snapshot retry per step.
- [x] **`harvest_shop` retry loop** (Task 10) — iterate links, re-point is_harvest_source.
- [x] **Fake merchant active flow + 404 fixtures** (Task 11).
- [x] **Integration: happy path** (Task 12) — captures pjnclick at /checkouts/cn/abc123.
- [x] **Integration: 404 retry + all-links-fail** (Task 13).

Identifiers used consistently across tasks:
- `AttemptSuccess.landing_url` / `checkout_url` / `cookies` / `redirect_chain` / `tracker_domains` ✓
- `AttemptFailure.kind` ∈ {`Error404`, `NoProduct`, `NoCart`, `NoCheckout`, `Error`} ✓
- `attempt_link(page, context, affiliate_url, *, finder, ...)` ✓
- `ElementFinder.find_element(*, candidates, goal, url) -> Optional[int]` ✓
- `Store.list_affiliate_links_ordered(shop_id) -> list` ✓
- `HarvestContext.checkout_url`, `HarvestContext.checkout_etld1` ✓
- `cookie_domain_etld1(cookie: dict) -> Optional[str]` ✓

No placeholders remain.

---

## Out of scope (per spec)

- Multi-step checkout flows (login wall, shipping picker before payment): captured at first checkout page.
- Caching LLM-discovered selectors across runs.
- Per-CMS heuristic shortcuts (Shopify-specific selectors). LLM handles all cases.
