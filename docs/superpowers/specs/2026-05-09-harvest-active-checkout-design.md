# Harvest pipeline: active-checkout navigation + per-link retry

**Status:** Design approved 2026-05-09. Spec ready for plan-writing.

## Background

Two correctness gaps surfaced from QA against the live Coinfiliate UI (see `docs/files/Perbaikan automation partnershop.pdf`):

1. **Dead affiliate links pollute the harvest.** `harvest_shop` (`coinfiliate/harvest.py:71`) follows the first affiliate link captured by the sync phase and reads cookies from whatever URL it lands on — including 404 pages, "out of stock" screens, region blocks, etc. The cookies on those pages are not the affiliate-tracking cookies the partner network sets at the merchant's real entry point; we persist garbage.
2. **`checkout_domains` and `tracking_cookie_domains` are wrong by construction.** Both fields are populated from `final_etld1` of the *landing* redirect (`coinfiliate/harvest.py:54`, `coinfiliate/decision.py:54-55`). The fields are read by Coinfiliate's matcher at conversion time on the *checkout* page. For Shopify-hosted merchants (the dominant case in the current FlexOffers batch) the values happen to match because checkout is `<merchant>.com/checkouts/...`. For merchants that hand off to `pay.shopify.com`, `<shop>.myshopify.com`, `secure.<merchant>.com`, or a third-party processor, the landing eTLD+1 silently disagrees with where the conversion actually fires.

## Goals

- Harvest only succeeds on a link that reaches a real checkout page.
- `checkout_domains` reflects the eTLD+1 of the URL bar at the checkout page.
- `tracking_cookie_domains` reflects the captured primary cookie's authoritative scope (its `domain` attribute, eTLD+1-normalized), with the checkout eTLD+1 also included when the two differ.
- When the first affiliate link is dead, automatically try the next one. Mark the shop failed only when every link is exhausted.
- The change preserves the recently-shipped concurrency model (`asyncio.Semaphore(max_concurrency=6)`, `fresh_context` per shop) and the strict-keyword → LLM decision flow.

## Non-goals

- No new selector heuristics per CMS. Element discovery is LLM-driven across all storefronts.
- No human review queue UI. Failures still flow to `needs_review` / `failed` in the existing way.
- No change to the writeback phase. It already accepts list-of-strings for both fields.
- No retry of LLM-driven *element* discovery beyond the existing one-shot re-snapshot. Retry happens at the affiliate-link level, not the click-step level.

## Design overview

The harvest phase moves from **passive observation** ("open URL, listen for cookies, infer") to **active navigation** ("open URL, click product, click add-to-cart, click checkout, then read cookies"). Cookies captured at the checkout page are more authoritative — some affiliate networks only stamp their tracking cookie during cart or checkout, never on landing.

### Per-shop control flow

```
links = ordered_attempt_list(shop)
       # current is_harvest_source first, then by affiliate_link.id ascending
for link in links:
    async with fresh_context(browser) as ctx:
        page = await ctx.new_page()
        result = await attempt_link(page, ctx, link)
    if result is success:
        write harvest row, mark is_harvest_source = link.id, break
    else:
        log structured failure (NoProduct | NoCart | NoCheckout | Error404 | Error)
        continue
else:  # all links exhausted
    mark shop "failed" with last_error summarizing per-link failure modes
```

### `attempt_link` (replaces the body of today's `collect_signals`)

```
goto(landing_url, wait_until="domcontentloaded")
wait for networkidle (existing 15s timeout, best-effort)
click consent if visible (existing logic)

if landing is 404 or error → return Error404

# Step 1: find a product
selector = await llm_find_element(page, goal="link to a product detail page")
click(selector); wait for nav settle
if click fails after one retry → return NoProduct

# Step 2: add to cart
selector = await llm_find_element(page, goal="add the current product to cart")
click(selector); wait for nav/UI settle
if click fails after one retry → return NoCart

# Step 3: proceed to checkout
selector = await llm_find_element(page, goal="proceed to checkout")
click(selector); wait for navigation to checkout page
if click fails after one retry → return NoCheckout

# Capture
checkout_url = page.url
cookies      = await ctx.cookies()
tracker_domains, redirect_chain  ← from the ctx.on("response") listener
                                   attached at the start of attempt_link
return Success(checkout_url, cookies, ...)
```

The response listener (`context.on("response")`) stays attached for the entire attempt, so `tracker_domains` and `redirect_chain` reflect the full landing→PDP→cart→checkout journey.

### `llm_find_element`

A new helper in `coinfiliate/llm/`:

```
async def llm_find_element(page, *, goal: str) -> Optional[str]:
    snapshot = await page.accessibility.snapshot()  # or simplified DOM
    return await llm.find(goal=goal, snapshot=snapshot, url=page.url)
```

The LLM call uses gpt-4o-mini (cheap, structured output). Prompt asks for a CSS selector + confidence. On click failure, one re-snapshot retry — total two attempts per step (original + retry). If both fail, return None and the caller maps it to the appropriate typed failure (`NoProduct`, `NoCart`, `NoCheckout`).

Per-step timeout: 15s. No hard per-shop cap — the `Semaphore(6)` already limits blast radius if one shop is slow.

### 404 / error detection

Lightweight pre-check before Step 1, deterministic and fast:

- HTTP status of the landing navigation (Playwright exposes `response.status`).
- Page `<title>` matches `/(404|not found|error)/i`.
- `<h1>` matches the same.

If any signal fires, skip the LLM steps and return `Error404`.

### Decision integration

`decide()` in `coinfiliate/decision.py` keeps its current shape: strict-keyword match → LLM fallback. Two field changes:

- **`checkout_domains`**: filled from `checkout_etld1` (was `final_etld1`).
- **`tracking_cookie_domains`**: filled from the primary cookie's `domain` attribute (eTLD+1-normalized, leading dot stripped). When that value differs from `checkout_etld1`, both are included. When the primary cookie has no `domain` attribute (host-only cookie), fall back to `checkout_etld1`.

The LLM-fallback prompt (`coinfiliate/llm/prompt.py`) gets two new context fields for clarity (`checkout_url`, `checkout_etld1`); the LLM is *not* asked to invent these — they're computed deterministically from the captured navigation.

### Schema additions

Three new columns on `harvest`:

```sql
ALTER TABLE harvest ADD COLUMN checkout_url        TEXT;
ALTER TABLE harvest ADD COLUMN checkout_etld1      TEXT;
ALTER TABLE harvest ADD COLUMN attempted_link_id   TEXT;
```

`attempted_link_id` records which `affiliate_link.link_id` produced this row — auditability when retry succeeds on a non-first link.

`affiliate_link.is_harvest_source` is re-pointed by `mark_harvest_source(shop_id, link_id)` (existing helper) when retry succeeds.

**Migration idempotency note for the implementation plan:** `schema.sql` is currently re-applied on every `Store.init()` via `executescript`. SQLite's bare `ALTER TABLE ADD COLUMN` is not idempotent on older versions; the plan should either use `ADD COLUMN IF NOT EXISTS` (SQLite ≥ 3.35) or guard each ALTER with a `PRAGMA table_info(harvest)` check.

### Failure modes and what they map to

| Failure | Meaning | Shop status |
|---|---|---|
| `Error404` on every link | Every captured affiliate URL dead | `failed` (last_error: "all N links 404 or error") |
| `NoProduct` on every link | LLM could not find a product link on any landing | `failed` (last_error: "all N links: no product found") |
| `NoCart` / `NoCheckout` on every link | Storefront UI doesn't expose the path the LLM expects | `needs_review` (these are likely real shops, just unusual UIs worth a human look) |
| First-link success | Normal path | `harvested` (or `needs_review` if confidence below threshold) |
| Nth-link success | Retry recovered | `harvested`; `is_harvest_source` re-pointed; `attempted_link_id` records which link |

### Concurrency and isolation

Unchanged.

- `run_harvest` keeps `asyncio.Semaphore(settings.runner.max_concurrency)` and `asyncio.gather`.
- Each shop still uses `fresh_context(browser)` so cookies from one shop never leak into another's harvest. Critical: an affiliate network would get false attribution if cookies leaked.
- The new active-navigation flow runs *inside* one shop's context, so it doesn't multiply browser contexts.

### Cost and time envelope

Per shop, success on link 1, gpt-4o-mini:
- 3 element-discovery LLM calls (~$0.0005 each) + the existing decision LLM call = ~$0.002 per shop.
- Wall clock: 30-60s (was 15-25s). At concurrency 6 and 50 shops: ~6-10 min for the harvest phase.

Worst case (every link of every shop dead): bounded by the number of links per shop. In practice this caps at a few minutes per shop because attempts fail fast on `Error404` or `NoProduct`.

## Testing

### Unit

- `attempt_link` against a Playwright route-mocked fixture server: 404 page returns `Error404`; happy path returns `Success` with expected `checkout_url`.
- `decide()` with new fields: cookie has `domain=.example.com`, checkout at `example.com/checkouts/x` → `tracking_cookie_domains=['example.com']`, `checkout_domains=['example.com']`. Different domains → both included.
- `ordered_attempt_list` returns current `is_harvest_source` first, then siblings by id.

### Integration

- Existing fixture server in `tests/integration/` extended with: a 404 fixture, a happy-path PDP→cart→checkout fixture, a "no product" landing fixture.
- `harvest_shop` end-to-end: 3-link shop where links 1 and 2 are dead → asserts the final harvest row's `attempted_link_id` is link 3, `is_harvest_source` updated, shop status is `harvested`.

### Manual smoke

Before rolling to all 50 shops:
- Run `harvest --limit 5` against a hand-picked mix: known-good Shopify, known-404, non-Shopify (Magento or custom). Verify the harvest row's `checkout_etld1` matches what the URL bar shows.

## Open risks

1. **LLM picks the wrong button.** "Add to Wishlist" instead of "Add to Cart"; "Proceed to Quote" instead of "Checkout". Mitigation: explicit goal phrasing; reject selectors whose accessible name doesn't contain the goal's expected keywords. Acceptable failure mode is `NoCart` / `NoCheckout` → `needs_review`.
2. **Storefronts that gate cart/checkout behind login.** Will surface as `NoCart` or `NoCheckout`. Already covered by the `needs_review` mapping.
3. **Cart sessions left on merchant sites.** Real but low-impact (no payment, no PII). Each `fresh_context` is closed at the end of the attempt, so the cart is abandoned client-side; merchant-side abandoned-cart emails won't fire because no email is entered.
4. **Per-step 15s timeout too tight on slow merchant sites.** Bumpable per-merchant later via config; not worth a knob in v1.

## Resolved during brainstorming

| Question | Decision |
|---|---|
| Active vs passive vs LLM-infer for checkout discovery | Active navigation |
| LLM-driven vs heuristic vs hybrid for element discovery | LLM-driven |
| Try every link vs cap retries | Try every link |
| Cookie capture timing | After checkout flow lands |
| Tracking cookie domain — checkout eTLD+1 vs cookie's actual domain | Cookie's actual `domain` attribute, with checkout eTLD+1 added when they differ |
| Skip Step 1 when landing is already a PDP | No — always click. Robustness over a saved LLM call. |

## Out of scope

- Multi-checkout-step flows (login wall, shipping picker before payment). Captured at *first* page after the Checkout click; that's enough for cookie scoping in practice.
- Shops with no affiliate links at all. Existing `harvest.shop.skipped` path handles this unchanged.
- Translating the LLM-found selectors into more durable selectors. Each run re-discovers; we don't cache.
