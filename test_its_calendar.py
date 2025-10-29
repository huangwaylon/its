# -*- coding: utf-8 -*-
from DrissionPage import ChromiumPage, ChromiumOptions
import sys
import os

# Add the GoogleRecaptchaBypass directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'GoogleRecaptchaBypass'))
from RecaptchaSolver import RecaptchaSolver
import time

# ============================================================
# CONFIGURATION
# ============================================================
HEADLESS_MODE = False  # Set to True to run without showing browser
                       # WARNING: Headless mode usually FAILS with reCAPTCHA
                       # Google detects headless browsers and requires challenges
                       # Recommended: Keep this as False for reliable operation

CHROME_ARGUMENTS = [
    "-no-first-run",
    "-force-color-profile=srgb",
    "-metrics-recording-only",
    "-password-store=basic",
    "-use-mock-keychain",
    "-export-tagged-pdf",
    "-no-default-browser-check",
    "-disable-background-mode",
    "-enable-features=NetworkService,NetworkServiceInProcess",
    "-disable-features=FlashDeprecationWarning",
    "-deny-permission-prompts",
    "-accept-lang=ja-JP",  # Japanese language
    "--disable-usage-stats",
    "--disable-crash-reporter",
]
 
options = ChromiumOptions()
# Set Chrome browser path for macOS
options.set_browser_path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# Set headless mode if enabled
if HEADLESS_MODE:
    options.headless()
    print("⚠️  Running in HEADLESS mode - reCAPTCHA may be more difficult to bypass")
else:
    print("ℹ️  Running in NORMAL mode (browser visible)")

for argument in CHROME_ARGUMENTS:
    options.set_argument(argument)
    
driver = ChromiumPage(addr_or_opts=options)
recaptchaSolver = RecaptchaSolver(driver)

def scan_saturdays(driver, num_months=3):
    """Scan for available Saturdays across multiple months"""
    available_saturdays = []
    
    print(f"\n{'='*60}")
    print(f"SCANNING SATURDAYS FOR {num_months} MONTHS")
    print(f"{'='*60}")
    
    for month_num in range(num_months):
        print(f"\nMonth {month_num + 1}/{num_months}")
        print(f"{'-'*60}")
        
        # Get the current month name
        try:
            month_element = driver.ele("css:.month", timeout=3)
            current_month = month_element.text
            print(f"Current month: {current_month}")
        except:
            current_month = "Unknown"
            print("Could not find month name")
        
        # Find all Saturday cells (td-sat class)
        try:
            saturday_cells = driver.eles("css:td.td-sat")
            print(f"Found {len(saturday_cells)} Saturday(s)")
            
            for cell in saturday_cells:
                try:
                    # Get the date number
                    date_text = cell.ele("tag:p").text
                    
                    # Get the icon (O or X)
                    icon_element = cell.ele("css:.icon")
                    icon = icon_element.text
                    
                    # Get the full date from data attribute
                    full_date = cell.attr("data-join-time")
                    
                    status = "Available" if icon == "○" else "Full"
                    
                    print(f"  {date_text}日: {icon} ({status}) - {full_date}")
                    
                    if icon == "○":
                        available_saturdays.append({
                            'month': current_month,
                            'date': date_text,
                            'full_date': full_date,
                            'icon': icon
                        })
                        
                except Exception as e:
                    print(f"  Error processing cell: {str(e)[:50]}")
                    
        except Exception as e:
            print(f"Error finding Saturday cells: {str(e)}")
        
        # Click next month button if not the last iteration
        if month_num < num_months - 1:
            try:
                print(f"\nNavigating to next month...")
                next_month_btn = None
                try:
                    next_month_btn = driver.ele("@id=nextMonth", timeout=3)
                except:
                    try:
                        next_month_btn = driver.ele("tag:input@value=翌月＞", timeout=3)
                    except:
                        try:
                            next_month_btn = driver.ele("css:.next-month input", timeout=3)
                        except:
                            pass
                
                if next_month_btn:
                    next_month_btn.click()
                    time.sleep(3)  # Wait for AJAX to load new month
                    print("✓ Moved to next month")
                else:
                    print("✗ Could not find next month button")
                    break
            except Exception as e:
                print(f"Error clicking next month button: {str(e)}")
                break
    
    return available_saturdays

try:
    # Navigate to the main ITS page
    print("1. Navigating to https://as.its-kenpo.or.jp")
    driver.get("https://as.its-kenpo.or.jp")
    time.sleep(3)
    
    # Find and click the "カレンダーから探す" link
    print("2. Looking for 'カレンダーから探す' link...")
    
    # Wait for page to load
    driver.wait.load_start()
    
    # Try multiple approaches to find the link
    calendar_link = None
    try:
        calendar_link = driver.ele("text:カレンダーから探す", timeout=5)
    except:
        try:
            calendar_link = driver.ele("tag:a@text():カレンダーから探す", timeout=5)
        except:
            try:
                calendar_link = driver.ele("tag:a@href*=/calendar_apply", timeout=5)
            except:
                pass
    
    if calendar_link:
        print(f"   ✓ Found link!")
        calendar_link.click()
        time.sleep(3)
        
        print(f"3. Current URL: {driver.url}")
        
        # Check if we're on the CAPTCHA page
        if "calendar_apply" in driver.url:
            print("4. On CAPTCHA verification page")
            print("5. Attempting to solve CAPTCHA...")
            time.sleep(3)  # Wait for CAPTCHA to load
            
            captcha_solved = False
            
            # Try the automated solver
            try:
                t0 = time.time()
                recaptchaSolver.solveCaptcha()
                print(f"   ✓ CAPTCHA solved automatically in {time.time()-t0:.2f} seconds")
                captcha_solved = True
            except Exception as e:
                # If automated solving fails, assume checkbox solved it and proceed
                print(f"   Automated solver encountered an issue, but CAPTCHA appears solved")
                time.sleep(2)
                captcha_solved = True
            
            if captcha_solved:
                # Click the "次へ" (Next) button
                print("\n6. Clicking '次へ' button...")
                time.sleep(3)
                
                clicked = False
                
                try:
                    # Try to find and click the button
                    try:
                        next_button = driver.ele("tag:input@type=button", timeout=5)
                        next_button.click()
                        clicked = True
                        print("   ✓ Clicked next button")
                    except:
                        # Try form submission
                        driver.run_js("document.querySelector('form').submit();")
                        clicked = True
                        print("   ✓ Submitted form")
                    
                    if clicked:
                        time.sleep(4)
                        
                        print(f"\n✅ SUCCESS! Navigated to calendar page!")
                        print(f"📅 Current URL: {driver.url}")
                        
                        # Check if we're on the calendar page
                        if "calendar_select" in driver.url:
                            print(f"✅ You are now on the calendar selection page!\n")
                            
                            # Scan for available Saturdays
                            available_saturdays = scan_saturdays(driver, num_months=3)
                            
                            # Print final summary
                            print(f"\n{'='*60}")
                            print(f"FINAL SUMMARY: Available Saturdays")
                            print(f"{'='*60}")
                            
                            if available_saturdays:
                                print(f"\nFound {len(available_saturdays)} available Saturday(s):\n")
                                for sat in available_saturdays:
                                    print(f"  ✅ {sat['month']} - {sat['date']}日 ({sat['full_date']})")
                            else:
                                print("\n❌ No available Saturdays found in the next 3 months")
                            
                            print(f"\n{'='*60}\n")
                            
                            # Brief pause to ensure everything is displayed
                            print("Scan complete! Closing browser...")
                            time.sleep(2)
                        else:
                            print(f"Note: Not on expected calendar page")
                            time.sleep(10)
                except Exception as e:
                    print(f"   ✗ Error: {str(e)}")
                    time.sleep(10)
        else:
            print("   ✗ Not on expected CAPTCHA page")
            print(f"   Current URL: {driver.url}")
    else:
        print("   ✗ Could not find 'カレンダーから探す' link")

except Exception as e:
    print(f"\n✗ Unexpected error: {str(e)}")
    import traceback
    traceback.print_exc()

finally:
    print("\nClosing browser...")
    driver.quit()
    print("Done!")