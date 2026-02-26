# -*- coding: utf-8 -*-
"""Navigation and CAPTCHA handling for ITS Calendar Scanner."""

import asyncio
from pydoll.browser.chromium import Chrome
from browser import create_browser_options, extract_script_value
from config import (
    MAIN_URL,
    TAG_ANCHOR,
    TAG_TD,
    TAG_INPUT,
    CLASS_MONTH,
    ATTR_DATA_JOIN_TIME,
    RECAPTCHA_IFRAME_SELECTOR,
    FORM_SUBMIT_SCRIPT,
    WINDOW_LOCATION_SCRIPT,
    URL_CALENDAR_APPLY,
    URL_CALENDAR_SELECT,
    TEXT_CALENDAR_SEARCH,
    TEXT_NEXT_BUTTON,
    SLEEP_SHORT,
    DEFAULT_TIMEOUT,
    EXTENDED_TIMEOUT,
    SCROLL_DOWN_DISTANCE,
    SCROLL_UP_DISTANCE,
    SEPARATOR_WIDTH,
    LOG_ARROW,
    LOG_SUCCESS,
    LOG_ERROR,
    LOG_WARNING,
    LOG_EQUALS,
)


async def is_valid_calendar_page(tab):
    """Check if current page is a valid calendar page.

    Args:
        tab: Browser tab instance

    Returns:
        bool: True if valid calendar page
    """
    try:
        month_element = await tab.find(
            class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False
        )
        if month_element is None:
            return False

        all_cells = await tab.find(
            tag_name=TAG_TD, find_all=True, timeout=DEFAULT_TIMEOUT, raise_exc=False
        )
        if all_cells:
            for cell in all_cells[:10]:
                try:
                    attr_result = await cell.execute_script(
                        f"return this.getAttribute('{ATTR_DATA_JOIN_TIME}')"
                    )
                    date_attr = extract_script_value(attr_result)
                    if date_attr and date_attr not in ["None", None]:
                        return True
                except:
                    pass
        return False
    except:
        return False


async def navigate_to_calendar_link(tab):
    """Navigate from main page to calendar CAPTCHA page.

    Args:
        tab: Browser tab instance
    """
    print(f"{LOG_ARROW} Navigating to {MAIN_URL}")
    await tab.go_to(MAIN_URL)
    await asyncio.sleep(SLEEP_SHORT)

    print(f"{LOG_ARROW} Looking for calendar link...")

    calendar_link = None

    try:
        calendar_link = await tab.find(
            text=TEXT_CALENDAR_SEARCH, timeout=EXTENDED_TIMEOUT, raise_exc=False
        )
    except:
        pass

    if not calendar_link:
        try:
            links = await tab.find(
                tag_name=TAG_ANCHOR,
                find_all=True,
                timeout=EXTENDED_TIMEOUT,
                raise_exc=False,
            )
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

    print(f"{LOG_ARROW} Clicking calendar link...")
    await calendar_link.click()
    await asyncio.sleep(SLEEP_SHORT)

    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    if URL_CALENDAR_APPLY not in current_url:
        raise Exception(f"Not on CAPTCHA page. URL: {current_url}")

    print(f"{LOG_SUCCESS} On CAPTCHA page")


async def bypass_captcha_and_proceed(tab):
    """Bypass CAPTCHA and proceed to calendar.

    Args:
        tab: Browser tab instance

    Returns:
        str: Calendar URL
    """
    print(f"{LOG_ARROW} Bypassing CAPTCHA...")

    await asyncio.sleep(SLEEP_SHORT)

    # Simulate natural behavior
    try:
        from pydoll.constants import ScrollPosition

        await tab.scroll.by(ScrollPosition.DOWN, SCROLL_DOWN_DISTANCE, smooth=True)
        await asyncio.sleep(SLEEP_SHORT)
        await tab.scroll.by(ScrollPosition.UP, SCROLL_UP_DISTANCE, smooth=True)
        await asyncio.sleep(SLEEP_SHORT)
    except Exception as e:
        print(f"{LOG_WARNING} Behavior simulation: {e}")

    # Click reCAPTCHA
    try:
        recaptcha_iframe = await tab.query(
            RECAPTCHA_IFRAME_SELECTOR, timeout=EXTENDED_TIMEOUT, raise_exc=False
        )

        if recaptcha_iframe:
            await asyncio.sleep(SLEEP_SHORT)
            try:
                await recaptcha_iframe.scroll_into_view()
                await asyncio.sleep(SLEEP_SHORT)
                await recaptcha_iframe.click()
                await asyncio.sleep(SLEEP_SHORT)
            except Exception as e:
                print(f"{LOG_WARNING} CAPTCHA click: {e}")

        await asyncio.sleep(SLEEP_SHORT)
    except Exception as e:
        print(f"{LOG_WARNING} CAPTCHA interaction: {e}")

    print(f"{LOG_ARROW} Clicking {TEXT_NEXT_BUTTON}...")
    await asyncio.sleep(SLEEP_SHORT)

    try:
        buttons = await tab.find(
            tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False
        )
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
        else:
            await tab.execute_script(FORM_SUBMIT_SCRIPT)
    except Exception as e:
        print(f"{LOG_WARNING} Form submit: {e}")
        await tab.execute_script(FORM_SUBMIT_SCRIPT)

    await asyncio.sleep(SLEEP_SHORT)

    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    calendar_url = extract_script_value(url_response)

    if URL_CALENDAR_SELECT not in calendar_url:
        raise Exception(f"Not on calendar page. URL: {calendar_url}")

    print(f"{LOG_SUCCESS} Reached calendar page")
    return calendar_url


async def acquire_calendar_url_with_captcha():
    """Get calendar URL by bypassing CAPTCHA.

    Returns:
        str: Calendar URL or None
    """
    print(f"\n{LOG_EQUALS * SEPARATOR_WIDTH}")
    print("ACQUIRING NEW CALENDAR URL")
    print(f"{LOG_EQUALS * SEPARATOR_WIDTH}")

    options = create_browser_options(headless=False)
    async with Chrome(options=options) as browser:
        try:
            tab = await browser.start()
            await navigate_to_calendar_link(tab)
            calendar_url = await bypass_captcha_and_proceed(tab)
            return calendar_url
        except Exception as e:
            print(f"{LOG_ERROR} Error acquiring URL: {e}")
            return None
