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


async def sync_partner_shops(page: Page, *, network: str, page_num: int, page_size: int,
                             timeout_ms: int = 60_000) -> None:
    log.info("sync_shops.start", network=network)
    await page.click(sel("shoplist.sync_btn"))
    dlg = page.locator('div[role="dialog"]')
    await dlg.wait_for(state="visible", timeout=10_000)
    # Network select - try standard <select> first, then custom combobox
    select = dlg.locator('select[role="combobox"], [role="combobox"]').first
    try:
        await select.select_option(network)
    except Exception:
        # Custom combobox -- click to open, pick option by text
        await select.click()
        await page.click(f'text="{network}"')
    await dlg.locator("input").nth(0).fill(str(page_num))
    await dlg.locator("input").nth(1).fill(str(page_size))
    await dlg.locator('button:has-text("Sync Now")').click()
    # The sync triggers a page reload; wait for navigation to complete
    await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    log.info("sync_shops.ok")


async def scrape_shops(page: Page) -> list:
    rows = page.locator(sel("shoplist.row"))
    count = await rows.count()
    out = []
    for i in range(count):
        row = rows.nth(i)
        cfi = await row.get_attribute("data-cfi") or ""
        name_loc = row.locator(".name")
        name = (await name_loc.inner_text()).strip() if await name_loc.count() else ""
        network_loc = row.locator(".network")
        network = (await network_loc.inner_text()).strip() if await network_loc.count() else ""
        status_loc = row.locator(".status")
        status = (await status_loc.inner_text()).strip() if await status_loc.count() else ""
        edit_href = await row.locator("a.edit, a:has-text('Edit')").first.get_attribute("href") or ""
        out.append({
            "coinfiliate_id": cfi, "name": name, "network": network,
            "status": status, "edit_url": edit_href,
        })
    return out
