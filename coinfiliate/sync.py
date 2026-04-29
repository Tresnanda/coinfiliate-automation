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


async def sync_shop_affiliate_links(page: Page, shop_edit_url: str, *, network: str,
                                    page_num: int, page_size: int,
                                    timeout_ms: int = 60_000) -> list:
    log.info("sync_links.start", shop_edit_url=shop_edit_url)
    await page.goto(shop_edit_url)
    # The Affiliate Links tab may be the default; clicking is idempotent.
    await page.locator(sel("editshop.tab_affiliate_links")).first.click()
    await page.click(sel("editshop.sync_affiliate_btn"))

    dlg = page.locator('div[role="dialog"]')
    await dlg.wait_for(state="visible", timeout=10_000)
    select = dlg.locator('[role="combobox"]').first
    try:
        await select.select_option(network)
    except Exception:
        await select.click()
        await page.click(f'text="{network}"')
    await dlg.locator("input").nth(0).fill(str(page_num))
    await dlg.locator("input").nth(1).fill(str(page_size))
    await dlg.locator('button:has-text("Sync Now")').click()
    # Same wait strategy as sync_partner_shops -- handles either modal-hide or page-reload patterns
    try:
        await dlg.wait_for(state="hidden", timeout=5_000)
    except Exception:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)

    # Scrape the links list
    items = page.locator("#links .link, .link")  # support either container
    count = await items.count()
    out = []
    for i in range(count):
        it = items.nth(i)
        out.append({
            "link_id": await it.get_attribute("data-link-id") or "",
            "name": (await it.locator(".name").inner_text()).strip(),
            "affiliate_url": (await it.locator(".url").inner_text()).strip(),
        })
    return out


async def run_sync(settings, store, browser_ctx) -> None:
    """Top-level sync orchestrator: login, pull shops, then per-shop link sync."""
    page = await browser_ctx.new_page()
    await login(
        page,
        login_url="https://www.coinfiliate.com/login",
        email=settings.coinfiliate_email,
        password=settings.coinfiliate_pass,
    )

    for network in settings.networks:
        await page.goto("https://www.coinfiliate.com/admin/partner-shop")
        await sync_partner_shops(
            page, network=network,
            page_num=settings.sync.page,
            page_size=settings.sync.page_size,
        )
        shops = await scrape_shops(page)
        for s in shops:
            await store.upsert_shop(
                coinfiliate_id=s["coinfiliate_id"], name=s["name"],
                network=network, advertiser_id=None, website_url=None,
                edit_url=s["edit_url"],
            )

    pending = await store.list_shops(status="pending")
    pending = pending[: settings.runner.max_shops_per_batch]
    for shop in pending:
        edit_url = shop["edit_url"]
        if edit_url.startswith("/"):
            edit_url = f"https://www.coinfiliate.com{edit_url}"
        links = await sync_shop_affiliate_links(
            page, edit_url,
            network=shop["network"],
            page_num=settings.sync.page,
            page_size=settings.sync.page_size,
        )
        for link in links:
            await store.upsert_affiliate_link(shop["id"], **link)
        if links:
            await store.mark_harvest_source(shop["id"], links[0]["link_id"])
