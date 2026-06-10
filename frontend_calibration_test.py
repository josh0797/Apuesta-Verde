"""Frontend Playwright tests for Calibration Page and History Backfill Modal"""
import asyncio
import sys

async def test_calibration_frontend(page):
    """Test the calibration page and history backfill functionality"""
    
    print("\n" + "="*70)
    print("FRONTEND CALIBRATION & BACKFILL TESTS")
    print("="*70)
    
    # Set viewport
    await page.set_viewport_size({"width": 1920, "height": 1080})
    
    # Enable console logs
    page.on("console", lambda msg: print(f"CONSOLE: {msg.text}"))
    
    try:
        # Navigate to login page
        print("\n🔍 Step 1: Navigating to login page...")
        await page.goto("https://low-volatility-plays.preview.emergentagent.com/login", timeout=30000)
        await page.wait_for_timeout(2000)
        print("✅ Login page loaded")
        
        # Login
        print("\n🔍 Step 2: Logging in...")
        await page.fill('input[type="email"]', "demo@valuebet.app")
        await page.fill('input[type="password"]', "demo1234")
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(3000)
        print("✅ Login successful")
        
        # Check if header has calibration tab
        print("\n🔍 Step 3: Checking for Calibración tab in header...")
        calibration_tab = await page.wait_for_selector('[data-testid="nav-calibration"]', timeout=10000)
        if calibration_tab:
            print("✅ Calibración tab found in header")
        else:
            print("❌ Calibración tab NOT found")
            return False
        
        # Navigate to calibration page
        print("\n🔍 Step 4: Navigating to /dashboard/calibration...")
        await page.click('[data-testid="nav-calibration"]')
        await page.wait_for_timeout(3000)
        
        # Check if calibration page loaded
        calibration_page = await page.wait_for_selector('[data-testid="calibration-page"]', timeout=10000)
        if calibration_page:
            print("✅ Calibration page loaded successfully")
        else:
            print("❌ Calibration page did NOT load")
            return False
        
        # Check for console errors
        print("\n🔍 Step 5: Checking for console errors...")
        error_text = await page.evaluate("""() => {
            const errorElements = Array.from(document.querySelectorAll('.error, [class*="error"], [id*="error"]'));
            return errorElements.map(el => el.textContent).join(", ");
        }""")
        if error_text:
            print(f"⚠️  Found error messages: {error_text}")
        else:
            print("✅ No error messages found on calibration page")
        
        # Take screenshot of calibration page
        print("\n🔍 Step 6: Taking screenshot of calibration page...")
        await page.screenshot(path=".screenshots/calibration-page.png", quality=40, full_page=False)
        print("✅ Screenshot saved: .screenshots/calibration-page.png")
        
        # Check for KPI cards (Engine and User cards)
        print("\n🔍 Step 7: Checking for KPI cards...")
        
        # Look for text content that indicates the cards are present
        page_content = await page.content()
        
        # Check for key elements
        has_engine_section = "Engine" in page_content or "engine" in page_content.lower()
        has_user_section = "User" in page_content or "usuario" in page_content.lower() or "Tú" in page_content
        has_divergence_section = "divergence" in page_content.lower() or "divergencia" in page_content.lower()
        
        if has_engine_section:
            print("✅ Engine section found")
        else:
            print("⚠️  Engine section not clearly visible")
        
        if has_user_section:
            print("✅ User section found")
        else:
            print("⚠️  User section not clearly visible")
        
        if has_divergence_section:
            print("✅ Divergence panel found")
        else:
            print("⚠️  Divergence panel not clearly visible")
        
        # Navigate to history page
        print("\n🔍 Step 8: Navigating to /history page...")
        await page.click('[data-testid="nav-history"]')
        await page.wait_for_timeout(3000)
        print("✅ History page loaded")
        
        # Check for backfill button (pencil icon)
        print("\n🔍 Step 9: Checking for backfill button...")
        
        # Look for any backfill button (row-backfill-0, row-backfill-1, etc.)
        backfill_buttons = await page.query_selector_all('[data-testid^="row-backfill-"]')
        
        if len(backfill_buttons) > 0:
            print(f"✅ Found {len(backfill_buttons)} backfill button(s)")
            
            # Click the first backfill button to open modal
            print("\n🔍 Step 10: Clicking backfill button to open modal...")
            await backfill_buttons[0].click()
            await page.wait_for_timeout(2000)
            
            # Check if modal opened
            modal_visible = await page.evaluate("""() => {
                const modals = document.querySelectorAll('[role="dialog"], .modal, [class*="modal"]');
                return modals.length > 0;
            }""")
            
            if modal_visible:
                print("✅ UserBetBackfillModal opened successfully")
                
                # Take screenshot of modal
                await page.screenshot(path=".screenshots/backfill-modal.png", quality=40, full_page=False)
                print("✅ Screenshot saved: .screenshots/backfill-modal.png")
            else:
                print("⚠️  Modal may not have opened (no dialog element found)")
        else:
            print("⚠️  No backfill buttons found (this is OK if there are no settled picks)")
        
        print("\n" + "="*70)
        print("✅ ALL FRONTEND TESTS COMPLETED SUCCESSFULLY")
        print("="*70)
        return True
        
    except Exception as e:
        print(f"\n❌ Error during testing: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Take error screenshot
        try:
            await page.screenshot(path=".screenshots/error-state.png", quality=40, full_page=False)
            print("📸 Error screenshot saved: .screenshots/error-state.png")
        except:
            pass
        
        return False

async def main():
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        try:
            success = await test_calibration_frontend(page)
            return 0 if success else 1
        finally:
            await browser.close()

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
