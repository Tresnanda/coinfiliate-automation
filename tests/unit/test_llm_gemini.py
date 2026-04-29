from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from coinfiliate.models import HarvestContext
from coinfiliate.llm.gemini_client import GeminiCookieAnalyzer


def _ctx():
    return HarvestContext(shop_name="K", network="flexoffers",
                          final_url="https://k.com/", final_etld1="k.com",
                          cookies=[{"name": "awc", "value": "1"}],
                          redirect_chain=[], tracker_domains=[])


async def test_gemini_analyzer_parses_valid_response():
    # Mock client.aio.models.generate_content
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=MagicMock(
        text=json.dumps({
            "primary_cookie_name": "awc",
            "tracking_cookie_names": ["awc"],
            "checkout_domains": ["k.com"],
            "tracking_cookie_domains": ["k.com"],
            "confidence": 0.95,
            "rationale": "Awin awc cookie present.",
        })
    ))

    a = GeminiCookieAnalyzer(client=mock_client, model="gemini-2.5-flash", max_retries=1)
    d = await a.analyze(_ctx())

    assert d.primary_cookie_name == "awc"
    assert d.confidence == 0.95
    assert d.decision_source == "llm"


async def test_gemini_analyzer_strips_code_fences():
    """Gemini sometimes wraps JSON in ```json ... ``` fences; client should handle that."""
    fenced = "```json\n" + json.dumps({
        "primary_cookie_name": "x",
        "tracking_cookie_names": [],
        "checkout_domains": [],
        "tracking_cookie_domains": [],
        "confidence": 0.5,
        "rationale": "r",
    }) + "\n```"
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=MagicMock(text=fenced))

    a = GeminiCookieAnalyzer(client=mock_client, model="gemini-2.5-flash", max_retries=1)
    d = await a.analyze(_ctx())
    assert d.primary_cookie_name == "x"


async def test_gemini_analyzer_retries_then_raises():
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=MagicMock(text="not json"))

    a = GeminiCookieAnalyzer(client=mock_client, model="gemini-2.5-flash", max_retries=2)
    with pytest.raises(ValueError):
        await a.analyze(_ctx())
    assert mock_client.aio.models.generate_content.await_count == 2
