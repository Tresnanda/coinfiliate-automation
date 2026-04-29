from __future__ import annotations

from typing import List, Optional
from playwright.async_api import BrowserContext, Page
from coinfiliate.decision import extract_etld1
from coinfiliate.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_CONSENT_TEXTS = [
    "Accept", "Allow All", "Accept All", "I Accept", "Agree", "Got it",
    "Alle akzeptieren", "Accepter", "Aceptar", "同意", "同意する",
]


async def collect_signals(page: Page, context: BrowserContext, affiliate_url: str,
                          *, consent_texts: Optional[List[str]] = None,
                          consent_wait_ms: int = 2000,
                          networkidle_timeout_s: int = 15) -> dict:
    consent_texts = consent_texts or DEFAULT_CONSENT_TEXTS
    response_urls: List[str] = []
    redirect_chain: List[str] = []

    def _on_response(resp):
        response_urls.append(resp.url)
        if 300 <= resp.status < 400:
            redirect_chain.append(resp.url)

    context.on("response", _on_response)

    await page.goto(affiliate_url, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=networkidle_timeout_s * 1000)
    except Exception:
        pass  # networkidle is best-effort

    # Auto-accept consent
    for text in consent_texts:
        try:
            btn = page.locator(f"button:has-text('{text}'), a:has-text('{text}')").first
            if await btn.is_visible(timeout=500):
                await btn.click()
                await page.wait_for_timeout(consent_wait_ms)
                break
        except Exception:
            continue

    cookies = await context.cookies()
    final_url = page.url
    final_etld1 = extract_etld1(final_url)

    # Tracker domains: any response host whose eTLD+1 differs from the landed domain
    tracker_domains = sorted({
        extract_etld1(u) for u in response_urls
        if extract_etld1(u) and extract_etld1(u) != final_etld1
    })

    return {
        "final_url": final_url,
        "final_etld1": final_etld1,
        "cookies": cookies,
        "redirect_chain": redirect_chain,
        "tracker_domains": tracker_domains,
    }
