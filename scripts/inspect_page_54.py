"""Navigate to Partner Shop list page 54 and dump every visible row.

Uses the existing live-DOM-aware Next-button walk. Waits aggressively after
each click to let Convex finish streaming rows in. The goal is to answer:
does page 54 actually have 3 rows, or did our pipeline scrape too eagerly?
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from coinfiliate.browser import BrowserSession


async def main() -> None:
    target_page = 54
    async with BrowserSession(Path(".playwright/coinfiliate")) as ctx:
        page = await ctx.new_page()
        await page.goto(
            "https://www.coinfiliate.com/admin/partner-shop",
            wait_until="domcontentloaded",
        )
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await page.wait_for_timeout(2_000)

        rows_loc = page.locator(
            'tbody[data-slot="table-body"] tr[data-slot="table-row"]'
        )
        next_btn = page.locator('button[aria-label="Next page"]').first

        # Walk to target_page
        for step in range(target_page - 1):
            try:
                before = (await rows_loc.first.locator('td').nth(2).inner_text()).strip()
            except Exception:
                before = ""
            if not await next_btn.is_enabled():
                print(f"  Next disabled at step {step + 1}; only {step + 1} pages exist.")
                return
            await next_btn.click()
            try:
                await page.wait_for_function(
                    """([sel, prev]) => {
                        const r = document.querySelector(sel);
                        if (!r) return false;
                        const cells = r.querySelectorAll('td');
                        if (cells.length < 3) return false;
                        return (cells[2].innerText || '').trim() !== prev;
                    }""",
                    arg=['tbody[data-slot="table-body"] tr[data-slot="table-row"]', before],
                    timeout=15_000,
                )
            except Exception:
                pass

        # On target page now. Watch the row count over time so we can tell whether
        # rows stream in progressively (which would explain the 3-row scrape).
        for delay_s in (0.0, 0.5, 1.0, 2.0, 4.0):
            await page.wait_for_timeout(int(delay_s * 1000))
            n = await rows_loc.count()
            print(f"  +{delay_s:>4.1f}s : count={n}")

        # Final dump.
        n = await rows_loc.count()
        print(f"\nFinal rows on page {target_page}: {n}")
        for i in range(n):
            cells = rows_loc.nth(i).locator('td[data-slot="table-cell"]')
            try:
                name = (await cells.nth(2).inner_text()).strip()
                network = (await cells.nth(3).inner_text()).strip()
                status = (await cells.nth(4).inner_text()).strip()
            except Exception as e:
                name, network, status = f"<err {e}>", "", ""
            print(f"  [{i}] {name:30s}  {network:12s}  {status}")


if __name__ == "__main__":
    asyncio.run(main())
