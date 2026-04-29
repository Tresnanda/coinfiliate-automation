from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
VIEWPORT = {"width": 1280, "height": 800}


class BrowserSession:
    """Persistent context for Coinfiliate admin (keeps login). Not used for harvest."""
    def __init__(self, user_data_dir: Path, headless: bool = True):
        self.user_data_dir = Path(user_data_dir)
        self.headless = headless
        self._pw: Optional[Playwright] = None
        self._ctx: Optional[BrowserContext] = None

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
