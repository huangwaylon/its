# -*- coding: utf-8 -*-
from DrissionPage import ChromiumPage, ChromiumOptions
from RecaptchaSolver import RecaptchaSolver
import time
import os
from datetime import datetime

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


def create_driver(headless=False):
    """Create a ChromiumPage driver with specified headless mode."""
    options = ChromiumOptions()
    options.set_browser_path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    
    if headless:
        options.headless()
        print("Running in HEADLESS mode")
    else:
        print("Running in NORMAL mode (browser visible)")
    
    for argument in CHROME_ARGUMENTS:
        options.set_argument(argument)
    
    return ChromiumPage(addr_or_opts=options)


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


def is_valid_calendar_page(driver):
    """Check if current page is a valid calendar page."""
    try:
        month_element = driver.ele("css:.month", timeout=3)
        saturday_cells = driver.eles("css:td.td-sat", timeout=3)
        return month_element is not None and len(saturday_cells) > 0
    except:
        return False


def validate_cached_url(cached_url):
    """Validate cached URL in headless mode."""
    print(f"\nValidating cached URL in headless mode...")
    print(f"URL: {cached_url[:80]}...")
    
    driver = create_driver(headless=True)
    try:
        driver.get(cached_url)
        time.sleep(3)
        
        if is_valid_calendar_page(driver):
            print("Cached URL is valid")
            return True
        else:
            print("Cached URL is invalid or expired")
            return False
    finally:
        driver.quit()


def navigate_to_calendar_link(driver):
    """Navigate from main page to calendar CAPTCHA page."""
    print(f"\nNavigating to {MAIN_URL}")
    driver.get(MAIN_URL)
    time.sleep(3)
    
    print("Looking for calendar link...")
    driver.wait.load_start()
    
    # Try multiple selectors to find the calendar link
    calendar_link = None
    selectors = [
        ("text:カレンダーから探す", 5),
        ("tag:a@text():カレンダーから探す", 5),
        ("tag:a@href*=/calendar_apply", 5),
    ]
    
    for selector, timeout in selectors:
        try:
            calendar_link = driver.ele(selector, timeout=timeout)
            if calendar_link:
                break
        except:
            continue
    
    if not calendar_link:
        raise Exception("Could not find calendar link")
    
    print("Found calendar link, clicking...")
    calendar_link.click()
    time.sleep(3)
    
    if "calendar_apply" not in driver.url:
        raise Exception(f"Not on expected CAPTCHA page. Current URL: {driver.url}")
    
    print("On CAPTCHA verification page")


def solve_captcha_and_proceed(driver):
    """Solve CAPTCHA and proceed to calendar page."""
    print("Attempting to solve CAPTCHA...")
    time.sleep(3)
    
    recaptcha_solver = RecaptchaSolver(driver)
    
    try:
        t0 = time.time()
        recaptcha_solver.solveCaptcha()
        print(f"CAPTCHA solved in {time.time()-t0:.2f} seconds")
    except Exception as e:
        print(f"Automated solver encountered issue: {e}")
        print("CAPTCHA may have been solved via checkbox")
        time.sleep(2)
    
    print("Clicking next button...")
    time.sleep(3)
    
    # Try to click the next button or submit form
    try:
        next_button = driver.ele("tag:input@type=button", timeout=5)
        next_button.click()
        print("Clicked next button")
    except:
        driver.run_js("document.querySelector('form').submit();")
        print("Submitted form via JavaScript")
    
    time.sleep(4)
    
    calendar_url = driver.url
    print(f"Navigated to: {calendar_url}")
    
    if "calendar_select" not in calendar_url:
        raise Exception(f"Not on expected calendar page. Current URL: {calendar_url}")
    
    print("Successfully reached calendar page")
    return calendar_url


def acquire_calendar_url_with_captcha():
    """Get calendar URL by solving CAPTCHA in non-headless mode."""
    print("\n" + "="*60)
    print("ACQUIRING NEW CALENDAR URL (NON-HEADLESS MODE)")
    print("="*60)
    
    driver = create_driver(headless=False)
    try:
        navigate_to_calendar_link(driver)
        calendar_url = solve_captcha_and_proceed(driver)
        return calendar_url
    except Exception as e:
        print(f"Error acquiring calendar URL: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        print("Closing browser...")
        driver.quit()


def navigate_to_next_month(driver):
    """Navigate to the next month in the calendar."""
    # Try multiple selectors for the next month button
    selectors = [
        ("@id=nextMonth", 3),
        ("tag:input@value=翌月＞", 3),
        ("css:.next-month input", 3),
    ]
    
    for selector, timeout in selectors:
        try:
            next_button = driver.ele(selector, timeout=timeout)
            if next_button:
                next_button.click()
                time.sleep(3)
                return True
        except:
            continue
    
    return False


def scan_month_saturdays(driver):
    """Scan current month for available Saturdays."""
    try:
        month_element = driver.ele("css:.month", timeout=3)
        current_month = month_element.text
    except:
        current_month = "Unknown"
    
    print(f"Scanning: {current_month}")
    
    available_saturdays = []
    
    try:
        saturday_cells = driver.eles("css:td.td-sat")
        print(f"Found {len(saturday_cells)} Saturday(s)")
        
        for cell in saturday_cells:
            try:
                date_text = cell.ele("tag:p").text
                icon_element = cell.ele("css:.icon")
                icon = icon_element.text
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
        print(f"Error finding Saturday cells: {e}")
    
    return available_saturdays


def scan_calendar(driver, num_months=3):
    """Scan calendar for available Saturdays across multiple months."""
    print("\n" + "="*60)
    print(f"SCANNING SATURDAYS FOR {num_months} MONTHS")
    print("="*60 + "\n")
    
    all_available = []
    
    for month_num in range(num_months):
        print(f"Month {month_num + 1}/{num_months}")
        print("-"*60)
        
        month_saturdays = scan_month_saturdays(driver)
        all_available.extend(month_saturdays)
        
        # Navigate to next month if not the last iteration
        if month_num < num_months - 1:
            print("Navigating to next month...\n")
            if not navigate_to_next_month(driver):
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


def scan_calendar_headless(calendar_url):
    """Scan calendar in headless mode using the provided URL."""
    print("\n" + "="*60)
    print("SCANNING CALENDAR (HEADLESS MODE)")
    print("="*60)
    
    driver = create_driver(headless=True)
    try:
        driver.get(calendar_url)
        time.sleep(3)
        
        if not is_valid_calendar_page(driver):
            raise Exception("Failed to load valid calendar page")
        
        available_saturdays = scan_calendar(driver, num_months=3)
        print_summary(available_saturdays)
        
        print("Scan complete")
        time.sleep(2)
    finally:
        driver.quit()


def scan_once():
    """Perform a single scan iteration."""
    # Step 1: Try using cached URL in headless mode
    cached_url = load_cached_url()
    
    if cached_url:
        if validate_cached_url(cached_url):
            # Cached URL is valid - scan in headless mode
            scan_calendar_headless(cached_url)
            return True
    
    # Step 2: Cache invalid or doesn't exist - acquire new URL
    print("\nNeed to acquire new calendar URL")
    new_url = acquire_calendar_url_with_captcha()
    
    if not new_url:
        print("Failed to acquire calendar URL")
        return False
    
    # Step 3: Save new URL and scan in headless mode
    save_calendar_url(new_url)
    print("\n" + "="*60)
    print("URL ACQUIRED - RESTARTING IN HEADLESS MODE")
    print("="*60)
    time.sleep(2)
    scan_calendar_headless(new_url)
    return True


def main():
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
                scan_once()
            except Exception as e:
                print(f"\nError during scan: {e}")
                import traceback
                traceback.print_exc()
            
            print(f"\n[{timestamp}] Waiting {SCAN_INTERVAL_SECONDS} seconds until next scan...")
            time.sleep(SCAN_INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        print("\n\n" + "="*60)
        print("SCANNER STOPPED BY USER")
        print(f"Total iterations completed: {iteration}")
        print("="*60)


if __name__ == "__main__":
    main()
    print("Done!")