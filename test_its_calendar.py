# -*- coding: utf-8 -*-
import asyncio
import json
import os
import re
from datetime import datetime
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions

# ============================================================
# CONFIGURATION
# ============================================================

# File paths
CALENDAR_URL_CACHE = "calendar_url_cache.txt"
BOOKINGS_FILE = "bookings.json"

# URLs and API endpoints
MAIN_URL = "https://as.its-kenpo.or.jp"

# User configuration
TARGET_EMAIL = "wwaylonhuang@gmail.com"
NUM_GUESTS = 2

# Scanning configuration
SCAN_INTERVAL_SECONDS = 10  # Check every X seconds
NUM_MONTHS_TO_SCAN = 3

# Day of week configuration
# Available day classes and their display names
DAY_CONFIG = {
    "td-sun": "Sunday",
    "td-sat": "Saturday",
    "td-n": "Weekday",  # Mon-Fri are all td-n
    "td-hol": "Holiday"
}

# Day name mapping
WEEKDAY_NAMES = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday"
}

# Specify which days to scan by weekday number (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun)
TARGET_WEEKDAYS = [3]  # Thursday = 3

# Booking mode
AUTO_BOOK = True  # Set to True to automatically attempt booking when available dates found

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
SLEEP_SHORT = 0.2
SLEEP_STANDARD = 0.5
SLEEP_MONTH_NAV = 1.0  # Longer wait for month navigation

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

# ============================================================
# DISPLAY AND FORMATTING
# ============================================================

SEPARATOR_WIDTH = 60
SEPARATOR_CHAR = "="
SUBSEPARATOR_CHAR = "-"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def extract_script_value(result):
    """Extract value from script execution result.
    
    Handles both nested dict format and direct values.
    """
    if isinstance(result, dict) and 'result' in result:
        nested = result['result']
        if isinstance(nested, dict) and 'result' in nested:
            return nested['result'].get('value')
        return nested.get('value')
    return str(result) if result else None


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


# ============================================================
# BOOKING TRACKING FUNCTIONS
# ============================================================

def load_bookings():
    """Load bookings history from JSON file.
    
    Returns:
        dict: Dictionary with date as key and list of hotel names as value.
              Example: {"2024-03-15": ["Hotel A", "Hotel B"]}
    """
    if not os.path.exists(BOOKINGS_FILE):
        return {}
    
    try:
        with open(BOOKINGS_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return {}
            bookings = json.loads(content)
            return bookings
    except Exception as e:
        print(f"Error loading bookings: {e}")
        return {}


def save_booking(date, hotel_name):
    """Record a successful booking to avoid rebooking.
    
    Args:
        date: Date string in format 'YYYY-MM-DD'
        hotel_name: Name of the hotel booked
    """
    try:
        bookings = load_bookings()
        
        # Initialize date entry if it doesn't exist
        if date not in bookings:
            bookings[date] = []
        
        # Add hotel to the date if not already there
        if hotel_name not in bookings[date]:
            bookings[date].append(hotel_name)
            
            # Save to file
            with open(BOOKINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(bookings, f, ensure_ascii=False, indent=2)
            
            print(f"✓ Recorded booking: {date} - {hotel_name}")
        else:
            print(f"Booking already recorded: {date} - {hotel_name}")
    except Exception as e:
        print(f"Error saving booking: {e}")


def is_already_booked(date, hotel_name):
    """Check if a date and hotel combination is already booked.
    
    Args:
        date: Date string in format 'YYYY-MM-DD'
        hotel_name: Name of the hotel to check
    
    Returns:
        bool: True if already booked, False otherwise
    """
    bookings = load_bookings()
    return date in bookings and hotel_name in bookings[date]


def get_booked_hotels_for_date(date):
    """Get list of hotels already booked for a specific date.
    
    Args:
        date: Date string in format 'YYYY-MM-DD'
    
    Returns:
        list: List of hotel names already booked for this date
    """
    bookings = load_bookings()
    return bookings.get(date, [])


async def is_valid_calendar_page(tab):
    """Check if current page is a valid calendar page."""
    try:
        month_element = await tab.find(class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if month_element is None:
            return False
        
        # Check if any td elements with data-join-time are present
        all_cells = await tab.find(tag_name=TAG_TD, find_all=True, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if all_cells:
            for cell in all_cells[:10]:  # Check first 10 cells
                try:
                    attr_result = await cell.execute_script(f"return this.getAttribute('{ATTR_DATA_JOIN_TIME}')")
                    date_attr = extract_script_value(attr_result)
                    if date_attr and date_attr != NONE_STRING and date_attr != 'None':
                        return True
                except:
                    pass
        return False
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
        await asyncio.sleep(SLEEP_SHORT)
        
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
    await asyncio.sleep(SLEEP_SHORT)
    
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
    await asyncio.sleep(SLEEP_SHORT)
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    if URL_CALENDAR_APPLY not in current_url:
        raise Exception(f"Not on expected CAPTCHA page. Current URL: {current_url}")
    
    print("On CAPTCHA verification page")


async def bypass_captcha_and_proceed(tab):
    """Bypass CAPTCHA by clicking checkbox and proceeding."""
    print("Bypassing CAPTCHA with pydoll's stealth capabilities...")
    
    await asyncio.sleep(SLEEP_SHORT)
    print("Waiting for CAPTCHA to load...")
    await asyncio.sleep(SLEEP_SHORT)
    
    # Simulate natural behavior
    try:
        print("Simulating natural user behavior...")
        from pydoll.constants import ScrollPosition
        
        await tab.scroll.by(ScrollPosition.DOWN, SCROLL_DOWN_DISTANCE, smooth=True)
        await asyncio.sleep(SLEEP_SHORT)
        await tab.scroll.by(ScrollPosition.UP, SCROLL_UP_DISTANCE, smooth=True)
        await asyncio.sleep(SLEEP_SHORT)
        print("Human-like behavior simulation complete")
    except Exception as e:
        print(f"Note: Behavioral simulation: {e}")
    
    # Click reCAPTCHA
    try:
        print("Looking for reCAPTCHA checkbox...")
        recaptcha_iframe = await tab.query(RECAPTCHA_IFRAME_SELECTOR, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        
        if recaptcha_iframe:
            print("Found reCAPTCHA checkbox iframe")
            await asyncio.sleep(SLEEP_SHORT)
            
            try:
                await recaptcha_iframe.scroll_into_view()
                await asyncio.sleep(SLEEP_SHORT)
                await recaptcha_iframe.click()
                print("Clicked reCAPTCHA iframe area")
                await asyncio.sleep(SLEEP_SHORT)
            except Exception as click_err:
                print(f"Could not click iframe directly: {click_err}")
            
            print("Waiting for captcha to be solved...")
            await asyncio.sleep(SLEEP_SHORT)
        else:
            print("No reCAPTCHA iframe found, proceeding anyway...")
    except Exception as e:
        print(f"Note: reCAPTCHA interaction: {e}")
    
    # Click next button
    print(f"Looking for {TEXT_NEXT_BUTTON} (Next) button...")
    await asyncio.sleep(SLEEP_SHORT)
    
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
            await asyncio.sleep(SLEEP_SHORT)
            await next_button.click()
            print(f"Clicked {TEXT_NEXT_BUTTON} button")
        else:
            await tab.execute_script(FORM_SUBMIT_SCRIPT)
            print("Submitted form via JavaScript")
    except Exception as e:
        print(f"Error: {e}")
        await tab.execute_script(FORM_SUBMIT_SCRIPT)
        print("Submitted form via JavaScript (fallback)")
    
    await asyncio.sleep(SLEEP_SHORT)
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    calendar_url = extract_script_value(url_response)
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
            return None


async def navigate_to_next_month(tab):
    """Navigate to the next month in the calendar with validation."""
    # Get current month before navigation  
    current_month = None
    try:
        month_element = await tab.find(class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if month_element:
            text_result = await month_element.execute_script("return this.textContent")
            current_month = extract_script_value(text_result)
    except:
        pass
    
    # Try clicking next month button
    clicked = False
    try:
        next_button = await tab.find(id=ID_NEXT_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if next_button:
            await next_button.click()
            clicked = True
    except:
        pass
    
    if not clicked:
        try:
            inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=DEFAULT_TIMEOUT, raise_exc=False)
            if inputs:
                for input_elem in inputs:
                    try:
                        value = await input_elem.get_property("value")
                        if value and TEXT_NEXT_MONTH in value:
                            await input_elem.click()
                            clicked = True
                            break
                    except:
                        pass
        except:
            pass
    
    if not clicked:
        return False
    
    # Wait longer for page to update
    await asyncio.sleep(SLEEP_MONTH_NAV)
    
    # Verify month changed
    if current_month:
        try:
            month_element = await tab.find(class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
            if month_element:
                text_result = await month_element.execute_script("return this.textContent")
                new_month = extract_script_value(text_result)
                if new_month != current_month:
                    return True
                else:
                    print(f"Warning: Month didn't change after navigation, still on: {new_month}")
                    return False
        except:
            pass
    
    return True


def is_target_weekday(date_string):
    """Check if a date string matches any of our target weekdays.
    
    Args:
        date_string: Date in format 'YYYY-MM-DD'
    
    Returns:
        tuple: (is_match, weekday_name) or (False, None) if invalid
    """
    try:
        date_obj = datetime.strptime(date_string, '%Y-%m-%d')
        weekday = date_obj.weekday()  # 0=Monday, 6=Sunday
        
        if weekday in TARGET_WEEKDAYS:
            return True, WEEKDAY_NAMES[weekday]
        return False, None
    except:
        return False, None


async def scan_month_days(tab):
    """Scan current month for available days on target weekdays."""
    try:
        month_element = await tab.find(class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if month_element:
            text_result = await month_element.execute_script("return this.textContent")
            current_month = extract_script_value(text_result) or STATUS_UNKNOWN
        else:
            current_month = STATUS_UNKNOWN
    except Exception as e:
        print(f"Error getting month: {e}")
        current_month = STATUS_UNKNOWN
    
    print(f"Scanning: {current_month}")
    
    available_days = []
    
    # Get all td elements with data-join-time (actual date cells)
    try:
        all_cells = await tab.find(tag_name=TAG_TD, find_all=True, raise_exc=False)
        if not all_cells:
            print("No td elements found")
            return available_days
        
        # Filter to cells with data-join-time attribute
        date_cells = []
        for cell in all_cells:
            try:
                attr_result = await cell.execute_script(f"return this.getAttribute('{ATTR_DATA_JOIN_TIME}')")
                date_attr = extract_script_value(attr_result)
                
                if date_attr and date_attr != NONE_STRING and date_attr != 'None':
                    date_cells.append((cell, date_attr))
            except:
                pass
        
        if not date_cells:
            print("No date cells found with data-join-time attribute")
            return available_days
        
        print(f"Found {len(date_cells)} total date cells")
        
        # Filter to target weekdays
        target_cells = []
        for cell, date_str in date_cells:
            is_match, day_name = is_target_weekday(date_str)
            if is_match:
                target_cells.append((cell, date_str, day_name))
        
        target_day_names = [WEEKDAY_NAMES[wd] for wd in TARGET_WEEKDAYS]
        days_str = ", ".join(target_day_names)
        print(f"Found {len(target_cells)} {days_str} date(s)")
        
        # Process each target weekday cell
        for cell, full_date, day_name in target_cells:
            try:
                # Get date text from <p> tag
                date_text = ""
                date_elem = await cell.find(tag_name=TAG_PARAGRAPH, raise_exc=False)
                if date_elem:
                    text_result = await date_elem.execute_script("return this.textContent.trim()")
                    date_text = extract_script_value(text_result) or ''
                
                # Fallback: extract from cell text
                if not date_text:
                    text_result = await cell.execute_script("return this.textContent.trim()")
                    cell_text = extract_script_value(text_result) or ''
                    match = re.search(r'(\d+)', cell_text)
                    if match:
                        date_text = match.group(1)
                
                # Get availability icon from cell text
                text_result = await cell.execute_script("return this.textContent.trim()")
                cell_text = extract_script_value(text_result) or ''
                
                # Check for availability markers
                if ICON_AVAILABLE in cell_text or "○" in cell_text:
                    icon = ICON_AVAILABLE
                    status = STATUS_AVAILABLE
                elif "☓" in cell_text or "×" in cell_text or "X" in cell_text:
                    icon = "×"
                    status = STATUS_FULL
                else:
                    icon = ""
                    status = STATUS_UNKNOWN
                
                print(f"  {date_text}日 ({day_name}): {icon} ({status}) - {full_date}")
                
                if icon == ICON_AVAILABLE:
                    available_days.append({
                        'month': current_month,
                        'date': date_text,
                        'day_name': day_name,
                        'full_date': full_date,
                        'icon': icon,
                    })
            except Exception as e:
                print(f"  Error processing cell: {str(e)[:TEXT_TRUNCATE_LENGTH]}")
    
    except Exception as e:
        print(f"Error scanning calendar: {e}")
    
    return available_days


# ============================================================
# BOOKING AUTOMATION FUNCTIONS
# ============================================================

async def click_date_by_attribute(tab, target_date):
    """Click a date cell by finding it via data-join-time attribute."""
    try:
        print(f"Looking for date cell with data-join-time='{target_date}'...")
        await asyncio.sleep(SLEEP_SHORT)
        
        # Get all td elements
        all_cells = await tab.find(tag_name=TAG_TD, find_all=True, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if not all_cells:
            print("No td elements found")
            return False
        
        # Find the cell with matching data-join-time
        for cell in all_cells:
            try:
                attr_result = await cell.execute_script(f"return this.getAttribute('{ATTR_DATA_JOIN_TIME}')")
                date_attr = extract_script_value(attr_result)
                
                if date_attr == target_date:
                    print(f"Found date cell for {target_date}")
                    await cell.scroll_into_view()
                    await asyncio.sleep(SLEEP_SHORT)
                    await cell.execute_script("this.click()")
                    print(f"Clicked date cell for {target_date}")
                    await asyncio.sleep(SLEEP_STANDARD)  # Wait for navigation
                    return True
            except Exception as e:
                continue
        
        print(f"Could not find date cell for {target_date}")
        return False
    except Exception as e:
        print(f"Error clicking date by attribute: {e}")
        return False


async def verify_on_service_group_page(tab):
    """Verify we're on the service_group_select page."""
    try:
        await asyncio.sleep(SLEEP_SHORT)
        url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
        current_url = extract_script_value(url_response)
        
        if URL_SERVICE_GROUP_SELECT in current_url:
            print(f"✓ On {URL_SERVICE_GROUP_SELECT} page")
            return True
        else:
            print(f"Not on {URL_SERVICE_GROUP_SELECT} page. Current: {current_url[:URL_TRUNCATE_LENGTH]}...")
            return False
    except Exception as e:
        print(f"Error verifying page: {e}")
        return False


async def get_hotel_names_on_service_group_page(tab, skip_blueberry_hill=SKIP_BLUEBERRY_HILL):
    """On service_group_select page, collect all hotel names."""
    print(f"\nOn {URL_SERVICE_GROUP_SELECT} page, collecting hotel names...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    
    if URL_SERVICE_GROUP_SELECT not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return []
    
    hotel_names = []
    
    try:
        await asyncio.sleep(SLEEP_SHORT)
        links = await tab.find(tag_name=TAG_ANCHOR, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if links:
            for link in links:
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = extract_script_value(text_result) or ""
                    
                    href_result = await link.execute_script("return this.href")
                    href = extract_script_value(href_result) or ""
                    
                    # Skip navigation links, empty links, and header links
                    if any(skip in link_text for skip in SKIP_LINK_TEXTS):
                        continue
                    
                    # Skip links with http URLs (we want javascript: links)
                    if href and (PROTOCOL_HTTP in href or PROTOCOL_HTTPS in href):
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
                    continue
        
        print(f"Total hotels: {len(hotel_names)}")
        return hotel_names
    except Exception as e:
        print(f"Error collecting hotel names: {e}")
        return []


async def click_hotel_by_name(tab, hotel_name):
    """Click a hotel link by its name on service_group_select page."""
    print(f"Looking for hotel: {hotel_name[:TEXT_TRUNCATE_LENGTH]}...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    
    if URL_SERVICE_GROUP_SELECT not in current_url:
        print(f"Not on {URL_SERVICE_GROUP_SELECT} page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_SHORT)
        links = await tab.find(tag_name=TAG_ANCHOR, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if links:
            for link in links:
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = extract_script_value(text_result) or ""
                    
                    if link_text == hotel_name:
                        print(f"Clicking hotel: {hotel_name[:TEXT_TRUNCATE_LENGTH]}...")
                        await link.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await link.click()
                        await asyncio.sleep(SLEEP_SHORT)
                        return True
                except Exception as e:
                    continue
        
        print(f"Could not find hotel: {hotel_name[:TEXT_TRUNCATE_LENGTH]}")
        return False
    except Exception as e:
        print(f"Error clicking hotel: {e}")
        return False


async def select_service_on_apply_page(tab):
    """On apply_service_select page, click the service link."""
    print(f"\nOn {URL_APPLY_SERVICE_SELECT} page, looking for service link...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    
    if URL_APPLY_SERVICE_SELECT not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        # Wait longer for page to fully load
        await asyncio.sleep(SLEEP_STANDARD)
        links = await tab.find(tag_name=TAG_ANCHOR, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if links:
            # Collect all valid service links
            service_links = []
            for link in links:
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = extract_script_value(text_result) or ""
                    
                    href_result = await link.execute_script("return this.href")
                    href = extract_script_value(href_result) or ""
                    
                    # Skip navigation and header links
                    if any(skip in link_text for skip in SKIP_LINK_TEXTS_SERVICE):
                        continue
                    
                    # Service link: has javascript: href and substantial text
                    # Can end with 申込 or just be the hotel name
                    if link_text and len(link_text.strip()) > MIN_LINK_TEXT_LENGTH and PROTOCOL_JAVASCRIPT in href:
                        service_links.append((link, link_text))
                except Exception as e:
                    continue
            
            # Try to click the first valid service link
            if service_links:
                link, link_text = service_links[0]
                print(f"Clicking service link: {link_text[:TEXT_TRUNCATE_LENGTH]}...")
                await link.scroll_into_view()
                await asyncio.sleep(SLEEP_STANDARD)  # Wait longer before clicking
                await link.click()
                await asyncio.sleep(SLEEP_STANDARD)  # Wait longer after clicking
                return True
            else:
                print("No valid service links found")
                # Debug: print all links found
                print("Available links:")
                for link in links[:10]:  # Show first 10 links
                    try:
                        text_result = await link.execute_script("return this.textContent.trim()")
                        link_text = extract_script_value(text_result) or ""
                        if link_text and len(link_text) > 2:
                            print(f"  - {link_text[:TEXT_TRUNCATE_LENGTH]}")
                    except:
                        pass
                return False
        
        print("No links found on page")
        return False
    except Exception as e:
        print(f"Error selecting service: {e}")
        return False


async def fill_booking_form_and_search(tab, target_date):
    """Fill in the booking form and search for availability."""
    print(f"\nOn {URL_APPLY_EMPTY_NEW} page, filling booking form...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    
    if URL_APPLY_EMPTY_NEW not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_SHORT)
        print(f"Verifying date matches: {target_date}")
        print(f"Filling in number of guests: {NUM_GUESTS}")
        
        # Find the guest count input by name: apply[stay_persons]
        inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        guest_filled = False
        
        if inputs:
            for input_elem in inputs:
                try:
                    name_result = await input_elem.execute_script("return this.name")
                    input_name = extract_script_value(name_result) or ""
                    
                    # Look for the stay_persons input
                    if INPUT_NAME_STAY_PERSONS in input_name:
                        await input_elem.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await input_elem.execute_script("this.value = ''")
                        await input_elem.insert_text(str(NUM_GUESTS))
                        print(f"Filled guest count: {NUM_GUESTS} in {input_name}")
                        guest_filled = True
                        await asyncio.sleep(SLEEP_SHORT)
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
                    button_value = extract_script_value(value_result) or ""
                    
                    # Exact match for 空き検索
                    if button_value == TEXT_SEARCH_AVAILABILITY:
                        await button.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await button.click()
                        print(f"Clicked {TEXT_SEARCH_AVAILABILITY} button")
                        await asyncio.sleep(SLEEP_SHORT)
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
        await asyncio.sleep(SLEEP_SHORT)
        print("Looking for room checkboxes...")
        all_inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        
        room_selected = False
        if all_inputs:
            # Find checkboxes with name starting with "apply[coma["
            # Skip the first checkbox (name="no-name" with value="on")
            for inp in all_inputs:
                try:
                    type_result = await inp.execute_script("return this.type")
                    input_type = extract_script_value(type_result) or ""
                    
                    if input_type == "checkbox":
                        name_result = await inp.execute_script("return this.name")
                        input_name = extract_script_value(name_result) or ""
                        
                        # Skip the "no-name" checkbox (select all)
                        if input_name == INPUT_NAME_NO_NAME:
                            continue
                        
                        # Look for room checkboxes: name starts with "apply[coma["
                        if input_name and INPUT_NAME_ROOM_PREFIX in input_name:
                            disabled_result = await inp.execute_script("return this.disabled")
                            is_disabled = extract_script_value(disabled_result)
                            
                            if not is_disabled:
                                await inp.scroll_into_view()
                                await asyncio.sleep(SLEEP_SHORT)
                                # Use JavaScript click
                                await inp.execute_script("this.click()")
                                print(f"Selected room checkbox: {input_name}")
                                room_selected = True
                                await asyncio.sleep(SLEEP_SHORT)
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
                    button_value = extract_script_value(value_result) or ""
                    
                    # Exact match for the proceed button
                    if button_value == TEXT_PROCEED_TO_BOOKING:
                        await button.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await button.click()
                        print(f"Clicked {TEXT_PROCEED_TO_BOOKING} button")
                        await asyncio.sleep(SLEEP_SHORT)
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
    current_url = extract_script_value(url_response)
    
    if URL_APPLY_RULE not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_SHORT)
        buttons = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if buttons:
            for button in buttons:
                try:
                    value_result = await button.execute_script("return this.value")
                    button_value = extract_script_value(value_result) or ""
                    
                    if TEXT_AGREE in button_value:
                        await button.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await button.click()
                        print(f"Clicked {TEXT_AGREE}する button")
                        await asyncio.sleep(SLEEP_SHORT)
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
    current_url = extract_script_value(url_response)
    
    if URL_APPLY_EMAIL_INPUT not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_SHORT)
        print(f"Filling email: {TARGET_EMAIL}")
        inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        
        email_filled = False
        if inputs:
            for input_elem in inputs:
                try:
                    name_result = await input_elem.execute_script("return this.name")
                    input_name = extract_script_value(name_result) or ""
                    
                    # Look for email input: name="email"
                    if input_name == INPUT_NAME_EMAIL:
                        await input_elem.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await input_elem.execute_script("this.value = ''")
                        await input_elem.insert_text(TARGET_EMAIL)
                        print(f"Filled email: {TARGET_EMAIL}")
                        email_filled = True
                        await asyncio.sleep(SLEEP_SHORT)
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
        await asyncio.sleep(SLEEP_SHORT)
        
        # Check if we reached send_complete page
        final_url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
        final_url = extract_script_value(final_url_response)
        
        if URL_SEND_COMPLETE in final_url:
            print(f"✓ Reached {URL_SEND_COMPLETE} page: {final_url}")
            return True
        else:
            print(f"Warning: Final URL: {final_url}")
            return True  # May still have succeeded
        
    except Exception as e:
        print(f"Error in email/submit: {e}")
        return False


async def try_book_hotel_for_date(tab, date, hotel):
    """Attempt to book a specific hotel for a date.
    
    Returns:
        bool: True if booking successful, False otherwise
    """
    print(f"\n→ Attempting to book: {date} - {hotel[:TEXT_TRUNCATE_LENGTH]}")
    
    # Click hotel (we should already be on service_group_select page)
    if not await click_hotel_by_name(tab, hotel):
        print("Failed to click hotel")
        return False
    
    if not await select_service_on_apply_page(tab):
        print("Failed to select service")
        return False
    
    if not await fill_booking_form_and_search(tab, date):
        print("Failed to fill form")
        return False
    
    if not await select_room_and_proceed(tab):
        print("No rooms available")
        return False
    
    if not await agree_to_rules(tab):
        print("Failed to agree to rules")
        return False
    
    if not await fill_email_and_submit(tab):
        print("Failed to submit email")
        return False
    
    # Success!
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print(f"✓ BOOKING COMPLETED: {hotel[:TEXT_TRUNCATE_LENGTH]}")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
    
    save_booking(date, hotel)
    return True


async def process_available_day(tab, date_info, calendar_url):
    """Process a single available day: check hotels and attempt booking.
    
    Returns:
        bool: True if a booking was made, False otherwise
    """
    date = date_info['full_date']
    print(f"\nProcessing date: {date} ({date_info['day_name']})")
    
    # Click date to see hotels
    if not await click_date_by_attribute(tab, date):
        print(f"Could not click date {date}")
        return False
    
    if not await verify_on_service_group_page(tab):
        print(f"Not on service group page for {date}")
        return False
    
    # Get hotel names (Blueberry Hills already filtered by get_hotel_names_on_service_group_page)
    hotels = await get_hotel_names_on_service_group_page(tab, skip_blueberry_hill=SKIP_BLUEBERRY_HILL)
    
    if not hotels:
        print("No hotels available")
        return False
    
    # Get already booked hotels for this date
    booked_hotels = get_booked_hotels_for_date(date)
    
    # Filter out already booked hotels
    available_hotels = [h for h in hotels if h not in booked_hotels]
    
    if not available_hotels:
        print(f"All hotels already booked for {date}")
        return False
    
    print(f"Available hotels: {len(available_hotels)}")
    
    # Try to book first available hotel
    for hotel in available_hotels:
        if await try_book_hotel_for_date(tab, date, hotel):
            return True
        
        # Booking failed, return to calendar to try next hotel or next date
        print("Returning to calendar...")
        await tab.go_to(calendar_url)
        await asyncio.sleep(SLEEP_MONTH_NAV)
        
        if not await is_valid_calendar_page(tab):
            print("Failed to return to calendar")
            return False
        
        # Click date again to see hotels for next attempt
        if not await click_date_by_attribute(tab, date):
            print("Failed to navigate back to date")
            return False
    
    return False


async def scan_and_book_one(tab, num_months=NUM_MONTHS_TO_SCAN):
    """Scan all months first, then process available days.
    
    Two-phase approach:
    Phase 1: Scan through ALL months and collect available days
    Phase 2: For each available day, check hotels and attempt booking
    
    Returns:
        bool: True if a booking was made, False otherwise
    """
    target_day_names = [WEEKDAY_NAMES[wd] for wd in TARGET_WEEKDAYS]
    days_str = ", ".join(target_day_names)
    
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print(f"SCANNING {days_str.upper()} FOR {num_months} MONTHS")
    print("FINDING FIRST BOOKING OPPORTUNITY")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH + "\n")
    
    calendar_url = load_cached_url()
    
    # PHASE 1: Scan all months and collect available days
    print("=" * SEPARATOR_WIDTH)
    print("PHASE 1: Scanning all months for available days")
    print("=" * SEPARATOR_WIDTH + "\n")
    
    all_available_days = []
    for month_num in range(num_months):
        print(f"\nMONTH {month_num + 1}/{num_months}")
        print("-" * SEPARATOR_WIDTH)
        
        # Scan current month for available days
        available_days = await scan_month_days(tab)
        
        if available_days:
            # Store days with their month number for later navigation
            for day_info in available_days:
                day_info['month_num'] = month_num
                all_available_days.append(day_info)
        else:
            print("No available days found in this month")
        
        # Move to next month if not last
        if month_num < num_months - 1:
            print("\nNavigating to next month...")
            if not await navigate_to_next_month(tab):
                print("Could not navigate to next month")
                break
    
    # PHASE 2: Process each available day
    print(f"\n\n" + "=" * SEPARATOR_WIDTH)
    print(f"PHASE 2: Processing {len(all_available_days)} available days")
    print("=" * SEPARATOR_WIDTH + "\n")
    
    if not all_available_days:
        print("No available days found in any month")
        return False
    
    for day_info in all_available_days:
        month_num = day_info['month_num']
        date = day_info['full_date']
        
        print(f"\n{'='*SEPARATOR_WIDTH}")
        print(f"Processing: {date} ({day_info['day_name']}) - Month {month_num + 1}")
        print('='*SEPARATOR_WIDTH)
        
        # Return to calendar and navigate to correct month
        await tab.go_to(calendar_url)
        await asyncio.sleep(SLEEP_MONTH_NAV)
        
        if not await is_valid_calendar_page(tab):
            print("Failed to return to calendar")
            continue
        
        # Navigate forward to the correct month
        for i in range(month_num):
            if not await navigate_to_next_month(tab):
                print(f"Failed to navigate to month {month_num + 1}")
                break
        
        # Try to book for this day
        if await process_available_day(tab, day_info, calendar_url):
            print("\n✓ Booking successful!")
            return True
    
    print("\nNo booking opportunities found")
    return False


async def scan_calendar_and_book(calendar_url):
    """Scan calendar and attempt one booking per iteration."""
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print("STARTING BOOKING SCAN")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
    
    options = create_browser_options(headless=False)
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(calendar_url)
        await asyncio.sleep(SLEEP_SHORT)
        
        if not await is_valid_calendar_page(tab):
            raise Exception("Failed to load valid calendar page")
        
        # Scan and book one
        booking_made = await scan_and_book_one(tab, num_months=NUM_MONTHS_TO_SCAN)
        
        if booking_made:
            print("\n✓ Iteration complete: 1 booking made")
        else:
            print("\n✗ Iteration complete: No bookings made")
        
        await asyncio.sleep(SLEEP_SHORT)
        return booking_made


async def scan_once():
    """Perform a single scan iteration."""
    cached_url = load_cached_url()
    
    if cached_url:
        if await validate_cached_url(cached_url):
            await scan_calendar_and_book(cached_url)
            return True
    
    print("\nNeed to acquire new calendar URL")
    new_url = await acquire_calendar_url_with_captcha()
    
    if not new_url:
        print("Failed to acquire calendar URL")
        return False
    
    save_calendar_url(new_url)
    print("\n" + SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print("URL ACQUIRED - STARTING SCAN")
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
    await asyncio.sleep(SLEEP_SHORT)
    await scan_calendar_and_book(new_url)
    return True


async def main():
    """Main execution flow."""
    target_day_names = [WEEKDAY_NAMES[wd] for wd in TARGET_WEEKDAYS]
    days_str = ", ".join(target_day_names)
    
    print(SEPARATOR_CHAR * SEPARATOR_WIDTH)
    print("ITS CALENDAR SCANNER - CONTINUOUS MODE")
    print(f"Target Days: {days_str}")
    print(f"Auto-booking: {'ENABLED' if AUTO_BOOK else 'DISABLED'}")
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