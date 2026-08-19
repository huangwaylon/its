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
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

import captcha_solver
from captcha_solver import get_calendar_url
from config import (
    CALENDAR_URL_CACHE, URL_CHECK_INTERVAL, URL_REFRESH_INTERVAL, LOG_FILE,
    LOG_MAX_BYTES, LOG_BACKUPS, TARGET_DATES, EMAIL, PRIORITY_HOTELS,
)
import book_hotels
from book_hotels import R, G, Y, C, B, X

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

MONTH_ABBR = ['', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
              'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

# How often the watchdog checks that every worker thread is still alive.
WATCHDOG_INTERVAL = 30

_sink = None  # set in main(); prints to stdout and appends to LOG_FILE


def url_log(msg=''):
    """Log a URL-monitor message to whichever sink main() wired up."""
    formatted = f'{datetime.now().strftime("%H:%M:%S")} {msg}'
    if _sink:
        _sink(formatted)
    else:
        # Before main() runs, and in captcha_solver.py's standalone mode.
        print(_ANSI_RE.sub('', formatted), flush=True)


def group_dates_by_month(dates):
    """Group date strings by YYYY-MM. Returns {month_str: [dates]}."""
    months = {}
    for d in dates:
        m = d[:7]
        months.setdefault(m, []).append(d)
    return months


def check_cached_url():
    """Read calendar_url_cache.txt and test if the URL is still valid (HTTP 200).

    Returns (url, confirmed_valid):
      - (url, True)  — server returned 200, session confirmed active
      - (url, False) — server error (5xx) or connection failure; session assumed valid
      - (None, False) — no cached URL, or session expired (302, 4xx, etc.)
    """
    try:
        with open(CALENDAR_URL_CACHE) as f:
            url = f.read().strip()
    except OSError:
        return None, False
    if not url:
        return None, False
    try:
        r = subprocess.run(
            ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10']
            + book_hotels.header_args() + [url],
            capture_output=True, text=True, timeout=30,
        )
        status = r.stdout.strip()
    except Exception as e:
        # Treated as a server-side blip, not an expired session: a local failure
        # to run curl says nothing about whether the token is still good, and
        # discarding a working URL over it would force a needless CAPTCHA solve.
        url_log(f"{Y}URL check: could not run curl ({e!r}), will retry{X}")
        return url, False
    if status == '200':
        url_log(f"{C}URL check: valid (200){X}")
        return url, True
    if not status or status == '000' or status.startswith('5'):
        url_log(f"{Y}URL check: server error ({status}), will retry{X}")
        return url, False
    url_log(f"{Y}URL check: session invalid ({status}){X}")
    return None, False


def url_monitor():
    """Monitor the calendar URL cache and solve CAPTCHA when needed.

    Runs forever in its own thread. Proactively refreshes the URL every
    URL_REFRESH_INTERVAL seconds, even if the current URL is still valid.
    Because the CAPTCHA solve is synchronous, it naturally blocks
    re-triggering while a solve is in progress.
    """
    last_solve = time.time()
    while True:
        try:
            url, confirmed = check_cached_url()
            due_for_refresh = time.time() - last_solve >= URL_REFRESH_INTERVAL

            if url and (not due_for_refresh or not confirmed):
                time.sleep(URL_CHECK_INTERVAL)
                continue

            if url:
                # A proactive refresh replaces a token that is still working.
                # Bookings hold the old one across a ~7-request chain, so
                # swapping it underneath them risks losing a slot to housekeeping.
                # A repair (url is None) is not deferred - there is nothing left
                # to protect.
                active = book_hotels.active_bookings()
                if active:
                    url_log(f"{Y}Proactive refresh deferred "
                            f"({active} booking(s) in flight){X}")
                    time.sleep(URL_CHECK_INTERVAL)
                    continue
                url_log(f"{Y}Proactive refresh "
                        f"({int(time.time() - last_solve)}s since last solve)...{X}")
            else:
                url_log(f"{B}URL invalid or missing, solving CAPTCHA...{X}")

            try:
                new_url = asyncio.run(get_calendar_url())
                if new_url:
                    last_solve = time.time()
                    # The path, not the token: the URL is a bearer credential
                    # and the log is not the place for it.
                    url_log(f"{G}New URL saved — "
                            f"{book_hotels.redact_url(new_url)}{X}")
                    continue
                url_log(f"{R}CAPTCHA solve failed, will retry next cycle{X}")
            except Exception as e:
                url_log(f"{R}CAPTCHA solve error: {e!r}{X}")
            if url:  # Had a valid URL; reset the timer to avoid spamming retries
                last_solve = time.time()

        except Exception as e:
            # This thread is the only thing that re-solves the CAPTCHA. If it
            # dies, booking stops forever while the process still looks healthy.
            # The watchdog would restart it, but not losing it is cheaper.
            url_log(f"{R}URL monitor error: {e!r}{X}")

        time.sleep(URL_CHECK_INTERVAL)


def _rotate_log():
    """Roll LOG_FILE over once it passes LOG_MAX_BYTES, keeping LOG_BACKUPS.

    The last unattended run wrote 9 MB in six days with no bound at all. Rotation
    happens at startup rather than mid-write so no log handler needs a lock.
    """
    try:
        if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) < LOG_MAX_BYTES:
            return
        for i in range(LOG_BACKUPS - 1, 0, -1):
            src, dst = f'{LOG_FILE}.{i}', f'{LOG_FILE}.{i + 1}'
            if os.path.exists(src):
                os.replace(src, dst)
        os.replace(LOG_FILE, f'{LOG_FILE}.1')
    except OSError as e:
        print(f'Could not rotate {LOG_FILE}: {e}', file=sys.stderr)


class _Worker:
    """A named restartable daemon thread.

    Every worker here is a daemon and main() never joins any of them, so a thread
    that dies leaves the process running with one fewer month being scanned - or,
    for the URL monitor, with nothing left that can re-mint a session. Both
    failures are invisible without a watchdog.
    """

    def __init__(self, name, target, args=()):
        self.name, self.target, self.args = name, target, args
        self.thread = None
        self.restarts = 0

    def start(self):
        self.thread = threading.Thread(target=self.target, args=self.args,
                                       name=self.name, daemon=True)
        self.thread.start()

    def ensure_alive(self):
        if self.thread is not None and self.thread.is_alive():
            return False
        self.restarts += 1
        self.start()
        return True


def watchdog(workers):
    """Restart any worker thread that has stopped. Runs forever."""
    while True:
        time.sleep(WATCHDOG_INTERVAL)
        for w in workers:
            try:
                if w.ensure_alive():
                    url_log(f"{R}Worker '{w.name}' died - restarted "
                            f"(restart #{w.restarts}){X}")
            except Exception as e:
                url_log(f"{R}Watchdog could not restart '{w.name}': {e!r}{X}")


def main():
    global _sink

    _rotate_log()
    log_file = open(LOG_FILE, 'a', encoding='utf-8')
    log_file.write(f'\n=== Session started {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===\n')
    log_file.flush()

    def sink(msg):
        plain = _ANSI_RE.sub('', msg)
        print(plain, flush=True)
        try:
            log_file.write(plain + '\n')
            log_file.flush()
        except OSError:
            pass  # a full disk must not take down a booking thread

    _sink = sink
    book_hotels._log_handler = sink
    captcha_solver._log_handler = sink
    url_log("=" * 60)
    url_log(f"{B}ITS BOOKING SYSTEM{X}")
    url_log(f"Email: {EMAIL}")
    url_log(f"Dates: {', '.join(TARGET_DATES)}")
    url_log(f"Priority hotels: {', '.join(PRIORITY_HOTELS) or '(none)'}")
    url_log("=" * 60)

    months = group_dates_by_month(TARGET_DATES)
    workers = [_Worker('url-monitor', url_monitor)]
    for month_str, dates in months.items():
        label = MONTH_ABBR[int(month_str[5:7])]
        workers.append(_Worker(f'scan-{label}', book_hotels.scan_and_book_month,
                               (month_str, dates, label)))

    url_log(f"{B}Starting {len(months)} scanner threads "
            f"({', '.join(MONTH_ABBR[int(m[5:7])] for m in months)}) "
            f"for {len(TARGET_DATES)} dates{X}")
    url_log("=" * 60)

    for w in workers:
        w.start()

    threading.Thread(target=watchdog, args=(workers,),
                     name='watchdog', daemon=True).start()
    url_log(f"{C}Watchdog started ({len(workers)} workers, "
            f"{WATCHDOG_INTERVAL}s interval){X}")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    print(f"{Y}Interrupted, exiting.{X}")


if __name__ == '__main__':
    main()
