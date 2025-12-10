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
    NUM_MONTHS_TO_SKIP,
    NUM_MONTHS_TO_SCAN,
    TARGET_WEEKDAYS,
    WEEKDAY_NAMES,
    AUTO_BOOK,
    INCLUDE_HOLIDAYS,
    SEPARATOR_WIDTH,
    SLEEP_SHORT,
    SLEEP_MONTH_NAV,
    LOG_ARROW,
    LOG_SUCCESS,
    LOG_ERROR,
    LOG_WARNING,
    LOG_SEPARATOR,
    LOG_EQUALS
)
from browser import create_browser_options
from cache import load_cached_url, save_calendar_url
from navigation import (
    acquire_calendar_url_with_captcha,
    is_valid_calendar_page
)
from calendar_scanner import scan_month_days, navigate_to_next_month
from booking import process_available_day


async def scan_and_book_one(tab, calendar_url, num_months=NUM_MONTHS_TO_SCAN, skip_months=NUM_MONTHS_TO_SKIP):
    """Scan all months and process available days.
    
    Two-phase approach:
    Phase 1: Scan through all months and collect available days
    Phase 2: Process each available day and attempt booking
    
    Args:
        tab: Browser tab instance
        calendar_url: Calendar URL to use for navigation
        num_months: Number of months to scan
        skip_months: Number of months to skip before scanning
        
    Returns:
        bool: True if booking made
    """    
    # Skip initial months if configured
    for skip_idx in range(skip_months):
        print(f"{LOG_ARROW} Skip month {skip_idx + 1}/{skip_months}")
        if not await navigate_to_next_month(tab):
            print(f"{LOG_ERROR} Skip navigation failed")
            return False
    
    all_available_days = []
    for month_num in range(num_months):
        print(f"\n{LOG_ARROW} Month {month_num + 1}/{num_months}")
        
        available_days = await scan_month_days(tab)
        
        if available_days:
            for day_info in available_days:
                day_info['month_num'] = month_num
                day_info['skip_months'] = skip_months
                all_available_days.append(day_info)
        
        # Move to next month
        if month_num < num_months - 1:
            if not await navigate_to_next_month(tab):
                print(f"{LOG_ERROR} Navigation failed")
                break
    
    if not all_available_days:
        print(f"{LOG_ERROR} No available dates")
        return False
    
    for day_info in all_available_days:
        month_num = day_info['month_num']
        skip_months = day_info['skip_months']
        date = day_info['full_date']
        
        print(f"\n{LOG_EQUALS * SEPARATOR_WIDTH}")
        print(f"{date} ({day_info['day_name']}) - Month {month_num + 1}")
        print(f"{LOG_EQUALS * SEPARATOR_WIDTH}")
        
        # Return to calendar and navigate to correct month
        await tab.go_to(calendar_url)
        await asyncio.sleep(SLEEP_MONTH_NAV)
        
        if not await is_valid_calendar_page(tab):
            print(f"{LOG_ERROR} Failed to return to calendar")
            continue
        
        # Navigate to correct month (skip + scan position)
        total_nav_months = skip_months + month_num
        for i in range(total_nav_months):
            if not await navigate_to_next_month(tab):
                print(f"{LOG_ERROR} Failed to navigate to month {total_nav_months + 1}")
                break
        
        # Try to book
        if await process_available_day(tab, day_info, calendar_url):
            print(f"\n{LOG_SUCCESS * 3} BOOKING SUCCESSFUL {LOG_SUCCESS * 3}")
            return True
    
    print(f"\n{LOG_ERROR} No booking opportunities")
    return False


async def scan_calendar_and_book(calendar_url, validate=False):
    """Scan calendar and attempt booking.
    
    Args:
        calendar_url: Calendar URL to scan
        validate: Whether to validate the URL first (for cached URLs)
        
    Returns:
        tuple: (success: bool, booking_made: bool)
               success indicates if URL was valid and scan completed
               booking_made indicates if a booking was actually made
    """    
    options = create_browser_options(headless=True)
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(calendar_url)
        await asyncio.sleep(SLEEP_SHORT)
        
        # Validate page if requested (for cached URLs)
        if not await is_valid_calendar_page(tab):
            if validate:
                print(f"{LOG_ERROR} Cached URL invalid")
                return False, False
            raise Exception("Failed to load calendar page")
        
        if validate:
            print(f"{LOG_SUCCESS} Cached URL valid")
        
        booking_made = await scan_and_book_one(tab, calendar_url, num_months=NUM_MONTHS_TO_SCAN, skip_months=NUM_MONTHS_TO_SKIP)
        
        print(f"\n{LOG_SUCCESS if booking_made else LOG_ERROR} Iteration: {'1 booking' if booking_made else 'No bookings'}")
        
        await asyncio.sleep(SLEEP_SHORT)
        return True, booking_made


async def scan_once():
    """Perform single scan iteration.
    
    Returns:
        bool: True if successful
    """
    cached_url = load_cached_url()
    
    # Try cached URL first
    if cached_url:
        print(f"{LOG_ARROW} Validating cached URL...")
        success, booking_made = await scan_calendar_and_book(cached_url, validate=True)
        if success:
            return True
    
    print(f"\n{LOG_ARROW} Acquiring new calendar URL...")
    new_url = await acquire_calendar_url_with_captcha()
    
    if not new_url:
        print(f"{LOG_ERROR} Failed to acquire URL")
        return False
    
    save_calendar_url(new_url)
    print(f"\n{LOG_EQUALS * SEPARATOR_WIDTH}")
    print("URL ACQUIRED - SCAN START")
    print(f"{LOG_EQUALS * SEPARATOR_WIDTH}")
    await asyncio.sleep(SLEEP_SHORT)
    
    success, booking_made = await scan_calendar_and_book(new_url, validate=False)
    return success


async def main():
    """Main execution flow - continuous scanning mode."""    
    iteration = 0
    try:
        while True:
            iteration += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            print(f"\n{LOG_EQUALS * SEPARATOR_WIDTH}")
            print(f"ITERATION #{iteration} | {timestamp}")
            print(f"{LOG_EQUALS * SEPARATOR_WIDTH}")
            
            try:
                await scan_once()
            except Exception as e:
                print(f"\n{LOG_ERROR} Scan error: {e}")
            
            print(f"\n{LOG_ARROW} Wait {SCAN_INTERVAL_SECONDS}s | {timestamp}")
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        print(f"\n\n{LOG_EQUALS * SEPARATOR_WIDTH}")
        print(f"STOPPED | Iterations: {iteration}")
        print(f"{LOG_EQUALS * SEPARATOR_WIDTH}")


if __name__ == "__main__":
    asyncio.run(main())
    print("\nDone!")