from __future__ import annotations

import pytest
from coinfiliate.decision import cookie_domain_etld1


@pytest.mark.unit
def test_strips_leading_dot_and_extracts_etld1():
    assert cookie_domain_etld1({"domain": ".example.com"}) == "example.com"


@pytest.mark.unit
def test_handles_subdomain():
    assert cookie_domain_etld1({"domain": "checkout.shop.example.co.uk"}) == "example.co.uk"


@pytest.mark.unit
def test_returns_none_when_no_domain_key():
    assert cookie_domain_etld1({"name": "x", "value": "v"}) is None


@pytest.mark.unit
def test_returns_none_for_empty_domain():
    assert cookie_domain_etld1({"domain": ""}) is None


@pytest.mark.unit
def test_returns_none_for_localhost_or_ip():
    # tldextract returns suffix='' for these — we treat them as no eTLD+1.
    assert cookie_domain_etld1({"domain": "localhost"}) is None
    assert cookie_domain_etld1({"domain": "127.0.0.1"}) is None
