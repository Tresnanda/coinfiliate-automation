# Coinfiliate Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the staged sync → harvest → writeback pipeline described in `docs/superpowers/specs/2026-04-24-coinfiliate-automation-design.md`, replacing the current skeleton `main.py` with a full Python package that automates Coinfiliate Partner Shop cookie configuration.

**Architecture:** Three-phase pipeline (`coinfiliate sync | harvest | writeback | run`) over a Playwright browser, SQLite state store, heuristic→LLM decision pipeline. Pluggable LLM provider (OpenAI or Gemini) via a Protocol boundary. Fully autonomous by default; HITL supported.

**Tech Stack:** Python 3.10+, Playwright async, SQLite (via `aiosqlite`), Pydantic v2 + pydantic-settings, Typer, structlog, tldextract, OpenAI SDK, google-genai SDK (`from google import genai` — NOT the deprecated `google-generativeai`), pytest + pytest-asyncio, aiohttp (test fixture server only).

---

## File Structure

```
coinfiliate-automation/
├── main.py                          # thin shim → coinfiliate.cli:main
├── schema.sql                       # SQLite DDL
├── config.yaml                      # runtime config
├── .env.example                     # credential template
├── requirements.txt
├── coinfiliate/
│   ├── __init__.py
│   ├── cli.py                       # Typer app
│   ├── config.py                    # pydantic-settings loader
│   ├── store.py                     # SQLite ops + status transitions
│   ├── selectors.py                 # DOM selectors keyed by semantic name
│   ├── browser.py                   # Playwright session + context factory
│   ├── logging_setup.py             # structlog config
│   ├── models.py                    # HarvestContext / HarvestDecision dataclasses
│   ├── decision.py                  # heuristic pipeline + LLM dispatch
│   ├── sync.py                      # sync phase
│   ├── harvest.py                   # harvest phase
│   ├── writeback.py                 # writeback phase
│   └── llm/
│       ├── __init__.py
│       ├── base.py                  # CookieAnalyzer Protocol
│       ├── prompt.py                # prompt template
│       ├── openai_client.py
│       └── gemini_client.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py                  # shared fixtures
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_store.py
│   │   ├── test_domain.py
│   │   ├── test_heuristic.py
│   │   ├── test_decision.py
│   │   ├── test_llm_openai.py
│   │   └── test_llm_gemini.py
│   ├── integration/
│   │   ├── test_harvest_e2e.py      # against fake merchant server
│   │   └── test_writeback_selectors.py  # against saved HTML fixture
│   └── fixtures/
│       ├── fake_merchant_server.py  # aiohttp server for integration tests
│       ├── coinfiliate_edit_page.html  # saved HTML of the real Edit page
│       └── sample_cookies.json      # anonymized cookie arrays
└── docs/
    ├── superpowers/specs/…          # already committed
    └── tutorial-images/…            # already committed
```

---

## Task 1: Project bootstrap

**Files:**
- Modify: `requirements.txt`
- Create: `coinfiliate/__init__.py`, `coinfiliate/llm/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/fixtures/__init__.py`
- Create: `pyproject.toml` (for pytest config)
- Modify: `.env.example`
- Create: `config.yaml`

- [ ] **Step 1: Replace `requirements.txt` with full dependency list**

```
playwright==1.47.*
pydantic==2.*
pydantic-settings==2.*
typer==0.12.*
structlog==24.*
tldextract==5.*
aiosqlite==0.20.*
openai==1.*
google-generativeai==0.8.*
pyyaml==6.*
python-dotenv==1.*
pytest==8.*
pytest-asyncio==0.23.*
aiohttp==3.*
```

- [ ] **Step 2: Create the package + tests directory scaffolding**

Create empty `__init__.py` files in every directory listed under "Files" above.

- [ ] **Step 3: Write `pyproject.toml` for pytest config**

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "unit: fast tests, no browser",
    "integration: slower tests, uses Playwright against local fixture server",
]
```

- [ ] **Step 4: Expand `.env.example`**

```
COINFILIATE_EMAIL="your_coinfiliate_email@example.com"
COINFILIATE_PASS="your_coinfiliate_password"
OPENAI_API_KEY=""
GEMINI_API_KEY=""
```

- [ ] **Step 5: Write `config.yaml` with defaults from spec §13**

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
  review_threshold: 0.0

writeback:
  verify_after_save: true

llm:
  provider: "openai"
  model: "gpt-4o-mini"
  max_retries: 3
  timeout_seconds: 30

logging:
  level: "INFO"
  debug_log_retention_days: 7
```

- [ ] **Step 6: Install deps and verify pytest runs**

Run: `pip install -r requirements.txt && playwright install chromium && pytest`
Expected: `no tests ran` (0 tests collected, no errors).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pyproject.toml config.yaml .env.example coinfiliate/ tests/
git commit -m "chore: bootstrap package structure and pytest config"
```

---

## Task 2: Config loader

**Files:**
- Create: `tests/unit/test_config.py`
- Create: `coinfiliate/config.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_config.py`:
```python
from pathlib import Path
import yaml
from coinfiliate.config import Settings, load_settings


def test_load_settings_from_yaml_and_env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({
        "networks": ["flexoffers", "awin"],
        "sync": {"page": 2, "page_size": 50, "selectable_fields": "all"},
        "runner": {"max_shops_per_batch": 10, "max_concurrency": 2, "inter_shop_jitter_ms": [100, 500]},
        "harvest": {"networkidle_timeout_seconds": 10, "consent_wait_ms": 1000, "review_threshold": 0.5},
        "writeback": {"verify_after_save": False},
        "llm": {"provider": "gemini", "model": "gemini-2.5-flash", "max_retries": 2, "timeout_seconds": 20},
        "logging": {"level": "DEBUG", "debug_log_retention_days": 3},
    }))
    monkeypatch.setenv("COINFILIATE_EMAIL", "a@b.com")
    monkeypatch.setenv("COINFILIATE_PASS", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "gk")

    s = load_settings(cfg)

    assert s.networks == ["flexoffers", "awin"]
    assert s.harvest.review_threshold == 0.5
    assert s.llm.provider == "gemini"
    assert s.coinfiliate_email == "a@b.com"
    assert s.gemini_api_key == "gk"


def test_missing_credentials_raises(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"networks": ["flexoffers"]}))
    try:
        load_settings(cfg)
    except Exception as e:
        assert "COINFILIATE_EMAIL" in str(e) or "coinfiliate_email" in str(e).lower()
    else:
        raise AssertionError("expected exception")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py -v`
Expected: ImportError / ModuleNotFoundError (`coinfiliate.config` not implemented).

- [ ] **Step 3: Implement `coinfiliate/config.py`**

```python
from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SyncConfig(BaseModel):
    page: int = 1
    page_size: int = 100
    selectable_fields: str = "all"


class RunnerConfig(BaseModel):
    max_shops_per_batch: int = 50
    max_concurrency: int = 4
    inter_shop_jitter_ms: tuple[int, int] = (500, 2000)


class HarvestConfig(BaseModel):
    networkidle_timeout_seconds: int = 15
    consent_wait_ms: int = 2000
    review_threshold: float = 0.0


class WritebackConfig(BaseModel):
    verify_after_save: bool = True


class LLMConfig(BaseModel):
    provider: Literal["openai", "gemini"] = "openai"
    model: str = "gpt-4o-mini"
    max_retries: int = 3
    timeout_seconds: int = 30


class LoggingConfig(BaseModel):
    level: str = "INFO"
    debug_log_retention_days: int = 7


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # From config.yaml
    networks: list[str] = Field(default_factory=lambda: ["flexoffers"])
    sync: SyncConfig = Field(default_factory=SyncConfig)
    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    harvest: HarvestConfig = Field(default_factory=HarvestConfig)
    writeback: WritebackConfig = Field(default_factory=WritebackConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # From env only
    coinfiliate_email: str
    coinfiliate_pass: str
    openai_api_key: str | None = None
    gemini_api_key: str | None = None


def load_settings(config_path: Path = Path("config.yaml")) -> Settings:
    yaml_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    return Settings(**yaml_data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/config.py tests/unit/test_config.py
git commit -m "feat: config loader (yaml + env via pydantic-settings)"
```

---

## Task 3: SQLite schema + store

**Files:**
- Create: `schema.sql`
- Create: `tests/unit/test_store.py`
- Create: `coinfiliate/store.py`

- [ ] **Step 1: Write `schema.sql` from spec §6**

Copy the three `CREATE TABLE` blocks and the two `CREATE INDEX` statements from spec §6 verbatim.

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_store.py`:
```python
import json
import pytest
from coinfiliate.store import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "test.db")
    await s.init()
    yield s
    await s.close()


async def test_upsert_shop_is_idempotent(store):
    a = await store.upsert_shop(
        coinfiliate_id="cfi-1", name="Notch", network="flexoffers",
        advertiser_id="215583", website_url="https://notchgear.com",
        edit_url="/admin/partner-shop/cfi-1/edit",
    )
    b = await store.upsert_shop(
        coinfiliate_id="cfi-1", name="Notch", network="flexoffers",
        advertiser_id="215583", website_url="https://notchgear.com",
        edit_url="/admin/partner-shop/cfi-1/edit",
    )
    assert a == b  # same row id
    rows = await store.list_shops()
    assert len(rows) == 1


async def test_upsert_shop_preserves_status(store):
    sid = await store.upsert_shop(coinfiliate_id="x", name="X", network="n",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    await store.update_shop_status(sid, "harvested")
    await store.upsert_shop(coinfiliate_id="x", name="X2", network="n",
                            advertiser_id=None, website_url=None, edit_url="/e2")
    rows = await store.list_shops()
    assert rows[0]["status"] == "harvested"
    assert rows[0]["name"] == "X2"


async def test_upsert_affiliate_link_idempotent_and_flags_one_source(store):
    sid = await store.upsert_shop(coinfiliate_id="s", name="S", network="n",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    await store.upsert_affiliate_link(sid, link_id="a", name="A", affiliate_url="u1")
    await store.upsert_affiliate_link(sid, link_id="b", name="B", affiliate_url="u2")
    await store.upsert_affiliate_link(sid, link_id="a", name="A", affiliate_url="u1")  # re-upsert
    await store.mark_harvest_source(sid, link_id="a")

    links = await store.list_affiliate_links(sid)
    assert len(links) == 2
    sources = [l for l in links if l["is_harvest_source"] == 1]
    assert len(sources) == 1 and sources[0]["link_id"] == "a"


async def test_insert_harvest_and_list_pending_shops(store):
    s1 = await store.upsert_shop(coinfiliate_id="s1", name="S1", network="n", advertiser_id=None, website_url=None, edit_url="/")
    s2 = await store.upsert_shop(coinfiliate_id="s2", name="S2", network="n", advertiser_id=None, website_url=None, edit_url="/")

    await store.insert_harvest(
        shop_id=s1,
        final_url="https://s.com/",
        final_etld1="s.com",
        cookies=[{"name": "__kla_id", "value": "abc"}],
        redirect_chain=["https://s.com/"],
        tracker_domains=[],
        primary_cookie_name="__kla_id",
        tracking_cookie_names=["__kla_id"],
        checkout_domains=["s.com"],
        tracking_cookie_domains=["s.com"],
        decision_source="heuristic",
        confidence=0.6,
        llm_rationale=None,
        ok=True,
    )
    await store.update_shop_status(s1, "harvested")

    pending = await store.list_shops(status="pending")
    harvested = await store.list_shops(status="harvested")
    assert [r["id"] for r in pending] == [s2]
    assert [r["id"] for r in harvested] == [s1]

    latest = await store.latest_harvest(s1)
    assert latest["primary_cookie_name"] == "__kla_id"
    assert json.loads(latest["cookies_json"])[0]["name"] == "__kla_id"
```

- [ ] **Step 3: Implement `coinfiliate/store.py`**

```python
import json
from pathlib import Path
from typing import Any, Sequence
import aiosqlite


SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"

VALID_STATUSES = {"pending", "harvested", "writeback_done", "needs_review", "failed"}


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA_PATH.read_text())
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def upsert_shop(self, *, coinfiliate_id: str, name: str, network: str,
                          advertiser_id: str | None, website_url: str | None,
                          edit_url: str) -> int:
        cur = await self._conn.execute("SELECT id FROM shop WHERE coinfiliate_id = ?", (coinfiliate_id,))
        row = await cur.fetchone()
        if row:
            await self._conn.execute(
                "UPDATE shop SET name=?, network=?, advertiser_id=?, website_url=?, edit_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (name, network, advertiser_id, website_url, edit_url, row["id"]),
            )
            await self._conn.commit()
            return row["id"]
        cur = await self._conn.execute(
            "INSERT INTO shop (coinfiliate_id, name, network, advertiser_id, website_url, edit_url) VALUES (?, ?, ?, ?, ?, ?)",
            (coinfiliate_id, name, network, advertiser_id, website_url, edit_url),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def update_shop_status(self, shop_id: int, status: str, last_error: str | None = None) -> None:
        assert status in VALID_STATUSES
        await self._conn.execute(
            "UPDATE shop SET status=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, last_error, shop_id),
        )
        await self._conn.commit()

    async def list_shops(self, status: str | None = None) -> list[dict]:
        if status:
            cur = await self._conn.execute("SELECT * FROM shop WHERE status=? ORDER BY id", (status,))
        else:
            cur = await self._conn.execute("SELECT * FROM shop ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]

    async def upsert_affiliate_link(self, shop_id: int, *, link_id: str, name: str | None, affiliate_url: str) -> None:
        await self._conn.execute(
            """INSERT INTO affiliate_link (shop_id, link_id, name, affiliate_url)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(shop_id, link_id) DO UPDATE SET
                 name=excluded.name, affiliate_url=excluded.affiliate_url""",
            (shop_id, link_id, name, affiliate_url),
        )
        await self._conn.commit()

    async def mark_harvest_source(self, shop_id: int, link_id: str) -> None:
        await self._conn.execute("UPDATE affiliate_link SET is_harvest_source=0 WHERE shop_id=?", (shop_id,))
        await self._conn.execute(
            "UPDATE affiliate_link SET is_harvest_source=1 WHERE shop_id=? AND link_id=?",
            (shop_id, link_id),
        )
        await self._conn.commit()

    async def list_affiliate_links(self, shop_id: int) -> list[dict]:
        cur = await self._conn.execute("SELECT * FROM affiliate_link WHERE shop_id=? ORDER BY id", (shop_id,))
        return [dict(r) for r in await cur.fetchall()]

    async def get_harvest_source(self, shop_id: int) -> dict | None:
        cur = await self._conn.execute(
            "SELECT * FROM affiliate_link WHERE shop_id=? AND is_harvest_source=1 LIMIT 1",
            (shop_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def insert_harvest(self, *, shop_id: int, final_url: str | None, final_etld1: str | None,
                             cookies: Sequence[dict], redirect_chain: Sequence[str],
                             tracker_domains: Sequence[str], primary_cookie_name: str | None,
                             tracking_cookie_names: Sequence[str] | None,
                             checkout_domains: Sequence[str] | None,
                             tracking_cookie_domains: Sequence[str] | None,
                             decision_source: str, confidence: float | None,
                             llm_rationale: str | None, ok: bool) -> int:
        cur = await self._conn.execute(
            """INSERT INTO harvest
                 (shop_id, final_url, final_etld1, cookies_json, redirect_chain_json, tracker_domains_json,
                  primary_cookie_name, tracking_cookie_names_json, checkout_domains_json,
                  tracking_cookie_domains_json, decision_source, confidence, llm_rationale, ok)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shop_id, final_url, final_etld1,
             json.dumps(list(cookies)), json.dumps(list(redirect_chain)), json.dumps(list(tracker_domains)),
             primary_cookie_name,
             json.dumps(list(tracking_cookie_names)) if tracking_cookie_names is not None else None,
             json.dumps(list(checkout_domains)) if checkout_domains is not None else None,
             json.dumps(list(tracking_cookie_domains)) if tracking_cookie_domains is not None else None,
             decision_source, confidence, llm_rationale, 1 if ok else 0),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def latest_harvest(self, shop_id: int) -> dict | None:
        cur = await self._conn.execute(
            "SELECT * FROM harvest WHERE shop_id=? ORDER BY attempted_at DESC, id DESC LIMIT 1",
            (shop_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_store.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add schema.sql coinfiliate/store.py tests/unit/test_store.py
git commit -m "feat: SQLite store with shop/affiliate_link/harvest tables"
```

---

## Task 4: Domain helper + cookie heuristics

**Files:**
- Create: `tests/unit/test_domain.py`
- Create: `tests/unit/test_heuristic.py`
- Create: `coinfiliate/decision.py` (first pass — just helpers)

- [ ] **Step 1: Write `tests/unit/test_domain.py`**

```python
from coinfiliate.decision import extract_etld1


def test_etld1_from_real_affiliate_url():
    assert extract_etld1("https://track.flexlinkspro.com/g.ashx?foid=...") == "flexlinkspro.com"


def test_etld1_from_merchant_url():
    assert extract_etld1("https://www.kryptek.com/spring-sale") == "kryptek.com"


def test_etld1_from_subdomain():
    assert extract_etld1("https://checkout.notchgear.com/cart") == "notchgear.com"


def test_etld1_handles_cc_tld():
    assert extract_etld1("https://shop.example.co.uk/x") == "example.co.uk"
```

- [ ] **Step 2: Write `tests/unit/test_heuristic.py`**

```python
from coinfiliate.decision import strict_match, loose_match


def _cookie(name: str) -> dict:
    return {"name": name, "value": "v", "domain": ".x", "path": "/"}


def test_strict_match_finds_pjnclick():
    cookies = [_cookie("_ga"), _cookie("pjnclick"), _cookie("_fbp")]
    match = strict_match(cookies)
    assert match is not None and match["name"] == "pjnclick"


def test_strict_match_finds_fobs_prefix():
    cookies = [_cookie("fobs_12345")]
    assert strict_match(cookies)["name"] == "fobs_12345"


def test_strict_match_returns_none_when_no_match():
    cookies = [_cookie("__kla_id"), _cookie("_ga")]
    assert strict_match(cookies) is None


def test_loose_match_finds_kla_id():
    cookies = [_cookie("_ga"), _cookie("__kla_id")]
    assert loose_match(cookies)["name"] == "__kla_id"


def test_loose_match_prefers_kla_over_ga():
    # __kla_id is more specific (per-user ID) than _ga
    cookies = [_cookie("_ga"), _cookie("__kla_id")]
    assert loose_match(cookies)["name"] == "__kla_id"
```

- [ ] **Step 3: Implement `coinfiliate/decision.py`**

```python
import tldextract

_STRICT_KEYWORDS = [
    "pjnclick", "irclick", "ir_", "awc", "fobs_", "_ck_", "_wg_",
    "cj_source", "cje", "impact", "partnerize_", "rakuten_", "click_id",
]

# Ordered by preference: per-user tracking IDs beat session IDs beat generic analytics
_LOOSE_KEYWORDS_ORDERED = [
    "__kla_id",           # Klaviyo
    "ajs_anonymous_id",   # Segment
    "_gcl_aw",            # Google Ads click ID
    "_fbp",               # Facebook Pixel
    "_shopify_y",         # Shopify long-lived anon
    "_ga",                # Google Analytics (fallback)
]


def extract_etld1(url: str) -> str:
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain


def strict_match(cookies: list[dict]) -> dict | None:
    for c in cookies:
        name = c["name"].lower()
        for kw in _STRICT_KEYWORDS:
            if kw in name:
                return c
    return None


def loose_match(cookies: list[dict]) -> dict | None:
    by_name = {c["name"]: c for c in cookies}
    for kw in _LOOSE_KEYWORDS_ORDERED:
        if kw in by_name:
            return by_name[kw]
    return None
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_domain.py tests/unit/test_heuristic.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/decision.py tests/unit/test_domain.py tests/unit/test_heuristic.py
git commit -m "feat: eTLD+1 extractor and cookie heuristic matchers"
```

---

## Task 5: Data models (HarvestContext, HarvestDecision)

**Files:**
- Create: `coinfiliate/models.py`

- [ ] **Step 1: Write `coinfiliate/models.py`**

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HarvestContext:
    """All signals collected from the browser; input to the decision pipeline."""
    shop_name: str
    network: str
    final_url: str
    final_etld1: str
    cookies: list[dict] = field(default_factory=list)
    redirect_chain: list[str] = field(default_factory=list)
    tracker_domains: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HarvestDecision:
    """Output of the decision pipeline."""
    primary_cookie_name: str | None
    tracking_cookie_names: list[str]
    checkout_domains: list[str]
    tracking_cookie_domains: list[str]
    decision_source: str  # "heuristic" | "llm" | "manual"
    confidence: float     # 0.0..1.0
    rationale: str | None = None
```

- [ ] **Step 2: Commit**

No test needed — pure data containers with no logic.

```bash
git add coinfiliate/models.py
git commit -m "feat: HarvestContext and HarvestDecision dataclasses"
```

---

## Task 6: LLM Protocol + prompt + OpenAI client

**Files:**
- Create: `coinfiliate/llm/base.py`
- Create: `coinfiliate/llm/prompt.py`
- Create: `coinfiliate/llm/openai_client.py`
- Create: `tests/unit/test_llm_openai.py`

- [ ] **Step 1: Write `coinfiliate/llm/base.py`**

```python
from typing import Protocol
from coinfiliate.models import HarvestContext, HarvestDecision


class CookieAnalyzer(Protocol):
    async def analyze(self, ctx: HarvestContext) -> HarvestDecision: ...
```

- [ ] **Step 2: Write `coinfiliate/llm/prompt.py`**

```python
import json
from coinfiliate.models import HarvestContext

SYSTEM = (
    "You are identifying the primary affiliate-tracking cookie on a merchant site. "
    "Prefer cookies that carry a per-click unique value over session IDs. "
    "Prefer network-native cookies (e.g., pjnclick, IR_*, awc, fobs_*) over first-party trackers "
    "(e.g., __kla_id, _ga) when both are present. "
    "Output strict JSON only. No prose."
)

SCHEMA = {
    "primary_cookie_name": "string",
    "tracking_cookie_names": "string[]",
    "checkout_domains": "string[]",
    "tracking_cookie_domains": "string[]",
    "confidence": "number 0..1",
    "rationale": "one-line string",
}


def build_user_prompt(ctx: HarvestContext) -> str:
    return (
        f"Shop: {ctx.shop_name} (network={ctx.network})\n"
        f"Final URL: {ctx.final_url}\n"
        f"eTLD+1: {ctx.final_etld1}\n\n"
        f"Cookies set on the landing page:\n{json.dumps(ctx.cookies, indent=2)}\n\n"
        f"Third-party tracker domains seen in the redirect chain:\n{json.dumps(ctx.tracker_domains, indent=2)}\n\n"
        f"Redirect chain:\n{json.dumps(ctx.redirect_chain, indent=2)}\n\n"
        f"Respond with strict JSON matching this schema:\n{json.dumps(SCHEMA, indent=2)}"
    )
```

- [ ] **Step 3: Write the failing test**

`tests/unit/test_llm_openai.py`:
```python
import json
from unittest.mock import AsyncMock, MagicMock
from coinfiliate.models import HarvestContext
from coinfiliate.llm.openai_client import OpenAICookieAnalyzer


def _ctx():
    return HarvestContext(shop_name="Notch", network="flexoffers",
                          final_url="https://notchgear.com/",
                          final_etld1="notchgear.com",
                          cookies=[{"name": "__kla_id", "value": "abc"}],
                          redirect_chain=["https://notchgear.com/"],
                          tracker_domains=[])


async def test_openai_analyzer_parses_valid_response():
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "primary_cookie_name": "__kla_id",
            "tracking_cookie_names": ["__kla_id"],
            "checkout_domains": ["notchgear.com"],
            "tracking_cookie_domains": ["notchgear.com"],
            "confidence": 0.8,
            "rationale": "Klaviyo ID is the only per-user tracker present.",
        })))]
    ))

    a = OpenAICookieAnalyzer(client=mock_client, model="gpt-4o-mini", max_retries=1, timeout_seconds=10)
    d = await a.analyze(_ctx())

    assert d.primary_cookie_name == "__kla_id"
    assert d.confidence == 0.8
    assert d.decision_source == "llm"


async def test_openai_analyzer_malformed_json_raises_after_retries():
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content="not json at all"))]
    ))

    a = OpenAICookieAnalyzer(client=mock_client, model="gpt-4o-mini", max_retries=2, timeout_seconds=10)
    import pytest
    with pytest.raises(ValueError):
        await a.analyze(_ctx())
    assert mock_client.chat.completions.create.await_count == 2
```

- [ ] **Step 4: Implement `coinfiliate/llm/openai_client.py`**

```python
import asyncio
import json
from coinfiliate.models import HarvestContext, HarvestDecision
from coinfiliate.llm.prompt import SYSTEM, build_user_prompt


class OpenAICookieAnalyzer:
    def __init__(self, *, client, model: str, max_retries: int, timeout_seconds: int):
        self._client = client
        self._model = model
        self._max_retries = max_retries
        self._timeout = timeout_seconds

    async def analyze(self, ctx: HarvestContext) -> HarvestDecision:
        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.chat.completions.create(
                    model=self._model,
                    timeout=self._timeout,
                    messages=[
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": build_user_prompt(ctx)},
                    ],
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp.choices[0].message.content)
                return HarvestDecision(
                    primary_cookie_name=data["primary_cookie_name"],
                    tracking_cookie_names=list(data.get("tracking_cookie_names", [])),
                    checkout_domains=list(data.get("checkout_domains", [])),
                    tracking_cookie_domains=list(data.get("tracking_cookie_domains", [])),
                    decision_source="llm",
                    confidence=float(data.get("confidence", 0.0)),
                    rationale=data.get("rationale"),
                )
            except Exception as e:
                last_err = e
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        raise ValueError(f"LLM failed after {self._max_retries} attempts: {last_err}")
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_llm_openai.py -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add coinfiliate/llm/ tests/unit/test_llm_openai.py
git commit -m "feat: LLM Protocol + OpenAI cookie analyzer with retry"
```

---

## Task 7: Gemini LLM client

**Files:**
- Create: `coinfiliate/llm/gemini_client.py`
- Create: `tests/unit/test_llm_gemini.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_llm_gemini.py`:
```python
import json
from unittest.mock import AsyncMock, MagicMock
from coinfiliate.models import HarvestContext
from coinfiliate.llm.gemini_client import GeminiCookieAnalyzer


def _ctx():
    return HarvestContext(shop_name="K", network="flexoffers",
                          final_url="https://k.com/", final_etld1="k.com",
                          cookies=[{"name": "awc", "value": "1"}],
                          redirect_chain=[], tracker_domains=[])


async def test_gemini_analyzer_parses_valid_response():
    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=MagicMock(
        text=json.dumps({
            "primary_cookie_name": "awc",
            "tracking_cookie_names": ["awc"],
            "checkout_domains": ["k.com"],
            "tracking_cookie_domains": ["k.com"],
            "confidence": 0.95,
            "rationale": "Awin awc cookie present.",
        })
    ))

    a = GeminiCookieAnalyzer(model=mock_model, max_retries=1)
    d = await a.analyze(_ctx())

    assert d.primary_cookie_name == "awc"
    assert d.confidence == 0.95
    assert d.decision_source == "llm"
```

- [ ] **Step 2: Implement `coinfiliate/llm/gemini_client.py`**

```python
import asyncio
import json
import re
from coinfiliate.models import HarvestContext, HarvestDecision
from coinfiliate.llm.prompt import SYSTEM, build_user_prompt


class GeminiCookieAnalyzer:
    def __init__(self, *, model, max_retries: int = 3):
        self._model = model
        self._max_retries = max_retries

    async def analyze(self, ctx: HarvestContext) -> HarvestDecision:
        prompt = SYSTEM + "\n\n" + build_user_prompt(ctx)
        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._model.generate_content_async(prompt)
                # Gemini sometimes wraps JSON in ```json ... ``` fences; strip if present.
                text = resp.text.strip()
                m = re.search(r"\{.*\}", text, re.DOTALL)
                data = json.loads(m.group(0) if m else text)
                return HarvestDecision(
                    primary_cookie_name=data["primary_cookie_name"],
                    tracking_cookie_names=list(data.get("tracking_cookie_names", [])),
                    checkout_domains=list(data.get("checkout_domains", [])),
                    tracking_cookie_domains=list(data.get("tracking_cookie_domains", [])),
                    decision_source="llm",
                    confidence=float(data.get("confidence", 0.0)),
                    rationale=data.get("rationale"),
                )
            except Exception as e:
                last_err = e
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        raise ValueError(f"Gemini failed after {self._max_retries} attempts: {last_err}")
```

- [ ] **Step 3: Run test**

Run: `pytest tests/unit/test_llm_gemini.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add coinfiliate/llm/gemini_client.py tests/unit/test_llm_gemini.py
git commit -m "feat: Gemini cookie analyzer"
```

---

## Task 8: Decision orchestrator

**Files:**
- Modify: `coinfiliate/decision.py`
- Create: `tests/unit/test_decision.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_decision.py`:
```python
from unittest.mock import AsyncMock, MagicMock
from coinfiliate.models import HarvestContext, HarvestDecision
from coinfiliate.decision import decide


def _ctx(cookies):
    return HarvestContext(shop_name="S", network="flexoffers",
                          final_url="https://s.com/", final_etld1="s.com",
                          cookies=cookies, redirect_chain=[], tracker_domains=[])


def _cookie(name):
    return {"name": name, "value": "v"}


async def test_decide_uses_strict_match_without_calling_llm():
    llm = MagicMock()
    llm.analyze = AsyncMock()
    d = await decide(_ctx([_cookie("pjnclick"), _cookie("_ga")]), llm=llm)
    assert d.primary_cookie_name == "pjnclick"
    assert d.decision_source == "heuristic"
    assert d.confidence == 1.0
    llm.analyze.assert_not_awaited()


async def test_decide_uses_loose_match_without_calling_llm():
    llm = MagicMock()
    llm.analyze = AsyncMock()
    d = await decide(_ctx([_cookie("__kla_id"), _cookie("_ga")]), llm=llm)
    assert d.primary_cookie_name == "__kla_id"
    assert d.decision_source == "heuristic"
    assert d.confidence == 0.6
    llm.analyze.assert_not_awaited()


async def test_decide_falls_back_to_llm_when_heuristics_miss():
    llm = MagicMock()
    llm.analyze = AsyncMock(return_value=HarvestDecision(
        primary_cookie_name="custom_id",
        tracking_cookie_names=["custom_id"],
        checkout_domains=["s.com"],
        tracking_cookie_domains=["s.com"],
        decision_source="llm", confidence=0.7, rationale="it looked unique",
    ))
    d = await decide(_ctx([_cookie("random_name"), _cookie("session")]), llm=llm)
    assert d.primary_cookie_name == "custom_id"
    assert d.decision_source == "llm"
    llm.analyze.assert_awaited_once()


async def test_decide_returns_empty_when_llm_also_yields_nothing():
    llm = MagicMock()
    llm.analyze = AsyncMock(side_effect=ValueError("LLM failed"))
    d = await decide(_ctx([_cookie("session")]), llm=llm)
    assert d.primary_cookie_name is None
    assert d.decision_source == "llm"
    assert d.confidence == 0.0
```

- [ ] **Step 2: Append `decide()` to `coinfiliate/decision.py`**

```python
from coinfiliate.models import HarvestContext, HarvestDecision
from coinfiliate.llm.base import CookieAnalyzer


async def decide(ctx: HarvestContext, *, llm: CookieAnalyzer) -> HarvestDecision:
    # 1. strict heuristic
    strict = strict_match(ctx.cookies)
    if strict:
        return HarvestDecision(
            primary_cookie_name=strict["name"],
            tracking_cookie_names=[strict["name"]],
            checkout_domains=[ctx.final_etld1],
            tracking_cookie_domains=[ctx.final_etld1],
            decision_source="heuristic",
            confidence=1.0,
            rationale=None,
        )
    # 2. loose heuristic
    loose = loose_match(ctx.cookies)
    if loose:
        return HarvestDecision(
            primary_cookie_name=loose["name"],
            tracking_cookie_names=[loose["name"]],
            checkout_domains=[ctx.final_etld1],
            tracking_cookie_domains=[ctx.final_etld1],
            decision_source="heuristic",
            confidence=0.6,
            rationale=None,
        )
    # 3. LLM fallback
    try:
        return await llm.analyze(ctx)
    except Exception as e:
        return HarvestDecision(
            primary_cookie_name=None,
            tracking_cookie_names=[],
            checkout_domains=[ctx.final_etld1],
            tracking_cookie_domains=[ctx.final_etld1],
            decision_source="llm",
            confidence=0.0,
            rationale=f"LLM failed: {e}",
        )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/test_decision.py -v`
Expected: all 4 PASS.

- [ ] **Step 4: Commit**

```bash
git add coinfiliate/decision.py tests/unit/test_decision.py
git commit -m "feat: decide() pipeline (strict → loose → LLM)"
```

---

## Task 9: Selectors module

**Files:**
- Create: `coinfiliate/selectors.py`

- [ ] **Step 1: Copy the `SELECTORS` dict from spec §9.2 verbatim**

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
    # Sync-modal fields (appears both at shop list and inside Edit)
    "syncmodal.network_select":         'div[role="dialog"] >> text=Network >> xpath=following::*[@role="combobox"][1]',
    "syncmodal.page_input":             'div[role="dialog"] >> input >> near(text="Page")',
    "syncmodal.page_size_input":        'div[role="dialog"] >> input >> near(text="Page Size")',
    "syncmodal.sync_now_btn":           'div[role="dialog"] >> button:has-text("Sync Now")',
}


def sel(key: str) -> str:
    return SELECTORS[key]
```

- [ ] **Step 2: Commit**

```bash
git add coinfiliate/selectors.py
git commit -m "feat: centralized selectors dict"
```

---

## Task 10: Browser factory + logging setup

**Files:**
- Create: `coinfiliate/browser.py`
- Create: `coinfiliate/logging_setup.py`

- [ ] **Step 1: Write `coinfiliate/browser.py`**

```python
from contextlib import asynccontextmanager
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
VIEWPORT = {"width": 1280, "height": 800}


class BrowserSession:
    """Persistent context for Coinfiliate admin (keeps login). Not used for harvest."""
    def __init__(self, user_data_dir: Path, headless: bool = True):
        self.user_data_dir = Path(user_data_dir)
        self.headless = headless
        self._pw: Playwright | None = None
        self._ctx: BrowserContext | None = None

    async def __aenter__(self) -> BrowserContext:
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        self._ctx = await self._pw.chromium.launch_persistent_context(
            self.user_data_dir, headless=self.headless,
            user_agent=USER_AGENT, viewport=VIEWPORT,
        )
        return self._ctx

    async def __aexit__(self, *exc):
        if self._ctx:
            await self._ctx.close()
        if self._pw:
            await self._pw.stop()


@asynccontextmanager
async def harvest_browser(headless: bool = True):
    """Shared browser; caller creates fresh contexts per shop."""
    pw = await async_playwright().start()
    browser: Browser = await pw.chromium.launch(headless=headless)
    try:
        yield browser
    finally:
        await browser.close()
        await pw.stop()


@asynccontextmanager
async def fresh_context(browser: Browser):
    ctx = await browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
    try:
        yield ctx
    finally:
        await ctx.close()
```

- [ ] **Step 2: Write `coinfiliate/logging_setup.py`**

```python
import logging
from pathlib import Path
from datetime import datetime
import structlog


def configure(level: str = "INFO", log_dir: Path = Path("logs")) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    logging.basicConfig(
        level=getattr(logging, level),
        format="%(message)s",
        handlers=[
            logging.FileHandler(log_dir / f"run_{ts}.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
```

- [ ] **Step 3: Commit**

```bash
git add coinfiliate/browser.py coinfiliate/logging_setup.py
git commit -m "feat: browser factories and structlog setup"
```

---

## Task 11: Sync phase — login

**Files:**
- Create: `coinfiliate/sync.py`
- Create: `tests/integration/test_login.py`
- Create: `tests/fixtures/fake_coinfiliate_server.py`

- [ ] **Step 1: Write the local fake Coinfiliate server**

`tests/fixtures/fake_coinfiliate_server.py`:
```python
from aiohttp import web


async def _login_page(req):
    return web.Response(text="""
        <html><body>
          <form method="post" action="/login">
            <input type="email" name="email" />
            <input type="password" name="password" />
            <button type="submit">Sign in</button>
          </form>
        </body></html>
    """, content_type="text/html")


async def _do_login(req):
    raise web.HTTPFound("/admin/partner-shop")


async def _admin_page(req):
    return web.Response(text="<html><body><h1>Partner Shop</h1></body></html>",
                        content_type="text/html")


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/login", _login_page)
    app.router.add_post("/login", _do_login)
    app.router.add_get("/admin/partner-shop", _admin_page)
    return app
```

- [ ] **Step 2: Write the failing integration test**

`tests/integration/test_login.py`:
```python
import pytest
from aiohttp import web
from coinfiliate.browser import harvest_browser, fresh_context
from coinfiliate.sync import login
from tests.fixtures.fake_coinfiliate_server import make_app


@pytest.fixture
async def server():
    app = make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


@pytest.mark.integration
async def test_login_redirects_to_admin(server):
    async with harvest_browser(headless=True) as browser, fresh_context(browser) as ctx:
        page = await ctx.new_page()
        await login(page, login_url=f"{server}/login", email="a@b.com", password="x",
                    success_url_substring="/admin/")
        assert "/admin/" in page.url
```

- [ ] **Step 3: Implement `login()` in `coinfiliate/sync.py`**

```python
from playwright.async_api import Page
from coinfiliate.selectors import sel
from coinfiliate.logging_setup import get_logger

log = get_logger(__name__)


async def login(page: Page, *, login_url: str, email: str, password: str,
                success_url_substring: str = "/admin/") -> None:
    log.info("login.start", url=login_url)
    await page.goto(login_url)
    await page.fill(sel("login.email"), email)
    await page.fill(sel("login.password"), password)
    await page.click(sel("login.submit"))
    await page.wait_for_url(f"**{success_url_substring}**", timeout=30_000)
    log.info("login.ok", landed=page.url)
```

- [ ] **Step 4: Run test**

Run: `pytest tests/integration/test_login.py -v -m integration`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/sync.py tests/integration/test_login.py tests/fixtures/fake_coinfiliate_server.py
git commit -m "feat: sync.login with integration test against fake server"
```

---

## Task 12: Sync phase — Partner Shop sync + shop table scrape

**Files:**
- Modify: `coinfiliate/sync.py`
- Modify: `tests/fixtures/fake_coinfiliate_server.py` (add shop table + sync modal)

- [ ] **Step 1: Extend the fake server with a Partner Shop page**

Add to `fake_coinfiliate_server.py`:
```python
_SHOPS_STATE = []  # mutated by /admin/sync-shops


async def _partner_shop_page(req):
    rows = "".join(
        f'<tr data-cfi="{s["id"]}"><td><a href="/admin/partner-shop/{s["id"]}/edit" class="edit">Edit</a></td>'
        f'<td class="name">{s["name"]}</td><td class="network">{s["network"]}</td>'
        f'<td class="status">{s["status"]}</td></tr>'
        for s in _SHOPS_STATE
    )
    # Emulate the modal with a plain form (Playwright will click the button, fill inputs, click Sync Now).
    return web.Response(text=f"""
        <html><body>
          <button id="sync-open">Sync Partner Shop</button>
          <div role="dialog" id="sync-modal" style="display:none">
            <label>Network</label>
            <select role="combobox" id="net"><option>flexoffers</option><option>awin</option></select>
            <label>Page</label><input id="page" />
            <label>Page Size</label><input id="page-size" />
            <button id="sync-now">Sync Now</button>
          </div>
          <table><tbody id="rows">{rows}</tbody></table>
          <script>
            document.getElementById("sync-open").onclick = () =>
              document.getElementById("sync-modal").style.display="block";
            document.getElementById("sync-now").onclick = async () => {{
              await fetch("/admin/sync-shops", {{method: "POST"}});
              location.reload();
            }};
          </script>
        </body></html>
    """, content_type="text/html")


async def _sync_shops(req):
    _SHOPS_STATE.clear()
    _SHOPS_STATE.extend([
        {"id": "cfi-1", "name": "Notch", "network": "flexoffers", "status": "draft"},
        {"id": "cfi-2", "name": "Kryptek", "network": "flexoffers", "status": "draft"},
    ])
    return web.Response(status=204)


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/login", _login_page)
    app.router.add_post("/login", _do_login)
    app.router.add_get("/admin/partner-shop", _partner_shop_page)
    app.router.add_post("/admin/sync-shops", _sync_shops)
    return app
```

Update the selector file so the scraper can find rows on this simplified DOM — or keep the existing `shoplist.*` selectors; adjust only if the integration test fails.

- [ ] **Step 2: Write the failing test**

`tests/integration/test_sync_shops.py`:
```python
import pytest
from aiohttp import web
from coinfiliate.browser import harvest_browser, fresh_context
from coinfiliate.sync import login, sync_partner_shops, scrape_shops
from tests.fixtures.fake_coinfiliate_server import make_app


@pytest.fixture
async def server():
    app = make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


@pytest.mark.integration
async def test_sync_and_scrape(server):
    async with harvest_browser(headless=True) as browser, fresh_context(browser) as ctx:
        page = await ctx.new_page()
        await login(page, login_url=f"{server}/login", email="a@b.com", password="x")
        await page.goto(f"{server}/admin/partner-shop")
        await sync_partner_shops(page, network="flexoffers", page_num=1, page_size=100)
        shops = await scrape_shops(page)
        assert {s["name"] for s in shops} == {"Notch", "Kryptek"}
        assert all(s["edit_url"].endswith("/edit") for s in shops)
```

- [ ] **Step 3: Extend `coinfiliate/sync.py`**

```python
async def sync_partner_shops(page, *, network: str, page_num: int, page_size: int,
                             timeout_ms: int = 60_000) -> None:
    log.info("sync_shops.start", network=network)
    await page.click(sel("shoplist.sync_btn"))
    dlg = page.locator('div[role="dialog"]')
    await dlg.wait_for(state="visible", timeout=10_000)
    # Network select
    select = dlg.locator('select[role="combobox"], [role="combobox"]').first
    try:
        await select.select_option(network)
    except Exception:
        # Custom combobox — click to open, pick option by text
        await select.click()
        await page.click(f'text="{network}"')
    await dlg.locator("input").nth(0).fill(str(page_num))
    await dlg.locator("input").nth(1).fill(str(page_size))
    await dlg.locator('button:has-text("Sync Now")').click()
    await dlg.wait_for(state="hidden", timeout=timeout_ms)
    log.info("sync_shops.ok")


async def scrape_shops(page) -> list[dict]:
    rows = page.locator(sel("shoplist.row"))
    count = await rows.count()
    out = []
    for i in range(count):
        row = rows.nth(i)
        cfi = await row.get_attribute("data-cfi") or ""
        name = (await row.locator(".name").inner_text()).strip() if await row.locator(".name").count() else ""
        network = (await row.locator(".network").inner_text()).strip() if await row.locator(".network").count() else ""
        status = (await row.locator(".status").inner_text()).strip() if await row.locator(".status").count() else ""
        edit_href = await row.locator("a.edit, a:has-text('Edit')").first.get_attribute("href") or ""
        out.append({
            "coinfiliate_id": cfi, "name": name, "network": network,
            "status": status, "edit_url": edit_href,
        })
    return out
```

- [ ] **Step 4: Run test**

Run: `pytest tests/integration/test_sync_shops.py -v -m integration`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/sync.py tests/fixtures/fake_coinfiliate_server.py tests/integration/test_sync_shops.py
git commit -m "feat: sync_partner_shops and scrape_shops"
```

---

## Task 13: Sync phase — per-shop inner sync + orchestrator

**Files:**
- Modify: `coinfiliate/sync.py`
- Extend fake server with an Edit Shop page + Affiliate Links table

- [ ] **Step 1: Add to `fake_coinfiliate_server.py`**

```python
_LINKS_STATE: dict[str, list[dict]] = {}


async def _edit_shop_page(req):
    cfi = req.match_info["cfi"]
    links = _LINKS_STATE.get(cfi, [])
    rows = "".join(
        f'<div class="link" data-link-id="{l["id"]}"><span class="name">{l["name"]}</span>'
        f'<span class="url">{l["url"]}</span></div>'
        for l in links
    )
    return web.Response(text=f"""
        <html><body>
          <button>Affiliate Links</button>
          <button id="sync-aff">Sync Affiliate Link</button>
          <div role="dialog" id="sync-aff-modal" style="display:none">
            <label>Network</label><select role="combobox"><option>flexoffers</option></select>
            <label>Page</label><input />
            <label>Page Size</label><input />
            <button>Sync Now</button>
          </div>
          <div id="links">{rows}</div>
          <script>
            document.getElementById("sync-aff").onclick = async () => {{
              await fetch("/admin/partner-shop/{cfi}/sync-links", {{method: "POST"}});
              location.reload();
            }};
          </script>
        </body></html>
    """, content_type="text/html")


async def _sync_links(req):
    cfi = req.match_info["cfi"]
    _LINKS_STATE[cfi] = [
        {"id": f"{cfi}-L1", "name": "Ad 1", "url": f"https://track.example/g?id={cfi}-1"},
        {"id": f"{cfi}-L2", "name": "Ad 2", "url": f"https://track.example/g?id={cfi}-2"},
    ]
    return web.Response(status=204)


# Add routes:
app.router.add_get("/admin/partner-shop/{cfi}/edit", _edit_shop_page)
app.router.add_post("/admin/partner-shop/{cfi}/sync-links", _sync_links)
```

- [ ] **Step 2: Implement `sync_shop_affiliate_links` + `run_sync` in `coinfiliate/sync.py`**

```python
from coinfiliate.store import Store
from coinfiliate.config import Settings


async def sync_shop_affiliate_links(page, shop_edit_url: str, *, network: str,
                                    page_num: int, page_size: int,
                                    timeout_ms: int = 60_000) -> list[dict]:
    log.info("sync_links.start", shop_edit_url=shop_edit_url)
    await page.goto(shop_edit_url)
    # The Affiliate Links tab may be the default; click is idempotent.
    await page.locator(sel("editshop.tab_affiliate_links")).first.click()
    await page.click(sel("editshop.sync_affiliate_btn"))

    dlg = page.locator('div[role="dialog"]')
    await dlg.wait_for(state="visible", timeout=10_000)
    select = dlg.locator('[role="combobox"]').first
    try:
        await select.select_option(network)
    except Exception:
        await select.click()
        await page.click(f'text="{network}"')
    await dlg.locator("input").nth(0).fill(str(page_num))
    await dlg.locator("input").nth(1).fill(str(page_size))
    await dlg.locator('button:has-text("Sync Now")').click()
    await dlg.wait_for(state="hidden", timeout=timeout_ms)

    # Scrape the links list
    items = page.locator("#links .link")
    count = await items.count()
    out = []
    for i in range(count):
        it = items.nth(i)
        out.append({
            "link_id": await it.get_attribute("data-link-id") or "",
            "name": (await it.locator(".name").inner_text()).strip(),
            "affiliate_url": (await it.locator(".url").inner_text()).strip(),
        })
    return out


async def run_sync(settings: Settings, store: Store, browser_ctx) -> None:
    page = await browser_ctx.new_page()
    await login(page,
                login_url="https://www.coinfiliate.com/login",
                email=settings.coinfiliate_email,
                password=settings.coinfiliate_pass)

    for network in settings.networks:
        await page.goto("https://www.coinfiliate.com/admin/partner-shop")
        await sync_partner_shops(page, network=network,
                                 page_num=settings.sync.page,
                                 page_size=settings.sync.page_size)
        shops = await scrape_shops(page)
        for s in shops:
            await store.upsert_shop(
                coinfiliate_id=s["coinfiliate_id"], name=s["name"],
                network=network, advertiser_id=None, website_url=None,
                edit_url=s["edit_url"],
            )

    pending = await store.list_shops(status="pending")
    pending = pending[: settings.runner.max_shops_per_batch]
    for shop in pending:
        edit_url = shop["edit_url"]
        if edit_url.startswith("/"):
            edit_url = f"https://www.coinfiliate.com{edit_url}"
        links = await sync_shop_affiliate_links(page, edit_url,
                                                 network=shop["network"],
                                                 page_num=settings.sync.page,
                                                 page_size=settings.sync.page_size)
        for l in links:
            await store.upsert_affiliate_link(shop["id"], **l)
        if links:
            await store.mark_harvest_source(shop["id"], links[0]["link_id"])
```

- [ ] **Step 3: Write integration test**

`tests/integration/test_sync_full.py`:
```python
import pytest
from aiohttp import web
from coinfiliate.browser import harvest_browser, fresh_context
from coinfiliate.store import Store
from coinfiliate.sync import login, sync_partner_shops, scrape_shops, sync_shop_affiliate_links
from tests.fixtures.fake_coinfiliate_server import make_app


@pytest.mark.integration
async def test_full_sync_writes_shops_and_links(tmp_path):
    app = make_app()
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    store = Store(tmp_path / "t.db")
    await store.init()

    async with harvest_browser(headless=True) as browser, fresh_context(browser) as ctx:
        page = await ctx.new_page()
        await login(page, login_url=f"{base}/login", email="a@b.com", password="x")
        await page.goto(f"{base}/admin/partner-shop")
        await sync_partner_shops(page, network="flexoffers", page_num=1, page_size=100)
        shops = await scrape_shops(page)
        for s in shops:
            await store.upsert_shop(coinfiliate_id=s["coinfiliate_id"], name=s["name"],
                                    network="flexoffers", advertiser_id=None,
                                    website_url=None, edit_url=f"{base}{s['edit_url']}")

        for shop in await store.list_shops(status="pending"):
            links = await sync_shop_affiliate_links(page, shop["edit_url"],
                                                    network="flexoffers", page_num=1, page_size=100)
            for l in links:
                await store.upsert_affiliate_link(shop["id"], **l)
            await store.mark_harvest_source(shop["id"], links[0]["link_id"])

    all_shops = await store.list_shops()
    assert len(all_shops) == 2
    for s in all_shops:
        links = await store.list_affiliate_links(s["id"])
        assert len(links) == 2
        assert sum(1 for l in links if l["is_harvest_source"]) == 1

    await store.close()
    await runner.cleanup()
```

- [ ] **Step 4: Run test**

Run: `pytest tests/integration/test_sync_full.py -v -m integration`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/sync.py tests/fixtures/fake_coinfiliate_server.py tests/integration/test_sync_full.py
git commit -m "feat: per-shop affiliate-link sync + run_sync orchestrator"
```

---

## Task 14: Harvest phase — recorders, consent, signal collection

**Files:**
- Create: `coinfiliate/harvest.py`
- Create: `tests/fixtures/fake_merchant_server.py`
- Create: `tests/integration/test_harvest_signals.py`

- [ ] **Step 1: Write the fake merchant server**

`tests/fixtures/fake_merchant_server.py`:
```python
from aiohttp import web


async def _aff_redirect(req):
    # Simulates an affiliate network redirecting to the final merchant with a tracker domain hop.
    raise web.HTTPFound("/tracker")


async def _tracker(req):
    raise web.HTTPFound("/merchant")


async def _merchant(req):
    # Sets a Klaviyo-style cookie on the response.
    resp = web.Response(text="""
        <html><body>
          <div id="consent"><button id="accept">Accept</button></div>
          <script>document.getElementById("accept").onclick = () =>
            document.cookie = "__kla_id=clickid-abc; path=/";
          </script>
        </body></html>
    """, content_type="text/html")
    return resp


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/aff", _aff_redirect)
    app.router.add_get("/tracker", _tracker)
    app.router.add_get("/merchant", _merchant)
    return app
```

- [ ] **Step 2: Write the failing integration test**

`tests/integration/test_harvest_signals.py`:
```python
import pytest
from aiohttp import web
from coinfiliate.browser import harvest_browser, fresh_context
from coinfiliate.harvest import collect_signals
from tests.fixtures.fake_merchant_server import make_app


@pytest.fixture
async def merchant():
    app = make_app()
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


@pytest.mark.integration
async def test_collect_signals_follows_redirects_and_clicks_consent(merchant):
    async with harvest_browser(headless=True) as browser, fresh_context(browser) as ctx:
        page = await ctx.new_page()
        sig = await collect_signals(page, ctx, f"{merchant}/aff",
                                    consent_texts=["Accept"], consent_wait_ms=500,
                                    networkidle_timeout_s=10)

    names = [c["name"] for c in sig["cookies"]]
    assert "__kla_id" in names
    assert sig["final_url"].endswith("/merchant")
    assert any("/tracker" in u for u in sig["redirect_chain"])
```

- [ ] **Step 3: Implement `coinfiliate/harvest.py`**

```python
from urllib.parse import urlparse
from playwright.async_api import BrowserContext, Page
from coinfiliate.decision import extract_etld1
from coinfiliate.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_CONSENT_TEXTS = [
    "Accept", "Allow All", "Accept All", "I Accept", "Agree", "Got it",
    "Alle akzeptieren", "Accepter", "Aceptar", "同意", "同意する",
]


async def collect_signals(page: Page, context: BrowserContext, affiliate_url: str,
                          *, consent_texts: list[str] = None,
                          consent_wait_ms: int = 2000,
                          networkidle_timeout_s: int = 15) -> dict:
    consent_texts = consent_texts or DEFAULT_CONSENT_TEXTS
    response_urls: list[str] = []
    redirect_chain: list[str] = []

    def _on_response(resp):
        response_urls.append(resp.url)
        if 300 <= resp.status < 400:
            redirect_chain.append(resp.url)

    context.on("response", _on_response)

    await page.goto(affiliate_url, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=networkidle_timeout_s * 1000)
    except Exception:
        pass  # networkidle is best-effort

    # Auto-accept consent
    for text in consent_texts:
        try:
            btn = page.locator(f"button:has-text('{text}'), a:has-text('{text}')").first
            if await btn.is_visible(timeout=500):
                await btn.click()
                await page.wait_for_timeout(consent_wait_ms)
                break
        except Exception:
            continue

    cookies = await context.cookies()
    final_url = page.url
    final_etld1 = extract_etld1(final_url)

    # Tracker domains: any response host whose eTLD+1 differs from the landed domain
    tracker_domains = sorted({
        extract_etld1(u) for u in response_urls
        if extract_etld1(u) and extract_etld1(u) != final_etld1
    })

    return {
        "final_url": final_url,
        "final_etld1": final_etld1,
        "cookies": cookies,
        "redirect_chain": redirect_chain,
        "tracker_domains": tracker_domains,
    }
```

- [ ] **Step 4: Run test**

Run: `pytest tests/integration/test_harvest_signals.py -v -m integration`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/harvest.py tests/fixtures/fake_merchant_server.py tests/integration/test_harvest_signals.py
git commit -m "feat: collect_signals (cookies + redirect chain + tracker domains)"
```

---

## Task 15: Harvest phase — per-shop runner + orchestrator

**Files:**
- Modify: `coinfiliate/harvest.py`
- Create: `tests/integration/test_harvest_shop.py`

- [ ] **Step 1: Write integration test**

```python
# tests/integration/test_harvest_shop.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web
from coinfiliate.browser import harvest_browser
from coinfiliate.store import Store
from coinfiliate.harvest import harvest_shop
from coinfiliate.config import Settings, HarvestConfig, LLMConfig, SyncConfig, RunnerConfig, WritebackConfig, LoggingConfig
from tests.fixtures.fake_merchant_server import make_app


def _settings():
    return Settings(
        coinfiliate_email="a@b.com", coinfiliate_pass="x", openai_api_key="k",
        networks=["flexoffers"], sync=SyncConfig(), runner=RunnerConfig(),
        harvest=HarvestConfig(networkidle_timeout_seconds=5, consent_wait_ms=300, review_threshold=0.0),
        writeback=WritebackConfig(), llm=LLMConfig(), logging=LoggingConfig(),
    )


@pytest.mark.integration
async def test_harvest_shop_writes_row_and_updates_status(tmp_path):
    app = make_app()
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    store = Store(tmp_path / "t.db"); await store.init()
    sid = await store.upsert_shop(coinfiliate_id="x", name="Fake", network="flexoffers",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    await store.upsert_affiliate_link(sid, link_id="L1", name="L1", affiliate_url=f"{base}/aff")
    await store.mark_harvest_source(sid, "L1")

    llm = MagicMock(); llm.analyze = AsyncMock()  # should not be called; loose match hits __kla_id

    async with harvest_browser(headless=True) as browser:
        await harvest_shop(store, shop_id=sid, settings=_settings(), llm=llm, browser=browser)

    shop = (await store.list_shops())[0]
    assert shop["status"] == "harvested"
    latest = await store.latest_harvest(sid)
    assert latest["primary_cookie_name"] == "__kla_id"
    assert latest["decision_source"] == "heuristic"

    await store.close()
    await runner.cleanup()
```

- [ ] **Step 2: Add `harvest_shop` + `run_harvest` to `coinfiliate/harvest.py`**

```python
import asyncio
import json
import random
from coinfiliate.browser import fresh_context
from coinfiliate.decision import decide
from coinfiliate.models import HarvestContext


async def harvest_shop(store, *, shop_id: int, settings, llm, browser) -> None:
    shop = next(s for s in await store.list_shops() if s["id"] == shop_id)
    src = await store.get_harvest_source(shop_id)
    if not src:
        await store.update_shop_status(shop_id, "failed", last_error="no harvest_source link")
        return

    try:
        async with fresh_context(browser) as ctx:
            page = await ctx.new_page()
            sig = await collect_signals(
                page, ctx, src["affiliate_url"],
                consent_wait_ms=settings.harvest.consent_wait_ms,
                networkidle_timeout_s=settings.harvest.networkidle_timeout_seconds,
            )
        hctx = HarvestContext(
            shop_name=shop["name"], network=shop["network"],
            final_url=sig["final_url"], final_etld1=sig["final_etld1"],
            cookies=sig["cookies"], redirect_chain=sig["redirect_chain"],
            tracker_domains=sig["tracker_domains"],
        )
        decision = await decide(hctx, llm=llm)
        ok = decision.primary_cookie_name is not None

        await store.insert_harvest(
            shop_id=shop_id,
            final_url=sig["final_url"], final_etld1=sig["final_etld1"],
            cookies=sig["cookies"], redirect_chain=sig["redirect_chain"],
            tracker_domains=sig["tracker_domains"],
            primary_cookie_name=decision.primary_cookie_name,
            tracking_cookie_names=decision.tracking_cookie_names,
            checkout_domains=decision.checkout_domains,
            tracking_cookie_domains=decision.tracking_cookie_domains,
            decision_source=decision.decision_source,
            confidence=decision.confidence,
            llm_rationale=decision.rationale, ok=ok,
        )

        if ok and decision.confidence >= settings.harvest.review_threshold:
            await store.update_shop_status(shop_id, "harvested")
        else:
            await store.update_shop_status(shop_id, "needs_review")
    except Exception as e:
        await store.update_shop_status(shop_id, "failed", last_error=f"{type(e).__name__}: {e}")
        raise


async def run_harvest(store, *, settings, llm, browser) -> None:
    pending = await store.list_shops(status="pending")
    pending = pending[: settings.runner.max_shops_per_batch]
    sem = asyncio.Semaphore(settings.runner.max_concurrency)

    async def _one(shop_id: int):
        async with sem:
            lo, hi = settings.runner.inter_shop_jitter_ms
            await asyncio.sleep(random.randint(lo, hi) / 1000)
            try:
                await harvest_shop(store, shop_id=shop_id, settings=settings, llm=llm, browser=browser)
            except Exception as e:
                log.error("harvest.shop_failed", shop_id=shop_id, err=str(e))

    await asyncio.gather(*[_one(s["id"]) for s in pending])
```

- [ ] **Step 3: Run test**

Run: `pytest tests/integration/test_harvest_shop.py -v -m integration`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add coinfiliate/harvest.py tests/integration/test_harvest_shop.py
git commit -m "feat: harvest_shop per-shop runner + run_harvest orchestrator"
```

---

## Task 16: Writeback phase — modal fill + save + verify

**Files:**
- Create: `coinfiliate/writeback.py`
- Save: `tests/fixtures/coinfiliate_edit_page.html` (hand-authored mini-fixture of the modal DOM)
- Create: `tests/integration/test_writeback_modal.py`

- [ ] **Step 1: Create the HTML fixture**

`tests/fixtures/coinfiliate_edit_page.html`:

```html
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Edit Partner Shop</title></head>
<body>
  <h1>Edit Partner Shop</h1>

  <button>Affiliate Links</button>

  <label><input type="checkbox" id="selectAll" /> Select All</label>
  <div class="link" data-id="L1">Link 1</div>
  <div class="link" data-id="L2">Link 2</div>

  <button id="selectedData">Selected Data (2)</button>
  <div role="menu" id="menu" style="display:none">
    <div>Edit</div>
  </div>

  <div role="dialog" id="modal" style="display:none">
    <h2>Edit Selected Partner Shop Links</h2>

    <label>Published</label>
    <button role="switch" name="Published" aria-checked="false" id="pubToggle">off</button>

    <div>
      <label>Primary Tracking Cookie Name</label>
      <span><input id="primaryCookieName" /></span>
    </div>

    <div id="checkoutDomains">Checkout Domains
      <button type="button" onclick="addInput('checkoutDomains')">Add</button>
    </div>

    <div id="trackingNames">Tracking Cookie Names
      <button type="button" onclick="addInput('trackingNames')">Add</button>
    </div>

    <div id="trackingDomains">Tracking Cookie Domains
      <button type="button" onclick="addInput('trackingDomains')">Add</button>
    </div>

    <button id="saveChanges">Save Changes</button>
  </div>

  <button id="publishedBtn">Published</button>
  <button id="updateBtn">Update</button>

  <pre id="out"></pre>

  <script>
    const $ = (s) => document.querySelector(s);
    const state = { published: false, submitted: false };

    $('#selectedData').onclick = () => {
      $('#menu').style.display = 'block';
      $('#menu div').onclick = () => { $('#modal').style.display = 'block'; $('#menu').style.display = 'none'; };
    };

    $('#pubToggle').onclick = () => {
      const t = $('#pubToggle');
      const on = t.getAttribute('aria-checked') !== 'true';
      t.setAttribute('aria-checked', on ? 'true' : 'false');
      t.textContent = on ? 'on' : 'off';
      state.published = on;
    };

    function addInput(containerId) {
      const c = document.getElementById(containerId);
      const i = document.createElement('input');
      c.appendChild(i);
    }

    function collect(containerId) {
      return [...document.querySelectorAll('#' + containerId + ' input')].map(i => i.value).filter(v => v);
    }

    $('#saveChanges').onclick = () => {
      const payload = {
        published: state.published,
        primary_cookie_name: $('#primaryCookieName').value,
        tracking_cookie_names: collect('trackingNames'),
        checkout_domains: collect('checkoutDomains'),
        tracking_cookie_domains: collect('trackingDomains'),
      };
      $('#out').textContent = JSON.stringify(payload);
      $('#modal').style.display = 'none';
    };

    $('#publishedBtn').onclick = () => { state.outerPublished = true; };
    $('#updateBtn').onclick = () => { state.submitted = true; };
  </script>
</body></html>
```

The harness-test reads `#out` to verify what was submitted. Add/extend as needed for verification flows in Task 17.

- [ ] **Step 2: Write the failing test**

`tests/integration/test_writeback_modal.py`:
```python
import json
import pytest
from pathlib import Path
from aiohttp import web
from coinfiliate.browser import harvest_browser, fresh_context
from coinfiliate.writeback import fill_bulk_edit_modal, save_and_verify


@pytest.fixture
async def page_server(tmp_path):
    html_path = Path("tests/fixtures/coinfiliate_edit_page.html")
    async def handler(req):
        return web.Response(text=html_path.read_text(), content_type="text/html")
    app = web.Application(); app.router.add_get("/edit", handler)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    yield base
    await runner.cleanup()


@pytest.mark.integration
async def test_writeback_fills_modal_and_submits(page_server):
    decision = {
        "primary_cookie_name": "__kla_id",
        "tracking_cookie_names": ["__kla_id"],
        "checkout_domains": ["kryptek.com"],
        "tracking_cookie_domains": ["kryptek.com"],
    }
    async with harvest_browser(headless=True) as browser, fresh_context(browser) as ctx:
        page = await ctx.new_page()
        await page.goto(f"{page_server}/edit")
        # Open the modal (select all → selected data → edit)
        await page.click('label:has-text("Select All") input[type="checkbox"]')
        await page.click('button:has-text("Selected Data")')
        await page.click('div[role="menu"] >> text=Edit')
        await page.locator('div[role="dialog"]').wait_for(state="visible")

        await fill_bulk_edit_modal(page, decision)
        submitted = await save_and_verify(page, decision)

    assert submitted["primary_cookie_name"] == "__kla_id"
    assert submitted["checkout_domains"] == ["kryptek.com"]
    assert submitted["published"] is True
```

- [ ] **Step 3: Implement `coinfiliate/writeback.py`**

```python
import json
from playwright.async_api import Page
from coinfiliate.selectors import sel
from coinfiliate.logging_setup import get_logger

log = get_logger(__name__)


async def fill_bulk_edit_modal(page: Page, decision: dict) -> None:
    dlg = page.locator(sel("modal.root"))
    await dlg.wait_for(state="visible", timeout=10_000)

    # Published toggle: turn ON if currently OFF
    toggle = dlg.locator(sel("modal.published_toggle"))
    if await toggle.get_attribute("aria-checked") != "true":
        await toggle.click()

    # Primary cookie name
    primary_name = decision["primary_cookie_name"]
    if primary_name:
        await dlg.locator(sel("modal.primary_cookie_name")).fill(primary_name)

    # Tracking cookie names list
    for name in decision.get("tracking_cookie_names", []):
        await dlg.locator(sel("modal.tracking_names_add")).click()
        await dlg.locator(sel("modal.tracking_names_input_last")).fill(name)

    # Checkout domains list
    for d in decision.get("checkout_domains", []):
        await dlg.locator(sel("modal.checkout_domains_add")).click()
        await dlg.locator(sel("modal.checkout_domain_input_last")).fill(d)

    # Tracking cookie domains list
    for d in decision.get("tracking_cookie_domains", []):
        await dlg.locator(sel("modal.tracking_domains_add")).click()
        await dlg.locator(sel("modal.tracking_domains_input_last")).fill(d)


async def save_and_verify(page: Page, decision: dict) -> dict:
    await page.locator(sel("modal.save_changes")).click()
    await page.locator(sel("modal.root")).wait_for(state="hidden", timeout=10_000)

    # Outer-page Published + Update buttons
    await page.locator(sel("editshop.published_btn")).click()
    await page.locator(sel("editshop.update_btn")).click()

    # The fixture writes a JSON summary into <pre id="out"> for assertions.
    # In the real UI, verification means reloading and reading the field back.
    out = await page.locator("#out").inner_text()
    return json.loads(out) if out.strip() else {}
```

- [ ] **Step 4: Run test**

Run: `pytest tests/integration/test_writeback_modal.py -v -m integration`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coinfiliate/writeback.py tests/fixtures/coinfiliate_edit_page.html tests/integration/test_writeback_modal.py
git commit -m "feat: writeback modal fill + save/verify against HTML fixture"
```

---

## Task 17: Writeback phase — per-shop + orchestrator

**Files:**
- Modify: `coinfiliate/writeback.py`

- [ ] **Step 1: Add `writeback_shop` + `run_writeback`**

```python
import json
from coinfiliate.store import Store


async def writeback_shop(store: Store, *, shop_id: int, settings, browser_ctx, dry_run: bool = False) -> None:
    shop = next(s for s in await store.list_shops() if s["id"] == shop_id)
    latest = await store.latest_harvest(shop_id)
    if not latest or not latest["ok"]:
        await store.update_shop_status(shop_id, "needs_review",
                                       last_error="no ok harvest row for writeback")
        return

    decision = {
        "primary_cookie_name": latest["primary_cookie_name"],
        "tracking_cookie_names": json.loads(latest["tracking_cookie_names_json"] or "[]"),
        "checkout_domains":     json.loads(latest["checkout_domains_json"] or "[]"),
        "tracking_cookie_domains": json.loads(latest["tracking_cookie_domains_json"] or "[]"),
    }

    try:
        page = await browser_ctx.new_page()
        await page.goto(shop["edit_url"])
        # Re-sync if the list is empty (defensive; stale session)
        await page.locator(sel("editshop.tab_affiliate_links")).first.click()
        links_count = await page.locator(".link").count()
        if links_count == 0:
            from coinfiliate.sync import sync_shop_affiliate_links
            await sync_shop_affiliate_links(page, shop["edit_url"],
                                             network=shop["network"],
                                             page_num=settings.sync.page,
                                             page_size=settings.sync.page_size)
        # Select all → Selected Data → Edit
        await page.click(sel("editshop.select_all"))
        await page.click(sel("editshop.selected_data_dd"))
        await page.click(sel("editshop.edit_selected"))

        await fill_bulk_edit_modal(page, decision)

        if dry_run:
            # Cancel instead of save; leave shop status unchanged so a later real run re-tries.
            await page.locator('button:has-text("Cancel")').first.click()
            log.info("writeback.dry_run_done", shop_id=shop_id)
            return

        submitted = await save_and_verify(page, decision)

        if settings.writeback.verify_after_save:
            if submitted.get("primary_cookie_name") != decision["primary_cookie_name"]:
                await store.update_shop_status(
                    shop_id, "failed",
                    last_error=f"verify mismatch: got {submitted.get('primary_cookie_name')!r}",
                )
                return

        await store.update_shop_status(shop_id, "writeback_done")
    except Exception as e:
        await store.update_shop_status(shop_id, "failed", last_error=f"{type(e).__name__}: {e}")
        raise


async def run_writeback(store, *, settings, browser_ctx, dry_run: bool = False) -> None:
    shops = await store.list_shops(status="harvested")
    shops = shops[: settings.runner.max_shops_per_batch]
    for shop in shops:
        try:
            await writeback_shop(store, shop_id=shop["id"], settings=settings,
                                 browser_ctx=browser_ctx, dry_run=dry_run)
        except Exception as e:
            log.error("writeback.failed", shop_id=shop["id"], err=str(e))
```

- [ ] **Step 2: Extend the fixture HTML so that, after the outer Update button is clicked, the page redirects to itself (or stays) with the same `#out` present, so a second reload shows the persisted state. Then extend `test_writeback_modal.py` to add:**

```python
@pytest.mark.integration
async def test_run_writeback_marks_shop_done(tmp_path, page_server):
    from coinfiliate.store import Store
    from coinfiliate.writeback import run_writeback
    from coinfiliate.config import Settings, SyncConfig, RunnerConfig, HarvestConfig, WritebackConfig, LLMConfig, LoggingConfig

    store = Store(tmp_path / "t.db"); await store.init()
    sid = await store.upsert_shop(coinfiliate_id="c1", name="Kryptek", network="flexoffers",
                                  advertiser_id=None, website_url=None, edit_url=f"{page_server}/edit")
    await store.upsert_affiliate_link(sid, link_id="L1", name="L1", affiliate_url="u")
    await store.mark_harvest_source(sid, "L1")
    await store.insert_harvest(shop_id=sid, final_url="https://kryptek.com/", final_etld1="kryptek.com",
        cookies=[{"name": "__kla_id", "value": "v"}], redirect_chain=[], tracker_domains=[],
        primary_cookie_name="__kla_id", tracking_cookie_names=["__kla_id"],
        checkout_domains=["kryptek.com"], tracking_cookie_domains=["kryptek.com"],
        decision_source="heuristic", confidence=0.6, llm_rationale=None, ok=True)
    await store.update_shop_status(sid, "harvested")

    settings = Settings(coinfiliate_email="a@b.com", coinfiliate_pass="x",
                        openai_api_key="k", networks=["flexoffers"],
                        sync=SyncConfig(), runner=RunnerConfig(),
                        harvest=HarvestConfig(), writeback=WritebackConfig(verify_after_save=True),
                        llm=LLMConfig(), logging=LoggingConfig())

    from coinfiliate.browser import harvest_browser, fresh_context
    async with harvest_browser(headless=True) as browser, fresh_context(browser) as ctx:
        await run_writeback(store, settings=settings, browser_ctx=ctx)

    final = (await store.list_shops())[0]
    assert final["status"] == "writeback_done"
    await store.close()
```

- [ ] **Step 3: Run test**

Run: `pytest tests/integration/test_writeback_modal.py -v -m integration`
Expected: both tests PASS.

- [ ] **Step 4: Commit**

```bash
git add coinfiliate/writeback.py tests/integration/test_writeback_modal.py tests/fixtures/coinfiliate_edit_page.html
git commit -m "feat: writeback_shop and run_writeback orchestrator"
```

---

## Task 18: CLI (sync | harvest | writeback | run | doctor | review)

**Files:**
- Create: `coinfiliate/cli.py`
- Modify: `main.py`

- [ ] **Step 1: Implement `coinfiliate/cli.py`**

```python
import asyncio
from pathlib import Path
import typer
from coinfiliate.config import load_settings
from coinfiliate.store import Store
from coinfiliate.browser import BrowserSession, harvest_browser
from coinfiliate.logging_setup import configure, get_logger

app = typer.Typer(no_args_is_help=True)
log = get_logger(__name__)


def _settings_and_store(config_path: Path, db_path: Path):
    s = load_settings(config_path)
    configure(level=s.logging.level)
    store = Store(db_path)
    return s, store


def _make_llm(settings):
    if settings.llm.provider == "openai":
        from openai import AsyncOpenAI
        from coinfiliate.llm.openai_client import OpenAICookieAnalyzer
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        return OpenAICookieAnalyzer(client=client, model=settings.llm.model,
                                    max_retries=settings.llm.max_retries,
                                    timeout_seconds=settings.llm.timeout_seconds)
    elif settings.llm.provider == "gemini":
        import google.generativeai as genai
        from coinfiliate.llm.gemini_client import GeminiCookieAnalyzer
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.llm.model)
        return GeminiCookieAnalyzer(model=model, max_retries=settings.llm.max_retries)
    raise typer.BadParameter(f"Unknown LLM provider: {settings.llm.provider}")


@app.command()
def sync(config: Path = Path("config.yaml"), db: Path = Path("state.db"),
         limit: int | None = typer.Option(None, help="Cap number of shops processed; overrides max_shops_per_batch")):
    """Pull Partner Shops + affiliate links into SQLite."""
    from coinfiliate.sync import run_sync

    async def _run():
        s, store = _settings_and_store(config, db)
        if limit is not None:
            s.runner.max_shops_per_batch = limit
        await store.init()
        try:
            async with BrowserSession(Path(".playwright/coinfiliate")) as ctx:
                await run_sync(s, store, ctx)
        finally:
            await store.close()
    asyncio.run(_run())


@app.command()
def harvest(config: Path = Path("config.yaml"), db: Path = Path("state.db"),
            limit: int | None = typer.Option(None)):
    """For each pending shop: open affiliate URL, decide, store harvest row."""
    from coinfiliate.harvest import run_harvest

    async def _run():
        s, store = _settings_and_store(config, db)
        if limit is not None:
            s.runner.max_shops_per_batch = limit
        await store.init()
        llm = _make_llm(s)
        try:
            async with harvest_browser(headless=True) as browser:
                await run_harvest(store, settings=s, llm=llm, browser=browser)
        finally:
            await store.close()
    asyncio.run(_run())


@app.command()
def writeback(config: Path = Path("config.yaml"), db: Path = Path("state.db"),
              limit: int | None = typer.Option(None),
              dry_run: bool = typer.Option(False, "--dry-run",
                                           help="Fill fields but cancel instead of saving")):
    """For each harvested shop: drive Edit modal, save, verify."""
    from coinfiliate.writeback import run_writeback

    async def _run():
        s, store = _settings_and_store(config, db)
        if limit is not None:
            s.runner.max_shops_per_batch = limit
        if dry_run:
            s.writeback.verify_after_save = False  # no save happens in dry-run
        await store.init()
        try:
            async with BrowserSession(Path(".playwright/coinfiliate")) as ctx:
                await run_writeback(store, settings=s, browser_ctx=ctx, dry_run=dry_run)
        finally:
            await store.close()
    asyncio.run(_run())


@app.command()
def run(config: Path = Path("config.yaml"), db: Path = Path("state.db"),
        limit: int | None = typer.Option(None),
        dry_run: bool = typer.Option(False, "--dry-run")):
    """sync → harvest → writeback."""
    sync(config=config, db=db, limit=limit)
    harvest(config=config, db=db, limit=limit)
    writeback(config=config, db=db, limit=limit, dry_run=dry_run)


@app.command()
def doctor(config: Path = Path("config.yaml")):
    """Validate every selector against a live throwaway shop."""
    from coinfiliate.selectors import SELECTORS
    typer.echo("Selectors defined:")
    for k, v in SELECTORS.items():
        typer.echo(f"  {k:40s} {v}")
    typer.echo("\nTo validate live: run against a throwaway shop with --live (not implemented in v1).")


@app.command()
def review(config: Path = Path("config.yaml"), db: Path = Path("state.db")):
    """List needs_review shops for manual decision."""
    async def _run():
        s, store = _settings_and_store(config, db)
        await store.init()
        rows = await store.list_shops(status="needs_review")
        for r in rows:
            latest = await store.latest_harvest(r["id"])
            typer.echo(f"[{r['id']}] {r['name']} ({r['network']}) — last_error={r['last_error']}")
            if latest:
                typer.echo(f"    decision_source={latest['decision_source']} confidence={latest['confidence']}")
        await store.close()
    asyncio.run(_run())


def main():
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Replace `main.py` with a thin shim**

```python
from coinfiliate.cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-test the CLI help output**

Run: `python main.py --help`
Expected: shows `sync | harvest | writeback | run | doctor | review`.

Run: `python main.py doctor`
Expected: prints the selector table.

- [ ] **Step 4: Commit**

```bash
git add coinfiliate/cli.py main.py
git commit -m "feat: Typer CLI with sync/harvest/writeback/run/doctor/review"
```

---

## Task 19: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace README with the v2 usage guide**

```markdown
# Coinfiliate Automation

Automated cookie harvester for the Coinfiliate Partner Shop dashboard.

## Usage

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # fill in COINFILIATE_EMAIL, COINFILIATE_PASS, OPENAI_API_KEY or GEMINI_API_KEY

python main.py run              # sync → harvest → writeback
python main.py sync             # just pull shops + affiliate links into state.db
python main.py harvest          # decide cookies for pending shops
python main.py writeback        # push decisions to Coinfiliate
python main.py doctor           # selector sanity check
python main.py review           # list needs_review shops
```

See `config.yaml` for tuning (concurrency, review threshold, LLM provider).
See `docs/superpowers/specs/2026-04-24-coinfiliate-automation-design.md` for design.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README for v2 CLI"
```

---

## Summary

| # | Task | Files | Key output |
|---|---|---|---|
| 1 | Project bootstrap | `requirements.txt`, `pyproject.toml`, `config.yaml`, `.env.example`, package dirs | pytest runs green |
| 2 | Config loader | `coinfiliate/config.py` | `load_settings()` |
| 3 | SQLite store | `schema.sql`, `coinfiliate/store.py` | `Store` class, 3 tables |
| 4 | Domain + heuristics | `coinfiliate/decision.py` (part 1) | `extract_etld1`, `strict_match`, `loose_match` |
| 5 | Data models | `coinfiliate/models.py` | `HarvestContext`, `HarvestDecision` |
| 6 | OpenAI analyzer | `coinfiliate/llm/base.py`, `prompt.py`, `openai_client.py` | `OpenAICookieAnalyzer` |
| 7 | Gemini analyzer | `coinfiliate/llm/gemini_client.py` | `GeminiCookieAnalyzer` |
| 8 | Decision orchestrator | `coinfiliate/decision.py` (part 2) | `decide()` |
| 9 | Selectors module | `coinfiliate/selectors.py` | `SELECTORS` dict |
| 10 | Browser + logging | `coinfiliate/browser.py`, `logging_setup.py` | `harvest_browser`, `fresh_context`, `configure()` |
| 11 | Sync: login | `coinfiliate/sync.py` + fake server | `login()` |
| 12 | Sync: partner shops + scrape | `coinfiliate/sync.py` | `sync_partner_shops`, `scrape_shops` |
| 13 | Sync: inner sync + orchestrator | `coinfiliate/sync.py` | `sync_shop_affiliate_links`, `run_sync` |
| 14 | Harvest: signals | `coinfiliate/harvest.py` + fake merchant | `collect_signals` |
| 15 | Harvest: shop + orchestrator | `coinfiliate/harvest.py` | `harvest_shop`, `run_harvest` |
| 16 | Writeback: modal | `coinfiliate/writeback.py` + HTML fixture | `fill_bulk_edit_modal`, `save_and_verify` |
| 17 | Writeback: orchestrator | `coinfiliate/writeback.py` | `writeback_shop`, `run_writeback` |
| 18 | CLI | `coinfiliate/cli.py`, `main.py` | Typer entry points |
| 19 | README | `README.md` | docs |

Total: 19 tasks.
