# -*- coding: utf-8 -*-
import asyncio
import os
from datetime import datetime
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions

# Configuration
CALENDAR_URL_CACHE = "calendar_url_cache.txt"
MAIN_URL = "https://as.its-kenpo.or.jp"
SCAN_INTERVAL_SECONDS = 10  # Check every 1 minute
TARGET_EMAIL = "waylonh@apple.com"
NUM_GUESTS = 2

# Day of week configuration (for testing)
# Use "td-sun" for Sunday, "td-sat" for Saturday
TARGET_DAY_CLASS = "td-sat"  # Testing with Sunday (change to "td-sat" for Saturday)
TARGET_DAY_NAME = "Sunday" if TARGET_DAY_CLASS == "td-sun" else "Saturday"

# Booking mode
AUTO_BOOK = True  # Set to True to automatically attempt booking when available dates found
SCAN_ONLY = False  # Set to True to only scan without booking
MANUAL_MODE = False  # Set to True for manual interaction/testing

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
    # Let pydoll auto-detect Chrome path
    
    if headless:
        options.headless = True
        print("Running in HEADLESS mode")
    else:
        print("Running in NORMAL mode (browser visible)")
    
    for argument in CHROME_ARGUMENTS:
        options.add_argument(argument)
    
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
        day_cells = await tab.find(tag_name="td", class_name=TARGET_DAY_CLASS, find_all=True, timeout=3, raise_exc=False)
        return month_element is not None and day_cells is not None and len(day_cells) > 0
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
    
    calendar_link = None
    
    # Try finding by text first
    try:
        calendar_link = await tab.find(text="カレンダーから探す", timeout=5, raise_exc=False)
    except:
        pass
    
    # Try finding by href attribute
    if not calendar_link:
        try:
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
    
    url_response = await tab.execute_script("return window.location.href")
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    if "calendar_apply" not in current_url:
        raise Exception(f"Not on expected CAPTCHA page. Current URL: {current_url}")
    
    print("On CAPTCHA verification page")


async def bypass_captcha_and_proceed(tab):
    """Bypass CAPTCHA by clicking checkbox and proceeding."""
    print("Bypassing CAPTCHA with pydoll's stealth capabilities...")
    
    await asyncio.sleep(3)
    print("Waiting for CAPTCHA to load...")
    await asyncio.sleep(5)
    
    # Simulate natural behavior
    try:
        print("Simulating natural user behavior...")
        from pydoll.constants import ScrollPosition
        
        await tab.scroll.by(ScrollPosition.DOWN, 100, smooth=True)
        await asyncio.sleep(1.5)
        await tab.scroll.by(ScrollPosition.UP, 50, smooth=True)
        await asyncio.sleep(1)
        print("Human-like behavior simulation complete")
    except Exception as e:
        print(f"Note: Behavioral simulation: {e}")
    
    # Click reCAPTCHA
    try:
        print("Looking for reCAPTCHA checkbox...")
        recaptcha_iframe = await tab.query('iframe[src*="recaptcha/api2/anchor"]', timeout=5, raise_exc=False)
        
        if recaptcha_iframe:
            print("Found reCAPTCHA checkbox iframe")
            await asyncio.sleep(2)
            
            try:
                await recaptcha_iframe.scroll_into_view()
                await asyncio.sleep(1)
                await recaptcha_iframe.click()
                print("Clicked reCAPTCHA iframe area")
                await asyncio.sleep(3)
            except Exception as click_err:
                print(f"Could not click iframe directly: {click_err}")
            
            print("Waiting for captcha to be solved...")
            await asyncio.sleep(5)
        else:
            print("No reCAPTCHA iframe found, proceeding anyway...")
    except Exception as e:
        print(f"Note: reCAPTCHA interaction: {e}")
    
    # Click next button
    print("Looking for 次へ (Next) button...")
    await asyncio.sleep(2)
    
    try:
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
            await next_button.scroll_into_view()
            await asyncio.sleep(1)
            await next_button.click()
            print("Clicked 次へ button")
        else:
            await tab.execute_script("document.querySelector('form').submit();")
            print("Submitted form via JavaScript")
    except Exception as e:
        print(f"Error: {e}")
        await tab.execute_script("document.querySelector('form').submit();")
        print("Submitted form via JavaScript (fallback)")
    
    await asyncio.sleep(4)
    
    url_response = await tab.execute_script("return window.location.href")
    calendar_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    print(f"Navigated to: {calendar_url}")
    
    if "calendar_select" not in calendar_url:
        raise Exception(f"Not on expected calendar page. Current URL: {calendar_url}")
    
    print("Successfully reached calendar page")
    return calendar_url


async def acquire_calendar_url_with_captcha():
    """Get calendar URL by bypassing CAPTCHA."""
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
    try:
        next_button = await tab.find(id="nextMonth", timeout=3, raise_exc=False)
        if next_button:
            await next_button.click()
            await asyncio.sleep(3)
            return True
    except:
        pass
    
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


async def scan_month_days(tab):
    """Scan current month for available days."""
    try:
        month_element = await tab.find(class_name="month", timeout=3, raise_exc=False)
        if month_element:
            text_result = await month_element.execute_script("return this.textContent")
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
    
    available_days = []
    
    try:
        day_cells = await tab.find(tag_name="td", class_name=TARGET_DAY_CLASS, find_all=True, raise_exc=False)
        if not day_cells:
            print(f"Found 0 {TARGET_DAY_NAME}(s)")
            return available_days
        
        print(f"Found {len(day_cells)} {TARGET_DAY_NAME}(s)")
        
        for cell in day_cells:
            try:
                # Get date text
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
                
                # Get icon
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
                
                # Get full date
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
                    available_days.append({
                        'month': current_month,
                        'date': date_text,
                        'full_date': full_date,
                        'icon': icon,
                        'cell': cell  # Store cell reference for booking
                    })
            except Exception as e:
                print(f"  Error processing cell: {str(e)[:50]}")
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
        await asyncio.sleep(1)
        # Use JavaScript click to avoid visibility issues
        await cell.execute_script("this.click()")
        print("Clicked date cell")
        await asyncio.sleep(3)
        return True
    except Exception as e:
        print(f"Error clicking date cell: {e}")
        return False


async def select_hotel_on_service_group_page(tab):
    """On service_group_select page, click the hotel link (javascript: link with hotel name)."""
    print("\nOn service_group_select page, looking for hotel link...")
    
    url_response = await tab.execute_script("return window.location.href")
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if "service_group_select" not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(2)
        links = await tab.find(tag_name="a", find_all=True, timeout=5, raise_exc=False)
        if links:
            for link in links:
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = text_result['result']['result']['value'] if isinstance(text_result, dict) and 'result' in text_result else ""
                    
                    href_result = await link.execute_script("return this.href")
                    href = href_result['result']['result']['value'] if isinstance(href_result, dict) and 'result' in href_result else ""
                    
                    # Skip navigation links, empty links, and header links
                    skip_texts = ["ページ先頭", "関東ITソフトウェア", "健康保険組合", "公式サイト"]
                    if any(skip in link_text for skip in skip_texts):
                        continue
                    
                    # Skip links with http URLs or .html files (we want javascript: links)
                    if href and ("http://" in href or "https://" in href or href.endswith(".html")):
                        continue
                    
                    # Hotel link has substantial text and javascript: href
                    if link_text and len(link_text.strip()) > 3 and "javascript:" in href:
                        print(f"Clicking hotel link: {link_text[:50]}...")
                        await link.scroll_into_view()
                        await asyncio.sleep(1)
                        await link.click()
                        await asyncio.sleep(3)
                        return True
                except Exception as e:
                    print(f"Error checking link: {e}")
                    pass
        
        print("Could not find hotel link")
        return False
    except Exception as e:
        print(f"Error selecting hotel: {e}")
        return False


async def select_service_on_apply_page(tab):
    """On apply_service_select page, click the service link (ends with 申込)."""
    print("\nOn apply_service_select page, looking for service link...")
    
    url_response = await tab.execute_script("return window.location.href")
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if "apply_service_select" not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(2)
        links = await tab.find(tag_name="a", find_all=True, timeout=5, raise_exc=False)
        if links:
            for link in links:
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = text_result['result']['result']['value'] if isinstance(text_result, dict) and 'result' in text_result else ""
                    
                    href_result = await link.execute_script("return this.href")
                    href = href_result['result']['result']['value'] if isinstance(href_result, dict) and 'result' in href_result else ""
                    
                    # Skip navigation and header links
                    skip_texts = ["ページ先頭", "関東ITソフトウェア", "健康保険組合"]
                    if any(skip in link_text for skip in skip_texts):
                        continue
                    
                    # Service link ends with 申込 and has javascript: href
                    if link_text and "申込" in link_text and "javascript:" in href:
                        print(f"Clicking service link: {link_text[:50]}...")
                        await link.scroll_into_view()
                        await asyncio.sleep(1)
                        await link.click()
                        await asyncio.sleep(3)
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
    print("\nOn empty_new page, filling booking form...")
    
    url_response = await tab.execute_script("return window.location.href")
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if "apply/empty_new" not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(2)
        print(f"Verifying date matches: {target_date}")
        print(f"Filling in number of guests: {NUM_GUESTS}")
        
        # Find the guest count input by name: apply[stay_persons]
        inputs = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        guest_filled = False
        
        if inputs:
            for input_elem in inputs:
                try:
                    name_result = await input_elem.execute_script("return this.name")
                    input_name = name_result['result']['result']['value'] if isinstance(name_result, dict) and 'result' in name_result else ""
                    
                    # Look for the stay_persons input
                    if "stay_persons" in input_name:
                        await input_elem.scroll_into_view()
                        await asyncio.sleep(0.5)
                        await input_elem.execute_script("this.value = ''")
                        await input_elem.insert_text(str(NUM_GUESTS))
                        print(f"Filled guest count: {NUM_GUESTS} in {input_name}")
                        guest_filled = True
                        await asyncio.sleep(1)
                        break
                except:
                    pass
        
        if not guest_filled:
            print("Warning: Could not fill guest count")
        
        # Click 空き検索 button
        print("Looking for 空き検索 button...")
        buttons = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        if buttons:
            for button in buttons:
                try:
                    value_result = await button.execute_script("return this.value")
                    button_value = value_result['result']['result']['value'] if isinstance(value_result, dict) and 'result' in value_result else ""
                    
                    # Exact match for 空き検索
                    if button_value == "空き検索":
                        await button.scroll_into_view()
                        await asyncio.sleep(1)
                        await button.click()
                        print("Clicked 空き検索 button")
                        await asyncio.sleep(4)
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
        await asyncio.sleep(2)
        print("Looking for room checkboxes...")
        all_inputs = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        
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
                        if input_name == "no-name":
                            continue
                        
                        # Look for room checkboxes: name starts with "apply[coma["
                        if input_name and "apply[coma[" in input_name:
                            disabled_result = await inp.execute_script("return this.disabled")
                            is_disabled = disabled_result['result']['result']['value'] if isinstance(disabled_result, dict) and 'result' in disabled_result else True
                            
                            if not is_disabled:
                                await inp.scroll_into_view()
                                await asyncio.sleep(1)
                                # Use JavaScript click
                                await inp.execute_script("this.click()")
                                print(f"Selected room checkbox: {input_name}")
                                room_selected = True
                                await asyncio.sleep(1)
                                break
                except:
                    pass
        
        if not room_selected:
            print("No room checkbox found")
            return False
        
        # Click 予約手続きに進む button (exact match)
        print("Looking for 予約手続きに進む button...")
        buttons = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        if buttons:
            for button in buttons:
                try:
                    value_result = await button.execute_script("return this.value")
                    button_value = value_result['result']['result']['value'] if isinstance(value_result, dict) and 'result' in value_result else ""
                    
                    # Exact match for the proceed button
                    if button_value == "予約手続きに進む":
                        await button.scroll_into_view()
                        await asyncio.sleep(1)
                        await button.click()
                        print("Clicked 予約手続きに進む button")
                        await asyncio.sleep(3)
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
    print("\nOn rule page, clicking 同意する...")
    
    url_response = await tab.execute_script("return window.location.href")
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if "apply/rule" not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(2)
        buttons = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        if buttons:
            for button in buttons:
                try:
                    value_result = await button.execute_script("return this.value")
                    button_value = value_result['result']['result']['value'] if isinstance(value_result, dict) and 'result' in value_result else ""
                    
                    if "同意" in button_value:
                        await button.scroll_into_view()
                        await asyncio.sleep(1)
                        await button.click()
                        print("Clicked 同意する button")
                        await asyncio.sleep(3)
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
    print("\nOn email_input page, filling email and submitting...")
    
    url_response = await tab.execute_script("return window.location.href")
    current_url = url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response
    
    if "apply/email_input" not in current_url:
        print(f"Not on expected page. Current URL: {current_url}")
        return False
    
    try:
        await asyncio.sleep(2)
        print(f"Filling email: {TARGET_EMAIL}")
        inputs = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        
        email_filled = False
        if inputs:
            for input_elem in inputs:
                try:
                    name_result = await input_elem.execute_script("return this.name")
                    input_name = name_result['result']['result']['value'] if isinstance(name_result, dict) and 'result' in name_result else ""
                    
                    # Look for email input: name="email"
                    if input_name == "email":
                        await input_elem.scroll_into_view()
                        await asyncio.sleep(0.5)
                        await input_elem.execute_script("this.value = ''")
                        await input_elem.insert_text(TARGET_EMAIL)
                        print(f"Filled email: {TARGET_EMAIL}")
                        email_filled = True
                        await asyncio.sleep(1)
                        break
                except:
                    pass
        
        if not email_filled:
            print("Warning: Could not fill email")
            return False
        
        # Click 送信 button and handle confirmation dialog
        print("Clicking 送信 button...")
        
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
        await asyncio.sleep(0.5)
        
        # Submit form (triggers dialog)
        try:
            await tab.execute_script("document.querySelector('form').submit();")
            print("Submitted form")
        except:
            pass
        
        # Wait for dialog handling and navigation
        await asyncio.sleep(5)
        
        # Check if we reached send_complete page
        final_url_response = await tab.execute_script("return window.location.href")
        final_url = final_url_response['result']['result']['value'] if isinstance(final_url_response, dict) else final_url_response
        
        if "send_complete" in final_url:
            print(f"✓ Reached send_complete page: {final_url}")
            return True
        else:
            print(f"Warning: Final URL: {final_url}")
            return True  # May still have succeeded
        
    except Exception as e:
        print(f"Error in email/submit: {e}")
        return False


async def process_booking_for_date(tab, date_info):
    """Process complete booking flow for a date."""
    print("\n" + "="*60)
    print(f"BOOKING: {date_info['date']}日 ({date_info['full_date']})")
    print("="*60)
    
    try:
        if not await click_available_date_cell(date_info['cell']):
            return False
        
        if not await select_hotel_on_service_group_page(tab):
            return False
        
        if not await select_service_on_apply_page(tab):
            return False
        
        if not await fill_booking_form_and_search(tab, date_info['full_date']):
            return False
        
        if not await select_room_and_proceed(tab):
            return False
        
        if not await agree_to_rules(tab):
            return False
        
        if not await fill_email_and_submit(tab):
            return False
        
        print("\n" + "="*60)
        print("BOOKING COMPLETED SUCCESSFULLY!")
        print("="*60)
        return True
    except Exception as e:
        print(f"Error in booking: {e}")
        import traceback
        traceback.print_exc()
        return False


async def scan_calendar(tab, num_months=3, attempt_booking=False):
    """Scan calendar for available dates."""
    print("\n" + "="*60)
    print(f"SCANNING {TARGET_DAY_NAME.upper()}S FOR {num_months} MONTHS")
    if attempt_booking:
        print("AUTO-BOOKING ENABLED")
    print("="*60 + "\n")
    
    all_available = []
    
    for month_num in range(num_months):
        print(f"Month {month_num + 1}/{num_months}")
        print("-"*60)
        
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
                    await tab.execute_script("window.history.go(-5);")  # Go back 5 steps
                    await asyncio.sleep(3)
        
        if month_num < num_months - 1:
            print("Navigating to next month...\n")
            if not await navigate_to_next_month(tab):
                print("Could not navigate to next month")
                break
    
    return all_available


def print_summary(available_dates):
    """Print summary of available dates."""
    print("\n" + "="*60)
    print(f"SUMMARY: Available {TARGET_DAY_NAME}s")
    print("="*60)
    
    if available_dates:
        print(f"\nFound {len(available_dates)} available {TARGET_DAY_NAME}(s):\n")
        for date in available_dates:
            print(f"  {date['month']} - {date['date']}日 ({date['full_date']})")
    else:
        print(f"\nNo available {TARGET_DAY_NAME}s found")
    
    print("\n" + "="*60 + "\n")


async def scan_calendar_headless(calendar_url):
    """Scan calendar in headless mode."""
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
        
        available_dates = await scan_calendar(tab, num_months=3, attempt_booking=(AUTO_BOOK and not SCAN_ONLY))
        print_summary(available_dates)
        
        print("Scan complete")
        await asyncio.sleep(2)


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
    print("\n" + "="*60)
    print("URL ACQUIRED - RESTARTING IN HEADLESS MODE")
    print("="*60)
    await asyncio.sleep(2)
    await scan_calendar_headless(new_url)
    return True


async def main():
    """Main execution flow."""
    print("="*60)
    print("ITS CALENDAR SCANNER - CONTINUOUS MODE")
    print(f"Target: {TARGET_DAY_NAME}s")
    print(f"Auto-booking: {'ENABLED' if AUTO_BOOK and not SCAN_ONLY else 'DISABLED'}")
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
            
            print(f"\n[{timestamp}] Waiting {SCAN_INTERVAL_SECONDS} seconds...")
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        print("\n\n" + "="*60)
        print("SCANNER STOPPED BY USER")
        print(f"Total iterations: {iteration}")
        print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
    print("Done!")