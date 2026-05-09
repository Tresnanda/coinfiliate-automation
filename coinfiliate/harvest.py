from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from typing import List, Optional, Union
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

_ERROR_TITLE_PATTERN = re.compile(r"\b(404|not found|error|page does not exist)\b", re.I)


async def is_error_page(page, *, response_status: int | None) -> bool:
    """Return True if the landing page looks like a 404 / error / 'no product'.

    Three signals checked, any of which fires:
      - HTTP status >= 400 on the navigation response
      - <title> matches a 404/not-found/error pattern
      - <h1> matches the same pattern
    """
    if response_status is not None and response_status >= 400:
        return True
    info = await page.evaluate(
        """() => ({
            title: document.title || '',
            h1: (document.querySelector('h1')?.innerText || '').slice(0, 200),
        })"""
    )
    if _ERROR_TITLE_PATTERN.search(info.get("title") or ""):
        return True
    if _ERROR_TITLE_PATTERN.search(info.get("h1") or ""):
        return True
    return False


_CLICKABLE_JS = r"""
(() => {
    // Select clickable elements: links, buttons, role=link/button.
    // Compute a short, stable CSS selector for each by walking up to a
    // unique ancestor or using nth-of-type as a last resort.
    const NODES = Array.from(document.querySelectorAll(
        'a[href], button, [role="button"], [role="link"], input[type="submit"], input[type="button"]'
    )).filter(el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return false;          // hidden
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.display === 'none') return false;
        return true;
    });

    function selectorFor(el) {
        if (el.id) return '#' + CSS.escape(el.id);
        // Build a path with tag + nth-of-type, capped at 6 segments.
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && parts.length < 6) {
            let part = cur.tagName.toLowerCase();
            const parent = cur.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
                if (siblings.length > 1) {
                    const i = siblings.indexOf(cur) + 1;
                    part += `:nth-of-type(${i})`;
                }
            }
            parts.unshift(part);
            if (cur.id) { parts[0] = '#' + CSS.escape(cur.id); break; }
            cur = parent;
        }
        return parts.join(' > ');
    }

    return NODES.slice(0, 80).map((el, idx) => ({
        idx,
        tag: el.tagName.toLowerCase(),
        text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 120),
        href: el.getAttribute('href') || null,
        aria_label: el.getAttribute('aria-label') || null,
        selector: selectorFor(el),
    }));
})()
"""


async def collect_clickable_candidates(page) -> list[dict]:
    """Return up to 80 visible clickable elements with text + a CSS selector.

    The element-finder LLM picks an `idx`; the caller maps back to `selector`.
    Capped at 80 to keep the LLM prompt small.
    """
    return await page.evaluate(_CLICKABLE_JS)


@dataclass(frozen=True)
class AttemptSuccess:
    landing_url: str           # URL after consent + redirects (before product click)
    checkout_url: str          # URL bar at the end of the active flow
    cookies: List[dict]
    redirect_chain: List[str]
    tracker_domains: List[str]


@dataclass(frozen=True)
class AttemptFailure:
    kind: str                  # "Error404" | "NoProduct" | "NoCart" | "NoCheckout" | "Error"
    detail: Optional[str] = None


AttemptResult = Union[AttemptSuccess, AttemptFailure]


async def _click_one(page, candidates: list, *, idx: Optional[int],
                     post_click_wait_ms: int = 1500) -> bool:
    """Click candidates[idx]; return True on apparent success."""
    if idx is None or not (0 <= idx < len(candidates)):
        return False
    selector = candidates[idx]["selector"]
    try:
        loc = page.locator(selector).first
        await loc.click(timeout=10_000)
        await page.wait_for_timeout(post_click_wait_ms)
        return True
    except Exception:
        return False


async def _drive_step(page, *, finder, goal: str, url: str) -> bool:
    """One element-finder step with one re-snapshot retry on click failure."""
    candidates = await collect_clickable_candidates(page)
    idx = await finder.find_element(candidates=candidates, goal=goal, url=url)
    if await _click_one(page, candidates, idx=idx):
        return True
    # One re-snapshot retry: the page may have updated since the snapshot.
    candidates = await collect_clickable_candidates(page)
    idx = await finder.find_element(candidates=candidates, goal=goal, url=page.url)
    return await _click_one(page, candidates, idx=idx)


async def attempt_link(
    page, context, affiliate_url: str, *,
    finder,
    consent_texts: Optional[List[str]] = None,
    consent_wait_ms: int = 2000,
    networkidle_timeout_s: int = 15,
) -> AttemptResult:
    """Drive Landing → Product → Add-to-Cart → Checkout for one affiliate link.

    Args:
      page: a fresh Playwright Page.
      context: the BrowserContext that owns `page` (we attach a response listener).
      affiliate_url: the link to follow.
      finder: an ElementFinder.

    Returns AttemptSuccess on full traversal, or AttemptFailure with `kind`
    set to one of "Error404"/"NoProduct"/"NoCart"/"NoCheckout"/"Error".
    """
    consent_texts = consent_texts or DEFAULT_CONSENT_TEXTS
    response_urls: List[str] = []
    redirect_chain: List[str] = []

    def _on_response(resp):
        response_urls.append(resp.url)
        if 300 <= resp.status < 400:
            redirect_chain.append(resp.url)

    context.on("response", _on_response)

    try:
        nav = await page.goto(affiliate_url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle",
                                           timeout=networkidle_timeout_s * 1000)
        except Exception:
            pass

        for text in consent_texts:
            try:
                btn = page.locator(f"button:has-text('{text}'), a:has-text('{text}')").first
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    await page.wait_for_timeout(consent_wait_ms)
                    break
            except Exception:
                continue

        landing_url = page.url

        if await is_error_page(page, response_status=nav.status if nav else None):
            return AttemptFailure(kind="Error404", detail=f"landing={landing_url}")

        if not await _drive_step(page, finder=finder,
                                 goal="navigate to a product detail page", url=page.url):
            return AttemptFailure(kind="NoProduct", detail=f"landing={landing_url}")

        if not await _drive_step(page, finder=finder,
                                 goal="add the current product to cart", url=page.url):
            return AttemptFailure(kind="NoCart", detail=f"after_pdp={page.url}")

        if not await _drive_step(page, finder=finder,
                                 goal="proceed to checkout", url=page.url):
            return AttemptFailure(kind="NoCheckout", detail=f"after_cart={page.url}")

        # Wait for the post-checkout-click navigation to settle so we capture
        # the final URL bar (Shopify checkouts are JS-routed).
        try:
            await page.wait_for_load_state("networkidle",
                                           timeout=networkidle_timeout_s * 1000)
        except Exception:
            pass

        cookies = await context.cookies()
        checkout_url = page.url

        tracker_domains = sorted({
            extract_etld1(u) for u in response_urls
            if extract_etld1(u) and extract_etld1(u) != extract_etld1(landing_url)
        })

        return AttemptSuccess(
            landing_url=landing_url,
            checkout_url=checkout_url,
            cookies=list(cookies),
            redirect_chain=list(redirect_chain),
            tracker_domains=tracker_domains,
        )
    except Exception as e:
        return AttemptFailure(kind="Error", detail=f"{type(e).__name__}: {e}")
    finally:
        try:
            context.remove_listener("response", _on_response)
        except Exception:
            pass


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
    """Per-shop harvest with retry across all affiliate links.

    For each link in order (current is_harvest_source first, then by id),
    open a fresh context, run attempt_link, and break on AttemptSuccess.
    If all links fail, mark the shop failed/needs_review based on the
    failure kinds seen.
    """
    shop = next(s for s in await store.list_shops() if s["id"] == shop_id)
    log.info("harvest.shop.start", shop=shop["name"], network=shop["network"], shop_id=shop_id)

    links = await store.list_affiliate_links_ordered(shop_id)
    if not links:
        log.info("harvest.shop.skipped", shop=shop["name"], shop_id=shop_id,
                 reason="no affiliate links")
        await store.update_shop_status(shop_id, "failed",
                                       last_error="no affiliate links")
        return

    failures: list[tuple[str, str]] = []
    success: AttemptSuccess | None = None
    chosen_link_id: str | None = None

    for link in links:
        try:
            async with fresh_context(browser) as ctx:
                page = await ctx.new_page()
                result = await attempt_link(
                    page, ctx, link["affiliate_url"],
                    finder=llm,
                    consent_wait_ms=settings.harvest.consent_wait_ms,
                    networkidle_timeout_s=settings.harvest.networkidle_timeout_seconds,
                )
        except Exception as e:
            failures.append((link["link_id"], f"Error:{type(e).__name__}"))
            log.warning("harvest.shop.link.exception",
                        shop=shop["name"], shop_id=shop_id,
                        link_id=link["link_id"], err=f"{type(e).__name__}: {e}")
            continue

        if isinstance(result, AttemptSuccess):
            success = result
            chosen_link_id = link["link_id"]
            log.info("harvest.shop.link.success",
                     shop=shop["name"], shop_id=shop_id,
                     link_id=link["link_id"], checkout_url=result.checkout_url)
            break

        failures.append((link["link_id"], result.kind))
        log.info("harvest.shop.link.failed",
                 shop=shop["name"], shop_id=shop_id,
                 link_id=link["link_id"], kind=result.kind, detail=result.detail)

    if success is None:
        kinds = {kind for _, kind in failures}
        # 404 / no-product → almost certainly a dead shop. NoCart/NoCheckout/Error
        # could be us misreading the UI on a real shop — kick to review.
        if kinds and kinds <= {"Error404", "NoProduct"}:
            status = "failed"
        else:
            status = "needs_review"
        err = "; ".join(f"{lid}:{k}" for lid, k in failures)[:500]
        log.error("harvest.shop.exhausted",
                  shop=shop["name"], shop_id=shop_id,
                  status=status, failures=failures)
        await store.update_shop_status(shop_id, status, last_error=err)
        return

    checkout_etld1 = extract_etld1(success.checkout_url)
    landing_etld1 = extract_etld1(success.landing_url)

    hctx = HarvestContext(
        shop_name=shop["name"], network=shop["network"],
        final_url=success.landing_url, final_etld1=landing_etld1,
        cookies=success.cookies, redirect_chain=success.redirect_chain,
        tracker_domains=success.tracker_domains,
        checkout_url=success.checkout_url, checkout_etld1=checkout_etld1,
    )
    decision = await decide(hctx, llm=llm)
    ok = decision.primary_cookie_name is not None

    await store.insert_harvest(
        shop_id=shop_id,
        final_url=success.landing_url, final_etld1=landing_etld1,
        cookies=success.cookies, redirect_chain=success.redirect_chain,
        tracker_domains=success.tracker_domains,
        primary_cookie_name=decision.primary_cookie_name,
        tracking_cookie_names=decision.tracking_cookie_names,
        checkout_domains=decision.checkout_domains,
        tracking_cookie_domains=decision.tracking_cookie_domains,
        decision_source=decision.decision_source,
        confidence=decision.confidence,
        llm_rationale=decision.rationale, ok=ok,
        checkout_url=success.checkout_url,
        checkout_etld1=checkout_etld1,
        attempted_link_id=chosen_link_id,
    )

    if chosen_link_id and chosen_link_id != links[0]["link_id"]:
        await store.mark_harvest_source(shop_id, chosen_link_id)

    log.info("harvest.shop.ok",
             shop=shop["name"], shop_id=shop_id,
             attempted_link_id=chosen_link_id,
             decision_source=decision.decision_source,
             confidence=decision.confidence,
             primary_cookie_name=decision.primary_cookie_name, ok=ok)

    if ok and decision.confidence >= settings.harvest.review_threshold:
        await store.update_shop_status(shop_id, "harvested")
    else:
        await store.update_shop_status(shop_id, "needs_review")


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
