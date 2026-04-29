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
