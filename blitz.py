# -*- coding: utf-8 -*-
"""Blitz mode orchestrator for ITS Calendar Scanner.

Designed for the 27th midnight rush when next month's availability opens.
Uses multi-tab parallel booking to maximize booking speed.

Strategy:
1. Pre-warm: Browser + tabs ready before midnight
2. Scan: Batch JS scans all target dates in one call per month
3. Parallel book: Multiple tabs attempt different hotels simultaneously
4. Retry: Continuous scanning until bookings secured or timeout

Usage:
    uv run blitz.py                  # Normal blitz mode
    uv run blitz.py --dry-run        # Scan only, no booking
    uv run blitz.py --wait-until HH:MM  # Wait until specified time to start
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pydoll.browser.chromium import Chrome

from browser import create_browser_options, setup_network_blocking
from cache import load_cached_url, save_calendar_url, get_booked_hotels_for_date
from navigation import acquire_calendar_url_with_captcha
from blitz_scanner import batch_scan_month, fast_navigate_next_month, scan_all_months
from blitz_booking import fast_process_day, fast_click_date, fast_get_hotels, fast_book_hotel
from config import (
    NUM_MONTHS_TO_SKIP,
    NUM_MONTHS_TO_SCAN,
    TARGET_DATES,
    SEPARATOR_WIDTH,
    LOG_ARROW,
    LOG_SUCCESS,
    LOG_ERROR,
    LOG_WARNING,
    LOG_SEPARATOR,
    LOG_EQUALS,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_RESET,
)

# Blitz mode configuration
BLITZ_MAX_TABS = 3  # Max parallel booking tabs
BLITZ_SCAN_RETRIES = 50  # How many scan cycles before giving up
BLITZ_RETRY_DELAY = 0.5  # Delay between scan retries
BLITZ_TIMEOUT_MINUTES = 10  # Total timeout for blitz session


async def parallel_book_hotels(browser, calendar_url, date, hotels, skip_months, month_num):
    """Book multiple hotels in parallel using separate tabs.

    Opens up to BLITZ_MAX_TABS browser tabs, each attempting to book a different
    hotel for the same date simultaneously.

    Args:
        browser: Chrome browser instance
        calendar_url: Calendar URL
        date: Target date string
        hotels: List of hotel names to try
        skip_months: Months to skip
        month_num: Month offset from skip position

    Returns:
        str or None: Name of successfully booked hotel
    """
    # Limit to max parallel tabs
    batch = hotels[:BLITZ_MAX_TABS]
    print(f"\n{LOG_EQUALS * SEPARATOR_WIDTH}")
    print(f"PARALLEL BOOKING: {date} - {len(batch)} hotels simultaneously")
    print(f"{LOG_EQUALS * SEPARATOR_WIDTH}")

    async def attempt_booking_in_tab(hotel_name):
        """Single tab booking attempt."""
        tab = None
        try:
            tab = await browser.new_tab(url=calendar_url)
            # Wait for page to fully load before navigating
            await asyncio.sleep(0.8)

            # Navigate to correct month
            total_nav = skip_months + month_num
            for i in range(total_nav):
                if not await fast_navigate_next_month(tab):
                    # Retry once after a brief wait
                    await asyncio.sleep(0.3)
                    if not await fast_navigate_next_month(tab):
                        return None

            # Click date
            if not await fast_click_date(tab, date):
                return None
            await asyncio.sleep(0.3)

            # Book this hotel
            if await fast_book_hotel(tab, date, hotel_name):
                return hotel_name

            return None
        except Exception as e:
            print(f"  {LOG_ERROR} Tab error for {hotel_name[:30]}: {str(e)[:50]}")
            return None

    # Run all booking attempts in parallel
    results = await asyncio.gather(
        *[attempt_booking_in_tab(h) for h in batch],
        return_exceptions=True,
    )

    # Check results
    for result in results:
        if isinstance(result, str):
            print(f"\n{LOG_SUCCESS * 3} PARALLEL BOOKING WON: {result}")
            return result

    # If first batch failed and there are more hotels, try next batch
    remaining = hotels[BLITZ_MAX_TABS:]
    if remaining:
        print(f"{LOG_ARROW} First batch failed, trying next {len(remaining[:BLITZ_MAX_TABS])} hotels...")
        return await parallel_book_hotels(
            browser, calendar_url, date, remaining, skip_months, month_num
        )

    return None


async def blitz_scan_and_book(browser, tab, calendar_url, dry_run=False):
    """Single blitz scan + book cycle.

    Scans all target months, then attempts parallel booking for available dates.

    Args:
        browser: Chrome browser instance
        tab: Main scanning tab
        calendar_url: Calendar URL
        dry_run: If True, only scan, don't book

    Returns:
        bool: True if a booking was made (or availability found in dry-run)
    """
    # Scan all months using batch JS
    all_available = await scan_all_months(
        tab,
        num_months=NUM_MONTHS_TO_SCAN,
        skip_months=NUM_MONTHS_TO_SKIP,
    )

    if not all_available:
        return False

    print(f"\n{COLOR_GREEN}{LOG_SUCCESS} Found {len(all_available)} available date(s){COLOR_RESET}")

    if dry_run:
        print(f"{LOG_ARROW} DRY RUN - skipping booking")
        return True

    # Process each available date
    for day_info in all_available:
        date = day_info["full_date"]
        month_num = day_info["month_num"]
        skip_months = day_info["skip_months"]

        # Navigate to date to get hotel list
        await tab.go_to(calendar_url)
        await asyncio.sleep(0.3)

        total_nav = skip_months + month_num
        for _ in range(total_nav):
            if not await fast_navigate_next_month(tab):
                continue

        if not await fast_click_date(tab, date):
            continue
        await asyncio.sleep(0.3)

        # Get available hotels
        hotels = await fast_get_hotels(tab)
        if not hotels:
            continue

        # Filter already booked
        booked = get_booked_hotels_for_date(date)
        available = [h for h in hotels if h not in booked]
        if not available:
            print(f"  {LOG_WARNING} All hotels already booked for {date}")
            continue

        # Parallel booking
        result = await parallel_book_hotels(
            browser, calendar_url, date, available, skip_months, month_num
        )
        if result:
            return True

    return False


async def blitz_mode(dry_run=False, wait_until=None):
    """Main blitz mode entry point.

    Args:
        dry_run: If True, only scan, don't book
        wait_until: Optional time string "HH:MM" to wait before starting
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'=' * SEPARATOR_WIDTH}")
    print(f"  BLITZ MODE {'(DRY RUN) ' if dry_run else ''}ACTIVATED")
    print(f"  {timestamp}")
    print(f"  Target dates: {', '.join(TARGET_DATES)}")
    print(f"  Max parallel tabs: {BLITZ_MAX_TABS}")
    print(f"  Scan retries: {BLITZ_SCAN_RETRIES}")
    print(f"{'=' * SEPARATOR_WIDTH}\n")

    # Wait until specified time
    if wait_until:
        await wait_for_time(wait_until)

    # Get calendar URL
    cached_url = load_cached_url()
    if not cached_url:
        print(f"{LOG_ARROW} No cached URL, acquiring...")
        cached_url = await acquire_calendar_url_with_captcha()
        if cached_url:
            save_calendar_url(cached_url)
        else:
            print(f"{LOG_ERROR} Failed to acquire URL")
            return

    calendar_url = cached_url
    print(f"{LOG_ARROW} Calendar URL ready")

    # Launch browser with fast options (INTERACTIVE page load + persistent cache)
    options = create_browser_options(headless=True, fast=True)
    deadline = datetime.now() + timedelta(minutes=BLITZ_TIMEOUT_MINUTES)

    async with Chrome(options=options) as browser:
        tab = await browser.start()

        # Block non-essential resources (fonts, analytics) for faster page loads
        await setup_network_blocking(tab)

        for attempt in range(1, BLITZ_SCAN_RETRIES + 1):
            if datetime.now() > deadline:
                print(f"\n{LOG_ERROR} Blitz timeout ({BLITZ_TIMEOUT_MINUTES}min)")
                break

            print(f"\n{LOG_SEPARATOR * SEPARATOR_WIDTH}")
            print(f"BLITZ SCAN #{attempt}/{BLITZ_SCAN_RETRIES} - {datetime.now().strftime('%H:%M:%S')}")
            print(f"{LOG_SEPARATOR * SEPARATOR_WIDTH}")

            try:
                await tab.go_to(calendar_url)
                await asyncio.sleep(0.2)

                if await blitz_scan_and_book(browser, tab, calendar_url, dry_run=dry_run):
                    print(f"\n{'=' * SEPARATOR_WIDTH}")
                    print(f"  {LOG_SUCCESS} BLITZ MODE COMPLETE - BOOKING SECURED!")
                    print(f"{'=' * SEPARATOR_WIDTH}")
                    return
            except Exception as e:
                print(f"{LOG_ERROR} Scan #{attempt} error: {str(e)[:80]}")

            if attempt < BLITZ_SCAN_RETRIES:
                await asyncio.sleep(BLITZ_RETRY_DELAY)

    print(f"\n{LOG_ERROR} Blitz mode ended - no bookings made")


async def wait_for_time(time_str):
    """Wait until the specified time (HH:MM format).

    Args:
        time_str: Time in "HH:MM" format
    """
    hour, minute = map(int, time_str.split(":"))
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if target <= now:
        target += timedelta(days=1)

    wait_seconds = (target - now).total_seconds()
    print(f"{LOG_ARROW} Waiting until {time_str} ({wait_seconds:.0f}s away)...")
    print(f"{LOG_ARROW} Will start at: {target.strftime('%Y-%m-%d %H:%M:%S')}")

    # Countdown with periodic updates
    while datetime.now() < target:
        remaining = (target - datetime.now()).total_seconds()
        if remaining > 60:
            print(f"  {LOG_ARROW} {remaining:.0f}s remaining...")
            await asyncio.sleep(30)
        elif remaining > 5:
            print(f"  {LOG_ARROW} {remaining:.0f}s remaining...")
            await asyncio.sleep(5)
        else:
            await asyncio.sleep(0.1)

    print(f"\n{LOG_SUCCESS} GO TIME! Starting blitz...")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    wait_until = None

    if "--wait-until" in sys.argv:
        idx = sys.argv.index("--wait-until")
        if idx + 1 < len(sys.argv):
            wait_until = sys.argv[idx + 1]

    asyncio.run(blitz_mode(dry_run=dry_run, wait_until=wait_until))
