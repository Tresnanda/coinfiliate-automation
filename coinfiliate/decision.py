from __future__ import annotations

from typing import List, Optional
import tldextract

_STRICT_KEYWORDS = [
    "pjnclick", "irclick", "ir_", "awc", "fobs_", "_ck_", "_wg_",
    "cj_source", "cje", "impact", "partnerize_", "rakuten_", "click_id",
]

# Ordered by preference: per-user tracking IDs beat session IDs beat generic analytics
_LOOSE_KEYWORDS_ORDERED = [
    "__kla_id",           # Klaviyo
    "ajs_anonymous_id",   # Segment
    "_gcl_aw",            # Google Ads click ID
    "_fbp",               # Facebook Pixel
    "_shopify_y",         # Shopify long-lived anon
    "_ga",                # Google Analytics (fallback)
]


def extract_etld1(url: str) -> str:
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain


def strict_match(cookies: List[dict]) -> Optional[dict]:
    for c in cookies:
        name = c["name"].lower()
        for kw in _STRICT_KEYWORDS:
            if kw in name:
                return c
    return None


def loose_match(cookies: List[dict]) -> Optional[dict]:
    by_name = {c["name"]: c for c in cookies}
    for kw in _LOOSE_KEYWORDS_ORDERED:
        if kw in by_name:
            return by_name[kw]
    return None
