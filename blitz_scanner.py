# -*- coding: utf-8 -*-
"""Fast batch scanning for blitz mode.

Uses single JavaScript calls to scan all calendar cells at once,
eliminating per-cell round-trips to the browser.
"""

import asyncio
from browser import extract_script_value
from config import (
    TARGET_DATES,
    ICON_AVAILABLE,
    STATUS_AVAILABLE,
    STATUS_FULL,
    STATUS_UNKNOWN,
    CLASS_MONTH,
    ID_NEXT_MONTH,
    TAG_INPUT,
    TEXT_NEXT_MONTH,
    DEFAULT_TIMEOUT,
    WEEKDAY_NAMES,
    LOG_ARROW,
    LOG_ERROR,
    LOG_WARNING,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_RESET,
)

# JavaScript that scans ALL calendar cells in one call.
# Returns JSON array of {date, text, available} for every cell with data-join-time.
BATCH_SCAN_JS = """
return JSON.stringify(
    Array.from(document.querySelectorAll('td[data-join-time]')).map(td => {
        const date = td.getAttribute('data-join-time');
        const text = td.textContent.trim();
        const hasCircle = text.includes('○') || text.includes('◎');
        const hasCross = text.includes('☓') || text.includes('×') || text.includes('X');
        return { date, text, available: hasCircle, full: hasCross };
    })
);
"""

GET_MONTH_JS = """
const el = document.querySelector('.month');
return el ? el.textContent.trim() : null;
"""

CLICK_NEXT_MONTH_JS = """
const btn = document.getElementById('nextMonth');
if (btn) { btn.click(); return true; }
const inputs = document.querySelectorAll('input');
for (const inp of inputs) {
    if (inp.value && inp.value.includes('翌月')) { inp.click(); return true; }
}
return false;
"""


async def batch_scan_month(tab, target_dates=None):
    """Scan current month using a single JS call.

    Returns all availability data in one browser round-trip instead of
    iterating cells one by one.

    Args:
        tab: Browser tab instance
        target_dates: List of target date strings, defaults to TARGET_DATES

    Returns:
        list: Available day info dicts matching target dates
    """
    if target_dates is None:
        target_dates = TARGET_DATES
    target_set = set(target_dates)

    # Get month name
    month_result = await tab.execute_script(GET_MONTH_JS)
    current_month = extract_script_value(month_result) or "Unknown"
    print(f"{LOG_ARROW} Batch scan: {current_month}")

    # Single JS call to get ALL cell data
    result = await tab.execute_script(BATCH_SCAN_JS)
    raw = extract_script_value(result)

    if not raw:
        print(f"  {LOG_ERROR} No cells found")
        return []

    import json
    try:
        cells = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        print(f"  {LOG_ERROR} Failed to parse scan results")
        return []

    available_days = []
    for cell in cells:
        date_str = cell.get("date", "")
        if date_str not in target_set:
            continue

        # Determine status
        if cell.get("available"):
            status = STATUS_AVAILABLE
            icon = ICON_AVAILABLE
        elif cell.get("full"):
            status = STATUS_FULL
            icon = "×"
        else:
            status = STATUS_UNKNOWN
            icon = ""

        # Get day name
        try:
            from datetime import datetime
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            day_name = WEEKDAY_NAMES[date_obj.weekday()]
        except Exception:
            day_name = "Unknown"

        # Extract day number
        import re
        match = re.search(r"(\d+)", cell.get("text", ""))
        date_text = match.group(1) if match else ""

        # Display
        if status == STATUS_AVAILABLE:
            print(f"  {date_text:>2}日 ({day_name}): {COLOR_GREEN}{icon} ({status}){COLOR_RESET}")
        elif status == STATUS_FULL:
            print(f"  {date_text:>2}日 ({day_name}): {COLOR_RED}{icon} ({status}){COLOR_RESET}")
        else:
            print(f"  {date_text:>2}日 ({day_name}): {icon} ({status})")

        if status == STATUS_AVAILABLE:
            available_days.append({
                "month": current_month,
                "date": date_text,
                "day_name": day_name,
                "full_date": date_str,
                "icon": icon,
            })

    return available_days


async def fast_navigate_next_month(tab, poll_interval=0.15, max_polls=15):
    """Navigate to next month with minimal waits.

    Uses JS-based click and aggressive polling.

    Args:
        tab: Browser tab instance
        poll_interval: Seconds between polls (default 0.15s, half of normal)
        max_polls: Maximum polling attempts

    Returns:
        bool: True if navigation succeeded
    """
    # Get current month in one JS call
    month_result = await tab.execute_script(GET_MONTH_JS)
    current_month = extract_script_value(month_result)

    # Click next month via JS (no element lookup round-trip)
    click_result = await tab.execute_script(CLICK_NEXT_MONTH_JS)
    clicked = extract_script_value(click_result)

    if not clicked:
        print(f"{LOG_ERROR} Next month button not found")
        return False

    # Aggressive polling for month change
    for _ in range(max_polls):
        await asyncio.sleep(poll_interval)
        new_month_result = await tab.execute_script(GET_MONTH_JS)
        new_month = extract_script_value(new_month_result)
        if new_month and new_month != current_month:
            return True

    print(f"{LOG_ERROR} Month did not change after {max_polls} polls")
    return False


async def scan_all_months(tab, num_months, skip_months, target_dates=None):
    """Scan multiple months using batch scanning.

    Args:
        tab: Browser tab instance
        num_months: Number of months to scan
        skip_months: Number of months to skip first
        target_dates: Optional target dates override

    Returns:
        list: All available days across all scanned months
    """
    # Skip months
    for i in range(skip_months):
        print(f"{LOG_ARROW} Skip month {i + 1}/{skip_months}")
        if not await fast_navigate_next_month(tab):
            print(f"{LOG_ERROR} Skip navigation failed")
            return []

    all_available = []
    for month_idx in range(num_months):
        days = await batch_scan_month(tab, target_dates)
        for day in days:
            day["month_num"] = month_idx
            day["skip_months"] = skip_months
        all_available.extend(days)

        if month_idx < num_months - 1:
            if not await fast_navigate_next_month(tab):
                print(f"{LOG_ERROR} Month navigation failed")
                break

    return all_available
