"""Open a shop's Edit page, expand each affiliate-link card, and dump every
input value so we can see what's actually persisted in Coinfiliate.

Usage:
  python scripts/inspect_persisted_fields.py "VersedSkin.com"
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
        await page.wait_for_timeout(2_000)

        # Number of affiliate-link cards visible.
        cards = page.locator('[data-slot="collapsible"]')
        n = await cards.count()
        print(f"  affiliate-link cards: {n}\n")
        if n == 0:
            print("  (no cards — try clicking 'Sync Now' first)")
            return

        # Sample first, middle, and last cards to detect partial bulk-update.
        sample_idx = sorted({0, 1, max(0, n // 4), max(0, n // 2), max(0, (3 * n) // 4), n - 1, n - 2})
        for i in sample_idx:
            card = cards.nth(i)
            chevron = card.locator('button[data-slot="collapsible-trigger"]').first
            try:
                await chevron.scroll_into_view_if_needed(timeout=5_000)
            except Exception:
                pass
            await chevron.click()
            await page.wait_for_timeout(700)

            print(f"--- card[{i}] ---")
            # Dump every input/value pair under this card.
            data = await card.evaluate(
                """el => {
                    const out = [];
                    for (const inp of el.querySelectorAll('input')) {
                        // The label is usually a <label> sibling preceding the input.
                        let label = '';
                        let prev = inp.parentElement;
                        while (prev && !label) {
                            const lab = prev.querySelector('label');
                            if (lab) { label = (lab.innerText || '').trim(); break; }
                            prev = prev.parentElement;
                        }
                        out.push({
                            label: label.slice(0, 40),
                            value: inp.value || '',
                            placeholder: inp.placeholder || '',
                        });
                    }
                    // Also: chips/badges/buttons with text inside list sections.
                    const chips = [];
                    for (const span of el.querySelectorAll('span, [data-slot="badge"]')) {
                        const t = (span.innerText || '').trim();
                        if (t && t.length < 80 && span.children.length === 0) chips.push(t);
                    }
                    return { inputs: out, chips: chips.slice(0, 30) };
                }"""
            )
            for inp in data["inputs"]:
                v = inp["value"] if inp["value"] else "(empty)"
                print(f"   {inp['label'][:32]:32s} = {v}")
            await chevron.click()
            await page.wait_for_timeout(300)


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "VersedSkin.com"
    asyncio.run(main(name))
