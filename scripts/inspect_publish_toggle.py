"""Inspect publish-toggle controls on a Partner Shop Edit page.

Uses the persistent Clerk session in `.playwright/coinfiliate`. Opens the edit
page for a known shop (defaults to VersedSkin.com via state.db) and dumps every
button + interesting widget that mentions publish/draft/unpublish/update.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

from coinfiliate.browser import BrowserSession


def _pick_shop_edit_url() -> str:
    con = sqlite3.connect("state.db")
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT name, edit_url FROM shop WHERE name IN ('VersedSkin.com', 'Alibaba LATAM') LIMIT 1"
    ).fetchone()
    con.close()
    if row is None:
        sys.exit("Couldn't find VersedSkin.com or Alibaba LATAM in state.db")
    edit = row["edit_url"]
    if edit.startswith("/"):
        edit = f"https://www.coinfiliate.com{edit}"
    print(f"Inspecting: {row['name']} -> {edit}")
    return edit


async def main() -> None:
    edit_url = _pick_shop_edit_url()
    async with BrowserSession(Path(".playwright/coinfiliate")) as ctx:
        page = await ctx.new_page()
        await page.goto(edit_url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        # Give Convex a moment so the form fully hydrates.
        await page.wait_for_timeout(2_000)

        print("\n=== buttons whose text/aria/title mentions publish/draft/unpublish/update ===")
        items = await page.evaluate(
            """
            () => {
              const out = [];
              const re = /publish|draft|unpublish|update/i;
              for (const el of document.querySelectorAll('button, [role="switch"], [role="checkbox"], a')) {
                const text = (el.innerText || '').trim();
                const al = el.getAttribute('aria-label') || '';
                const title = el.title || '';
                const ds = el.getAttribute('data-state') || '';
                if (re.test(text) || re.test(al) || re.test(title)) {
                  out.push({
                    tag: el.tagName,
                    role: el.getAttribute('role'),
                    text: text.slice(0, 80),
                    aria_label: al,
                    title,
                    data_state: ds,
                    data_slot: el.getAttribute('data-slot'),
                    aria_checked: el.getAttribute('aria-checked'),
                    disabled: el.hasAttribute('disabled'),
                    classes: (el.className || '').slice(0, 160),
                    outer: (el.outerHTML || '').slice(0, 400),
                  });
                }
              }
              return out;
            }
            """
        )
        for i, item in enumerate(items):
            print(f"\n[{i}] {item}")

        print("\n=== status pill / badge in vicinity of shop name ===")
        pill = await page.evaluate(
            """
            () => {
              const out = [];
              for (const el of document.querySelectorAll('span, div, [data-slot="badge"]')) {
                const t = (el.innerText || '').trim();
                if ((t === 'Draft' || t === 'Published') && el.children.length === 0) {
                  out.push({
                    tag: el.tagName,
                    text: t,
                    classes: (el.className || '').slice(0, 120),
                    parentTag: el.parentElement?.tagName,
                    parentText: (el.parentElement?.innerText || '').slice(0, 120),
                    outer: (el.outerHTML || '').slice(0, 400),
                  });
                }
              }
              return out;
            }
            """
        )
        for p in pill:
            print(p)


if __name__ == "__main__":
    asyncio.run(main())
