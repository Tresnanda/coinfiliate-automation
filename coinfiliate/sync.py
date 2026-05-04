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


async def _scrape_current_page(page: Page) -> list:
    """Scrape just the rows currently visible on the Partner Shop table.

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
        # menu content to body and only one menu can be open at a time.
        menu_btn = cells.nth(5).locator('button[data-slot="dropdown-menu-trigger"]')
        await menu_btn.click()
        edit_item = page.locator('a[role="menuitem"]').first
        await edit_item.wait_for(state="visible", timeout=5_000)
        edit_url = await edit_item.get_attribute("href") or ""
        coinfiliate_id = ""
        if edit_url:
            import re
            m = re.search(r"/partner-shop/edit/([^/?#]+)", edit_url)
            if m:
                coinfiliate_id = m.group(1)
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


async def _go_to_next_page(page: Page) -> bool:
    """Click the Pagination Next button. Returns False if there is no next page.

    Two correctness traps the live UI hits:

    1. The button briefly flips to disabled mid-transition (Convex refetch).
       Reading `disabled` once would falsely conclude "no more pages" on every
       page. We re-check after a short wait window before giving up.
    2. The table rows from the previous page stay mounted while the new page
       is fetching. A naive `wait_for tr visible` returns instantly because the
       old rows are still there. We capture the first row's name cell before
       clicking and wait for it to *change* — that's the only reliable signal
       the new page has rendered.
    """
    next_btn = page.locator('button[aria-label="Next page"]').first

    async def _is_at_last_page() -> bool:
        # Robust enable check (handles `disabled`, `aria-disabled`, etc.).
        # Retry briefly to absorb transient disable during page re-render.
        for _ in range(8):
            try:
                if await next_btn.is_enabled():
                    return False
            except Exception:
                pass
            await page.wait_for_timeout(250)
        return True

    if await _is_at_last_page():
        return False

    rows = page.locator('tbody[data-slot="table-body"] tr[data-slot="table-row"]')
    try:
        before_text = (await rows.first.locator('td').nth(2).inner_text()).strip()
    except Exception:
        before_text = ""

    await next_btn.click()

    # Wait for the first row's name cell to differ — proves the table actually
    # transitioned. If it doesn't change within the window, fall through and
    # let the caller decide based on what it scrapes.
    try:
        await page.wait_for_function(
            """([sel, prev]) => {
                const r = document.querySelector(sel);
                if (!r) return false;
                const cells = r.querySelectorAll('td');
                if (cells.length < 3) return false;
                return (cells[2].innerText || '').trim() !== prev;
            }""",
            arg=['tbody[data-slot="table-body"] tr[data-slot="table-row"]', before_text],
            timeout=15_000,
        )
    except Exception:
        # Best-effort — some pages may legitimately repeat the same first name
        # (rare but possible). Caller will scrape whatever is now rendered.
        pass
    return True


async def scrape_shops(
    page: Page,
    *,
    max_pages: int = 1,
    from_page: int = 1,
    to_page: int | None = None,
    matches: callable = None,
    target_count: int | None = None,
) -> list:
    """Scrape one or more pages of the Partner Shop table.

    Args:
      max_pages: hard cap on pages to walk *from from_page*. Acts as a safety
        net when to_page is None.
      from_page: 1-indexed list page to start scraping from. The function will
        click "Next" (from_page - 1) times before reading any rows.
      to_page: 1-indexed inclusive last page. None means "no upper bound beyond
        max_pages".
      matches: optional predicate(shop_dict) -> bool. Only matching rows count
        toward target_count and are returned.
      target_count: stop early once `matches` has produced this many shops.

    The Partner Shop list uses client-side pagination (10 rows per page, no
    page-size selector). To collect more than 10 rows we walk pages via the
    Pagination "Next" button.
    """
    # Walk to the requested start page first. If we run out of pages before
    # reaching it, there's nothing to scrape.
    for step in range(max(0, from_page - 1)):
        if not await _go_to_next_page(page):
            log.info(
                "scrape_shops.exhausted_before_start",
                from_page=from_page, reached=step + 1,
            )
            return []

    # Effective page budget: the smaller of max_pages and the to_page span.
    if to_page is None:
        budget = max_pages
    else:
        budget = min(max_pages, max(0, to_page - from_page + 1))

    out = []
    pages_walked = 0
    current_page_num = from_page
    while pages_walked < budget:
        rows_on_page = await _scrape_current_page(page)
        log.info(
            "scrape_shops.page",
            page_num=current_page_num, rows=len(rows_on_page),
        )
        for s in rows_on_page:
            if matches is None or matches(s):
                out.append(s)
                if target_count is not None and len(out) >= target_count:
                    return out
        pages_walked += 1
        if pages_walked >= budget:
            break
        if not await _go_to_next_page(page):
            break
        current_page_num += 1
    return out


async def sync_shop_affiliate_links(page: Page, shop_edit_url: str, *, network: str,
                                    page_num: int, page_size: int,
                                    shop_name: str | None = None,
                                    timeout_ms: int = 60_000) -> list:
    log.info("sync_links.start", shop=shop_name, network=network, shop_edit_url=shop_edit_url)
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

    # The Edit page is Convex-backed and the affiliate-link list streams in
    # asynchronously after the modal closes. Wait for the first link card to
    # render before scraping.
    items = page.locator('[data-slot="collapsible"]')
    try:
        await items.first.wait_for(state="visible", timeout=20_000)
    except Exception:
        log.info("sync_links.empty", shop=shop_name, network=network, shop_edit_url=shop_edit_url)
        return []

    count = await items.count()
    log.info("sync_links.found", shop=shop_name, network=network, count=count)
    out = []
    for i in range(count):
        it = items.nth(i)
        # Title is in the first collapsible-trigger <p>; strip the "published"
        # pill text that shares the same element.
        title_p = it.locator('[data-slot="collapsible-trigger"]').first
        full_text = (await title_p.inner_text()).strip()
        # The pill text is "published" / "draft" appended to the name.
        for badge in ("published", "Published", "draft", "Draft"):
            if full_text.endswith(badge):
                full_text = full_text[: -len(badge)].strip()
                break
        name_text = full_text

        # Expand to read the Affiliate URL input. We open by clicking the
        # chevron button (the second collapsible-trigger), then read any
        # input whose value starts with "http".
        chevron = it.locator('button[data-slot="collapsible-trigger"]').first
        await chevron.click()
        # Wait for inputs to render inside the now-open collapsible.
        try:
            await it.locator('input').first.wait_for(state="visible", timeout=5_000)
        except Exception:
            pass
        url_text = ""
        link_id = ""
        n_inputs = await it.locator('input').count()
        for j in range(n_inputs):
            val = await it.locator('input').nth(j).input_value()
            if val.startswith("http") and not url_text:
                url_text = val
            # Heuristic: the FlexOffers Link ID looks like "156099.14723.864801"
            # — three dot-separated number runs. Capture the first match.
            if not link_id and val and val.count(".") >= 2 and val.replace(".", "").isdigit():
                link_id = val
        if not link_id:
            link_id = f"link-{i}"

        # Collapse again so the next iteration's click hits a known state.
        await chevron.click()
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

    target_status = (settings.sync.target_status or "").strip().lower()
    target_count = settings.runner.max_shops_per_batch
    max_pages = settings.sync.max_pages

    for network in settings.networks:
        await page.goto("https://www.coinfiliate.com/admin/partner-shop")
        await sync_partner_shops(
            page, network=network,
            page_num=settings.sync.page,
            page_size=settings.sync.page_size,
        )
        # Walk pages and only persist rows that match (network, target_status).
        # Stop once we have target_count matching shops queued.
        already = {s["coinfiliate_id"] for s in await store.list_shops()}

        def _matches(s, _network=network, _status=target_status):
            row_network = (s.get("network") or "").strip().lower()
            if row_network != _network.lower():
                return False
            if _status:
                row_status = (s.get("status") or "").strip().lower()
                if _status not in row_status:
                    return False
            # Skip ones we've already persisted from a prior run.
            if s.get("coinfiliate_id") in already:
                return False
            return True

        shops = await scrape_shops(
            page,
            max_pages=max_pages,
            from_page=settings.sync.from_page,
            to_page=settings.sync.to_page,
            matches=_matches,
            target_count=target_count,
        )
        log.info(
            "scrape_shops.collected",
            network=network, count=len(shops),
            from_page=settings.sync.from_page, to_page=settings.sync.to_page,
        )
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
            shop_name=shop["name"],
        )
        for link in links:
            await store.upsert_affiliate_link(shop["id"], **link)
        if links:
            await store.mark_harvest_source(shop["id"], links[0]["link_id"])
