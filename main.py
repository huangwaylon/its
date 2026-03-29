#!/usr/bin/env python3
"""Entry point — starts booking threads and a URL monitor.

- Booking threads (1 per month) run indefinitely, reading the calendar URL
  from calendar_url_cache.txt each cycle. They only book; never trigger CAPTCHA.
- URL monitor thread runs indefinitely, checking the cached URL validity.
  If invalid or missing, it solves the CAPTCHA synchronously (blocking until done).

Usage:
    uv run main.py
"""
import asyncio
import subprocess
import threading
import time
from datetime import datetime

import captcha_solver
from captcha_solver import get_calendar_url, CALENDAR_URL_CACHE
import book_hotels
from book_hotels import R, G, Y, C, B, X
from display import SplitDisplay

MONTH_ABBR = ['', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
              'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
URL_CHECK_INTERVAL = 10  # seconds between URL validity checks
URL_REFRESH_INTERVAL = 600  # seconds between proactive URL refreshes

display = SplitDisplay()


def url_log(msg=''):
    """Log to the left (URL monitor) panel."""
    ts = datetime.now().strftime('%H:%M:%S')
    display.add_left(f'{ts} {msg}')


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
    r = subprocess.run(
        ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10', url],
        capture_output=True, text=True,
    )
    status = r.stdout.strip()
    if status == '200':
        url_log(f"{C}URL check: valid (200){X}")
        return url
    url_log(f"{Y}URL check: cached URL returned {status}{X}")
    return None


def url_monitor():
    """Monitor the calendar URL cache and solve CAPTCHA when needed.

    Runs forever in its own thread. Proactively refreshes the URL every
    URL_REFRESH_INTERVAL seconds, even if the current URL is still valid.
    Because the CAPTCHA solve is synchronous, it naturally blocks
    re-triggering while a solve is in progress.
    """
    last_solve = 0.0
    while True:
        url = check_cached_url()
        due_for_refresh = time.time() - last_solve >= URL_REFRESH_INTERVAL

        if url and not due_for_refresh:
            time.sleep(URL_CHECK_INTERVAL)
            continue

        if url:
            url_log(f"{Y}Proactive refresh ({int(time.time() - last_solve)}s since last solve)...{X}")
        else:
            url_log(f"{B}URL invalid or missing, solving CAPTCHA...{X}")

        try:
            new_url = asyncio.run(get_calendar_url())
            if new_url:
                last_solve = time.time()
                url_log(f"{G}New URL saved: {new_url[:80]}...{X}")
            else:
                url_log(f"{R}CAPTCHA solve failed, will retry next cycle{X}")
                if url:  # Had valid URL; reset timer to avoid spamming retries
                    last_solve = time.time()
        except Exception as e:
            url_log(f"{R}CAPTCHA solve error: {e}{X}")
            if url:  # Had valid URL; reset timer to avoid spamming retries
                last_solve = time.time()

        time.sleep(URL_CHECK_INTERVAL)


def main():
    # Wire up log handlers for split display
    book_hotels._log_handler = display.add_right
    captcha_solver._log_handler = display.add_left

    url_log("=" * 60)
    url_log(f"{B}ITS BOOKING SYSTEM{X}")
    url_log(f"Email: {book_hotels.EMAIL}")
    url_log(f"Dates: {', '.join(book_hotels.TARGET_DATES)}")
    url_log("=" * 60)

    # Start URL monitor thread
    monitor = threading.Thread(target=url_monitor, daemon=True)
    monitor.start()
    url_log(f"{C}URL monitor thread started{X}")

    # Start booking scanner threads (1 per month)
    months = group_dates_by_month(book_hotels.TARGET_DATES)
    month_labels = [MONTH_ABBR[int(m[5:7])] for m in months]
    url_log(f"{B}Starting {len(months)} scanner threads ({', '.join(month_labels)}) "
            f"for {len(book_hotels.TARGET_DATES)} dates{X}")
    url_log("=" * 60)

    threads = []
    for month_str, dates in months.items():
        label = MONTH_ABBR[int(month_str[5:7])]
        t = threading.Thread(
            target=book_hotels.scan_and_book_month,
            args=(month_str, dates, label),
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Run split display (blocks until Ctrl+C)
    try:
        display.run()
    except KeyboardInterrupt:
        pass
    print(f"{Y}Interrupted, exiting.{X}")


if __name__ == '__main__':
    main()
