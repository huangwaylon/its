#!/usr/bin/env python3
"""Indefinite booking loop with automatic CAPTCHA re-solving.

Usage:
    .venv/bin/python run.py
"""
import asyncio
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from captcha_solver import get_calendar_url, CALENDAR_URL_CACHE
import main as booking
from main import log, R, G, Y, C, B, X

MAX_CAPTCHA_FAILURES = 5


MONTH_ABBR = ['', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
              'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']


def group_dates_by_month(dates):
    """Group date strings by YYYY-MM. Returns {month_str: [dates]}."""
    months = {}
    for d in dates:
        m = d[:7]
        months.setdefault(m, []).append(d)
    return months


def check_cached_url():
    """Read calendar_url_cache.txt and test if the URL is still valid (HTTP 200).
    Returns the URL string if valid, None otherwise."""
    try:
        url = open(CALENDAR_URL_CACHE).read().strip()
    except FileNotFoundError:
        return None
    if not url:
        return None
    log(f"Testing cached URL: {url[:80]}...")
    r = subprocess.run(
        ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10', url],
        capture_output=True, text=True,
    )
    status = r.stdout.strip()
    if status == '200':
        log(f"{G}Cached URL is valid (200){X}")
        return url
    log(f"{Y}Cached URL returned {status}, need fresh CAPTCHA solve{X}")
    return None


def solve_captcha_sync():
    """Run the async captcha solver synchronously. Returns URL string or None."""
    return asyncio.run(get_calendar_url())


def run_booking_round(calendar_url):
    """Spawn one scanner thread per month, wait for all to finish.

    Each scanner polls its month's calendar with 2 requests/cycle (1 GET + 1 POST)
    and spawns parallel booking threads when availability is found.

    Returns:
        (results_dict, any_expired)
        results_dict: {date: [hotels_booked]}
        any_expired: True if any thread reported URL expiry
    """
    months = group_dates_by_month(booking.TARGET_DATES)
    month_labels = [f"{MONTH_ABBR[int(m[5:7])]}" for m in months]
    log(f"\n{B}Scanning {len(months)} months ({', '.join(month_labels)}) "
        f"for {len(booking.TARGET_DATES)} dates{X}")
    log(f"Calendar URL: {calendar_url[:80]}...")
    log("=" * 60)

    any_expired = False
    results = {}

    with ThreadPoolExecutor(max_workers=len(months)) as pool:
        futures = {}
        for month_str, dates in months.items():
            label = MONTH_ABBR[int(month_str[5:7])]
            futures[pool.submit(
                booking.scan_and_book_month, month_str, dates, label, calendar_url
            )] = month_str

        for future in as_completed(futures):
            booked_dict, expired = future.result()
            if expired:
                any_expired = True
            results.update(booked_dict)

    return results, any_expired


def print_round_results(results):
    log("\n" + "=" * 60)
    log(f"{B}ROUND RESULTS{X}")
    log("=" * 60)
    for date in booking.TARGET_DATES:
        booked_list = results.get(date, [])
        if booked_list:
            log(f"  {G}{date}: {len(booked_list)} booked{X}")
            for h in booked_list:
                log(f"    {G}- {h}{X}")
        else:
            log(f"  {date}: none booked")


def main():
    log("=" * 60)
    log(f"{B}ITS INDEFINITE BOOKING LOOP{X}")
    log(f"Email: {booking.EMAIL}")
    log(f"Dates: {', '.join(booking.TARGET_DATES)}")
    log("=" * 60)

    consecutive_captcha_failures = 0
    round_num = 0
    calendar_url = check_cached_url()

    while True:
        round_num += 1

        # Solve captcha if we don't have a valid URL
        if not calendar_url:
            log(f"\n{B}{'#' * 60}")
            log(f"# ROUND {round_num} -- Solving CAPTCHA...")
            log(f"{'#' * 60}{X}")

            calendar_url = solve_captcha_sync()
            if not calendar_url:
                consecutive_captcha_failures += 1
                log(f"{R}CAPTCHA solve failed ({consecutive_captcha_failures}/{MAX_CAPTCHA_FAILURES}){X}")
                if consecutive_captcha_failures >= MAX_CAPTCHA_FAILURES:
                    log(f"{R}Too many consecutive CAPTCHA failures, exiting.{X}")
                    sys.exit(1)
                log(f"{Y}Retrying in 30 seconds...{X}")
                time.sleep(30)
                continue

            consecutive_captcha_failures = 0
            log(f"{G}Got calendar URL: {calendar_url[:80]}...{X}")
        else:
            log(f"\n{B}{'#' * 60}")
            log(f"# ROUND {round_num} -- Using valid URL")
            log(f"{'#' * 60}{X}")

        results, any_expired = run_booking_round(calendar_url)
        print_round_results(results)

        if any_expired:
            log(f"\n{Y}URL expired during booking. Will re-solve CAPTCHA...{X}")
            calendar_url = None
        else:
            log(f"\n{C}All threads completed. Restarting booking round...{X}")


if __name__ == '__main__':
    main()
