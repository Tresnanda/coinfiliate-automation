from __future__ import annotations

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
