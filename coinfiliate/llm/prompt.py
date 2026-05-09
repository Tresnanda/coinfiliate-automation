from __future__ import annotations

import json
from coinfiliate.models import HarvestContext

SYSTEM = (
    "You are identifying the primary affiliate-tracking cookie on a merchant site. "
    "Prefer cookies that carry a per-click unique value over session IDs. "
    "Prefer network-native cookies (e.g., pjnclick, IR_*, awc, fobs_*) over first-party trackers "
    "(e.g., __kla_id, _ga) when both are present. "
    "Output strict JSON only. No prose."
)

SCHEMA = {
    "primary_cookie_name": "string",
    "tracking_cookie_names": "string[]",
    "checkout_domains": "string[]",
    "tracking_cookie_domains": "string[]",
    "confidence": "number 0..1",
    "rationale": "one-line string",
}


def build_user_prompt(ctx: HarvestContext) -> str:
    return (
        f"Shop: {ctx.shop_name} (network={ctx.network})\n"
        f"Final landing URL: {ctx.final_url}\n"
        f"Landing eTLD+1: {ctx.final_etld1}\n"
        f"Checkout URL: {ctx.checkout_url or '(not captured)'}\n"
        f"Checkout eTLD+1: {ctx.checkout_etld1 or '(not captured)'}\n\n"
        f"Cookies set on the landing page and through checkout:\n{json.dumps(ctx.cookies, indent=2)}\n\n"
        f"Third-party tracker domains seen in the redirect chain:\n{json.dumps(ctx.tracker_domains, indent=2)}\n\n"
        f"Redirect chain:\n{json.dumps(ctx.redirect_chain, indent=2)}\n\n"
        f"Respond with strict JSON matching this schema:\n{json.dumps(SCHEMA, indent=2)}"
    )


ELEMENT_FINDER_SYSTEM = (
    "You help drive a browser through an e-commerce checkout flow. "
    "You receive a list of clickable elements (each with idx, tag, visible text, "
    "and href). Pick the SINGLE element that best satisfies the goal. "
    "If no element clearly satisfies the goal, return idx=null. "
    "Output strict JSON only: {\"idx\": <int or null>, \"confidence\": 0..1}. "
    "No prose, no markdown fences."
)


def build_element_finder_prompt(*, candidates: list, goal: str, url: str) -> str:
    return (
        f"Goal: {goal}\n"
        f"Current URL: {url}\n\n"
        f"Clickable elements (pick one idx):\n"
        f"{json.dumps(candidates, indent=2)}\n\n"
        f"Respond with strict JSON: {{\"idx\": <int or null>, \"confidence\": 0..1}}"
    )
