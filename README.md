# Coinfiliate Automation

Automated cookie harvester for the [Coinfiliate](https://www.coinfiliate.com) Partner Shop dashboard. Logs into your Coinfiliate account, discovers shops that still need tracking configuration, follows each affiliate link in a real browser to capture the network's tracking cookie and final destination domain, and writes those values back to the dashboard.

Built with [Playwright](https://playwright.dev/python/) (async API) on headless Chromium.

## How it works

1. **Login** — authenticates against `https://www.coinfiliate.com/login` with email + password.
2. **Discover pending shops** — scrapes the Partner Shop admin table for shops that still need a primary cookie + domain configured.
3. **Harvest tracking cookie** — for each shop, opens the affiliate URL in a fresh tab, auto-clicks common cookie-consent banners ("Accept", "Allow All", etc.), waits for tracking scripts to fire (`networkidle`), and extracts all cookies from the browser context.
4. **Heuristic match** — identifies the primary affiliate cookie by matching well-known tracking keywords:
   `pjnclick`, `irclick`, `ir_`, `_ck_`, `_wg_`, `affiliateid`, `clickid`, `cid`, `awc`, `fpc`, `_fbp`, `impact`.
5. **Write back** — navigates to the shop's edit form and fills `TrackingPrimaryCookieAffiliate` (cookie name) and `DomainWebsite` (final URL domain), then submits.

When the heuristic fails, the script surfaces the full cookie list so a follow-up LLM fallback can be added later.

## Requirements

- Python 3.10+
- Chromium (installed automatically by Playwright)

## Setup

```bash
python -m venv venv
# macOS / Linux
source venv/bin/activate
# Windows (PowerShell)
venv\Scripts\Activate.ps1

pip install -r requirements.txt
playwright install chromium
```

## Configuration

Copy `.env.example` to `.env` and fill in your Coinfiliate credentials:

```bash
cp .env.example .env
```

```dotenv
COINFILIATE_EMAIL="your_coinfiliate_email@example.com"
COINFILIATE_PASS="your_coinfiliate_password"
```

Credentials are read from environment variables at runtime. `.env` is git-ignored.

## Usage

Export the credentials (or `source` your `.env`) and run:

```bash
export COINFILIATE_EMAIL="you@example.com"
export COINFILIATE_PASS="your_password"
python main.py
```

By default the browser runs **visible** (`headless=False`) to make debugging selectors easier. Flip the flag in `main.py` once selectors are stable:

```python
harvester = CookieHarvester(headless=True)
```

## Project structure

```
.
├── main.py             # CookieHarvester class + entrypoint
├── requirements.txt    # Python dependencies
├── .env.example        # Template for credentials
└── README.md
```

## Status & known limitations

- **Table extraction selectors are placeholders.** `get_pending_shops()` currently returns an empty list — the DOM selectors for the Partner Shop table rows (`.shop-name`, `.affiliate-link`, `.edit-btn`) need to be mapped to the live dashboard before the script can discover shops.
- **Edit-form selectors** (`input[name="TrackingPrimaryCookieAffiliate"]`, `input[name="DomainWebsite"]`) are best-effort guesses based on WPS tutorial screenshots and may need adjusting.
- **No LLM fallback yet.** When the cookie heuristic misses, the script logs the failure; full cookie context is already collected and ready to hand off to an LLM if needed.
- **Consent handling** is intentionally simple — only English "Accept"-style buttons are clicked.

## Development

Run directly with a visible browser to inspect and tune selectors:

```python
harvester = CookieHarvester(headless=False)
```

Use Playwright's inspector to record selectors against the live dashboard:

```bash
playwright codegen https://www.coinfiliate.com/login
```

## License

Private / internal automation. Do not distribute credentials.
