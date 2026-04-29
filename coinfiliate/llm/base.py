from __future__ import annotations

from typing import Protocol
from coinfiliate.models import HarvestContext, HarvestDecision


class CookieAnalyzer(Protocol):
    async def analyze(self, ctx: HarvestContext) -> HarvestDecision: ...
