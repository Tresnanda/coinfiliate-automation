from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from coinfiliate.harvest import is_error_page


def _page(*, status=200, title="OK", h1=""):
    page = MagicMock()
    page.evaluate = AsyncMock(return_value={"title": title, "h1": h1})
    return page, status


@pytest.mark.unit
async def test_is_error_page_when_status_404():
    page, status = _page(status=404, title="Page Not Found")
    assert await is_error_page(page, response_status=status) is True


@pytest.mark.unit
async def test_is_error_page_when_title_says_404():
    page, status = _page(status=200, title="404 - this product does not exist")
    assert await is_error_page(page, response_status=status) is True


@pytest.mark.unit
async def test_is_error_page_when_h1_says_not_found():
    page, status = _page(status=200, title="Cool Store", h1="Page not found")
    assert await is_error_page(page, response_status=status) is True


@pytest.mark.unit
async def test_is_not_error_page_on_normal_page():
    page, status = _page(status=200, title="Buy Roses | EnjoyFlowers", h1="Designer Bouquet")
    assert await is_error_page(page, response_status=status) is False


@pytest.mark.unit
async def test_collect_clickable_candidates_signature():
    """Smoke: function exists and is callable with a Page-like mock.

    Real DOM behavior is exercised in the integration test using the fake
    merchant server. Here we just ensure the function shape (it returns a
    list of dicts after evaluating page JS).
    """
    from coinfiliate.harvest import collect_clickable_candidates
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"idx": 0, "tag": "a", "text": "Shop now", "selector": "#hero a"},
        {"idx": 1, "tag": "button", "text": "Add to Cart", "selector": "form button"},
    ])
    out = await collect_clickable_candidates(page)
    assert len(out) == 2
    assert out[0]["selector"] == "#hero a"
