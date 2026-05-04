"""Open each target shop's Edit page and read the publish-toggle label.

Toggle label semantics (live UI 2026-05-04):
  "Published"   = shop is currently Draft (action: click to publish)
  "Unpublished" = shop is currently Published (action: click to revert)
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from coinfiliate.browser import BrowserSession


async def main() -> None:
    con = sqlite3.connect("state.db")
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT name, edit_url FROM shop WHERE name IN ('VersedSkin.com','Alibaba LATAM')"
    ).fetchall()
    con.close()

    async with BrowserSession(Path(".playwright/coinfiliate")) as ctx:
        page = await ctx.new_page()
        for r in rows:
            url = r["edit_url"]
            if url.startswith("/"):
                url = f"https://www.coinfiliate.com{url}"
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            await page.wait_for_timeout(2_000)

            label = ""
            try:
                btn = page.locator(
                    'button[data-slot="button"]:text-is("Published"), '
                    'button[data-slot="button"]:text-is("Unpublished")'
                ).first
                label = (await btn.inner_text()).strip()
            except Exception:
                pass
            persisted = "Published" if label == "Unpublished" else (
                "Draft" if label == "Published" else "UNKNOWN"
            )
            print(f"  {r['name']:25s} button={label!r:14s} -> persisted={persisted}")


if __name__ == "__main__":
    asyncio.run(main())
