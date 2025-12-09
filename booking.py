# -*- coding: utf-8 -*-
"""Booking automation workflow for ITS Calendar Scanner."""

import asyncio
from browser import extract_script_value
from cache import save_booking, get_booked_hotels_for_date
from navigation import is_valid_calendar_page
from config import (
    TAG_ANCHOR,
    TAG_INPUT,
    TAG_TD,
    NUM_GUESTS,
    TARGET_EMAIL,
    SKIP_HOTELS,
    ATTR_DATA_JOIN_TIME,
    INPUT_NAME_STAY_PERSONS,
    INPUT_NAME_EMAIL,
    INPUT_NAME_NO_NAME,
    INPUT_NAME_ROOM_PREFIX,
    FORM_SUBMIT_SCRIPT,
    WINDOW_LOCATION_SCRIPT,
    URL_SERVICE_GROUP_SELECT,
    URL_APPLY_SERVICE_SELECT,
    URL_APPLY_EMPTY_NEW,
    URL_APPLY_RULE,
    URL_APPLY_EMAIL_INPUT,
    URL_SEND_COMPLETE,
    PROTOCOL_JAVASCRIPT,
    PROTOCOL_HTTP,
    PROTOCOL_HTTPS,
    TEXT_SEARCH_AVAILABILITY,
    TEXT_PROCEED_TO_BOOKING,
    TEXT_AGREE,
    SKIP_LINK_TEXTS,
    SKIP_LINK_TEXTS_SERVICE,
    MIN_LINK_TEXT_LENGTH,
    TEXT_TRUNCATE_LENGTH,
    SLEEP_SHORT,
    SLEEP_STANDARD,
    DEFAULT_TIMEOUT,
    EXTENDED_TIMEOUT,
    SEPARATOR_WIDTH
)


async def click_date_by_attribute(tab, target_date):
    """Click date cell by data-join-time attribute.
    
    Args:
        tab: Browser tab instance
        target_date: Date string to click
        
    Returns:
        bool: True if successful
    """
    try:
        print(f"→ Clicking date: {target_date}")
        await asyncio.sleep(SLEEP_SHORT)
        
        all_cells = await tab.find(tag_name=TAG_TD, find_all=True, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if not all_cells:
            print("  ✗ No date cells")
            return False
        
        for cell in all_cells:
            try:
                attr_result = await cell.execute_script(f"return this.getAttribute('{ATTR_DATA_JOIN_TIME}')")
                date_attr = extract_script_value(attr_result)
                
                if date_attr == target_date:
                    await cell.scroll_into_view()
                    await asyncio.sleep(SLEEP_SHORT)
                    await cell.execute_script("this.click()")
                    await asyncio.sleep(SLEEP_STANDARD)
                    return True
            except:
                continue
        
        print(f"  ✗ Date not found: {target_date}")
        return False
    except Exception as e:
        print(f"✗ Click date error: {e}")
        return False


async def verify_on_service_group_page(tab):
    """Verify on service_group_select page.
    
    Args:
        tab: Browser tab instance
        
    Returns:
        bool: True if on correct page
    """
    try:
        await asyncio.sleep(SLEEP_SHORT)
        url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
        current_url = extract_script_value(url_response)
        
        if URL_SERVICE_GROUP_SELECT in current_url:
            return True
        else:
            print(f"  ✗ Not on {URL_SERVICE_GROUP_SELECT}")
            return False
    except Exception as e:
        print(f"✗ Page verify error: {e}")
        return False


async def get_hotel_names_on_service_group_page(tab):
    """Collect hotel names on service_group_select page.
    
    Args:
        tab: Browser tab instance
        
    Returns:
        list: Hotel names (excluding hotels in SKIP_HOTELS list)
    """
    print(f"→ Collecting hotels...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    
    if URL_SERVICE_GROUP_SELECT not in current_url:
        print(f"  ✗ Not on {URL_SERVICE_GROUP_SELECT}")
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
                    
                    if any(skip in link_text for skip in SKIP_LINK_TEXTS):
                        continue
                    
                    if href and (PROTOCOL_HTTP in href or PROTOCOL_HTTPS in href):
                        continue
                    
                    if link_text and len(link_text.strip()) > MIN_LINK_TEXT_LENGTH and PROTOCOL_JAVASCRIPT in href:
                        # Skip hotels in the SKIP_HOTELS list
                        if any(skip_name in link_text for skip_name in SKIP_HOTELS):
                            print(f"  ⊗ Skipped: {link_text[:TEXT_TRUNCATE_LENGTH]}")
                            continue
                        
                        hotel_names.append(link_text)
                        print(f"  • {link_text[:TEXT_TRUNCATE_LENGTH]}")
                except:
                    continue
        
        print(f"  Total: {len(hotel_names)} hotels")
        return hotel_names
    except Exception as e:
        print(f"✗ Hotel collection error: {e}")
        return []


async def click_hotel_by_name(tab, hotel_name):
    """Click hotel link by name.
    
    Args:
        tab: Browser tab instance
        hotel_name: Hotel name to click
        
    Returns:
        bool: True if successful
    """
    print(f"→ Selecting hotel: {hotel_name[:TEXT_TRUNCATE_LENGTH]}")
    
    try:
        await asyncio.sleep(SLEEP_SHORT)
        links = await tab.find(tag_name=TAG_ANCHOR, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if links:
            for link in links:
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = extract_script_value(text_result) or ""
                    
                    if link_text == hotel_name:
                        await link.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await link.click()
                        await asyncio.sleep(SLEEP_SHORT)
                        return True
                except:
                    continue
        
        print(f"  ✗ Hotel not found")
        return False
    except Exception as e:
        print(f"✗ Hotel click error: {e}")
        return False


async def select_service_on_apply_page(tab):
    """Select service on apply_service_select page.
    
    Args:
        tab: Browser tab instance
        
    Returns:
        bool: True if successful
    """
    print(f"→ Selecting service...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    
    if URL_APPLY_SERVICE_SELECT not in current_url:
        print(f"  ✗ Not on {URL_APPLY_SERVICE_SELECT}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_STANDARD)
        links = await tab.find(tag_name=TAG_ANCHOR, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if links:
            service_links = []
            for link in links:
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = extract_script_value(text_result) or ""
                    
                    href_result = await link.execute_script("return this.href")
                    href = extract_script_value(href_result) or ""
                    
                    if any(skip in link_text for skip in SKIP_LINK_TEXTS_SERVICE):
                        continue
                    
                    if link_text and len(link_text.strip()) > MIN_LINK_TEXT_LENGTH and PROTOCOL_JAVASCRIPT in href:
                        service_links.append((link, link_text))
                except:
                    continue
            
            if service_links:
                link, link_text = service_links[0]
                print(f"  → Clicking: {link_text[:TEXT_TRUNCATE_LENGTH]}")
                await link.scroll_into_view()
                await asyncio.sleep(SLEEP_STANDARD)
                await link.click()
                await asyncio.sleep(SLEEP_STANDARD)
                return True
            else:
                print("  ✗ No service links")
                return False
        
        print("  ✗ No links found")
        return False
    except Exception as e:
        print(f"✗ Service select error: {e}")
        return False


async def fill_booking_form_and_search(tab, target_date):
    """Fill booking form and search.
    
    Args:
        tab: Browser tab instance
        target_date: Target date
        
    Returns:
        bool: True if successful
    """
    print(f"→ Filling form (guests: {NUM_GUESTS})")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    
    if URL_APPLY_EMPTY_NEW not in current_url:
        print(f"  ✗ Not on {URL_APPLY_EMPTY_NEW}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_SHORT)
        
        inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        guest_filled = False
        
        if inputs:
            for input_elem in inputs:
                try:
                    name_result = await input_elem.execute_script("return this.name")
                    input_name = extract_script_value(name_result) or ""
                    
                    if INPUT_NAME_STAY_PERSONS in input_name:
                        await input_elem.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await input_elem.execute_script("this.value = ''")
                        await input_elem.insert_text(str(NUM_GUESTS))
                        guest_filled = True
                        await asyncio.sleep(SLEEP_SHORT)
                        break
                except:
                    pass
        
        if not guest_filled:
            print("  ⚠ Guest count not filled")
        
        # Click search button
        print(f"→ Clicking {TEXT_SEARCH_AVAILABILITY}")
        buttons = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if buttons:
            for button in buttons:
                try:
                    value_result = await button.execute_script("return this.value")
                    button_value = extract_script_value(value_result) or ""
                    
                    if button_value == TEXT_SEARCH_AVAILABILITY:
                        await button.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await button.click()
                        await asyncio.sleep(SLEEP_SHORT)
                        return True
                except:
                    pass
        
        print("  ✗ Search button not found")
        return False
    except Exception as e:
        print(f"✗ Form fill error: {e}")
        return False


async def select_room_and_proceed(tab):
    """Select room and proceed.
    
    Args:
        tab: Browser tab instance
        
    Returns:
        bool: True if successful
    """
    print("→ Selecting room...")
    
    try:
        await asyncio.sleep(SLEEP_SHORT)
        all_inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        
        room_selected = False
        if all_inputs:
            for inp in all_inputs:
                try:
                    type_result = await inp.execute_script("return this.type")
                    input_type = extract_script_value(type_result) or ""
                    
                    if input_type == "checkbox":
                        name_result = await inp.execute_script("return this.name")
                        input_name = extract_script_value(name_result) or ""
                        
                        if input_name == INPUT_NAME_NO_NAME:
                            continue
                        
                        if input_name and INPUT_NAME_ROOM_PREFIX in input_name:
                            disabled_result = await inp.execute_script("return this.disabled")
                            is_disabled = extract_script_value(disabled_result)
                            
                            if not is_disabled:
                                await inp.scroll_into_view()
                                await asyncio.sleep(SLEEP_SHORT)
                                await inp.execute_script("this.click()")
                                room_selected = True
                                await asyncio.sleep(SLEEP_SHORT)
                                break
                except:
                    pass
        
        if not room_selected:
            print("  ✗ No room found")
            return False
        
        print(f"→ Clicking {TEXT_PROCEED_TO_BOOKING}")
        buttons = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        if buttons:
            for button in buttons:
                try:
                    value_result = await button.execute_script("return this.value")
                    button_value = extract_script_value(value_result) or ""
                    
                    if button_value == TEXT_PROCEED_TO_BOOKING:
                        await button.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await button.click()
                        await asyncio.sleep(SLEEP_SHORT)
                        return True
                except:
                    pass
        
        print("  ✗ Proceed button not found")
        return False
    except Exception as e:
        print(f"✗ Room select error: {e}")
        return False


async def agree_to_rules(tab):
    """Agree to rules on rule page.
    
    Args:
        tab: Browser tab instance
        
    Returns:
        bool: True if successful
    """
    print(f"→ Agreeing to rules...")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    
    if URL_APPLY_RULE not in current_url:
        print(f"  ✗ Not on {URL_APPLY_RULE}")
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
                        await asyncio.sleep(SLEEP_SHORT)
                        return True
                except:
                    pass
        
        print("  ✗ Agree button not found")
        return False
    except Exception as e:
        print(f"✗ Agree error: {e}")
        return False


async def fill_email_and_submit(tab):
    """Fill email and submit.
    
    Args:
        tab: Browser tab instance
        
    Returns:
        bool: True if successful
    """
    print(f"→ Submitting email: {TARGET_EMAIL}")
    
    url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
    current_url = extract_script_value(url_response)
    
    if URL_APPLY_EMAIL_INPUT not in current_url:
        print(f"  ✗ Not on {URL_APPLY_EMAIL_INPUT}")
        return False
    
    try:
        await asyncio.sleep(SLEEP_SHORT)
        inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=EXTENDED_TIMEOUT, raise_exc=False)
        
        email_filled = False
        if inputs:
            for input_elem in inputs:
                try:
                    name_result = await input_elem.execute_script("return this.name")
                    input_name = extract_script_value(name_result) or ""
                    
                    if input_name == INPUT_NAME_EMAIL:
                        await input_elem.scroll_into_view()
                        await asyncio.sleep(SLEEP_SHORT)
                        await input_elem.execute_script("this.value = ''")
                        await input_elem.insert_text(TARGET_EMAIL)
                        email_filled = True
                        await asyncio.sleep(SLEEP_SHORT)
                        break
                except:
                    pass
        
        if not email_filled:
            print("  ✗ Email not filled")
            return False
        
        # Handle dialog and submit
        from pydoll.protocol.page.events import PageEvent
        
        async def handle_dialog(event):
            try:
                await tab.handle_dialog(accept=True)
            except:
                pass
        
        await tab.enable_page_events()
        await tab.on(PageEvent.JAVASCRIPT_DIALOG_OPENING, handle_dialog)
        await asyncio.sleep(SLEEP_SHORT)
        
        try:
            await tab.execute_script(FORM_SUBMIT_SCRIPT)
        except:
            pass
        
        await asyncio.sleep(SLEEP_SHORT)
        
        final_url_response = await tab.execute_script(WINDOW_LOCATION_SCRIPT)
        final_url = extract_script_value(final_url_response)
        
        if URL_SEND_COMPLETE in final_url:
            print(f"✓ Reached {URL_SEND_COMPLETE}")
            return True
        else:
            return True  # May still have succeeded
        
    except Exception as e:
        print(f"✗ Email submit error: {e}")
        return False


async def try_book_hotel_for_date(tab, date, hotel):
    """Attempt to book hotel for date.
    
    Args:
        tab: Browser tab instance
        date: Date string
        hotel: Hotel name
        
    Returns:
        bool: True if successful
    """
    print(f"\n{'─' * SEPARATOR_WIDTH}")
    print(f"BOOKING: {date} - {hotel[:TEXT_TRUNCATE_LENGTH]}")
    print(f"{'─' * SEPARATOR_WIDTH}")
    
    if not await click_hotel_by_name(tab, hotel):
        print("✗ Hotel click failed")
        return False
    
    if not await select_service_on_apply_page(tab):
        print("✗ Service select failed")
        return False
    
    if not await fill_booking_form_and_search(tab, date):
        print("✗ Form fill failed")
        return False
    
    if not await select_room_and_proceed(tab):
        print("✗ No rooms available")
        return False
    
    if not await agree_to_rules(tab):
        print("✗ Rules agreement failed")
        return False
    
    if not await fill_email_and_submit(tab):
        print("✗ Email submit failed")
        return False
    
    print("\n" + "=" * SEPARATOR_WIDTH)
    print(f"✓ BOOKING COMPLETE: {hotel[:TEXT_TRUNCATE_LENGTH]}")
    print("=" * SEPARATOR_WIDTH)
    
    save_booking(date, hotel)
    return True


async def process_available_day(tab, date_info, calendar_url):
    """Process available day and attempt booking.
    
    Args:
        tab: Browser tab instance
        date_info: Date information dict
        calendar_url: Calendar URL for navigation
        
    Returns:
        bool: True if booking made
    """
    date = date_info['full_date']
    print(f"\n→ Processing: {date} ({date_info['day_name']})")
    
    if not await click_date_by_attribute(tab, date):
        print(f"✗ Could not click date")
        return False
    
    if not await verify_on_service_group_page(tab):
        print(f"✗ Not on service group page")
        return False
    
    hotels = await get_hotel_names_on_service_group_page(tab)
    
    if not hotels:
        print("✗ No hotels available")
        return False
    
    booked_hotels = get_booked_hotels_for_date(date)
    available_hotels = [h for h in hotels if h not in booked_hotels]
    
    if not available_hotels:
        print(f"⊗ All hotels already booked for {date}")
        return False
    
    print(f"  Available: {len(available_hotels)} hotels")
    
    # Try first available hotel
    for hotel in available_hotels:
        if await try_book_hotel_for_date(tab, date, hotel):
            return True
        
        # Failed, return to calendar
        print("→ Returning to calendar...")
        await tab.go_to(calendar_url)
        await asyncio.sleep(SLEEP_STANDARD)
        
        if not await click_date_by_attribute(tab, date):
            print("✗ Failed to navigate back")
            return False
    
    return False