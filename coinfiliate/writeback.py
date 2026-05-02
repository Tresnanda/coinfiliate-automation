from __future__ import annotations

import json
from playwright.async_api import Page
from coinfiliate.selectors import sel
from coinfiliate.logging_setup import get_logger
from coinfiliate.store import Store

log = get_logger(__name__)


async def _fill_list_field(dlg, label_text: str, values: list) -> None:
    """Click '+ Add' inside the section labeled `label_text`, fill each value into
    the newly-appended input.

    Live DOM shape:
      <div class="...rounded-lg">          <- section (bordered card)
        <div class="flex ...">             <- header
          <label>Tracking Cookie Names</label>
          <button>+ Add</button>
        </div>
        <div class="space-y-3">             <- list container; inputs appended here
        </div>
      </div>
    """
    # Find the label, walk to its nearest .rounded-lg ancestor (the section card).
    section = (
        dlg.locator(f'label:text-is("{label_text}")')
        .first.locator('xpath=ancestor::div[contains(@class, "rounded-lg")][1]')
    )
    add_btn = section.locator('button:has-text("Add")').first
    for value in values:
        await add_btn.click()
        new_input = section.locator('input').last
        await new_input.wait_for(state="visible", timeout=5_000)
        await new_input.fill(value)


async def fill_bulk_edit_modal(page: Page, decision: dict) -> None:
    """Fill the four tracking-related fields in the live bulk-edit modal.

    Live modal labels: Name, Network, Advertiser ID, Link ID, Affiliate URL,
    User Commission Rate (%), Coupon Code, Primary Tracking Cookie Name,
    Checkout Domains (list), Tracking Cookie Names (list), Tracking Cookie
    Domains (list). There is no Published toggle in the modal — the docx
    tutorial showed one but the current UI omits it; per-shop publishing is
    governed by the outer-page Published button instead.
    """
    dlg = page.locator(
        'div[role="dialog"]:has-text("Edit Selected Partner Shop Links")'
    ).first
    await dlg.wait_for(state="visible", timeout=10_000)

    # Primary cookie name: input directly after its <label>.
    primary_name = decision.get("primary_cookie_name")
    if primary_name:
        primary_input = dlg.locator(
            'label:text-is("Primary Tracking Cookie Name") + input'
        ).first
        await primary_input.fill(primary_name)

    await _fill_list_field(dlg, "Tracking Cookie Names",   decision.get("tracking_cookie_names", []))
    await _fill_list_field(dlg, "Checkout Domains",        decision.get("checkout_domains", []))
    await _fill_list_field(dlg, "Tracking Cookie Domains", decision.get("tracking_cookie_domains", []))


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

    # Resolve relative URL stored by the sync phase to an absolute one.
    edit_url = shop["edit_url"]
    if edit_url.startswith("/"):
        edit_url = f"https://www.coinfiliate.com{edit_url}"

    try:
        page = await browser_ctx.new_page()
        await page.goto(edit_url, wait_until="domcontentloaded")

        # The Affiliate Links tab is the default; clicking it is idempotent but
        # we wait for at least one collapsible card to render so we know the
        # Convex data has hydrated before we reach for Select All.
        try:
            await page.locator('[data-slot="collapsible"]').first.wait_for(
                state="visible", timeout=20_000
            )
        except Exception:
            # No links: fall back to a per-shop sync to fetch them now.
            from coinfiliate.sync import sync_shop_affiliate_links
            await sync_shop_affiliate_links(
                page, edit_url,
                network=shop["network"],
                page_num=settings.sync.page,
                page_size=settings.sync.page_size,
            )

        # Select All is rendered as a Radix checkbox button that's a sibling of
        # the "Select All" text. Walk up to the wrapping div and pick the button.
        select_all_btn = page.locator(
            'div:has(> p:text-is("Select All")) > button[role="checkbox"]'
        ).first
        await select_all_btn.wait_for(state="visible", timeout=10_000)
        await select_all_btn.click()
        # Then open the Selected Data action menu and click Edit.
        await page.locator('button:has-text("Selected Data")').click()
        await page.locator('[role="menuitem"]').filter(has_text="Edit").first.click()

        await fill_bulk_edit_modal(page, decision)

        if dry_run:
            # Cancel instead of save; leave shop status unchanged so a later real run re-tries.
            # Scope to the modal — the outer Edit page also has a Cancel button.
            await page.locator(
                'div[role="dialog"]:has-text("Edit Selected Partner Shop Links") '
                'button:has-text("Cancel")'
            ).first.click()
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
