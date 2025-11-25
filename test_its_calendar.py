# -*- coding: utf-8 -*-
import asyncio
import os
from datetime import datetime
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions

# Configuration
CALENDAR_URL_CACHE = "calendar_url_cache.txt"
MAIN_URL = "https://as.its-kenpo.or.jp"
SCAN_INTERVAL_SECONDS = 60  # Check every 1 minute

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
    "-accept-lang=ja-JP",
    "--disable-usage-stats",
    "--disable-crash-reporter",
]


def create_browser_options(headless=False):
    """Create ChromiumOptions with specified headless mode."""
    options = ChromiumOptions()
    # Let pydoll auto-detect Chrome path instead of hardcoding
    # Remove this line to use auto-detection:
    # options.binary_location = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    
    if headless:
        options.headless = True
        print("Running in HEADLESS mode")
    else:
        print("Running in NORMAL mode (browser visible)")
    
    for argument in CHROME_ARGUMENTS:
        options.add_argument(argument)
    
    # Increase startup timeout to handle slow connections
    options.start_timeout = 30
    
    return options


def load_cached_url():
    """Load the cached calendar URL if it exists."""
    if not os.path.exists(CALENDAR_URL_CACHE):
        return None
    
    try:
        with open(CALENDAR_URL_CACHE, 'r') as f:
            url = f.read().strip()
            return url if url else None
    except Exception as e:
        print(f"Error reading cache: {e}")
        return None


def save_calendar_url(url):
    """Save the calendar URL to cache file."""
    try:
        with open(CALENDAR_URL_CACHE, 'w') as f:
            f.write(url)
        print(f"Saved calendar URL to cache")
    except Exception as e:
        print(f"Error saving cache: {e}")


async def is_valid_calendar_page(tab):
    """Check if current page is a valid calendar page."""
    try:
        month_element = await tab.find(class_name="month", timeout=3, raise_exc=False)
        saturday_cells = await tab.find(tag_name="td", class_name="td-sat", find_all=True, timeout=3, raise_exc=False)
        return month_element is not None and saturday_cells is not None and len(saturday_cells) > 0
    except:
        return False


async def validate_cached_url(cached_url):
    """Validate cached URL in headless mode."""
    print(f"\nValidating cached URL in headless mode...")
    print(f"URL: {cached_url[:80]}...")
    
    options = create_browser_options(headless=True)
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(cached_url)
        await asyncio.sleep(3)
        
        if await is_valid_calendar_page(tab):
            print("Cached URL is valid")
            return True
        else:
            print("Cached URL is invalid or expired")
            return False


async def navigate_to_calendar_link(tab):
    """Navigate from main page to calendar CAPTCHA page."""
    print(f"\nNavigating to {MAIN_URL}")
    await tab.go_to(MAIN_URL)
    await asyncio.sleep(3)
    
    print("Looking for calendar link...")
    
    # Try to find the calendar link using different methods
    calendar_link = None
    
    # Try finding by text first
    try:
        calendar_link = await tab.find(text="カレンダーから探す", timeout=5, raise_exc=False)
    except:
        pass
    
    # Try finding by href attribute
    if not calendar_link:
        try:
            # Find all links and filter by href containing calendar_apply
            links = await tab.find(tag_name="a", find_all=True, timeout=5, raise_exc=False)
            if links:
                for link in links:
                    try:
                        href = await link.get_property("href")
                        if href and "calendar_apply" in href:
                            calendar_link = link
                            break
                    except:
                        pass
        except:
            pass
    
    if not calendar_link:
        raise Exception("Could not find calendar link")
    
    print("Found calendar link, clicking...")
    await calendar_link.click()
    await asyncio.sleep(3)
    
    # Get URL properly from execute_script response
    url_response = await tab.execute_script("return window.location.href")
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    if "calendar_apply" not in current_url:
        raise Exception(f"Not on expected CAPTCHA page. Current URL: {current_url}")
    
    print("On CAPTCHA verification page")


async def bypass_captcha_and_proceed(tab):
    """Bypass CAPTCHA by clicking checkbox and proceeding with human-like interactions."""
    print("Bypassing CAPTCHA with pydoll's stealth capabilities...")
    
    # Wait for page to fully load
    await asyncio.sleep(3)
    
    # Wait for reCAPTCHA iframe to load
    print("Waiting for CAPTCHA to load...")
    await asyncio.sleep(5)
    
    # Simulate natural user behavior before interacting with captcha
    try:
        print("Simulating natural user behavior...")
        from pydoll.constants import ScrollPosition
        
        # Scroll down smoothly to appear human
        await tab.scroll.by(ScrollPosition.DOWN, 100, smooth=True)
        await asyncio.sleep(1.5)
        
        # Scroll back up slightly
        await tab.scroll.by(ScrollPosition.UP, 50, smooth=True)
        await asyncio.sleep(1)
        
        print("Human-like behavior simulation complete")
        
    except Exception as e:
        print(f"Note: Behavioral simulation: {e}")
    
    # Now interact with the reCAPTCHA checkbox
    try:
        print("Looking for reCAPTCHA checkbox...")
        
        # Find the reCAPTCHA iframe
        recaptcha_iframe = await tab.query(
            'iframe[src*="recaptcha/api2/anchor"]',
            timeout=5,
            raise_exc=False
        )
        
        if recaptcha_iframe:
            print("Found reCAPTCHA checkbox iframe")
            
            # Switch to the iframe context
            # In pydoll, we need to find elements within the iframe
            # Wait a moment before clicking
            await asyncio.sleep(2)
            
            # Try to click the checkbox with human-like behavior
            # The checkbox is typically at specific coordinates in the iframe
            print("Attempting to click reCAPTCHA checkbox...")
            
            # Try to click the reCAPTCHA checkbox
            # Method 1: Try to click via coordinates on the iframe
            try:
                # Get iframe position and click in the center where checkbox usually is
                await recaptcha_iframe.scroll_into_view()
                await asyncio.sleep(1)
                
                # Click the iframe (pydoll will handle the click)
                await recaptcha_iframe.click()
                print("Clicked reCAPTCHA iframe area")
                await asyncio.sleep(3)
            except Exception as click_err:
                print(f"Could not click iframe directly: {click_err}")
                
                # Method 2: Try JavaScript click
                try:
                    checkbox_script = """
                    var iframe = document.querySelector('iframe[src*="recaptcha/api2/anchor"]');
                    if (iframe) {
                        var rect = iframe.getBoundingClientRect();
                        var event = new MouseEvent('click', {
                            view: window,
                            bubbles: true,
                            cancelable: true,
                            clientX: rect.left + rect.width/2,
                            clientY: rect.top + rect.height/2
                        });
                        iframe.dispatchEvent(event);
                        return true;
                    }
                    return false;
                    """
                    
                    await tab.execute_script(checkbox_script)
                    print("Triggered click event on reCAPTCHA iframe")
                    await asyncio.sleep(3)
                except Exception as js_err:
                    print(f"JavaScript click failed: {js_err}")
            
            # Wait for the captcha to be solved (checkbox animation)
            print("Waiting for captcha to be solved...")
            await asyncio.sleep(5)
        else:
            print("No reCAPTCHA iframe found, proceeding anyway...")
        
    except Exception as e:
        print(f"Note: reCAPTCHA interaction: {e}")
    
    # Now click the "次へ" (Next) button
    print("Looking for 次へ (Next) button...")
    await asyncio.sleep(2)
    
    try:
        # Try to find button by value "次へ"
        buttons = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        next_button = None
        
        if buttons:
            for button in buttons:
                try:
                    value = await button.get_property("value")
                    if value and "次へ" in value:
                        next_button = button
                        break
                except:
                    pass
        
        if next_button:
            # Scroll into view smoothly
            await next_button.scroll_into_view()
            await asyncio.sleep(1)
            
            # Click with human-like behavior
            await next_button.click()
            print("Clicked 次へ button with human-like interaction")
        else:
            # Fallback: try any input button
            fallback_button = await tab.find(tag_name="input", timeout=3, raise_exc=False)
            if fallback_button:
                try:
                    button_type = await fallback_button.get_property("type")
                    if button_type == "button":
                        await fallback_button.click()
                        print("Clicked button (fallback)")
                    else:
                        await tab.execute_script("document.querySelector('form').submit();")
                        print("Submitted form via JavaScript")
                except:
                    await tab.execute_script("document.querySelector('form').submit();")
                    print("Submitted form via JavaScript")
            else:
                # Final fallback: submit form via JavaScript
                await tab.execute_script("document.querySelector('form').submit();")
                print("Submitted form via JavaScript")
            
    except Exception as e:
        print(f"Error clicking button: {e}")
        try:
            await tab.execute_script("document.querySelector('form').submit();")
            print("Submitted form via JavaScript (fallback)")
        except:
            print("Waiting for auto-navigation...")
            await asyncio.sleep(10)
    
    # Wait for navigation
    await asyncio.sleep(4)
    
    # Get URL properly
    url_response = await tab.execute_script("return window.location.href")
    calendar_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    print(f"Navigated to: {calendar_url}")
    
    if "calendar_select" not in calendar_url:
        raise Exception(f"Not on expected calendar page. Current URL: {calendar_url}")
    
    print("Successfully reached calendar page")
    return calendar_url


async def acquire_calendar_url_with_captcha():
    """Get calendar URL by bypassing CAPTCHA in non-headless mode."""
    print("\n" + "="*60)
    print("ACQUIRING NEW CALENDAR URL (NON-HEADLESS MODE)")
    print("="*60)
    
    options = create_browser_options(headless=False)
    async with Chrome(options=options) as browser:
        try:
            tab = await browser.start()
            await navigate_to_calendar_link(tab)
            calendar_url = await bypass_captcha_and_proceed(tab)
            return calendar_url
        except Exception as e:
            print(f"Error acquiring calendar URL: {e}")
            import traceback
            traceback.print_exc()
            return None


async def navigate_to_next_month(tab):
    """Navigate to the next month in the calendar."""
    # Try to find the next month button using different selectors
    try:
        # Try by ID first
        next_button = await tab.find(id="nextMonth", timeout=3, raise_exc=False)
        if next_button:
            await next_button.click()
            await asyncio.sleep(3)
            return True
    except:
        pass
    
    # Try by value attribute
    try:
        inputs = await tab.find(tag_name="input", find_all=True, timeout=3, raise_exc=False)
        if inputs:
            for input_elem in inputs:
                try:
                    value = await input_elem.get_property("value")
                    if value and "翌月" in value:
                        await input_elem.click()
                        await asyncio.sleep(3)
                        return True
                except:
                    pass
    except:
        pass
    
    return False


async def scan_month_saturdays(tab):
    """Scan current month for available Saturdays."""
    try:
        month_element = await tab.find(class_name="month", timeout=3, raise_exc=False)
        if month_element:
            # Use execute_script to get text content reliably
            text_result = await month_element.execute_script("return this.textContent")
            # Handle the response structure properly
            if isinstance(text_result, dict) and 'result' in text_result:
                if 'result' in text_result['result']:
                    current_month = text_result['result']['result'].get('value', 'Unknown')
                else:
                    current_month = text_result['result'].get('value', 'Unknown')
            else:
                current_month = str(text_result)
        else:
            current_month = "Unknown"
    except Exception as e:
        print(f"Error getting month: {e}")
        current_month = "Unknown"
    
    print(f"Scanning: {current_month}")
    
    available_saturdays = []
    
    try:
        saturday_cells = await tab.find(tag_name="td", class_name="td-sat", find_all=True, raise_exc=False)
        if not saturday_cells:
            print("Found 0 Saturday(s)")
            return available_saturdays
        
        print(f"Found {len(saturday_cells)} Saturday(s)")
        
        for cell in saturday_cells:
            try:
                # Get the date text from <p> tag inside the cell
                date_elem = await cell.find(tag_name="p", raise_exc=False)
                if date_elem:
                    text_result = await date_elem.execute_script("return this.textContent")
                    if isinstance(text_result, dict) and 'result' in text_result:
                        if 'result' in text_result['result']:
                            date_text = text_result['result']['result'].get('value', '')
                        else:
                            date_text = text_result['result'].get('value', '')
                    else:
                        date_text = str(text_result)
                else:
                    date_text = ""
                
                # Get the icon element
                icon_element = await cell.find(class_name="icon", raise_exc=False)
                if icon_element:
                    text_result = await icon_element.execute_script("return this.textContent")
                    if isinstance(text_result, dict) and 'result' in text_result:
                        if 'result' in text_result['result']:
                            icon = text_result['result']['result'].get('value', '')
                        else:
                            icon = text_result['result'].get('value', '')
                    else:
                        icon = str(text_result)
                else:
                    icon = ""
                
                # Get the full date from data attribute
                attr_result = await cell.execute_script("return this.getAttribute('data-join-time')")
                if isinstance(attr_result, dict) and 'result' in attr_result:
                    if 'result' in attr_result['result']:
                        full_date = attr_result['result']['result'].get('value', '')
                    else:
                        full_date = attr_result['result'].get('value', '')
                else:
                    full_date = str(attr_result)
                    
                if not full_date or full_date == "None":
                    full_date = ""
                
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
        print(f"Error finding Saturday cells: {e}")
    
    return available_saturdays


async def scan_calendar(tab, num_months=3):
    """Scan calendar for available Saturdays across multiple months."""
    print("\n" + "="*60)
    print(f"SCANNING SATURDAYS FOR {num_months} MONTHS")
    print("="*60 + "\n")
    
    all_available = []
    
    for month_num in range(num_months):
        print(f"Month {month_num + 1}/{num_months}")
        print("-"*60)
        
        month_saturdays = await scan_month_saturdays(tab)
        all_available.extend(month_saturdays)
        
        # Navigate to next month if not the last iteration
        if month_num < num_months - 1:
            print("Navigating to next month...\n")
            if not await navigate_to_next_month(tab):
                print("Could not navigate to next month, stopping scan")
                break
    
    return all_available


def print_summary(available_saturdays):
    """Print summary of available Saturdays."""
    print("\n" + "="*60)
    print("FINAL SUMMARY: Available Saturdays")
    print("="*60)
    
    if available_saturdays:
        print(f"\nFound {len(available_saturdays)} available Saturday(s):\n")
        for sat in available_saturdays:
            print(f"  {sat['month']} - {sat['date']}日 ({sat['full_date']})")
    else:
        print("\nNo available Saturdays found")
    
    print("\n" + "="*60 + "\n")


async def scan_calendar_headless(calendar_url):
    """Scan calendar in headless mode using the provided URL."""
    print("\n" + "="*60)
    print("SCANNING CALENDAR (HEADLESS MODE)")
    print("="*60)
    
    options = create_browser_options(headless=True)
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(calendar_url)
        await asyncio.sleep(3)
        
        if not await is_valid_calendar_page(tab):
            raise Exception("Failed to load valid calendar page")
        
        available_saturdays = await scan_calendar(tab, num_months=3)
        print_summary(available_saturdays)
        
        print("Scan complete")
        await asyncio.sleep(2)


async def scan_once():
    """Perform a single scan iteration."""
    # Step 1: Try using cached URL in headless mode
    cached_url = load_cached_url()
    
    if cached_url:
        if await validate_cached_url(cached_url):
            # Cached URL is valid - scan in headless mode
            await scan_calendar_headless(cached_url)
            return True
    
    # Step 2: Cache invalid or doesn't exist - acquire new URL
    print("\nNeed to acquire new calendar URL")
    new_url = await acquire_calendar_url_with_captcha()
    
    if not new_url:
        print("Failed to acquire calendar URL")
        return False
    
    # Step 3: Save new URL and scan in headless mode
    save_calendar_url(new_url)
    print("\n" + "="*60)
    print("URL ACQUIRED - RESTARTING IN HEADLESS MODE")
    print("="*60)
    await asyncio.sleep(2)
    await scan_calendar_headless(new_url)
    return True


async def main():
    """Main execution flow with continuous loop."""
    print("="*60)
    print("ITS CALENDAR SCANNER - CONTINUOUS MODE")
    print(f"Checking every {SCAN_INTERVAL_SECONDS} seconds")
    print("Press Ctrl+C to stop")
    print("="*60 + "\n")
    
    iteration = 0
    
    try:
        while True:
            iteration += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            print("\n" + "="*60)
            print(f"ITERATION #{iteration} - {timestamp}")
            print("="*60)
            
            try:
                await scan_once()
            except Exception as e:
                print(f"\nError during scan: {e}")
                import traceback
                traceback.print_exc()
            
            print(f"\n[{timestamp}] Waiting {SCAN_INTERVAL_SECONDS} seconds until next scan...")
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        print("\n\n" + "="*60)
        print("SCANNER STOPPED BY USER")
        print(f"Total iterations completed: {iteration}")
        print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
    print("Done!")