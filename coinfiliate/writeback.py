from __future__ import annotations

import json
from playwright.async_api import Page
from coinfiliate.selectors import sel
from coinfiliate.logging_setup import get_logger
from coinfiliate.store import Store

log = get_logger(__name__)


async def fill_bulk_edit_modal(page: Page, decision: dict) -> None:
    """Fill all four tracking-related fields + Published toggle. Idempotent on re-runs."""
    dlg = page.locator(sel("modal.root"))
    await dlg.wait_for(state="visible", timeout=10_000)

    # Published toggle: turn ON if currently OFF
    toggle = dlg.locator(sel("modal.published_toggle"))
    if await toggle.get_attribute("aria-checked") != "true":
        await toggle.click()

    # Primary cookie name
    primary_name = decision.get("primary_cookie_name")
    if primary_name:
        await dlg.locator(sel("modal.primary_cookie_name")).fill(primary_name)

    # Tracking cookie names list
    for name in decision.get("tracking_cookie_names", []):
        await dlg.locator(sel("modal.tracking_names_add")).click()
        await dlg.locator(sel("modal.tracking_names_input_last")).fill(name)

    # Checkout domains list
    for d in decision.get("checkout_domains", []):
        await dlg.locator(sel("modal.checkout_domains_add")).click()
        await dlg.locator(sel("modal.checkout_domain_input_last")).fill(d)

    # Tracking cookie domains list
    for d in decision.get("tracking_cookie_domains", []):
        await dlg.locator(sel("modal.tracking_domains_add")).click()
        await dlg.locator(sel("modal.tracking_domains_input_last")).fill(d)


async def save_and_verify(page: Page, decision: dict) -> dict:
    """Click Save Changes (modal) then Published+Update (outer). Returns submitted payload."""
    await page.locator(sel("modal.save_changes")).click()
    await page.locator(sel("modal.root")).wait_for(state="hidden", timeout=10_000)

    # Outer-page Published + Update buttons
    await page.locator(sel("editshop.published_btn")).click()
    await page.locator(sel("editshop.update_btn")).click()

    # The fixture writes a JSON summary into <pre id="out"> for assertions.
    # In the real UI, verification means reloading and reading the field back (see Task 17).
    out = await page.locator("#out").inner_text()
    return json.loads(out) if out.strip() else {}


async def writeback_shop(
    store: Store,
    *,
    shop_id: int,
    settings,
    browser_ctx,
    dry_run: bool = False,
) -> None:
    """Drive the Edit modal for a single harvested shop. Marks shop status accordingly."""
    shop = next(s for s in await store.list_shops() if s["id"] == shop_id)
    latest = await store.latest_harvest(shop_id)
    if not latest or not latest["ok"]:
        await store.update_shop_status(
            shop_id, "needs_review",
            last_error="no ok harvest row for writeback",
        )
        return

    decision = {
        "primary_cookie_name": latest["primary_cookie_name"],
        "tracking_cookie_names": json.loads(latest["tracking_cookie_names_json"] or "[]"),
        "checkout_domains":     json.loads(latest["checkout_domains_json"] or "[]"),
        "tracking_cookie_domains": json.loads(latest["tracking_cookie_domains_json"] or "[]"),
    }

    try:
        page = await browser_ctx.new_page()
        await page.goto(shop["edit_url"])

        # Re-sync if the affiliate-links list is empty (defensive: stale session)
        await page.locator(sel("editshop.tab_affiliate_links")).first.click()
        links_count = await page.locator(".link").count()
        if links_count == 0:
            from coinfiliate.sync import sync_shop_affiliate_links
            await sync_shop_affiliate_links(
                page, shop["edit_url"],
                network=shop["network"],
                page_num=settings.sync.page,
                page_size=settings.sync.page_size,
            )

        # Select all -> Selected Data -> Edit
        await page.click(sel("editshop.select_all"))
        await page.click(sel("editshop.selected_data_dd"))
        await page.click(sel("editshop.edit_selected"))

        await fill_bulk_edit_modal(page, decision)

        if dry_run:
            # Cancel instead of save; leave shop status unchanged so a later real run re-tries.
            await page.locator('button:has-text("Cancel")').first.click()
            log.info("writeback.dry_run_done", shop_id=shop_id)
            return

        submitted = await save_and_verify(page, decision)

        if settings.writeback.verify_after_save:
            if submitted.get("primary_cookie_name") != decision["primary_cookie_name"]:
                await store.update_shop_status(
                    shop_id, "failed",
                    last_error=f"verify mismatch: got {submitted.get('primary_cookie_name')!r}",
                )
                return

        await store.update_shop_status(shop_id, "writeback_done")
    except Exception as e:
        await store.update_shop_status(shop_id, "failed", last_error=f"{type(e).__name__}: {e}")
        raise


async def run_writeback(
    store: Store,
    *,
    settings,
    browser_ctx,
    dry_run: bool = False,
) -> None:
    """Top-level orchestrator: writeback all 'harvested' shops sequentially."""
    shops = await store.list_shops(status="harvested")
    shops = shops[: settings.runner.max_shops_per_batch]
    for shop in shops:
        try:
            await writeback_shop(
                store,
                shop_id=shop["id"],
                settings=settings,
                browser_ctx=browser_ctx,
                dry_run=dry_run,
            )
        except Exception as e:
            log.error("writeback.failed", shop_id=shop["id"], err=str(e))
