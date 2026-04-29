from __future__ import annotations

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
