from __future__ import annotations

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
@pytest.mark.skip(reason="Fake-server fixture predates live-DOM rewrite (Clerk uses "
                         "input[name='identifier'], not [type='email']). TODO: update "
                         "tests/fixtures/fake_coinfiliate_server.py to mimic Clerk's form.")
async def test_login_redirects_to_admin(server):
    async with harvest_browser(headless=True) as browser, fresh_context(browser) as ctx:
        page = await ctx.new_page()
        await login(page, login_url=f"{server}/login", email="a@b.com", password="x",
                    success_url_substring="/admin/")
        assert "/admin/" in page.url
