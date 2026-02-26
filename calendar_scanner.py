# -*- coding: utf-8 -*-
"""Calendar scanning and date processing for ITS Calendar Scanner."""

import asyncio
import re
from datetime import datetime
from browser import extract_script_value
from config import (
    TARGET_DATES,
    TAG_TD,
    TAG_PARAGRAPH,
    TAG_INPUT,
    CLASS_MONTH,
    ATTR_DATA_JOIN_TIME,
    ID_NEXT_MONTH,
    TEXT_NEXT_MONTH,
    WEEKDAY_NAMES,
    ICON_AVAILABLE,
    STATUS_AVAILABLE,
    STATUS_FULL,
    STATUS_UNKNOWN,
    SLEEP_MONTH_NAV,
    DEFAULT_TIMEOUT,
    TEXT_TRUNCATE_LENGTH,
    MONTH_NAV_POLL_ATTEMPTS,
    MONTH_NAV_POLL_INTERVAL,
    LOG_ARROW,
    LOG_ERROR,
    LOG_WARNING,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_RESET,
)


def is_target_date(date_string):
    """Check if date is in target dates list.

    Args:
        date_string: Date in format 'YYYY-MM-DD'

    Returns:
        bool: True if date should be checked
    """
    return date_string in TARGET_DATES


def get_weekday_name(date_string):
    """Get weekday name for a date string.

    Args:
        date_string: Date in format 'YYYY-MM-DD'

    Returns:
        str: Weekday name or 'Unknown'
    """
    try:
        date_obj = datetime.strptime(date_string, "%Y-%m-%d")
        return WEEKDAY_NAMES[date_obj.weekday()]
    except:
        return "Unknown"


async def _click_next_month_button(tab):
    """Attempt to click next month button using multiple strategies.

    Args:
        tab: Browser tab instance

    Returns:
        bool: True if click succeeded
    """
    # Strategy 1: Find by ID
    try:
        next_button = await tab.find(
            id=ID_NEXT_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False
        )
        if next_button:
            await next_button.click()
            return True
    except:
        pass

    # Strategy 2: Find input with next month text
    try:
        inputs = await tab.find(
            tag_name=TAG_INPUT, find_all=True, timeout=DEFAULT_TIMEOUT, raise_exc=False
        )
        if inputs:
            for input_elem in inputs:
                try:
                    value = await input_elem.get_property("value")
                    if value and TEXT_NEXT_MONTH in value:
                        await input_elem.click()
                        return True
                except:
                    pass
    except:
        pass

    return False


async def verify_month_changed(
    tab,
    previous_month,
    max_attempts=MONTH_NAV_POLL_ATTEMPTS,
    poll_interval=MONTH_NAV_POLL_INTERVAL,
):
    """Poll for month change verification with retries.

    Args:
        tab: Browser tab instance
        previous_month: Previous month text to compare
        max_attempts: Number of polling attempts
        poll_interval: Time between polls in seconds

    Returns:
        tuple: (success: bool, new_month: str|None)
    """
    for attempt in range(max_attempts):
        try:
            month_element = await tab.find(
                class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False
            )
            if month_element:
                text_result = await month_element.execute_script(
                    "return this.textContent"
                )
                new_month = extract_script_value(text_result)

                if new_month and new_month != previous_month:
                    return True, new_month
        except:
            pass

        if attempt < max_attempts - 1:
            await asyncio.sleep(poll_interval)

    return False, None


async def navigate_to_next_month(tab):
    """Navigate to next month with polling verification for page load.

    Strategy:
    1. Get current month
    2. Click next button (try multiple methods)
    3. Initial wait (SLEEP_MONTH_NAV = 0.5s)
    4. Poll for month change (up to 5 attempts × 0.3s = 1.5s max additional wait)

    Args:
        tab: Browser tab instance

    Returns:
        bool: True if successful navigation
    """
    # Get current month
    current_month = None
    try:
        month_element = await tab.find(
            class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False
        )
        if month_element:
            text_result = await month_element.execute_script("return this.textContent")
            current_month = extract_script_value(text_result)
    except Exception as e:
        print(f"{LOG_WARNING} Could not get current month: {str(e)[:50]}")

    if not current_month:
        print(f"{LOG_WARNING} No current month, attempting navigation")

    # Click next button
    clicked = await _click_next_month_button(tab)

    if not clicked:
        print(f"{LOG_ERROR} Click failed - button not found")
        return False

    # Initial wait for page to start loading
    await asyncio.sleep(SLEEP_MONTH_NAV)

    # Poll for month change verification
    success, new_month = await verify_month_changed(tab, current_month)

    if success:
        return True

    print(f"{LOG_ERROR} Month unchanged after {MONTH_NAV_POLL_ATTEMPTS} polls")
    return False


async def scan_month_days(tab):
    """Scan current month for available days in target dates list.

    Args:
        tab: Browser tab instance

    Returns:
        list: Available day info dicts
    """
    try:
        month_element = await tab.find(
            class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False
        )
        if month_element:
            text_result = await month_element.execute_script("return this.textContent")
            current_month = extract_script_value(text_result) or STATUS_UNKNOWN
        else:
            current_month = STATUS_UNKNOWN
    except Exception:
        current_month = STATUS_UNKNOWN

    print(f"{LOG_ARROW} Scanning: {current_month}")

    available_days = []

    try:
        all_cells = await tab.find(tag_name=TAG_TD, find_all=True, raise_exc=False)
        if not all_cells:
            print("  No date cells found")
            return available_days

        # Filter to cells with data-join-time
        date_cells = []
        for cell in all_cells:
            try:
                attr_result = await cell.execute_script(
                    f"return this.getAttribute('{ATTR_DATA_JOIN_TIME}')"
                )
                date_attr = extract_script_value(attr_result)

                if date_attr and date_attr not in ["None", None]:
                    date_cells.append((cell, date_attr))
            except:
                pass

        if not date_cells:
            print("  No valid date cells")
            return available_days

        # Filter to target dates
        target_cells = []
        for cell, date_str in date_cells:
            if is_target_date(date_str):
                target_cells.append((cell, date_str))

        # Process each target cell
        for cell, full_date in target_cells:
            try:
                # Get weekday name for display
                day_name = get_weekday_name(full_date)

                # Get date text
                date_text = ""
                date_elem = await cell.find(tag_name=TAG_PARAGRAPH, raise_exc=False)
                if date_elem:
                    text_result = await date_elem.execute_script(
                        "return this.textContent.trim()"
                    )
                    date_text = extract_script_value(text_result) or ""

                if not date_text:
                    text_result = await cell.execute_script(
                        "return this.textContent.trim()"
                    )
                    cell_text = extract_script_value(text_result) or ""
                    match = re.search(r"(\d+)", cell_text)
                    if match:
                        date_text = match.group(1)

                # Get availability
                text_result = await cell.execute_script(
                    "return this.textContent.trim()"
                )
                cell_text = extract_script_value(text_result) or ""

                if ICON_AVAILABLE in cell_text or "○" in cell_text:
                    icon = ICON_AVAILABLE
                    status = STATUS_AVAILABLE
                elif "☓" in cell_text or "×" in cell_text or "X" in cell_text:
                    icon = "×"
                    status = STATUS_FULL
                else:
                    icon = ""
                    status = STATUS_UNKNOWN

                # Display status
                if status == STATUS_AVAILABLE:
                    print(
                        f"  {date_text:>2}日 ({day_name}): {COLOR_GREEN}{icon} ({status}){COLOR_RESET}"
                    )
                elif status == STATUS_FULL:
                    print(
                        f"  {date_text:>2}日 ({day_name}): {COLOR_RED}{icon} ({status}){COLOR_RESET}"
                    )
                else:
                    print(f"  {date_text:>2}日 ({day_name}): {icon} ({status})")

                if icon == ICON_AVAILABLE:
                    available_days.append(
                        {
                            "month": current_month,
                            "date": date_text,
                            "day_name": day_name,
                            "full_date": full_date,
                            "icon": icon,
                        }
                    )
            except Exception as e:
                print(f"  {LOG_ERROR} Cell: {str(e)[:TEXT_TRUNCATE_LENGTH]}")

    except Exception as e:
        print(f"{LOG_ERROR} Scan: {e}")

    return available_days
