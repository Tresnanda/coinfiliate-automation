from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
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

    llm = MagicMock(); llm.analyze = AsyncMock()  # heuristic should hit __kla_id; LLM not called

    async with harvest_browser(headless=True) as browser:
        await harvest_shop(store, shop_id=sid, settings=_settings(), llm=llm, browser=browser)

    shop = (await store.list_shops())[0]
    assert shop["status"] == "harvested"
    latest = await store.latest_harvest(sid)
    assert latest["primary_cookie_name"] == "__kla_id"
    assert latest["decision_source"] == "heuristic"
    llm.analyze.assert_not_called()

    await store.close()
    await runner.cleanup()
