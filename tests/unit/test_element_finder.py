from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from coinfiliate.llm.openai_client import OpenAICookieAnalyzer


def _fake_openai_response(content: dict):
    """Return a MagicMock shaped like an openai chat.completions response."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(content)
    return resp


@pytest.mark.unit
async def test_find_element_returns_idx_when_llm_picks_one():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response({"idx": 2, "confidence": 0.9})
    )
    finder = OpenAICookieAnalyzer(client=client, model="m", max_retries=1, timeout_seconds=10)

    candidates = [
        {"idx": 0, "tag": "a", "text": "Home", "selector": "nav a:nth-of-type(1)"},
        {"idx": 1, "tag": "a", "text": "About", "selector": "nav a:nth-of-type(2)"},
        {"idx": 2, "tag": "a", "text": "Roses Bouquet $30", "selector": "main a:nth-of-type(1)"},
    ]
    out = await finder.find_element(
        candidates=candidates, goal="navigate to a product detail page",
        url="https://shop.example.com/",
    )
    assert out == 2


@pytest.mark.unit
async def test_find_element_returns_none_when_idx_out_of_range():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response({"idx": 99, "confidence": 0.9})
    )
    finder = OpenAICookieAnalyzer(client=client, model="m", max_retries=1, timeout_seconds=10)
    out = await finder.find_element(
        candidates=[{"idx": 0, "tag": "a", "text": "x", "selector": "a"}],
        goal="g", url="https://x/",
    )
    assert out is None


@pytest.mark.unit
async def test_find_element_returns_none_when_idx_is_null():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response({"idx": None, "confidence": 0.0})
    )
    finder = OpenAICookieAnalyzer(client=client, model="m", max_retries=1, timeout_seconds=10)
    out = await finder.find_element(candidates=[], goal="g", url="https://x/")
    assert out is None


@pytest.mark.unit
async def test_gemini_find_element_returns_idx():
    from coinfiliate.llm.gemini_client import GeminiCookieAnalyzer

    fake_resp = MagicMock()
    fake_resp.text = '{"idx": 1, "confidence": 0.8}'
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=fake_resp)

    finder = GeminiCookieAnalyzer(client=client, model="m", max_retries=1)
    out = await finder.find_element(
        candidates=[
            {"idx": 0, "tag": "a", "text": "x", "selector": "a"},
            {"idx": 1, "tag": "button", "text": "Add to Cart", "selector": "form button"},
        ],
        goal="add to cart", url="https://x/",
    )
    assert out == 1


@pytest.mark.unit
async def test_gemini_find_element_handles_fenced_json():
    """Gemini sometimes wraps JSON in ```json ... ``` fences; the impl must extract."""
    from coinfiliate.llm.gemini_client import GeminiCookieAnalyzer

    fake_resp = MagicMock()
    fake_resp.text = '```json\n{"idx": 0, "confidence": 0.9}\n```'
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=fake_resp)
    finder = GeminiCookieAnalyzer(client=client, model="m", max_retries=1)
    out = await finder.find_element(
        candidates=[{"idx": 0, "tag": "a", "text": "x", "selector": "a"}],
        goal="g", url="https://x/",
    )
    assert out == 0
