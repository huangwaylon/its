# -*- coding: utf-8 -*-
"""
ITS Calendar Scanner - Main Entry Point

Automated booking scanner for ITS health insurance facility calendar.
Continuously monitors available dates and attempts automated bookings.
"""

import asyncio
from datetime import datetime
from pydoll.browser.chromium import Chrome

from config import (
    SCAN_INTERVAL_SECONDS,
    NUM_MONTHS_TO_SCAN,
    TARGET_WEEKDAYS,
    WEEKDAY_NAMES,
    AUTO_BOOK,
    INCLUDE_HOLIDAYS,
    SEPARATOR_WIDTH,
    SLEEP_SHORT,
    SLEEP_MONTH_NAV
)
from browser import create_browser_options
from cache import load_cached_url, save_calendar_url
from navigation import (
    validate_cached_url,
    acquire_calendar_url_with_captcha,
    is_valid_calendar_page
)
from calendar_scanner import scan_month_days, navigate_to_next_month
from booking import process_available_day


async def scan_and_book_one(tab, num_months=NUM_MONTHS_TO_SCAN):
    """Scan all months and process available days.
    
    Two-phase approach:
    Phase 1: Scan through all months and collect available days
    Phase 2: Process each available day and attempt booking
    
    Args:
        tab: Browser tab instance
        num_months: Number of months to scan
        
    Returns:
        bool: True if booking made
    """
    target_day_names = [WEEKDAY_NAMES[wd] for wd in TARGET_WEEKDAYS]
    days_str = ", ".join(target_day_names)
    holiday_note = " + National Holidays" if INCLUDE_HOLIDAYS else ""
    
    print("\n" + "=" * SEPARATOR_WIDTH)
    print(f"SCANNING {days_str.upper()}{holiday_note.upper()} FOR {num_months} MONTHS")
    print("=" * SEPARATOR_WIDTH)
    
    calendar_url = load_cached_url()
    
    # PHASE 1: Scan all months
    print("\n[PHASE 1] Scanning for available dates")
    print("─" * SEPARATOR_WIDTH)
    
    all_available_days = []
    for month_num in range(num_months):
        print(f"\nMonth {month_num + 1}/{num_months}")
        
        available_days = await scan_month_days(tab)
        
        if available_days:
            for day_info in available_days:
                day_info['month_num'] = month_num
                all_available_days.append(day_info)
        
        # Move to next month
        if month_num < num_months - 1:
            if not await navigate_to_next_month(tab):
                print("✗ Navigation failed")
                break
    
    # PHASE 2: Process available days
    print(f"\n[PHASE 2] Processing {len(all_available_days)} available dates")
    print("─" * SEPARATOR_WIDTH)
    
    if not all_available_days:
        print("✗ No available dates found")
        return False
    
    for day_info in all_available_days:
        month_num = day_info['month_num']
        date = day_info['full_date']
        
        print(f"\n{'='*SEPARATOR_WIDTH}")
        print(f"Date: {date} ({day_info['day_name']}) - Month {month_num + 1}")
        print('='*SEPARATOR_WIDTH)
        
        # Return to calendar and navigate to correct month
        await tab.go_to(calendar_url)
        await asyncio.sleep(SLEEP_MONTH_NAV)
        
        if not await is_valid_calendar_page(tab):
            print("✗ Failed to return to calendar")
            continue
        
        # Navigate to correct month
        for i in range(month_num):
            if not await navigate_to_next_month(tab):
                print(f"✗ Failed to navigate to month {month_num + 1}")
                break
        
        # Try to book
        if await process_available_day(tab, day_info, calendar_url):
            print("\n✓✓✓ BOOKING SUCCESSFUL ✓✓✓")
            return True
    
    print("\n✗ No booking opportunities found")
    return False


async def scan_calendar_and_book(calendar_url):
    """Scan calendar and attempt booking.
    
    Args:
        calendar_url: Calendar URL to scan
        
    Returns:
        bool: True if booking made
    """
    print("\n" + "=" * SEPARATOR_WIDTH)
    print("STARTING BOOKING SCAN")
    print("=" * SEPARATOR_WIDTH)
    
    options = create_browser_options(headless=True)
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(calendar_url)
        await asyncio.sleep(SLEEP_SHORT)
        
        if not await is_valid_calendar_page(tab):
            raise Exception("Failed to load calendar page")
        
        booking_made = await scan_and_book_one(tab, num_months=NUM_MONTHS_TO_SCAN)
        
        if booking_made:
            print("\n✓ Iteration complete: 1 booking made")
        else:
            print("\n✗ Iteration complete: No bookings made")
        
        await asyncio.sleep(SLEEP_SHORT)
        return booking_made


async def scan_once():
    """Perform single scan iteration.
    
    Returns:
        bool: True if successful
    """
    cached_url = load_cached_url()
    
    if cached_url:
        if await validate_cached_url(cached_url):
            await scan_calendar_and_book(cached_url)
            return True
    
    print("\n→ Acquiring new calendar URL...")
    new_url = await acquire_calendar_url_with_captcha()
    
    if not new_url:
        print("✗ Failed to acquire calendar URL")
        return False
    
    save_calendar_url(new_url)
    print("\n" + "=" * SEPARATOR_WIDTH)
    print("URL ACQUIRED - STARTING SCAN")
    print("=" * SEPARATOR_WIDTH)
    await asyncio.sleep(SLEEP_SHORT)
    await scan_calendar_and_book(new_url)
    return True


async def main():
    """Main execution flow - continuous scanning mode."""
    target_day_names = [WEEKDAY_NAMES[wd] for wd in TARGET_WEEKDAYS]
    days_str = ", ".join(target_day_names)
    holiday_note = " + National Holidays" if INCLUDE_HOLIDAYS else ""
    
    print("=" * SEPARATOR_WIDTH)
    print("ITS CALENDAR SCANNER")
    print("=" * SEPARATOR_WIDTH)
    print(f"Target Days: {days_str}{holiday_note}")
    print(f"Auto-booking: {'ENABLED' if AUTO_BOOK else 'DISABLED'}")
    print(f"Scan interval: {SCAN_INTERVAL_SECONDS}s")
    print("Press Ctrl+C to stop")
    print("=" * SEPARATOR_WIDTH + "\n")
    
    iteration = 0
    
    try:
        while True:
            iteration += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            print("\n" + "=" * SEPARATOR_WIDTH)
            print(f"ITERATION #{iteration} - {timestamp}")
            print("=" * SEPARATOR_WIDTH)
            
            try:
                await scan_once()
            except Exception as e:
                print(f"\n✗ Scan error: {e}")
            
            print(f"\n[{timestamp}] Waiting {SCAN_INTERVAL_SECONDS}s...")
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        print("\n\n" + "=" * SEPARATOR_WIDTH)
        print("SCANNER STOPPED BY USER")
        print(f"Total iterations: {iteration}")
        print("=" * SEPARATOR_WIDTH)


if __name__ == "__main__":
    asyncio.run(main())
    print("\nDone!")