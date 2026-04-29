from __future__ import annotations

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
