from __future__ import annotations

import json
import pytest
from pathlib import Path
from aiohttp import web
from coinfiliate.browser import harvest_browser, fresh_context
from coinfiliate.writeback import fill_bulk_edit_modal, save_and_verify


@pytest.fixture
async def page_server():
    html_path = Path("tests/fixtures/coinfiliate_edit_page.html")
    async def handler(req):
        return web.Response(text=html_path.read_text(encoding="utf-8"), content_type="text/html")
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
        # Open the modal (select all -> selected data -> edit)
        await page.click('label:has-text("Select All") input[type="checkbox"]')
        await page.click('button:has-text("Selected Data")')
        await page.click('div[role="menu"] >> text=Edit')
        await page.locator('div[role="dialog"]').wait_for(state="visible")

        await fill_bulk_edit_modal(page, decision)
        submitted = await save_and_verify(page, decision)

    assert submitted["primary_cookie_name"] == "__kla_id"
    assert submitted["checkout_domains"] == ["kryptek.com"]
    assert submitted["published"] is True


@pytest.mark.integration
async def test_run_writeback_marks_shop_done(tmp_path, page_server):
    from coinfiliate.store import Store
    from coinfiliate.writeback import run_writeback
    from coinfiliate.config import (
        Settings, SyncConfig, RunnerConfig, HarvestConfig,
        WritebackConfig, LLMConfig, LoggingConfig,
    )

    store = Store(tmp_path / "t.db")
    await store.init()
    sid = await store.upsert_shop(
        coinfiliate_id="c1", name="Kryptek", network="flexoffers",
        advertiser_id=None, website_url=None, edit_url=f"{page_server}/edit",
    )
    await store.upsert_affiliate_link(sid, link_id="L1", name="L1", affiliate_url="u")
    await store.mark_harvest_source(sid, "L1")
    await store.insert_harvest(
        shop_id=sid, final_url="https://kryptek.com/", final_etld1="kryptek.com",
        cookies=[{"name": "__kla_id", "value": "v"}], redirect_chain=[], tracker_domains=[],
        primary_cookie_name="__kla_id", tracking_cookie_names=["__kla_id"],
        checkout_domains=["kryptek.com"], tracking_cookie_domains=["kryptek.com"],
        decision_source="heuristic", confidence=0.6, llm_rationale=None, ok=True,
    )
    await store.update_shop_status(sid, "harvested")

    settings = Settings(
        coinfiliate_email="a@b.com", coinfiliate_pass="x", openai_api_key="k",
        networks=["flexoffers"],
        sync=SyncConfig(), runner=RunnerConfig(),
        harvest=HarvestConfig(),
        writeback=WritebackConfig(verify_after_save=True),
        llm=LLMConfig(), logging=LoggingConfig(),
    )

    from coinfiliate.browser import harvest_browser, fresh_context
    async with harvest_browser(headless=True) as browser, fresh_context(browser) as ctx:
        await run_writeback(store, settings=settings, browser_ctx=ctx)

    final = (await store.list_shops())[0]
    assert final["status"] == "writeback_done"
    await store.close()
