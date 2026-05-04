"""Open a shop's Edit page, Select All affiliate links, open the Selected Data
dropdown, and dump every menu item. Goal: find a 'Publish Selected' bulk
action so writeback can flip per-link draft → published.
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

        # Click the Affiliate Links tab to make sure we're there.
        try:
            await page.locator('[data-slot="tabs-trigger"]').filter(has_text="Affiliate Links").first.click()
        except Exception:
            pass
        await page.wait_for_timeout(800)

        # Select All
        select_all = page.locator(
            'div:has(> p:text-is("Select All")) > button[role="checkbox"]'
        ).first
        await select_all.wait_for(state="visible", timeout=10_000)
        await select_all.click()
        await page.wait_for_timeout(500)

        # Open the Selected Data dropdown
        sd = page.locator('button:has-text("Selected Data")').first
        await sd.wait_for(state="visible", timeout=10_000)
        await sd.click()
        await page.wait_for_timeout(700)

        # Dump every menu item visible (Radix portals these to body)
        items = await page.evaluate(
            """() => {
                const out = [];
                for (const el of document.querySelectorAll('[role="menuitem"]')) {
                    out.push({
                        text: (el.innerText || '').trim().slice(0, 60),
                        data_slot: el.getAttribute('data-slot') || '',
                        disabled: el.getAttribute('aria-disabled') === 'true',
                    });
                }
                return out;
            }"""
        )
        print("\n=== Selected Data menu items ===")
        for it in items:
            print(f"  {it}")

        # Also screenshot for sanity.
        out_path = Path("logs/selected_data_menu.png")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(out_path))
        print(f"\nscreenshot saved -> {out_path}")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Just Lawnmowers"
    asyncio.run(main(name))
