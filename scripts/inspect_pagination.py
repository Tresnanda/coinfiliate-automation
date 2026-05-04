"""One-shot DOM inspector for the Partner Shop pagination footer.

Run after `python main.py run` has already authenticated (so the persistent
context has a Clerk session). Prints the relevant fragment so we can write a
correct selector for `_go_to_next_page`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from coinfiliate.browser import BrowserSession


async def main() -> None:
    async with BrowserSession(Path(".playwright/coinfiliate")) as ctx:
        page = await ctx.new_page()
        await page.goto("https://www.coinfiliate.com/admin/partner-shop", wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass

        print("=== buttons with 'Next' text or aria-label containing 'next' (case-insensitive) ===")
        candidates = await page.evaluate(
            """
            () => {
              const out = [];
              for (const el of document.querySelectorAll('button, a')) {
                const text = (el.innerText || '').trim();
                const al = el.getAttribute('aria-label') || '';
                const tl = el.title || '';
                if (/next/i.test(text) || /next/i.test(al) || /next/i.test(tl)) {
                  out.push({
                    tag: el.tagName,
                    text,
                    aria_label: al,
                    title: tl,
                    disabled: el.hasAttribute('disabled'),
                    aria_disabled: el.getAttribute('aria-disabled'),
                    data_disabled: el.getAttribute('data-disabled'),
                    classes: el.className,
                    outerHTML: el.outerHTML.slice(0, 400),
                  });
                }
              }
              return out;
            }
            """
        )
        for c in candidates:
            print(c)

        print("\n=== 'Go to page' control HTML (if present) ===")
        gotopage = await page.evaluate(
            """
            () => {
              for (const el of document.querySelectorAll('*')) {
                const t = el.innerText || '';
                if (/go to page/i.test(t) && el.children.length < 8) {
                  return el.outerHTML.slice(0, 1000);
                }
              }
              return null;
            }
            """
        )
        print(gotopage)

        print("\n=== entire pagination footer HTML ===")
        footer_html = await page.evaluate(
            """
            () => {
              // Find a container whose text includes 'Go to page' OR matches the
              // numbered-pages pattern. Walk up to a reasonable wrapper.
              const probe = Array.from(document.querySelectorAll('*')).find(el => {
                const t = el.innerText || '';
                return /go to page/i.test(t) && el.children.length > 0 && el.children.length < 30;
              });
              if (!probe) return null;
              return probe.outerHTML.slice(0, 4000);
            }
            """
        )
        print(footer_html)


if __name__ == "__main__":
    asyncio.run(main())
