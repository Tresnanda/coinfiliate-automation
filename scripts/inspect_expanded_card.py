"""Expand the first affiliate-link card and dump every interactive element
(buttons, switches, checkboxes) to find the per-link publish toggle.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

from coinfiliate.browser import BrowserSession


async def main(name: str) -> None:
    con = sqlite3.connect("state.db")
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT name, edit_url FROM shop WHERE name=?", (name,)).fetchone()
    con.close()
    if row is None:
        sys.exit(f"shop {name!r} not in state.db")
    url = row["edit_url"]
    if url.startswith("/"):
        url = f"https://www.coinfiliate.com{url}"
    print(f"target: {row['name']} -> {url}")

    async with BrowserSession(Path(".playwright/coinfiliate")) as ctx:
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await page.wait_for_timeout(2_500)

        cards = page.locator('[data-slot="collapsible"]')
        n = await cards.count()
        print(f"  {n} cards on page")
        if n == 0:
            return

        card = cards.first
        chevron = card.locator('button[data-slot="collapsible-trigger"]').first
        await chevron.click()
        await page.wait_for_timeout(800)

        print("\n=== buttons + switches + checkboxes inside expanded card ===")
        items = await card.evaluate(
            """el => {
                const out = [];
                for (const node of el.querySelectorAll('button, [role="switch"], [role="checkbox"]')) {
                    out.push({
                        tag: node.tagName,
                        role: node.getAttribute('role'),
                        text: (node.innerText || '').trim().slice(0, 60),
                        aria_label: node.getAttribute('aria-label') || '',
                        data_slot: node.getAttribute('data-slot') || '',
                        data_state: node.getAttribute('data-state') || '',
                        aria_checked: node.getAttribute('aria-checked'),
                        disabled: node.hasAttribute('disabled'),
                    });
                }
                return out;
            }"""
        )
        for it in items:
            print(f"  {it}")

        # Also: scroll the card into view + screenshot for visual inspection.
        try:
            await card.scroll_into_view_if_needed()
            await page.screenshot(path="logs/expanded_card.png", clip=None)
            print("\nscreenshot -> logs/expanded_card.png")
        except Exception:
            pass


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Just Lawnmowers"
    asyncio.run(main(name))
