"""Probe what the 'Published' button on the Edit page actually does.

Opens the Edit page for VersedSkin.com (currently Draft), reads the button
state, clicks it, and reads it again. Does NOT click Update — so nothing is
persisted. We use this only to learn the toggle's behavior.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

from coinfiliate.browser import BrowserSession


def _pick_url() -> str:
    con = sqlite3.connect("state.db")
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT edit_url FROM shop WHERE name='VersedSkin.com'").fetchone()
    con.close()
    if row is None:
        sys.exit("VersedSkin.com not found")
    edit = row["edit_url"]
    return f"https://www.coinfiliate.com{edit}" if edit.startswith("/") else edit


async def _read_publish_state(page) -> dict:
    """Snapshot every button whose text is in the publish/draft action set."""
    return await page.evaluate(
        """
        () => {
          const out = [];
          for (const btn of document.querySelectorAll('button[data-slot="button"]')) {
            const t = (btn.innerText || '').trim();
            if (['Published', 'Draft', 'Unpublished', 'Publish', 'Unpublish'].includes(t)) {
              out.push({ text: t, disabled: btn.hasAttribute('disabled') });
            }
          }
          return out;
        }
        """
    )


async def main() -> None:
    url = _pick_url()
    print(f"target: {url}")
    async with BrowserSession(Path(".playwright/coinfiliate")) as ctx:
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await page.wait_for_timeout(2_000)

        before = await _read_publish_state(page)
        print(f"BEFORE click: {before}")

        # Click whichever publish-action button is currently rendered.
        for label in ("Published", "Draft", "Publish"):
            btn = page.get_by_role("button", name=label, exact=True)
            if await btn.count() > 0 and await btn.first.is_visible():
                print(f"clicking button with text {label!r}")
                await btn.first.click()
                break
        else:
            print("no publish-action button found")
            return

        await page.wait_for_timeout(1500)
        after = await _read_publish_state(page)
        print(f"AFTER click:  {after}")
        print("(NOTE: NOT clicking Update — nothing persisted)")


if __name__ == "__main__":
    asyncio.run(main())
