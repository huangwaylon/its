# -*- coding: utf-8 -*-
import asyncio
import os
from datetime import datetime
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions

# ============================================================
# CONFIGURATION
# ============================================================

# File paths
CALENDAR_URL_CACHE = "calendar_url_cache.txt"

# URLs and API endpoints
MAIN_URL = "https://as.its-kenpo.or.jp"

# User configuration
TARGET_EMAIL = "waylonh@apple.com"
NUM_GUESTS = 2

# Scanning configuration
SCAN_INTERVAL_SECONDS = 10  # Check every X seconds
NUM_MONTHS_TO_SCAN = 3

# Day of week configuration
# Use "td-sun" for Sunday, "td-sat" for Saturday
TARGET_DAY_CLASS = "td-sun"  # Testing with Sunday (change to "td-sat" for Saturday)
TARGET_DAY_NAME = "Sunday" if TARGET_DAY_CLASS == "td-sun" else "Saturday"

# Booking mode
AUTO_BOOK = True  # Set to True to automatically attempt booking when available dates found
SCAN_ONLY = False  # Set to True to only scan without booking
MANUAL_MODE = False  # Set to True for manual interaction/testing

# Hotel filtering
SKIP_BLUEBERRY_HILL = True
BLUEBERRY_HILL_NAME = "ブルーベリーヒル勝浦"

# Chrome browser arguments
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

# ============================================================
# TIMEOUT AND DELAY CONSTANTS
# ============================================================

# Browser timeouts
BROWSER_START_TIMEOUT = 30
DEFAULT_TIMEOUT = 3
EXTENDED_TIMEOUT = 5

# Sleep/wait durations (in seconds)
SLEEP_SHORT = 0.5
SLEEP_STANDARD = 1
SLEEP_MEDIUM = 1.5
SLEEP_LONG = 2
SLEEP_EXTENDED = 3
SLEEP_CAPTCHA_WAIT = 4
SLEEP_CAPTCHA_LOAD = 5
SLEEP_DIALOG_WAIT = 5

# ============================================================
# DOM SELECTORS AND ATTRIBUTES
# ============================================================

# HTML element types
TAG_INPUT = "input"
TAG_ANCHOR = "a"
TAG_TD = "td"
TAG_PARAGRAPH = "p"

# CSS class names
CLASS_MONTH = "month"
CLASS_ICON = "icon"

# HTML attributes
ATTR_DATA_JOIN_TIME = "data-join-time"

# Element IDs
ID_NEXT_MONTH = "nextMonth"

# Input field names
INPUT_NAME_STAY_PERSONS = "stay_persons"
INPUT_NAME_EMAIL = "email"
INPUT_NAME_NO_NAME = "no-name"
INPUT_NAME_ROOM_PREFIX = "apply[coma["

# Selectors
RECAPTCHA_IFRAME_SELECTOR = 'iframe[src*="recaptcha/api2/anchor"]'
FORM_SUBMIT_SCRIPT = "document.querySelector('form').submit();"
WINDOW_LOCATION_SCRIPT = "return window.location.href"

# ============================================================
# URL PATH COMPONENTS
# ============================================================

URL_CALENDAR_APPLY = "calendar_apply"
URL_CALENDAR_SELECT = "calendar_select"
URL_SERVICE_GROUP_SELECT = "service_group_select"
URL_APPLY_SERVICE_SELECT = "apply_service_select"
URL_APPLY_EMPTY_NEW = "apply/empty_new"
URL_APPLY_RULE = "apply/rule"
URL_APPLY_EMAIL_INPUT = "apply/email_input"
URL_SEND_COMPLETE = "send_complete"

# URL protocols
PROTOCOL_HTTP = "http://"
PROTOCOL_HTTPS = "https://"
PROTOCOL_JAVASCRIPT = "javascript:"

# ============================================================
# JAPANESE UI TEXT
# ============================================================

# Button and link text
TEXT_CALENDAR_SEARCH = "カレンダーから探す"
TEXT_NEXT_BUTTON = "次へ"
TEXT_NEXT_MONTH = "翌月"
TEXT_SEARCH_AVAILABILITY = "空き検索"
TEXT_PROCEED_TO_BOOKING = "予約手続きに進む"
TEXT_AGREE = "同意"
TEXT_SUBMIT = "送信"
TEXT_SERVICE_APPLICATION = "申込"

# Skip link texts
SKIP_LINK_TEXTS = ["ページ先頭", "関東ITソフトウェア", "健康保険組合", "公式サイト"]
SKIP_LINK_TEXTS_SERVICE = ["ページ先頭", "関東ITソフトウェア", "健康保険組合"]

# ============================================================
# STATUS INDICATORS
# ============================================================

# Availability icons
ICON_AVAILABLE = "○"
STATUS_AVAILABLE = "Available"
STATUS_FULL = "Full"
STATUS_UNKNOWN = "Unknown"
NONE_STRING = "None"

# ============================================================
# NUMERIC THRESHOLDS AND LIMITS
# ============================================================

# String truncation lengths
URL_TRUNCATE_LENGTH = 80
TEXT_TRUNCATE_LENGTH = 50
MIN_LINK_TEXT_LENGTH = 3

# Scroll distances
SCROLL_DOWN_DISTANCE = 100
SCROLL_UP_DISTANCE = 50

# File extensions
FILE_EXTENSION_HTML = ".html"

# ============================================================
# BROWSER NAVIGATION COMMANDS
# ============================================================

HISTORY_BACK = "window.history.back();"
HISTORY_GO_BACK_2 = "window.history.go(-2);"
HISTORY_GO_BACK_3 = "window.history.go(-3);"
HISTORY_GO_BACK_4 = "window.history.go(-4);"
HISTORY_GO_BACK_5 = "window.history.go(-5);"
HISTORY_GO_BACK_6 = "window.history.go(-6);"

# ============================================================
# DISPLAY AND FORMATTING
# ============================================================

SEPARATOR_WIDTH = 60
SEPARATOR_CHAR = "="
SUBSEPARATOR_CHAR = "-"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def create_browser_options(headless=False):
    """Create ChromiumOptions with specified headless mode."""
    options = ChromiumOptions()
    # Let pydoll auto-detect Chrome path
    
    if headless:
        options.headless = True
        print("Running in HEADLESS mode")
    else:
        print("Running in NORMAL mode (browser visible)")
    
    for argument in CHROME_ARGUMENTS:
        options.add_argument(argument)
    
    options.start_timeout = BROWSER_START_TIMEOUT
    
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
        month_element = await tab.find(class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        day_cells = await tab.find(tag_name=TAG_TD, class_name=TARGET_DAY_CLASS, find_all=True, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        return month_element is not None and day_cells is not None and len(day_cells) > 0
    except:
        return False


async def validate_cached_url(cached_url):
    """Validate cached URL in headless mode."""
    print(f"\nValidating cached URL in headless mode...")
    print(f"URL: {cached_url[:URL_TRUNCATE_LENGTH]}...")
    
    options = create_browser_options(headless=True)
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(cached_url)
        await asyncio.sleep(SLEEP_EXTENDED)
        
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
    await asyncio.sleep(SLEEP_EXTENDED)
    
    print("Looking for calendar link...")
    
    calendar_link = None
    
    # Try finding by text first
    try:
        calendar_link = await tab.find(text=TEXT_CALENDAR_SEARCH, timeout=EXTENDED_TIMEOUT, raise_exc=False)
    except:
        pass
    
    # Try finding by href attribute
    if not calendar_link:
        try:
            links = await tab.find(tag_name=TAG_ANCHOR, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
            if links:
                for link in links:
                    try:
                        href = await link.get_property("href")
                        if href and URL_CALENDAR_APPLY in href:
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
    await asyncio.sleep(SLEEP_EXTENDED)
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    if URL_CALENDAR_APPLY not in current_url:
        raise Exception(f"Not on expected CAPTCHA page. Current URL: {current_url}")
    
    print("On CAPTCHA verification page")


async def bypass_captcha_and_proceed(tab):
    """Bypass CAPTCHA by clicking checkbox and proceeding."""
    print("Bypassing CAPTCHA with pydoll's stealth capabilities...")
    
    await asyncio.sleep(SLEEP_EXTENDED)
    print("Waiting for CAPTCHA to load...")
    await asyncio.sleep(SLEEP_CAPTCHA_LOAD)
    
    # Simulate natural behavior
    try:
        print("Simulating natural user behavior...")
        from pydoll.constants import ScrollPosition
        
        await tab.scroll.by(ScrollPosition.DOWN, SCROLL_DOWN_DISTANCE, smooth=True)
        await asyncio.sleep(SLEEP_MEDIUM)
        await tab.scroll.by(ScrollPosition.UP, SCROLL_UP_DISTANCE, smooth=True)
        await asyncio.sleep(SLEEP_STANDARD)
        print("Human-like behavior simulation complete")
    except Exception as e:
        print(f"Note: Behavioral simulation: {e}")
    
    # Click reCAPTCHA
    try:
        print("Looking for reCAPTCHA checkbox...")
        recaptcha_iframe = await tab.query(RECAPTCHA_IFRAME_SELECTOR, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        
        if recaptcha_iframe:
            print("Found reCAPTCHA checkbox iframe")
            await asyncio.sleep(SLEEP_LONG)
            
            try:
                await recaptcha_iframe.scroll_into_view()
                await asyncio.sleep(SLEEP_STANDARD)
                await recaptcha_iframe.click()
                print("Clicked reCAPTCHA iframe area")
                await asyncio.sleep(SLEEP_EXTENDED)
            except Exception as click_err:
                print(f"Could not click iframe directly: {click_err}")
            
            print("Waiting for captcha to be solved...")
            await asyncio.sleep(SLEEP_CAPTCHA_LOAD)
        else:
            print("No reCAPTCHA iframe found, proceeding anyway...")
    except Exception as e:
        print(f"Note: reCAPTCHA interaction: {e}")
    
    # Click next button
    print(f"Looking for {TEXT_NEXT_BUTTON} (Next) button...")
    await asyncio.sleep(SLEEP_LONG)
    
    try:
        buttons = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        next_button = None
        
        if buttons:
            for button in buttons:
                try:
                    value = await button.get_property("value")
                    if value and TEXT_NEXT_BUTTON in value:
                        next_button = button
                        break
                except:
                    pass
        
        if next_button:
            await next_button.scroll_into_view()
            await asyncio.sleep(SLEEP_STANDARD)
            await next_button.click()
            print(f"Clicked {TEXT_NEXT_BUTTON} button")
        else:
            await tab.execute_script(FORM_SUBMIT_SCRIPT)
            print("Submitted form via JavaScript")
    except Exception as e:
        print(f"Error: {e}")
        await tab.execute_script(FORM_SUBMIT_SCRIPT)
        print("Submitted form via JavaScript (fallback)")
    
    await asyncio.sleep(SLEEP_CAPTCHA_WAIT)
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    calendar_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    print(f"Navigated to: {calendar_url}")
    
    if URL_CALENDAR_SELECT not in calendar_url:
        raise Exception(f"Not on expected calendar page. Current URL: {calendar_url}")
    
    print("Successfully reached calendar page")
    return calendar_url


async def acquire_calendar_url_with_captcha():
    """Get calendar URL by bypassing CAPTCHA."""
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print("ACQUIRING NEW CALENDAR URL (NON-HEADLESS MODE)")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
    
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
    try:
        next_button = await tab.find(id=ID_NEXT_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if next_button:
            await next_button.click()
            await asyncio.sleep(SLEEP_EXTENDED)
            return True
    except:
        pass
    
    try:
        inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if inputs:
            for input_elem in inputs:
                try:
                    value = await input_elem.get_property("value")
                    if value and TEXT_NEXT_MONTH in value:
                        await input_elem.click()
                        await asyncio.sleep(SLEEP_EXTENDED)
                        return True
                except:
                    pass
    except:
        pass
    
    return False


async def scan_month_days(tab):
    """Scan current month for available days."""
    try:
        month_element = await tab.find(class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if month_element:
            text_result = await month_element.execute_script("return this.textContent")
            if isinstance(text_result, dict) and 'result' in text_result:
                if 'result' in text_result['result']:
                    current_month = text_result['result']['result'].get('value', STATUS_UNKNOWN)
                else:
                    current_month = text_result['result'].get('value', STATUS_UNKNOWN)
            else:
                current_month = str(text_result)
        else:
            current_month = STATUS_UNKNOWN
    except Exception as e:
        print(f"Error getting month: {e}")
        current_month = STATUS_UNKNOWN
    
    print(f"Scanning: {current_month}")
    
    available_days = []
    
    try:
        day_cells = await tab.find(tag_name=TAG_TD, class_name=TARGET_DAY_CLASS, find_all=True, raise_exc=False)
        if not day_cells:
            print(f"Found 0 {TARGET_DAY_NAME}(s)")
            return available_days
        
        print(f"Found {len(day_cells)} {TARGET_DAY_NAME}(s)")
        
        for cell in day_cells:
            try:
                # Get date text
                date_elem = await cell.find(tag_name=TAG_PARAGRAPH, raise_exc=False)
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
                
                # Get icon
                icon_element = await cell.find(class_name=CLASS_ICON, raise_exc=False)
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
                
                # Get full date
                attr_result = await cell.execute_script(f"return this.getAttribute('{ATTR_DATA_JOIN_TIME}')")
                if isinstance(attr_result, dict) and 'result' in attr_result:
                    if 'result' in attr_result['result']:
                        full_date = attr_result['result']['result'].get('value', '')
                    else:
                        full_date = attr_result['result'].get('value', '')
                else:
                    full_date = str(attr_result)
                    
                if not full_date or full_date == NONE_STRING:
                    full_date = ""
                
                status = STATUS_AVAILABLE if icon == ICON_AVAILABLE else STATUS_FULL
                print(f"  {date_text}日: {icon} ({status}) - {full_date}")
                
                if icon == ICON_AVAILABLE:
                    available_days.append({
                        'month': current_month,
                        'date': date_text,
                        'full_date': full_date,
                        'icon': icon,
                        'cell': cell  # Store cell reference for booking
                    })
            except Exception as e:
                print(f"  Error processing cell: {str(e)[:TEXT_TRUNCATE_LENGTH]}")
    except Exception as e:
        print(f"Error finding {TARGET_DAY_NAME} cells: {e}")
    
    return available_days


# ============================================================
# BOOKING AUTOMATION FUNCTIONS
# ============================================================

async def click_available_date_cell(cell):
    """Click on an available date cell to start booking process."""
    try:
        await cell.scroll_into_view()
        await asyncio.sleep(SLEEP_STANDARD)
        # Use JavaScript click to avoid visibility issues
        await cell.execute_script("this.click()")
        print("Clicked date cell")
        await asyncio.sleep(SLEEP_EXTENDED)
        return True
    except Exception as e:
        print(f"Error clicking date cell: {e}")
        return False


async def get_hotel_names_on_service_group_page(tab, skip_blueberry_hill=SKIP_BLUEBERRY_HILL):
    """On service_group_select page, collect all hotel names.
    
    Args:
        tab: Browser tab
        skip_blueberry_hill: If True, skip hotel named "ブルーベリーヒル勝浦" (default: SKIP_BLUEBERRY_HILL)
    
    Returns:
        List of hotel names
    """
    print(f"\nOn {URL_SERVICE_GROUP_SELECT} page, collecting hotel names...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if URL_SERVICE_GROUP_SELECT not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return []
    
    hotel_names = []
    
    try:
        await asyncio.sleep(SLEEP_LONG)
        links = await tab.find(tag_name=TAG_ANCHOR, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if links:
            for link in links:
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = text_result['result']['result']['value'] if isinstance(text_result, dict) and 'result' in text_result else ""
                    
                    href_result = await link.execute_script("return this.href")
                    href = href_result['result']['result']['value'] if isinstance(href_result, dict) and 'result' in href_result else ""
                    
                    # Skip navigation links, empty links, and header links
                    if any(skip in link_text for skip in SKIP_LINK_TEXTS):
                        continue
                    
                    # Skip links with http URLs or .html files (we want javascript: links)
                    if href and (PROTOCOL_HTTP in href or PROTOCOL_HTTPS in href or href.endswith(FILE_EXTENSION_HTML)):
                        continue
                    
                    # Hotel link has substantial text and javascript: href
                    if link_text and len(link_text.strip()) > MIN_LINK_TEXT_LENGTH and PROTOCOL_JAVASCRIPT in href:
                        # Skip Blueberry Hill hotel if requested
                        if skip_blueberry_hill and BLUEBERRY_HILL_NAME in link_text:
                            print(f"Skipping hotel: {link_text[:TEXT_TRUNCATE_LENGTH]} (skip_blueberry_hill=True)")
                            continue
                        
                        hotel_names.append(link_text)
                        print(f"Found hotel: {link_text[:TEXT_TRUNCATE_LENGTH]}")
                except Exception as e:
                    print(f"Error checking link: {e}")
                    pass
        
        print(f"Total hotels to try: {len(hotel_names)}")
        return hotel_names
    except Exception as e:
        print(f"Error collecting hotel names: {e}")
        return []


async def click_hotel_by_name(tab, hotel_name):
    """Click a hotel link by its name on service_group_select page.
    
    Args:
        tab: Browser tab
        hotel_name: Exact name of the hotel to click
    
    Returns:
        True if successful, False otherwise
    """
    print(f"Looking for hotel: {hotel_name[:TEXT_TRUNCATE_LENGTH]}...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if URL_SERVICE_GROUP_SELECT not in current_url:
        print(f"Not on {URL_SERVICE_GROUP_SELECT} page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_LONG)
        links = await tab.find(tag_name=TAG_ANCHOR, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if links:
            for link in links:
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = text_result['result']['result']['value'] if isinstance(text_result, dict) and 'result' in text_result else ""
                    
                    if link_text == hotel_name:
                        print(f"Clicking hotel: {hotel_name[:TEXT_TRUNCATE_LENGTH]}...")
                        await link.scroll_into_view()
                        await asyncio.sleep(SLEEP_STANDARD)
                        await link.click()
                        await asyncio.sleep(SLEEP_EXTENDED)
                        return True
                except Exception as e:
                    print(f"Error checking link: {e}")
                    pass
        
        print(f"Could not find hotel: {hotel_name[:TEXT_TRUNCATE_LENGTH]}")
        return False
    except Exception as e:
        print(f"Error clicking hotel: {e}")
        return False


async def select_hotel_on_service_group_page(tab, skip_blueberry_hill=SKIP_BLUEBERRY_HILL):
    """On service_group_select page, click the first hotel link.
    
    Note: For processing all hotels, use get_hotel_names_on_service_group_page instead.
    
    Args:
        tab: Browser tab
        skip_blueberry_hill: If True, skip hotel named "ブルーベリーヒル勝浦" (default: SKIP_BLUEBERRY_HILL)
    
    Returns:
        True if successful, False otherwise
    """
    hotel_names = await get_hotel_names_on_service_group_page(tab, skip_blueberry_hill)
    if hotel_names:
        return await click_hotel_by_name(tab, hotel_names[0])
    return False


async def select_service_on_apply_page(tab):
    """On apply_service_select page, click the service link (ends with 申込)."""
    print(f"\nOn {URL_APPLY_SERVICE_SELECT} page, looking for service link...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if URL_APPLY_SERVICE_SELECT not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_LONG)
        links = await tab.find(tag_name=TAG_ANCHOR, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if links:
            for link in links:
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = text_result['result']['result']['value'] if isinstance(text_result, dict) and 'result' in text_result else ""
                    
                    href_result = await link.execute_script("return this.href")
                    href = href_result['result']['result']['value'] if isinstance(href_result, dict) and 'result' in href_result else ""
                    
                    # Skip navigation and header links
                    if any(skip in link_text for skip in SKIP_LINK_TEXTS_SERVICE):
                        continue
                    
                    # Service link ends with 申込 and has javascript: href
                    if link_text and TEXT_SERVICE_APPLICATION in link_text and PROTOCOL_JAVASCRIPT in href:
                        print(f"Clicking service link: {link_text[:TEXT_TRUNCATE_LENGTH]}...")
                        await link.scroll_into_view()
                        await asyncio.sleep(SLEEP_STANDARD)
                        await link.click()
                        await asyncio.sleep(SLEEP_EXTENDED)
                        return True
                except Exception as e:
                    print(f"Error checking link: {e}")
                    pass
        
        print("Could not find service link")
        return False
    except Exception as e:
        print(f"Error selecting service: {e}")
        return False


async def fill_booking_form_and_search(tab, target_date):
    """Fill in the booking form and search for availability."""
    print(f"\nOn {URL_APPLY_EMPTY_NEW} page, filling booking form...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if URL_APPLY_EMPTY_NEW not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_LONG)
        print(f"Verifying date matches: {target_date}")
        print(f"Filling in number of guests: {NUM_GUESTS}")
        
        # Find the guest count input by name: apply[stay_persons]
        inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        guest_filled = False
        
        if inputs:
            for input_elem in inputs:
                try:
                    name_result = await input_elem.execute_script("return this.name")
                    input_name = name_result['result']['result']['value'] if isinstance(name_result, dict) and 'result' in name_result else ""
                    
                    # Look for the stay_persons input
                    if INPUT_NAME_STAY_PERSONS in input_name:
                        await input_elem.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await input_elem.execute_script("this.value = ''")
                        await input_elem.insert_text(str(NUM_GUESTS))
                        print(f"Filled guest count: {NUM_GUESTS} in {input_name}")
                        guest_filled = True
                        await asyncio.sleep(SLEEP_STANDARD)
                        break
                except:
                    pass
        
        if not guest_filled:
            print("Warning: Could not fill guest count")
        
        # Click 空き検索 button
        print(f"Looking for {TEXT_SEARCH_AVAILABILITY} button...")
        buttons = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if buttons:
            for button in buttons:
                try:
                    value_result = await button.execute_script("return this.value")
                    button_value = value_result['result']['result']['value'] if isinstance(value_result, dict) and 'result' in value_result else ""
                    
                    # Exact match for 空き検索
                    if button_value == TEXT_SEARCH_AVAILABILITY:
                        await button.scroll_into_view()
                        await asyncio.sleep(SLEEP_STANDARD)
                        await button.click()
                        print(f"Clicked {TEXT_SEARCH_AVAILABILITY} button")
                        await asyncio.sleep(SLEEP_CAPTCHA_WAIT)
                        return True
                except:
                    pass
        
        print("Could not find search button")
        return False
    except Exception as e:
        print(f"Error filling form: {e}")
        return False


async def select_room_and_proceed(tab):
    """Select a room checkbox (skip first one) and click proceed button."""
    print("\nSelecting room and proceeding...")
    
    try:
        await asyncio.sleep(SLEEP_LONG)
        print("Looking for room checkboxes...")
        all_inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        
        room_selected = False
        if all_inputs:
            # Find checkboxes with name starting with "apply[coma["
            # Skip the first checkbox (name="no-name" with value="on")
            for inp in all_inputs:
                try:
                    type_result = await inp.execute_script("return this.type")
                    input_type = type_result['result']['result']['value'] if isinstance(type_result, dict) and 'result' in type_result else ""
                    
                    if input_type == "checkbox":
                        name_result = await inp.execute_script("return this.name")
                        input_name = name_result['result']['result']['value'] if isinstance(name_result, dict) and 'result' in name_result else ""
                        
                        # Skip the "no-name" checkbox (select all)
                        if input_name == INPUT_NAME_NO_NAME:
                            continue
                        
                        # Look for room checkboxes: name starts with "apply[coma["
                        if input_name and INPUT_NAME_ROOM_PREFIX in input_name:
                            disabled_result = await inp.execute_script("return this.disabled")
                            is_disabled = disabled_result['result']['result']['value'] if isinstance(disabled_result, dict) and 'result' in disabled_result else True
                            
                            if not is_disabled:
                                await inp.scroll_into_view()
                                await asyncio.sleep(SLEEP_STANDARD)
                                # Use JavaScript click
                                await inp.execute_script("this.click()")
                                print(f"Selected room checkbox: {input_name}")
                                room_selected = True
                                await asyncio.sleep(SLEEP_STANDARD)
                                break
                except:
                    pass
        
        if not room_selected:
            print("No room checkbox found")
            return False
        
        # Click 予約手続きに進む button (exact match)
        print(f"Looking for {TEXT_PROCEED_TO_BOOKING} button...")
        buttons = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if buttons:
            for button in buttons:
                try:
                    value_result = await button.execute_script("return this.value")
                    button_value = value_result['result']['result']['value'] if isinstance(value_result, dict) and 'result' in value_result else ""
                    
                    # Exact match for the proceed button
                    if button_value == TEXT_PROCEED_TO_BOOKING:
                        await button.scroll_into_view()
                        await asyncio.sleep(SLEEP_STANDARD)
                        await button.click()
                        print(f"Clicked {TEXT_PROCEED_TO_BOOKING} button")
                        await asyncio.sleep(SLEEP_EXTENDED)
                        return True
                except:
                    pass
        
        print("Could not find proceed button")
        return False
    except Exception as e:
        print(f"Error selecting room: {e}")
        return False


async def agree_to_rules(tab):
    """Click agree button on rule page."""
    print(f"\nOn {URL_APPLY_RULE} page, clicking {TEXT_AGREE}する...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if URL_APPLY_RULE not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_LONG)
        buttons = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if buttons:
            for button in buttons:
                try:
                    value_result = await button.execute_script("return this.value")
                    button_value = value_result['result']['result']['value'] if isinstance(value_result, dict) and 'result' in value_result else ""
                    
                    if TEXT_AGREE in button_value:
                        await button.scroll_into_view()
                        await asyncio.sleep(SLEEP_STANDARD)
                        await button.click()
                        print(f"Clicked {TEXT_AGREE}する button")
                        await asyncio.sleep(SLEEP_EXTENDED)
                        return True
                except:
                    pass
        
        print("Could not find agree button")
        return False
    except Exception as e:
        print(f"Error agreeing to rules: {e}")
        return False


async def fill_email_and_submit(tab):
    """Fill email and submit with dialog handling."""
    print(f"\nOn {URL_APPLY_EMAIL_INPUT} page, filling email and submitting...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if URL_APPLY_EMAIL_INPUT not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_LONG)
        print(f"Filling email: {TARGET_EMAIL}")
        inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        
        email_filled = False
        if inputs:
            for input_elem in inputs:
                try:
                    name_result = await input_elem.execute_script("return this.name")
                    input_name = name_result['result']['result']['value'] if isinstance(name_result, dict) and 'result' in name_result else ""
                    
                    # Look for email input: name="email"
                    if input_name == INPUT_NAME_EMAIL:
                        await input_elem.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await input_elem.execute_script("this.value = ''")
                        await input_elem.insert_text(TARGET_EMAIL)
                        print(f"Filled email: {TARGET_EMAIL}")
                        email_filled = True
                        await asyncio.sleep(SLEEP_STANDARD)
                        break
                except:
                    pass
        
        if not email_filled:
            print("Warning: Could not fill email")
            return False
        
        # Click 送信 button and handle confirmation dialog
        print(f"Clicking {TEXT_SUBMIT} button...")
        
        # Set up dialog handler
        from pydoll.protocol.page.events import PageEvent
        
        async def handle_dialog(event):
            """Auto-accept confirmation dialog."""
            try:
                print(f"[DIALOG] Accepting confirmation...")
                await tab.handle_dialog(accept=True)
                print("[DIALOG] ✓ Clicked OK")
            except Exception as e:
                print(f"[DIALOG ERROR] {e}")
        
        # Enable page events and register handler
        await tab.enable_page_events()
        callback_id = await tab.on(PageEvent.JAVASCRIPT_DIALOG_OPENING, handle_dialog)
        await asyncio.sleep(SLEEP_SHORT)
        
        # Submit form (triggers dialog)
        try:
            await tab.execute_script(FORM_SUBMIT_SCRIPT)
            print("Submitted form")
        except:
            pass
        
        # Wait for dialog handling and navigation
        await asyncio.sleep(SLEEP_DIALOG_WAIT)
        
        # Check if we reached send_complete page
        final_url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
        final_url = final_url_response['result']['result']['value'] if isinstance(final_url_response, dict) else final_url_response
        
        if URL_SEND_COMPLETE in final_url:
            print(f"✓ Reached {URL_SEND_COMPLETE} page: {final_url}")
            return True
        else:
            print(f"Warning: Final URL: {final_url}")
            return True  # May still have succeeded
        
    except Exception as e:
        print(f"Error in email/submit: {e}")
        return False


async def process_booking_for_date(tab, date_info, skip_blueberry_hill=SKIP_BLUEBERRY_HILL):
    """Process complete booking flow for a date, trying all available hotels.
    
    Args:
        tab: Browser tab
        date_info: Dictionary containing date information and cell reference
        skip_blueberry_hill: If True, skip hotel named "ブルーベリーヒル勝浦" (default: SKIP_BLUEBERRY_HILL)
    
    Returns:
        True if booking succeeded for any hotel, False otherwise
    """
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print(f"BOOKING: {date_info['date']}日 ({date_info['full_date']})")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
    
    try:
        # Click the date cell to get to service_group_select page
        if not await click_available_date_cell(date_info['cell']):
            print("Failed to click date cell")
            return False
        
        # Get all hotel names from the service_group_select page
        hotel_names = await get_hotel_names_on_service_group_page(tab, skip_blueberry_hill)
        
        if not hotel_names:
            print("No hotels found to try")
            return False
        
        print(f"\nWill try {len(hotel_names)} hotel(s) for this date")
        
        # Try each hotel
        for hotel_idx, hotel_name in enumerate(hotel_names):
            print("\n" + SUBSEPARATOR_CHAR * SEPARATOR_WIDTH)
            print(f"Hotel {hotel_idx + 1}/{len(hotel_names)}: {hotel_name[:TEXT_TRUNCATE_LENGTH]}")
            print(SUBSEPARATOR_CHAR * SEPARATOR_WIDTH)
            
            try:
                # Click the hotel link
                if not await click_hotel_by_name(tab, hotel_name):
                    print(f"Failed to click hotel: {hotel_name[:TEXT_TRUNCATE_LENGTH]}")
                    # Go back to hotel selection page for next hotel
                    if hotel_idx < len(hotel_names) - 1:
                        await tab.execute_script(HISTORY_BACK)
                        await asyncio.sleep(SLEEP_EXTENDED)
                    continue
                
                # Select service
                if not await select_service_on_apply_page(tab):
                    print("Failed to select service")
                    # Go back to hotel selection page for next hotel
                    if hotel_idx < len(hotel_names) - 1:
                        await tab.execute_script(HISTORY_GO_BACK_2)
                        await asyncio.sleep(SLEEP_EXTENDED)
                    continue
                
                # Fill booking form and search
                if not await fill_booking_form_and_search(tab, date_info['full_date']):
                    print("Failed to fill booking form")
                    # Go back to hotel selection page for next hotel
                    if hotel_idx < len(hotel_names) - 1:
                        await tab.execute_script(HISTORY_GO_BACK_3)
                        await asyncio.sleep(SLEEP_EXTENDED)
                    continue
                
                # Select room and proceed
                if not await select_room_and_proceed(tab):
                    print("No rooms available or failed to select room")
                    # Go back to hotel selection page for next hotel
                    if hotel_idx < len(hotel_names) - 1:
                        await tab.execute_script(HISTORY_GO_BACK_4)
                        await asyncio.sleep(SLEEP_EXTENDED)
                    continue
                
                # Agree to rules
                if not await agree_to_rules(tab):
                    print("Failed to agree to rules")
                    # Go back to hotel selection page for next hotel
                    if hotel_idx < len(hotel_names) - 1:
                        await tab.execute_script(HISTORY_GO_BACK_5)
                        await asyncio.sleep(SLEEP_EXTENDED)
                    continue
                
                # Fill email and submit
                if not await fill_email_and_submit(tab):
                    print("Failed to submit email")
                    # Go back to hotel selection page for next hotel
                    if hotel_idx < len(hotel_names) - 1:
                        await tab.execute_script(HISTORY_GO_BACK_6)
                        await asyncio.sleep(SLEEP_EXTENDED)
                    continue
                
                # If we got here, booking succeeded!
                print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
                print(f"BOOKING COMPLETED SUCCESSFULLY FOR: {hotel_name[:TEXT_TRUNCATE_LENGTH]}")
                print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
                return True
                
            except Exception as e:
                print(f"Error processing hotel {hotel_name[:TEXT_TRUNCATE_LENGTH]}: {e}")
                import traceback
                traceback.print_exc()
                # Try to go back to hotel selection page for next hotel
                if hotel_idx < len(hotel_names) - 1:
                    try:
                        await tab.execute_script(HISTORY_GO_BACK_6)
                        await asyncio.sleep(SLEEP_EXTENDED)
                    except:
                        pass
                continue
        
        # If we got here, all hotels failed
        print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
        print(f"BOOKING FAILED FOR ALL {len(hotel_names)} HOTELS")
        print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
        return False
        
    except Exception as e:
        print(f"Error in booking: {e}")
        import traceback
        traceback.print_exc()
        return False


async def scan_calendar(tab, num_months=NUM_MONTHS_TO_SCAN, attempt_booking=False):
    """Scan calendar for available dates."""
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print(f"SCANNING {TARGET_DAY_NAME.upper()}S FOR {num_months} MONTHS")
    if attempt_booking:
        print("AUTO-BOOKING ENABLED")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH + "\n")
    
    all_available = []
    
    for month_num in range(num_months):
        print(f"Month {month_num + 1}/{num_months}")
        print(SUBSEPARATOR_CHAR * SEPARATOR_WIDTH)
        
        month_dates = await scan_month_days(tab)
        all_available.extend(month_dates)
        
        # Auto-booking if enabled
        if attempt_booking and month_dates:
            print(f"\nFound {len(month_dates)} available {TARGET_DAY_NAME}(s)")
            for date_info in month_dates:
                print(f"\nAttempting to book: {date_info['date']}日")
                
                if await process_booking_for_date(tab, date_info):
                    print(f"✓ Booking successful for {date_info['date']}日")
                    return all_available
                else:
                    print(f"✗ Booking failed for {date_info['date']}日")
                    # Navigate back to calendar using current page's history
                    print("Navigating back to calendar...")
                    await tab.execute_script(HISTORY_GO_BACK_5)
                    await asyncio.sleep(SLEEP_EXTENDED)
        
        if month_num < num_months - 1:
            print("Navigating to next month...\n")
            if not await navigate_to_next_month(tab):
                print("Could not navigate to next month")
                break
    
    return all_available


def print_summary(available_dates):
    """Print summary of available dates."""
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print(f"SUMMARY: Available {TARGET_DAY_NAME}s")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
    
    if available_dates:
        print(f"\nFound {len(available_dates)} available {TARGET_DAY_NAME}(s):\n")
        for date in available_dates:
            print(f"  {date['month']} - {date['date']}日 ({date['full_date']})")
    else:
        print(f"\nNo available {TARGET_DAY_NAME}s found")
    
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH + "\n")


async def scan_calendar_headless(calendar_url):
    """Scan calendar in headless mode."""
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print("SCANNING CALENDAR (HEADLESS MODE)")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
    
    options = create_browser_options(headless=False)
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(calendar_url)
        await asyncio.sleep(SLEEP_EXTENDED)
        
        if not await is_valid_calendar_page(tab):
            raise Exception("Failed to load valid calendar page")
        
        available_dates = await scan_calendar(tab, num_months=NUM_MONTHS_TO_SCAN, attempt_booking=(AUTO_BOOK and not SCAN_ONLY))
        print_summary(available_dates)
        
        print("Scan complete")
        await asyncio.sleep(SLEEP_LONG)


async def scan_once():
    """Perform a single scan iteration."""
    cached_url = load_cached_url()
    
    if cached_url:
        if await validate_cached_url(cached_url):
            await scan_calendar_headless(cached_url)
            return True
    
    print("\nNeed to acquire new calendar URL")
    new_url = await acquire_calendar_url_with_captcha()
    
    if not new_url:
        print("Failed to acquire calendar URL")
        return False
    
    save_calendar_url(new_url)
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print("URL ACQUIRED - RESTARTING IN HEADLESS MODE")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
    await asyncio.sleep(SLEEP_LONG)
    await scan_calendar_headless(new_url)
    return True


async def main():
    """Main execution flow."""
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print("ITS CALENDAR SCANNER - CONTINUOUS MODE")
    print(f"Target: {TARGET_DAY_NAME}s")
    print(f"Auto-booking: {'ENABLED' if AUTO_BOOK and not SCAN_ONLY else 'DISABLED'}")
    print(f"Checking every {SCAN_INTERVAL_SECONDS} seconds")
    print("Press Ctrl+C to stop")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH + "\n")
    
    iteration = 0
    
    try:
        while True:
            iteration += 1
            timestamp = datetime.now().strftime(DATE_FORMAT)
            
            print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
            print(f"ITERATION #{iteration} - {timestamp}")
            print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
            
            try:
                await scan_once()
            except Exception as e:
                print(f"\nError during scan: {e}")
                import traceback
                traceback.print_exc()
            
            print(f"\n[{timestamp}] Waiting {SCAN_INTERVAL_SECONDS} seconds...")
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        print("\n\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
        print("SCANNER STOPPED BY USER")
        print(f"Total iterations: {iteration}")
        print(SEPARATOR_CHAR * SEPARATOR_WIDTH)


if __name__ == "__main__":
    asyncio.run(main())
    print("Done!")