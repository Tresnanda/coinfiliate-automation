"""Dump every button (with full outerHTML) inside an expanded affiliate-link
card so we can identify what each unnamed button does — looking for a
per-link Save / Update button.
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
        await page.wait_for_timeout(3_000)

        cards = page.locator('[data-slot="collapsible"]')
        if await cards.count() == 0:
            print("no cards")
            return
        card = cards.first
        chevron = card.locator('button[data-slot="collapsible-trigger"]').first
        await chevron.click()
        await page.wait_for_timeout(900)

        # Take a screenshot of the expanded card
        try:
            await card.scroll_into_view_if_needed()
            await page.screenshot(path="logs/expanded_card_full.png", full_page=True)
            print("screenshot -> logs/expanded_card_full.png")
        except Exception:
            pass

        # Dump every button with all attributes + a 200-char snippet of outerHTML
        items = await card.evaluate(
            """el => {
                const out = [];
                for (const node of el.querySelectorAll('button, a[role="button"]')) {
                    const rect = node.getBoundingClientRect();
                    out.push({
                        tag: node.tagName,
                        role: node.getAttribute('role'),
                        text: (node.innerText || '').trim().slice(0, 60),
                        aria_label: node.getAttribute('aria-label') || '',
                        title: node.title || '',
                        data_slot: node.getAttribute('data-slot') || '',
                        data_state: node.getAttribute('data-state') || '',
                        type: node.getAttribute('type') || '',
                        classes: (node.className || '').slice(0, 200),
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                        outer: (node.outerHTML || '').slice(0, 350),
                    });
                }
                return out;
            }"""
        )
        print(f"\n=== {len(items)} buttons inside expanded card ===")
        for i, it in enumerate(items):
            print(f"\n[{i}] text={it['text']!r}  type={it['type']!r}  data_slot={it['data_slot']!r}")
            print(f"     pos=({it['x']},{it['y']})  size={it['w']}x{it['h']}")
            print(f"     classes={it['classes'][:120]}")
            print(f"     outer={it['outer'][:200]}")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Just Lawnmowers"
    asyncio.run(main(name))
