from __future__ import annotations

import asyncio
import random
from typing import List, Optional
from playwright.async_api import BrowserContext, Page
from coinfiliate.browser import fresh_context
from coinfiliate.decision import decide, extract_etld1
from coinfiliate.logging_setup import get_logger
from coinfiliate.models import HarvestContext

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


async def harvest_shop(store, *, shop_id: int, settings, llm, browser) -> None:
    """Per-shop harvest: open affiliate URL, collect signals, decide, persist."""
    shop = next(s for s in await store.list_shops() if s["id"] == shop_id)
    log.info("harvest.shop.start", shop=shop["name"], network=shop["network"], shop_id=shop_id)
    src = await store.get_harvest_source(shop_id)
    if not src:
        # The sync phase produced zero affiliate links for this shop, so there
        # is no URL to follow for cookie/redirect signals. This is a data-state
        # outcome, not a runtime error — mark and move on without raising.
        log.info(
            "harvest.shop.skipped",
            shop=shop["name"], network=shop["network"], shop_id=shop_id,
            reason="no harvest_source link",
        )
        await store.update_shop_status(shop_id, "failed", last_error="no harvest_source link")
        return

    try:
        async with fresh_context(browser) as ctx:
            page = await ctx.new_page()
            sig = await collect_signals(
                page, ctx, src["affiliate_url"],
                consent_wait_ms=settings.harvest.consent_wait_ms,
                networkidle_timeout_s=settings.harvest.networkidle_timeout_seconds,
            )
        hctx = HarvestContext(
            shop_name=shop["name"], network=shop["network"],
            final_url=sig["final_url"], final_etld1=sig["final_etld1"],
            cookies=sig["cookies"], redirect_chain=sig["redirect_chain"],
            tracker_domains=sig["tracker_domains"],
        )
        decision = await decide(hctx, llm=llm)
        ok = decision.primary_cookie_name is not None

        await store.insert_harvest(
            shop_id=shop_id,
            final_url=sig["final_url"], final_etld1=sig["final_etld1"],
            cookies=sig["cookies"], redirect_chain=sig["redirect_chain"],
            tracker_domains=sig["tracker_domains"],
            primary_cookie_name=decision.primary_cookie_name,
            tracking_cookie_names=decision.tracking_cookie_names,
            checkout_domains=decision.checkout_domains,
            tracking_cookie_domains=decision.tracking_cookie_domains,
            decision_source=decision.decision_source,
            confidence=decision.confidence,
            llm_rationale=decision.rationale, ok=ok,
        )

        log.info(
            "harvest.shop.ok",
            shop=shop["name"], network=shop["network"], shop_id=shop_id,
            decision_source=decision.decision_source,
            confidence=decision.confidence,
            primary_cookie_name=decision.primary_cookie_name,
            ok=ok,
        )

        if ok and decision.confidence >= settings.harvest.review_threshold:
            await store.update_shop_status(shop_id, "harvested")
        else:
            await store.update_shop_status(shop_id, "needs_review")
    except Exception as e:
        log.error(
            "harvest.shop.failed",
            shop=shop["name"], network=shop["network"], shop_id=shop_id,
            err=f"{type(e).__name__}: {e}",
        )
        await store.update_shop_status(shop_id, "failed", last_error=f"{type(e).__name__}: {e}")
        raise


async def run_harvest(store, *, settings, llm, browser) -> None:
    """Top-level orchestrator: harvest all pending shops with bounded concurrency."""
    pending = await store.list_shops(status="pending")
    pending = pending[: settings.runner.max_shops_per_batch]
    sem = asyncio.Semaphore(settings.runner.max_concurrency)

    async def _one(shop_id: int):
        async with sem:
            lo, hi = settings.runner.inter_shop_jitter_ms
            await asyncio.sleep(random.randint(lo, hi) / 1000)
            try:
                await harvest_shop(store, shop_id=shop_id, settings=settings, llm=llm, browser=browser)
            except Exception:
                # Inner harvest_shop already logged + persisted the failure;
                # swallow here so a single bad shop doesn't poison the batch.
                pass

    await asyncio.gather(*[_one(s["id"]) for s in pending])
