from __future__ import annotations

import json
from playwright.async_api import Page
from coinfiliate.selectors import sel
from coinfiliate.logging_setup import get_logger

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
