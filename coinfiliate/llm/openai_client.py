from __future__ import annotations

import asyncio
import json
from typing import Optional
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

    async def find_element(
        self, *, candidates: list, goal: str, url: str,
    ) -> Optional[int]:
        """Ask the LLM which candidate idx satisfies goal. None = no match.

        No retries: a single LLM miss falls through to the caller's one-shot
        re-snapshot retry, not a transport-level retry. Errors return None
        rather than raising — element-finding is best-effort.
        """
        from coinfiliate.llm.prompt import ELEMENT_FINDER_SYSTEM, build_element_finder_prompt
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                timeout=self._timeout,
                messages=[
                    {"role": "system", "content": ELEMENT_FINDER_SYSTEM},
                    {"role": "user", "content": build_element_finder_prompt(
                        candidates=candidates, goal=goal, url=url,
                    )},
                ],
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            idx = data.get("idx")
            if idx is None:
                return None
            idx = int(idx)
            if not (0 <= idx < len(candidates)):
                return None
            return idx
        except Exception:
            return None
