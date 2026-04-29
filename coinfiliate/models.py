from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class HarvestContext:
    """All signals collected from the browser; input to the decision pipeline."""
    shop_name: str
    network: str
    final_url: str
    final_etld1: str
    cookies: List[dict] = field(default_factory=list)
    redirect_chain: List[str] = field(default_factory=list)
    tracker_domains: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class HarvestDecision:
    """Output of the decision pipeline."""
    primary_cookie_name: Optional[str]
    tracking_cookie_names: List[str]
    checkout_domains: List[str]
    tracking_cookie_domains: List[str]
    decision_source: str  # "heuristic" | "llm" | "manual"
    confidence: float     # 0.0..1.0
    rationale: Optional[str] = None
