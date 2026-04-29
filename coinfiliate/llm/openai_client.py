from __future__ import annotations

import asyncio
import json
from coinfiliate.models import HarvestContext, HarvestDecision
from coinfiliate.llm.prompt import SYSTEM, build_user_prompt


class OpenAICookieAnalyzer:
    def __init__(self, *, client, model: str, max_retries: int, timeout_seconds: int):
        self._client = client
        self._model = model
        self._max_retries = max_retries
        self._timeout = timeout_seconds

    async def analyze(self, ctx: HarvestContext) -> HarvestDecision:
        last_err = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.chat.completions.create(
                    model=self._model,
                    timeout=self._timeout,
                    messages=[
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": build_user_prompt(ctx)},
                    ],
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp.choices[0].message.content)
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
        raise ValueError(f"LLM failed after {self._max_retries} attempts: {last_err}")
