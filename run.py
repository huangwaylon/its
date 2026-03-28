#!/usr/bin/env python3
"""Indefinite booking loop with automatic CAPTCHA re-solving.

Usage:
    .venv/bin/python run.py
"""
import asyncio
import os
import subprocess
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from captcha_solver import get_calendar_url, CALENDAR_URL_CACHE
import main as booking

MAX_CAPTCHA_FAILURES = 5

# ANSI colors (reuse from booking module)
R = booking.R
G = booking.G
Y = booking.Y
C = booking.C
B = booking.B
X = booking.X


def check_cached_url():
    """Read calendar_url_cache.txt and test if the URL is still valid (HTTP 200).
    Returns the URL string if valid, None otherwise."""
    try:
        url = open(CALENDAR_URL_CACHE).read().strip()
    except FileNotFoundError:
        return None
    if not url:
        return None
    print(f"Testing cached URL: {url[:80]}...")
    r = subprocess.run(
        ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10', url],
        capture_output=True, text=True,
    )
    status = r.stdout.strip()
    if status == '200':
        print(f"{G}Cached URL is valid (200){X}")
        return url
    print(f"{Y}Cached URL returned {status}, need fresh CAPTCHA solve{X}")
    return None


def solve_captcha_sync():
    """Run the async captcha solver synchronously. Returns URL string or None."""
    return asyncio.run(get_calendar_url())


def run_booking_round(calendar_url):
    """Spawn one thread per target date, wait for all to finish.

    Returns:
        (results_dict, any_expired)
        results_dict: {date: [hotels_booked]}
        any_expired: True if any thread reported URL expiry
    """
    dates = booking.TARGET_DATES
    print(f"\n{B}Booking {len(dates)} dates in parallel: {', '.join(dates)}{X}")
    print(f"Calendar URL: {calendar_url[:80]}...")
    print("=" * 60)

    any_expired = False
    results = {}

    with ThreadPoolExecutor(max_workers=len(dates)) as pool:
        futures = {}
        for i, date in enumerate(dates):
            label = f"D{i+1} {date}"
            futures[pool.submit(
                booking.book_all_hotels_for_date, date, label, calendar_url
            )] = date

        for future in as_completed(futures):
            date, booked_list, expired = future.result()
            if expired:
                any_expired = True
            results[date] = booked_list

    return results, any_expired


def print_round_results(results):
    print("\n" + "=" * 60)
    print(f"{B}ROUND RESULTS{X}")
    print("=" * 60)
    for date in booking.TARGET_DATES:
        booked_list = results.get(date, [])
        if booked_list:
            print(f"  {G}{date}: {len(booked_list)} booked{X}")
            for h in booked_list:
                print(f"    {G}- {h}{X}")
        else:
            print(f"  {date}: none booked")


def main():
    print("=" * 60)
    print(f"{B}ITS INDEFINITE BOOKING LOOP{X}")
    print(f"Email: {booking.EMAIL}")
    print(f"Dates: {', '.join(booking.TARGET_DATES)}")
    print("=" * 60)

    consecutive_captcha_failures = 0
    round_num = 0
    calendar_url = check_cached_url()

    while True:
        round_num += 1

        # Solve captcha if we don't have a valid URL
        if not calendar_url:
            print(f"\n{B}{'#' * 60}")
            print(f"# ROUND {round_num} -- Solving CAPTCHA...")
            print(f"{'#' * 60}{X}")

            calendar_url = solve_captcha_sync()
            if not calendar_url:
                consecutive_captcha_failures += 1
                print(f"{R}CAPTCHA solve failed ({consecutive_captcha_failures}/{MAX_CAPTCHA_FAILURES}){X}")
                if consecutive_captcha_failures >= MAX_CAPTCHA_FAILURES:
                    print(f"{R}Too many consecutive CAPTCHA failures, exiting.{X}")
                    sys.exit(1)
                print(f"{Y}Retrying in 30 seconds...{X}")
                time.sleep(30)
                continue

            consecutive_captcha_failures = 0
            print(f"{G}Got calendar URL: {calendar_url[:80]}...{X}")
        else:
            print(f"\n{B}{'#' * 60}")
            print(f"# ROUND {round_num} -- Using valid URL")
            print(f"{'#' * 60}{X}")

        results, any_expired = run_booking_round(calendar_url)
        print_round_results(results)

        if any_expired:
            print(f"\n{Y}URL expired during booking. Will re-solve CAPTCHA...{X}")
            calendar_url = None
        else:
            print(f"\n{C}All threads completed. Restarting booking round...{X}")


if __name__ == '__main__':
    main()
