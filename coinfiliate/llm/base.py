from __future__ import annotations

from typing import List, Optional, Protocol
from coinfiliate.models import HarvestContext, HarvestDecision


class CookieAnalyzer(Protocol):
    async def analyze(self, ctx: HarvestContext) -> HarvestDecision: ...


class ElementFinder(Protocol):
    async def find_element(
        self, *,
        candidates: List[dict],
        goal: str,
        url: str,
    ) -> Optional[int]:
        """Return idx of the candidate that best satisfies goal, or None."""
        ...
