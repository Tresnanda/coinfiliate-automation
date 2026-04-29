from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from coinfiliate.models import HarvestContext
from coinfiliate.llm.openai_client import OpenAICookieAnalyzer


def _ctx():
    return HarvestContext(shop_name="Notch", network="flexoffers",
                          final_url="https://notchgear.com/",
                          final_etld1="notchgear.com",
                          cookies=[{"name": "__kla_id", "value": "abc"}],
                          redirect_chain=["https://notchgear.com/"],
                          tracker_domains=[])


async def test_openai_analyzer_parses_valid_response():
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "primary_cookie_name": "__kla_id",
            "tracking_cookie_names": ["__kla_id"],
            "checkout_domains": ["notchgear.com"],
            "tracking_cookie_domains": ["notchgear.com"],
            "confidence": 0.8,
            "rationale": "Klaviyo ID is the only per-user tracker present.",
        })))]
    ))

    a = OpenAICookieAnalyzer(client=mock_client, model="gpt-4o-mini", max_retries=1, timeout_seconds=10)
    d = await a.analyze(_ctx())

    assert d.primary_cookie_name == "__kla_id"
    assert d.confidence == 0.8
    assert d.decision_source == "llm"


async def test_openai_analyzer_malformed_json_raises_after_retries():
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content="not json at all"))]
    ))

    a = OpenAICookieAnalyzer(client=mock_client, model="gpt-4o-mini", max_retries=2, timeout_seconds=10)
    with pytest.raises(ValueError):
        await a.analyze(_ctx())
    assert mock_client.chat.completions.create.await_count == 2
