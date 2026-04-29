from __future__ import annotations

from coinfiliate.decision import extract_etld1


def test_etld1_from_real_affiliate_url():
    assert extract_etld1("https://track.flexlinkspro.com/g.ashx?foid=...") == "flexlinkspro.com"


def test_etld1_from_merchant_url():
    assert extract_etld1("https://www.kryptek.com/spring-sale") == "kryptek.com"


def test_etld1_from_subdomain():
    assert extract_etld1("https://checkout.notchgear.com/cart") == "notchgear.com"


def test_etld1_handles_cc_tld():
    assert extract_etld1("https://shop.example.co.uk/x") == "example.co.uk"
