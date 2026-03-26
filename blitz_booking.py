# -*- coding: utf-8 -*-
"""Streamlined fast booking for blitz mode.

Uses JavaScript-heavy operations to minimize browser round-trips.
Each function does as much as possible in a single JS call.
"""

import asyncio
from browser import extract_script_value
from cache import save_booking, get_booked_hotels_for_date
from config import (
    NUM_GUESTS,
    TARGET_EMAIL,
    SKIP_HOTELS,
    SKIP_LINK_TEXTS,
    SKIP_LINK_TEXTS_SERVICE,
    INPUT_NAME_ROOM_PREFIX,
    INPUT_NAME_NO_NAME,
    URL_SERVICE_GROUP_SELECT,
    URL_APPLY_SERVICE_SELECT,
    URL_APPLY_EMPTY_NEW,
    URL_APPLY_RULE,
    URL_APPLY_EMAIL_INPUT,
    URL_SEND_COMPLETE,
    WINDOW_LOCATION_SCRIPT,
    FORM_SUBMIT_SCRIPT,
    PROTOCOL_JAVASCRIPT,
    TEXT_SEARCH_AVAILABILITY,
    TEXT_PROCEED_TO_BOOKING,
    TEXT_AGREE,
    SEPARATOR_WIDTH,
    LOG_ARROW,
    LOG_SUCCESS,
    LOG_ERROR,
    LOG_WARNING,
    LOG_SKIP,
    LOG_SEPARATOR,
    LOG_EQUALS,
)

# Blitz mode uses shorter waits
BLITZ_WAIT = 0.1
BLITZ_WAIT_LONG = 0.3

# JS: Click a date cell by data-join-time attribute
CLICK_DATE_JS = """
const target = '{date}';
const cells = document.querySelectorAll('td[data-join-time]');
for (const cell of cells) {{
    if (cell.getAttribute('data-join-time') === target) {{
        cell.click();
        return true;
    }}
}}
return false;
"""

# JS: Get all hotel names on service_group_select page (batch)
GET_HOTELS_JS = """
const skipTexts = {skip_texts};
const skipHotels = {skip_hotels};
const results = [];
const links = document.querySelectorAll('a');
for (const link of links) {{
    const text = link.textContent.trim();
    const href = link.href || '';
    if (skipTexts.some(s => text.includes(s))) continue;
    if (href.startsWith('http://') || href.startsWith('https://')) continue;
    if (text.length > 3 && href.includes('javascript:')) {{
        const skipped = skipHotels.some(s => text.includes(s));
        results.push({{ name: text, skipped }});
    }}
}}
return JSON.stringify(results);
"""

# JS: Click a hotel by name
CLICK_HOTEL_JS = """
const target = `{hotel_name}`;
const links = document.querySelectorAll('a');
for (const link of links) {{
    if (link.textContent.trim() === target) {{
        link.click();
        return true;
    }}
}}
return false;
"""

# JS: Click first service link on apply_service_select page
CLICK_SERVICE_JS = """
const skipTexts = {skip_texts};
const links = document.querySelectorAll('a');
for (const link of links) {{
    const text = link.textContent.trim();
    const href = link.href || '';
    if (skipTexts.some(s => text.includes(s))) continue;
    if (text.length > 3 && href.includes('javascript:')) {{
        link.click();
        return text;
    }}
}}
return null;
"""

# JS: Fill guest count and click search
FILL_AND_SEARCH_JS = """
const inputs = document.querySelectorAll('input');
let filled = false;
for (const inp of inputs) {{
    if (inp.name && inp.name.includes('stay_persons')) {{
        inp.value = '{guests}';
        filled = true;
        break;
    }}
}}
for (const inp of inputs) {{
    if (inp.value === '空き検索') {{
        inp.click();
        return filled ? 'ok' : 'search_only';
    }}
}}
return filled ? 'no_button' : 'nothing';
"""

# JS: Select first available room checkbox and click proceed
SELECT_ROOM_JS = """
const inputs = document.querySelectorAll('input');
let roomSelected = false;
for (const inp of inputs) {
    if (inp.type === 'checkbox' && inp.name !== 'no-name'
        && inp.name && inp.name.includes('apply[coma[') && !inp.disabled) {
        inp.click();
        roomSelected = true;
        break;
    }
}
if (!roomSelected) return 'no_room';
for (const inp of inputs) {
    if (inp.value === '予約手続きに進む') {
        inp.click();
        return 'ok';
    }
}
return 'no_proceed_button';
"""

# JS: Click agree button
CLICK_AGREE_JS = """
const inputs = document.querySelectorAll('input');
for (const inp of inputs) {
    if (inp.value && inp.value.includes('同意')) {
        inp.click();
        return true;
    }
}
return false;
"""

# JS: Fill email field
FILL_EMAIL_JS = """
const target = '{email}';
const inputs = document.querySelectorAll('input');
for (const inp of inputs) {{
    if (inp.name === 'email') {{
        inp.value = target;
        return true;
    }}
}}
return false;
"""


async def fast_click_date(tab, target_date):
    """Click date cell using single JS call."""
    js = CLICK_DATE_JS.replace("{date}", target_date)
    result = await tab.execute_script(js)
    clicked = extract_script_value(result)
    if not clicked:
        print(f"  {LOG_ERROR} Date not found: {target_date}")
    return bool(clicked)


async def fast_get_hotels(tab):
    """Get all hotel names in a single JS call."""
    import json

    url_resp = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_resp) or ""
    if URL_SERVICE_GROUP_SELECT not in current_url:
        print(f"  {LOG_ERROR} Not on service_group_select page")
        return []

    skip_texts_json = json.dumps(SKIP_LINK_TEXTS)
    skip_hotels_json = json.dumps(SKIP_HOTELS)
    js = GET_HOTELS_JS.format(skip_texts=skip_texts_json, skip_hotels=skip_hotels_json)

    result = await tab.execute_script(js)
    raw = extract_script_value(result)
    if not raw:
        return []

    try:
        hotels_data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

    available = []
    for h in hotels_data:
        name = h.get("name", "")
        if h.get("skipped"):
            print(f"  {LOG_SKIP} {name[:50]}")
        else:
            print(f"  • {name[:50]}")
            available.append(name)

    print(f"  Total: {len(available)} hotels")
    return available


async def fast_click_hotel(tab, hotel_name):
    """Click hotel by name using single JS call."""
    js = CLICK_HOTEL_JS.replace("{hotel_name}", hotel_name.replace("`", "\\`"))
    result = await tab.execute_script(js)
    return bool(extract_script_value(result))


async def fast_select_service(tab):
    """Select first service link using single JS call."""
    import json

    url_resp = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_resp) or ""
    if URL_APPLY_SERVICE_SELECT not in current_url:
        print(f"  {LOG_ERROR} Not on service_select page")
        return False

    skip_texts_json = json.dumps(SKIP_LINK_TEXTS_SERVICE)
    js = CLICK_SERVICE_JS.format(skip_texts=skip_texts_json)
    result = await tab.execute_script(js)
    service_name = extract_script_value(result)
    if service_name:
        print(f"  {LOG_ARROW} Service: {str(service_name)[:50]}")
        return True
    print(f"  {LOG_ERROR} No service link found")
    return False


async def fast_fill_and_search(tab):
    """Fill guest count and click search in single JS call."""
    url_resp = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_resp) or ""
    if URL_APPLY_EMPTY_NEW not in current_url:
        print(f"  {LOG_ERROR} Not on empty_new page")
        return False

    js = FILL_AND_SEARCH_JS.replace("{guests}", str(NUM_GUESTS))
    result = await tab.execute_script(js)
    status = extract_script_value(result)
    if status in ("ok", "search_only"):
        if status == "search_only":
            print(f"  {LOG_WARNING} Guest count field not found")
        return True
    print(f"  {LOG_ERROR} Fill+search failed: {status}")
    return False


async def fast_select_room(tab):
    """Select room and proceed in single JS call."""
    result = await tab.execute_script(SELECT_ROOM_JS)
    status = extract_script_value(result)
    if status == "ok":
        return True
    print(f"  {LOG_ERROR} Room select: {status}")
    return False


async def fast_agree(tab):
    """Agree to rules using single JS call."""
    url_resp = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_resp) or ""
    if URL_APPLY_RULE not in current_url:
        print(f"  {LOG_ERROR} Not on rule page")
        return False

    result = await tab.execute_script(CLICK_AGREE_JS)
    return bool(extract_script_value(result))


async def fast_email_and_submit(tab):
    """Fill email and submit form."""
    url_resp = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_resp) or ""
    if URL_APPLY_EMAIL_INPUT not in current_url:
        print(f"  {LOG_ERROR} Not on email page")
        return False

    js = FILL_EMAIL_JS.replace("{email}", TARGET_EMAIL)
    result = await tab.execute_script(js)
    if not extract_script_value(result):
        print(f"  {LOG_ERROR} Email fill failed")
        return False

    # Set up dialog handler and submit
    from pydoll.protocol.page.events import PageEvent

    async def handle_dialog(event):
        await tab.handle_dialog(accept=True)

    await tab.enable_page_events()
    await tab.on(PageEvent.JAVASCRIPT_DIALOG_OPENING, handle_dialog)
    await asyncio.sleep(BLITZ_WAIT)
    await tab.execute_script(FORM_SUBMIT_SCRIPT)
    await asyncio.sleep(BLITZ_WAIT)

    final_resp = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    final_url = extract_script_value(final_resp) or ""
    if URL_SEND_COMPLETE in final_url:
        print(f"  {LOG_SUCCESS} Booking confirmed!")
    return True


async def fast_book_hotel(tab, date, hotel_name):
    """Execute full booking flow for a single hotel with minimal waits.

    Each step uses a single JS call. Waits are only inserted where
    page navigation actually occurs.

    Returns:
        bool: True if booking completed
    """
    print(f"\n{LOG_SEPARATOR * SEPARATOR_WIDTH}")
    print(f"BLITZ BOOK: {date} - {hotel_name[:50]}")
    print(f"{LOG_SEPARATOR * SEPARATOR_WIDTH}")

    # Step 1: Click hotel
    if not await fast_click_hotel(tab, hotel_name):
        print(f"  {LOG_ERROR} Hotel click failed")
        return False
    await asyncio.sleep(BLITZ_WAIT_LONG)  # page nav

    # Step 2: Select service
    if not await fast_select_service(tab):
        return False
    await asyncio.sleep(BLITZ_WAIT_LONG)  # page nav

    # Step 3: Fill form and search
    if not await fast_fill_and_search(tab):
        return False
    await asyncio.sleep(BLITZ_WAIT_LONG)  # page nav

    # Step 4: Select room and proceed
    if not await fast_select_room(tab):
        return False
    await asyncio.sleep(BLITZ_WAIT_LONG)  # page nav

    # Step 5: Agree to rules
    if not await fast_agree(tab):
        return False
    await asyncio.sleep(BLITZ_WAIT_LONG)  # page nav

    # Step 6: Email and submit
    if not await fast_email_and_submit(tab):
        return False

    print(f"\n{LOG_EQUALS * SEPARATOR_WIDTH}")
    print(f"{LOG_SUCCESS} BLITZ BOOKING COMPLETE: {hotel_name[:50]}")
    print(f"{LOG_EQUALS * SEPARATOR_WIDTH}")

    save_booking(date, hotel_name)
    return True


async def fast_process_day(tab, date_info, calendar_url):
    """Process an available day - click date, get hotels, book first available.

    Args:
        tab: Browser tab
        date_info: Day info dict from scanner
        calendar_url: Calendar URL for navigation

    Returns:
        bool: True if a booking was made
    """
    date = date_info["full_date"]
    print(f"\n{LOG_ARROW} Processing: {date} ({date_info['day_name']})")

    # Click the date
    if not await fast_click_date(tab, date):
        return False
    await asyncio.sleep(BLITZ_WAIT_LONG)

    # Get hotels
    hotels = await fast_get_hotels(tab)
    if not hotels:
        print(f"  {LOG_ERROR} No hotels")
        return False

    # Filter already booked
    booked = get_booked_hotels_for_date(date)
    available = [h for h in hotels if h not in booked]
    if not available:
        print(f"  {LOG_SKIP} All hotels already booked")
        return False

    # Try each hotel
    for hotel in available:
        if await fast_book_hotel(tab, date, hotel):
            return True

        # Return to calendar for next attempt
        await tab.go_to(calendar_url)
        await asyncio.sleep(BLITZ_WAIT_LONG)
        if not await fast_click_date(tab, date):
            print(f"  {LOG_ERROR} Failed to return to date")
            return False
        await asyncio.sleep(BLITZ_WAIT_LONG)

    return False
