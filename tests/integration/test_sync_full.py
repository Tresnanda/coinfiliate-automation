from __future__ import annotations

import pytest
from aiohttp import web
from coinfiliate.browser import harvest_browser, fresh_context
from coinfiliate.store import Store
from coinfiliate.sync import login, sync_partner_shops, scrape_shops, sync_shop_affiliate_links
from tests.fixtures.fake_coinfiliate_server import make_app


@pytest.mark.integration
@pytest.mark.skip(reason="Fake-server fixture predates live-DOM rewrite. TODO: rebuild "
                         "tests/fixtures/fake_coinfiliate_server.py with Radix-shaped DOM.")
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
            for link in links:
                await store.upsert_affiliate_link(shop["id"], **link)
            await store.mark_harvest_source(shop["id"], links[0]["link_id"])

    all_shops = await store.list_shops()
    assert len(all_shops) == 2
    for s in all_shops:
        links = await store.list_affiliate_links(s["id"])
        assert len(links) == 2
        assert sum(1 for l in links if l["is_harvest_source"]) == 1

    await store.close()
    await runner.cleanup()
