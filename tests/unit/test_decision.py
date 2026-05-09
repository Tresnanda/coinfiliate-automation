from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from coinfiliate.models import HarvestContext, HarvestDecision
from coinfiliate.decision import decide


def _ctx(cookies, *, checkout_url="https://s.com/checkouts/cn/abc", checkout_etld1="s.com"):
    return HarvestContext(shop_name="S", network="flexoffers",
                          final_url="https://s.com/", final_etld1="s.com",
                          cookies=cookies, redirect_chain=[], tracker_domains=[],
                          checkout_url=checkout_url, checkout_etld1=checkout_etld1)


def _cookie(name):
    return {"name": name, "value": "v"}


async def test_decide_uses_strict_match_without_calling_llm():
    llm = MagicMock()
    llm.analyze = AsyncMock()
    d = await decide(_ctx([_cookie("pjnclick"), _cookie("_ga")]), llm=llm)
    assert d.primary_cookie_name == "pjnclick"
    assert d.decision_source == "heuristic"
    assert d.confidence == 1.0
    llm.analyze.assert_not_awaited()


async def test_decide_falls_back_to_llm_when_only_generic_analytics_cookies_present():
    # __kla_id, _ga, _fbp etc. used to short-circuit as "loose" matches; now
    # they must be sent to the LLM since they aren't true affiliate cookies.
    llm = MagicMock()
    llm.analyze = AsyncMock(return_value=HarvestDecision(
        primary_cookie_name=None,
        tracking_cookie_names=[],
        checkout_domains=["s.com"],
        tracking_cookie_domains=["s.com"],
        decision_source="llm", confidence=0.2, rationale="no affiliate cookie",
    ))
    d = await decide(_ctx([_cookie("__kla_id"), _cookie("_ga")]), llm=llm)
    assert d.decision_source == "llm"
    llm.analyze.assert_awaited_once()


async def test_decide_falls_back_to_llm_when_heuristics_miss():
    llm = MagicMock()
    llm.analyze = AsyncMock(return_value=HarvestDecision(
        primary_cookie_name="custom_id",
        tracking_cookie_names=["custom_id"],
        checkout_domains=["s.com"],
        tracking_cookie_domains=["s.com"],
        decision_source="llm", confidence=0.7, rationale="it looked unique",
    ))
    d = await decide(_ctx([_cookie("random_name"), _cookie("session")]), llm=llm)
    assert d.primary_cookie_name == "custom_id"
    assert d.decision_source == "llm"
    llm.analyze.assert_awaited_once()


async def test_decide_returns_empty_when_llm_also_yields_nothing():
    llm = MagicMock()
    llm.analyze = AsyncMock(side_effect=ValueError("LLM failed"))
    d = await decide(_ctx([_cookie("session")]), llm=llm)
    assert d.primary_cookie_name is None
    assert d.decision_source == "llm"
    assert d.confidence == 0.0


async def test_strict_match_uses_checkout_etld1_for_checkout_domains():
    llm = MagicMock(); llm.analyze = AsyncMock()
    ctx = _ctx(
        [{"name": "pjnclick", "value": "v", "domain": ".s.com"}],
        checkout_url="https://s.com/checkouts/cn/x", checkout_etld1="s.com",
    )
    d = await decide(ctx, llm=llm)
    assert d.checkout_domains == ["s.com"]


async def test_strict_match_uses_cookie_domain_for_tracking_cookie_domains():
    llm = MagicMock(); llm.analyze = AsyncMock()
    # Cookie scoped to .merchant.com, checkout on pay.shopify.com — different eTLD+1s.
    ctx = _ctx(
        [{"name": "pjnclick", "value": "v", "domain": ".merchant.com"}],
        checkout_url="https://pay.shopify.com/x", checkout_etld1="shopify.com",
    )
    d = await decide(ctx, llm=llm)
    assert "merchant.com" in d.tracking_cookie_domains
    assert "shopify.com" in d.tracking_cookie_domains


async def test_strict_match_falls_back_to_checkout_etld1_when_cookie_has_no_domain():
    llm = MagicMock(); llm.analyze = AsyncMock()
    ctx = _ctx(
        [{"name": "pjnclick", "value": "v"}],  # host-only cookie, no domain attr
        checkout_url="https://s.com/checkouts/cn/x", checkout_etld1="s.com",
    )
    d = await decide(ctx, llm=llm)
    assert d.tracking_cookie_domains == ["s.com"]
