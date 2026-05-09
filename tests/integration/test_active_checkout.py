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
