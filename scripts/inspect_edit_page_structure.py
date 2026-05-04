"""Dump the top-level structure of a shop's Edit page so we can see whether
the user might be looking at an empty 'Assets' tab while affiliate-link
config lives elsewhere.
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

        # Tabs (Radix tab list pattern)
        print("\n=== role=tab elements ===")
        tabs = await page.evaluate(
            """() => {
                const out = [];
                for (const el of document.querySelectorAll('[role="tab"], [data-slot="tabs-trigger"]')) {
                    out.push({
                        text: (el.innerText || '').trim().slice(0, 60),
                        active: el.getAttribute('aria-selected') === 'true' || el.getAttribute('data-state') === 'active',
                        data_state: el.getAttribute('data-state') || '',
                    });
                }
                return out;
            }"""
        )
        for t in tabs:
            print(f"  {t}")

        # All H1/H2/H3 headings on page
        print("\n=== headings ===")
        heads = await page.evaluate(
            """() => Array.from(document.querySelectorAll('h1, h2, h3, h4'))
                .map(el => ({tag: el.tagName, text: (el.innerText || '').trim().slice(0, 80)}))
                .filter(h => h.text)"""
        )
        for h in heads:
            print(f"  <{h['tag']}> {h['text']}")

        # data-slot="collapsible" count (affiliate link cards)
        cards = await page.locator('[data-slot="collapsible"]').count()
        print(f"\n=== affiliate-link cards (data-slot=\"collapsible\") count: {cards} ===")

        # Anchor / link elements with text suggesting tabs/sections
        print("\n=== sidebar-ish links/buttons ===")
        nav = await page.evaluate(
            """() => {
                const interesting = [];
                const re = /asset|link|track|cookie|publish|payout|setting|info|config/i;
                for (const el of document.querySelectorAll('button, a, [role="link"]')) {
                    const t = (el.innerText || '').trim();
                    if (re.test(t) && t.length < 40) {
                        interesting.push({tag: el.tagName, text: t.slice(0, 50), data_slot: el.getAttribute('data-slot') || ''});
                    }
                }
                return interesting;
            }"""
        )
        for n in nav:
            print(f"  {n}")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "VersedSkin.com"
    asyncio.run(main(name))
