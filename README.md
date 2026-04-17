# Coinfiliate Automation

An automated script using Playwright to extract affiliate tracking cookies and write them back to the Coinfiliate Partner Shop dashboard.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Usage

Set your Coinfiliate credentials as environment variables:

```bash
COINFILIATE_EMAIL="your_email" COINFILIATE_PASS="your_password" python main.py
```
