from __future__ import annotations

from playwright.async_api import Page
from coinfiliate.selectors import sel
from coinfiliate.logging_setup import get_logger

log = get_logger(__name__)


async def login(page: Page, *, login_url: str, email: str, password: str,
                success_url_substring: str = "/admin/") -> None:
    """Login through Coinfiliate's Clerk-based auth.

    Clerk may render either a single combined form (email + password + submit) or a
    two-step flow (email -> Continue -> password -> Continue). We handle both by
    filling email, then conditionally filling password if it became interactable.
    """
    log.info("login.start", url=login_url)
    await page.goto(login_url)

    # If a persistent context already has a Clerk session cookie, /login bypasses
    # the form entirely. Detect "already logged in" by giving the email field a
    # short window to appear; if it doesn't, assume we're authenticated.
    email_input = page.locator(sel("login.email"))
    try:
        await email_input.wait_for(state="visible", timeout=5_000)
    except Exception:
        log.info("login.already_authenticated", landed=page.url)
        return
    await email_input.fill(email)

    password_input = page.locator(sel("login.password"))
    submit_btn = page.locator(sel("login.submit")).last  # primary submit, not hidden one

    # If password is already interactable (combined form), fill and submit once.
    # Otherwise click Continue first, wait for password to appear, then submit.
    pw_visible = False
    try:
        pw_visible = await password_input.is_editable(timeout=1000)
    except Exception:
        pw_visible = False

    if pw_visible:
        await password_input.fill(password)
        await submit_btn.click()
    else:
        await submit_btn.click()
        await password_input.wait_for(state="visible", timeout=15_000)
        await password_input.fill(password)
        await page.locator(sel("login.submit")).last.click()

    # Clerk redirects through /sign-in -> back to its redirect_url. We just need to
    # leave /sign-in and have a Clerk session cookie; the caller navigates to the
    # target admin page directly.
    await page.wait_for_url(lambda url: "/sign-in" not in url, timeout=30_000)
    log.info("login.ok", landed=page.url)


async def _select_radix_option(page: Page, dlg, option_text: str) -> None:
    """Open a Radix combobox inside dlg and pick an option matching option_text (case-insensitive)."""
    import re
    combobox = dlg.locator('button[role="combobox"]').first
    await combobox.click()
    # Radix portals options outside the dialog, so query at page scope
    await page.get_by_role("option").filter(
        has_text=re.compile(re.escape(option_text), re.I)
    ).first.click()


async def _check_all_selectable_fields(dlg) -> None:
    """Tick every Radix checkbox inside the Selectable Fields group that's currently unchecked."""
    boxes = dlg.locator('button[role="checkbox"][data-state="unchecked"]')
    count = await boxes.count()
    for i in range(count):
        # Each click toggles one box; the locator re-evaluates each iteration
        # because data-state changes invalidate the previous matches.
        unchecked = dlg.locator('button[role="checkbox"][data-state="unchecked"]').first
        if await unchecked.count() == 0:
            break
        await unchecked.click()


async def sync_partner_shops(page: Page, *, network: str, page_num: int, page_size: int,
                             timeout_ms: int = 60_000) -> None:
    """Open Sync Partner Shop modal, configure, and run sync."""
    log.info("sync_shops.start", network=network)
    await page.click(sel("shoplist.sync_btn"))
    dlg = page.locator('div[role="dialog"]')
    await dlg.wait_for(state="visible", timeout=10_000)

    # Step 1: pick the network from the Radix combobox.
    await _select_radix_option(page, dlg, network)

    # Step 2: Page + Page Size inputs are revealed after the network is picked.
    page_input = dlg.locator('input[placeholder="Page"]')
    page_size_input = dlg.locator('input[placeholder="Page Size"]')
    await page_input.wait_for(state="visible", timeout=10_000)
    await page_input.fill(str(page_num))
    await page_size_input.fill(str(page_size))

    # Step 3: tick every Selectable Fields checkbox.
    await _check_all_selectable_fields(dlg)

    # Step 4: click the now-enabled Sync Now button. It may take time to enable.
    sync_now = dlg.locator('button:has-text("Sync Now"):not([disabled])')
    await sync_now.wait_for(state="visible", timeout=10_000)
    await sync_now.click()

    # The sync usually closes the modal and refreshes the table. Wait for the
    # modal to disappear; if it stays (e.g. the app keeps it open), fall back to
    # waiting for network idle.
    try:
        await dlg.wait_for(state="hidden", timeout=timeout_ms)
    except Exception:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    log.info("sync_shops.ok")


async def scrape_shops(page: Page) -> list:
    """Scrape the Partner Shop table.

    Live DOM uses Radix Table + a per-row dropdown menu for Edit. We extract
    name/network/status from cell text; the unique id and edit href come from
    opening the row's dropdown menu and reading the Edit menu item href.
    """
    rows = page.locator('tbody[data-slot="table-body"] tr[data-slot="table-row"]')
    count = await rows.count()
    out = []
    for i in range(count):
        row = rows.nth(i)
        cells = row.locator('td[data-slot="table-cell"]')
        # Layout: [select-checkbox, logo-img, name-div, network-div, status-span, menu-trigger]
        name = (await cells.nth(2).inner_text()).strip()
        network = (await cells.nth(3).inner_text()).strip()
        status = (await cells.nth(4).inner_text()).strip()

        # Open the per-row dropdown to read the Edit href. Radix portals the
        # menu content to body and only one menu can be open at a time, so we
        # must explicitly wait for the previous menu to dismiss before opening
        # the next one — otherwise we read stale data.
        menu_btn = cells.nth(5).locator('button[data-slot="dropdown-menu-trigger"]')
        await menu_btn.click()
        # Edit menu item is the only <a role="menuitem"> in the popover; View
        # and Delete are <div role="menuitem">. Targeting the anchor avoids
        # text-matching surprises across i18n.
        edit_item = page.locator('a[role="menuitem"]').first
        await edit_item.wait_for(state="visible", timeout=5_000)
        edit_url = await edit_item.get_attribute("href") or ""
        # Derive a stable id from the URL pattern /admin/partner-shop/edit/<slug>.
        coinfiliate_id = ""
        if edit_url:
            import re
            m = re.search(r"/partner-shop/edit/([^/?#]+)", edit_url)
            if m:
                coinfiliate_id = m.group(1)
        # Close the menu and wait for it to actually leave the DOM before we
        # advance to the next row.
        await page.keyboard.press("Escape")
        try:
            await edit_item.wait_for(state="hidden", timeout=2_000)
        except Exception:
            pass

        out.append({
            "coinfiliate_id": coinfiliate_id or f"row-{i}",
            "name": name,
            "network": network,
            "status": status,
            "edit_url": edit_url,
        })
    return out


async def sync_shop_affiliate_links(page: Page, shop_edit_url: str, *, network: str,
                                    page_num: int, page_size: int,
                                    timeout_ms: int = 60_000) -> list:
    log.info("sync_links.start", shop_edit_url=shop_edit_url)
    await page.goto(shop_edit_url, wait_until="domcontentloaded")

    # The Edit page is Convex-backed and renders async; wait for the inner Sync
    # button to appear before clicking. The Affiliate Links tab is the default
    # so we don't need to click it.
    sync_btn = page.locator(sel("editshop.sync_affiliate_btn"))
    await sync_btn.wait_for(state="visible", timeout=20_000)
    await sync_btn.click()

    dlg = page.locator('div[role="dialog"]')
    await dlg.wait_for(state="visible", timeout=10_000)

    # Same Radix pattern as sync_partner_shops.
    await _select_radix_option(page, dlg, network)

    page_input = dlg.locator('input[placeholder="Page"]')
    page_size_input = dlg.locator('input[placeholder="Page Size"]')
    await page_input.wait_for(state="visible", timeout=10_000)
    await page_input.fill(str(page_num))
    await page_size_input.fill(str(page_size))

    await _check_all_selectable_fields(dlg)

    sync_now = dlg.locator('button:has-text("Sync Now"):not([disabled])')
    await sync_now.wait_for(state="visible", timeout=10_000)
    await sync_now.click()

    try:
        await dlg.wait_for(state="hidden", timeout=timeout_ms)
    except Exception:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)

    # Scrape the links list. The Edit page renders each link as a Radix
    # Accordion item. Prefer the explicit-id pattern when present, else fall
    # back to the live DOM structure.
    items = page.locator('[data-slot="accordion-item"]')
    if await items.count() == 0:
        items = page.locator('#links .link, .link')
    count = await items.count()
    out = []
    for i in range(count):
        it = items.nth(i)
        # Prefer explicit fixture id, else try to read from data attributes.
        link_id = await it.get_attribute("data-link-id") or ""
        if not link_id:
            link_id = await it.get_attribute("value") or ""
        if not link_id:
            link_id = f"link-{i}"
        # Name: first heading-like element in the accordion trigger.
        name_text = ""
        for sel_try in ['button[data-slot="accordion-trigger"]', '.name', 'h3', 'h4']:
            loc = it.locator(sel_try).first
            if await loc.count():
                name_text = (await loc.inner_text()).strip()
                if name_text:
                    break
        # Affiliate URL: read input[placeholder*="Affiliate URL"] or any text
        # that starts with http(s).
        url_text = ""
        url_input = it.locator('input').filter(has_text=" ")  # placeholder seeded
        # Simpler: read any input value that looks like a URL inside the item.
        n_inputs = await it.locator('input').count()
        for j in range(n_inputs):
            val = await it.locator('input').nth(j).input_value()
            if val.startswith("http"):
                url_text = val
                break
        if not url_text:
            url_loc = it.locator('.url').first
            if await url_loc.count():
                url_text = (await url_loc.inner_text()).strip()
        out.append({
            "link_id": link_id,
            "name": name_text,
            "affiliate_url": url_text,
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
        # Only persist rows that actually belong to the network we synced for.
        # The shop list displays all shops across all networks; "-" means none.
        for s in shops:
            row_network = (s.get("network") or "").strip().lower()
            if row_network != network.lower():
                continue
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
