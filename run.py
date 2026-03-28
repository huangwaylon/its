#!/usr/bin/env python3
"""Indefinite booking loop with automatic CAPTCHA re-solving.

Scanner threads run continuously, reading the calendar URL from cache each cycle.
When a thread detects an expired URL, a background CAPTCHA solve is triggered.
Only one CAPTCHA solve runs at a time.

Usage:
    uv run run.py
"""
import asyncio
import subprocess
import sys
import threading
import time

from captcha_solver import get_calendar_url, CALENDAR_URL_CACHE
import main as booking
from main import log, R, G, Y, C, B, X

MAX_CAPTCHA_FAILURES = 5
MONTH_ABBR = ['', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
              'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

# ── CAPTCHA solve coordination ────────────────────────────────────
_solve_lock = threading.Lock()
_solving = False
_consecutive_failures = 0


def request_url_refresh():
    """Trigger a background CAPTCHA solve. Only one runs at a time."""
    global _solving
    with _solve_lock:
        if _solving:
            return
        _solving = True

    def _solve():
        global _solving, _consecutive_failures
        try:
            log(f"\n{B}Background CAPTCHA solve starting...{X}")
            url = asyncio.run(get_calendar_url())
            if url:
                _consecutive_failures = 0
                log(f"{G}New URL saved: {url[:80]}...{X}")
            else:
                _consecutive_failures += 1
                log(f"{R}CAPTCHA solve failed ({_consecutive_failures}/{MAX_CAPTCHA_FAILURES}){X}")
                if _consecutive_failures >= MAX_CAPTCHA_FAILURES:
                    log(f"{R}Too many consecutive CAPTCHA failures, exiting.{X}")
                    sys.exit(1)
        except Exception as e:
            _consecutive_failures += 1
            log(f"{R}CAPTCHA solve error: {e}{X}")
        finally:
            with _solve_lock:
                _solving = False

    threading.Thread(target=_solve, daemon=True).start()


# ── Helpers ───────────────────────────────────────────────────────

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


# ── Main ──────────────────────────────────────────────────────────

def main():
    log("=" * 60)
    log(f"{B}ITS INDEFINITE BOOKING LOOP{X}")
    log(f"Email: {booking.EMAIL}")
    log(f"Dates: {', '.join(booking.TARGET_DATES)}")
    log("=" * 60)

    # Ensure we have a valid URL to start
    if not check_cached_url():
        log(f"\n{B}Solving initial CAPTCHA...{X}")
        url = asyncio.run(get_calendar_url())
        if not url:
            log(f"{R}Initial CAPTCHA solve failed, exiting.{X}")
            sys.exit(1)
        log(f"{G}Got calendar URL: {url[:80]}...{X}")

    # Start scanner threads (they run forever, reading URL from cache each cycle)
    months = group_dates_by_month(booking.TARGET_DATES)
    month_labels = [MONTH_ABBR[int(m[5:7])] for m in months]
    log(f"\n{B}Starting {len(months)} scanner threads ({', '.join(month_labels)}) "
        f"for {len(booking.TARGET_DATES)} dates{X}")
    log("=" * 60)

    threads = []
    for month_str, dates in months.items():
        label = MONTH_ABBR[int(month_str[5:7])]
        t = threading.Thread(
            target=booking.scan_and_book_month,
            args=(month_str, dates, label),
            kwargs={'on_url_expired': request_url_refresh},
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Wait forever (Ctrl+C to stop)
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        log(f"\n{Y}Interrupted, exiting.{X}")


if __name__ == '__main__':
    main()
