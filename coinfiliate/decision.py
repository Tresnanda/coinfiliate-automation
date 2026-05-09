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


async def decide(ctx: HarvestContext, *, llm: CookieAnalyzer) -> HarvestDecision:
    # 1. strict heuristic — only network-native affiliate cookies match here.
    strict = strict_match(ctx.cookies)
    if strict:
        return HarvestDecision(
            primary_cookie_name=strict["name"],
            tracking_cookie_names=[strict["name"]],
            checkout_domains=[ctx.final_etld1],
            tracking_cookie_domains=[ctx.final_etld1],
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
            checkout_domains=[ctx.final_etld1],
            tracking_cookie_domains=[ctx.final_etld1],
            decision_source="llm",
            confidence=0.0,
            rationale=f"LLM failed: {e}",
        )
