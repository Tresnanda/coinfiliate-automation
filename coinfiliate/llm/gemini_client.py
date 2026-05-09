from __future__ import annotations

import asyncio
import json
import re
from typing import Optional
from coinfiliate.models import HarvestContext, HarvestDecision
from coinfiliate.llm.prompt import SYSTEM, build_user_prompt


class GeminiCookieAnalyzer:
    def __init__(self, *, client, model: str, max_retries: int = 3):
        self._client = client
        self._model = model
        self._max_retries = max_retries

    async def analyze(self, ctx: HarvestContext) -> HarvestDecision:
        prompt = SYSTEM + "\n\n" + build_user_prompt(ctx)
        last_err = None
        for attempt in range(self._max_retries):
            try:
                # Use aio (async) entry point on google-genai client.
                resp = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=prompt,
                )
                # Gemini sometimes wraps JSON in ```json ... ``` fences; extract the JSON object.
                text = resp.text.strip()
                m = re.search(r"\{.*\}", text, re.DOTALL)
                data = json.loads(m.group(0) if m else text)
                return HarvestDecision(
                    primary_cookie_name=data["primary_cookie_name"],
                    tracking_cookie_names=list(data.get("tracking_cookie_names", [])),
                    checkout_domains=list(data.get("checkout_domains", [])),
                    tracking_cookie_domains=list(data.get("tracking_cookie_domains", [])),
                    decision_source="llm",
                    confidence=float(data.get("confidence", 0.0)),
                    rationale=data.get("rationale"),
                )
            except Exception as e:
                last_err = e
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        raise ValueError(f"Gemini failed after {self._max_retries} attempts: {last_err}")

    async def find_element(
        self, *, candidates: list, goal: str, url: str,
    ) -> Optional[int]:
        """Ask Gemini which candidate idx satisfies goal. None = no match.

        Mirrors OpenAICookieAnalyzer.find_element. Single-shot, errors → None.
        """
        from coinfiliate.llm.prompt import ELEMENT_FINDER_SYSTEM, build_element_finder_prompt
        prompt = ELEMENT_FINDER_SYSTEM + "\n\n" + build_element_finder_prompt(
            candidates=candidates, goal=goal, url=url,
        )
        try:
            resp = await self._client.aio.models.generate_content(
                model=self._model, contents=prompt,
            )
            text = resp.text.strip()
            m = re.search(r"\{.*\}", text, re.DOTALL)
            data = json.loads(m.group(0) if m else text)
            idx = data.get("idx")
            if idx is None:
                return None
            idx = int(idx)
            if not (0 <= idx < len(candidates)):
                return None
            return idx
        except Exception:
            return None
