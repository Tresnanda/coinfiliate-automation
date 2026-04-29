from __future__ import annotations

from coinfiliate.decision import strict_match, loose_match


def _cookie(name: str) -> dict:
    return {"name": name, "value": "v", "domain": ".x", "path": "/"}


def test_strict_match_finds_pjnclick():
    cookies = [_cookie("_ga"), _cookie("pjnclick"), _cookie("_fbp")]
    match = strict_match(cookies)
    assert match is not None and match["name"] == "pjnclick"


def test_strict_match_finds_fobs_prefix():
    cookies = [_cookie("fobs_12345")]
    assert strict_match(cookies)["name"] == "fobs_12345"


def test_strict_match_returns_none_when_no_match():
    cookies = [_cookie("__kla_id"), _cookie("_ga")]
    assert strict_match(cookies) is None


def test_loose_match_finds_kla_id():
    cookies = [_cookie("_ga"), _cookie("__kla_id")]
    assert loose_match(cookies)["name"] == "__kla_id"


def test_loose_match_prefers_kla_over_ga():
    # __kla_id is more specific (per-user ID) than _ga
    cookies = [_cookie("_ga"), _cookie("__kla_id")]
    assert loose_match(cookies)["name"] == "__kla_id"
