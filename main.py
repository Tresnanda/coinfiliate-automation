import asyncio
import json
import os
import re
from urllib.parse import urlparse
from playwright.async_api import async_playwright, Page, BrowserContext

# Coinfiliate URLs
COINFILIATE_LOGIN = "https://www.coinfiliate.com/login"
COINFILIATE_SHOP = "https://www.coinfiliate.com/admin/partner-shop"

class CookieHarvester:
    def __init__(self, headless=True):
        self.headless = headless
        self.tracking_keywords = [
            'pjnclick', 'irclick', 'ir_', '_ck_', '_wg_', 'affiliateid', 
            'clickid', 'cid', 'awc', 'fpc', '_fbp', 'impact'
        ]

    async def run(self, email, password):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            
            page = await context.new_page()
            
            print("[1] Logging into Coinfiliate...")
            await self.login(page, email, password)
            
            print("[2] Fetching pending Partner Shops...")
            shops = await self.get_pending_shops(page)
            
            if not shops:
                print("No shops need syncing.")
                await browser.close()
                return

            for shop in shops:
                print(f"\n--- Processing Shop: {shop['name']} ---")
                
                # Extract cookie & final domain
                result = await self.extract_tracking_cookie(shop['affiliate_url'], context)
                
                if result['cookie']:
                    print(f"[+] Found Tracking Cookie: {result['cookie']['name']} = {result['cookie']['value']}")
                    print(f"[+] Final Domain: {result['final_domain']}")
                    
                    print("[3] Writing back to Coinfiliate...")
                    await self.update_shop(page, shop['edit_url'], result['cookie']['name'], result['final_domain'])
                else:
                    print("[-] Failed to definitively find a tracking cookie. Fallback required.")
                    # LLM fallback could go here
            
            await browser.close()

    async def login(self, page: Page, email, password):
        await page.goto(COINFILIATE_LOGIN)
        # Note: Selectors may need adjustment based on actual Coinfiliate DOM
        await page.fill('input[type="email"]', email)
        await page.fill('input[type="password"]', password)
        await page.click('button[type="submit"]')
        await page.wait_for_url("**/admin**")

    async def get_pending_shops(self, page: Page):
        """Scrapes the PartnerShop table for shops that need configuration."""
        await page.goto(COINFILIATE_SHOP)
        await page.wait_for_selector('table') # wait for table load
        
        # NOTE: This is pseudo-code for the extraction. 
        # You will need to adjust the selectors based on the actual dashboard HTML.
        shops = []
        # Example: 
        # rows = await page.locator('table tbody tr').all()
        # for row in rows:
        #     name = await row.locator('.shop-name').inner_text()
        #     affiliate_url = await row.locator('.affiliate-link').get_attribute('href')
        #     edit_url = await row.locator('.edit-btn').get_attribute('href')
        #     shops.append({'name': name, 'affiliate_url': affiliate_url, 'edit_url': edit_url})
        
        print("NOTE: Table extraction selectors need to be mapped to the live site.")
        return shops

    async def extract_tracking_cookie(self, affiliate_url: str, context: BrowserContext):
        page = await context.new_page()
        print(f"Navigating to {affiliate_url}...")
        
        try:
            # wait_until="networkidle" ensures all tracking scripts have fired
            await page.goto(affiliate_url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            print(f"Page load warning/timeout: {e}")
            
        # 1. Auto-Click Cookie Consent Banners
        accept_texts = ["Accept", "Allow All", "Accept All Cookies", "I Accept", "Agree", "Got it"]
        for text in accept_texts:
            try:
                # Basic heuristic for consent buttons
                btn = page.locator(f"button:has-text('{text}'), a:has-text('{text}')").first
                if await btn.is_visible(timeout=1000):
                    print(f"Clicking consent button: '{text}'")
                    await btn.click()
                    await page.wait_for_timeout(2000) # Wait for cookies to write
                    break
            except:
                pass

        # 2. Extract Cookies
        cookies = await context.cookies()
        
        # 3. Heuristic Matching
        primary_cookie = None
        for cookie in cookies:
            name = cookie['name'].lower()
            if any(kw in name for kw in self.tracking_keywords):
                primary_cookie = cookie
                break
                
        # 4. Extract Final Domain
        final_url = page.url
        final_domain = urlparse(final_url).netloc
        
        await page.close()
        
        return {
            "cookie": primary_cookie,
            "all_cookies": cookies, # useful to pass to an LLM if heuristic fails
            "final_domain": final_domain
        }

    async def update_shop(self, page: Page, edit_url: str, cookie_name: str, final_domain: str):
        await page.goto(edit_url)
        
        # Fill in the tracking fields based on the WPS tutorial screenshots
        # The fields mentioned are "TrackingPrimaryCookieAffiliate" and "DomainWebsite"
        try:
            await page.fill('input[name="TrackingPrimaryCookieAffiliate"]', cookie_name)
            await page.fill('input[name="DomainWebsite"]', final_domain)
            
            # Click Update/Save
            await page.click('button:has-text("Update"), button:has-text("Save")')
            print("Successfully updated shop parameters.")
        except Exception as e:
            print(f"Failed to update shop. Selectors may need mapping. Error: {e}")

if __name__ == "__main__":
    email = os.getenv("COINFILIATE_EMAIL", "test@example.com")
    password = os.getenv("COINFILIATE_PASS", "password123")
    
    harvester = CookieHarvester(headless=False) # Set False to watch it work during debugging
    asyncio.run(harvester.run(email, password))
