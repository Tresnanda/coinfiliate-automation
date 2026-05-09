from __future__ import annotations

from typing import List, Optional
import tldextract

from coinfiliate.models import HarvestContext, HarvestDecision
from coinfiliate.llm.base import CookieAnalyzer

_STRICT_KEYWORDS = [
    "pjnclick", "irclick", "ir_", "awc", "fobs_", "_ck_", "_wg_",
    "cj_source", "cje", "impact", "partnerize_", "rakuten_", "click_id",
]


def extract_etld1(url: str) -> str:
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain


def cookie_domain_etld1(cookie: dict) -> Optional[str]:
    """Return eTLD+1 of a cookie's `domain` attribute, or None if absent/non-domain.

    Strips a leading dot (Set-Cookie domains often start with '.', meaning the
    cookie applies to subdomains). Returns None for localhost/IP/missing —
    these aren't real eTLD+1 scopes.
    """
    raw = (cookie.get("domain") or "").lstrip(".").strip()
    if not raw:
        return None
    ext = tldextract.extract(raw)
    if not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


def strict_match(cookies: List[dict]) -> Optional[dict]:
    for c in cookies:
        name = c["name"].lower()
        for kw in _STRICT_KEYWORDS:
            if kw in name:
                return c
    return None


def _checkout_domain(ctx: HarvestContext) -> str:
    """Return the eTLD+1 to use for checkout_domains.

    Falls back to landing eTLD+1 only when the active flow didn't run.
    """
    return ctx.checkout_etld1 or ctx.final_etld1


def _tracking_cookie_domains(primary_cookie: dict, ctx: HarvestContext) -> List[str]:
    """Compose tracking_cookie_domains from the cookie's own scope + checkout.

    Use the primary cookie's own `domain` attribute (eTLD+1-normalized). If it
    differs from the checkout eTLD+1, include both — the field is list-valued
    and Coinfiliate's matcher checks any-match-wins.
    """
    checkout = _checkout_domain(ctx)
    cookie_etld1 = cookie_domain_etld1(primary_cookie)
    if cookie_etld1 is None:
        return [checkout]
    if cookie_etld1 == checkout:
        return [checkout]
    return [cookie_etld1, checkout]


async def decide(ctx: HarvestContext, *, llm: CookieAnalyzer) -> HarvestDecision:
    # 1. strict heuristic — only network-native affiliate cookies match here.
    strict = strict_match(ctx.cookies)
    if strict:
        return HarvestDecision(
            primary_cookie_name=strict["name"],
            tracking_cookie_names=[strict["name"]],
            checkout_domains=[_checkout_domain(ctx)],
            tracking_cookie_domains=_tracking_cookie_domains(strict, ctx),
            decision_source="heuristic",
            confidence=1.0,
            rationale=None,
        )
    # 2. LLM fallback — generic analytics cookies (_ga, _fbp, __kla_id, …) are
    # not affiliate trackers, so we let the model decide instead of guessing.
    try:
        return await llm.analyze(ctx)
    except Exception as e:
        return HarvestDecision(
            primary_cookie_name=None,
            tracking_cookie_names=[],
            checkout_domains=[_checkout_domain(ctx)],
            tracking_cookie_domains=[_checkout_domain(ctx)],
            decision_source="llm",
            confidence=0.0,
            rationale=f"LLM failed: {e}",
        )
