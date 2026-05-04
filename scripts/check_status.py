"""Read the live Status column for VersedSkin.com and Alibaba LATAM."""
from __future__ import annotations

import asyncio
from pathlib import Path

from coinfiliate.browser import BrowserSession


TARGETS = ["VersedSkin.com", "Alibaba LATAM"]


async def main() -> None:
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

        # The list is sorted/paginated; just walk pages and look for the names.
        seen: dict[str, str] = {}
        for _ in range(80):
            rows = page.locator(
                'tbody[data-slot="table-body"] tr[data-slot="table-row"]'
            )
            n = await rows.count()
            for i in range(n):
                cells = rows.nth(i).locator('td[data-slot="table-cell"]')
                name = (await cells.nth(2).inner_text()).strip()
                status = (await cells.nth(4).inner_text()).strip()
                if name in TARGETS:
                    seen[name] = status
            if all(t in seen for t in TARGETS):
                break
            nxt = page.locator('button[aria-label="Next page"]').first
            if not await nxt.is_enabled():
                break
            # Reuse the live-DOM-aware navigation pattern from sync.py
            try:
                before = (await rows.first.locator('td').nth(2).inner_text()).strip()
            except Exception:
                before = ""
            await nxt.click()
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

        for name in TARGETS:
            print(f"  {name:25s} -> {seen.get(name, 'NOT FOUND')}")


if __name__ == "__main__":
    asyncio.run(main())
