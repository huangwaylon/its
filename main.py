#!/usr/bin/env python3
"""Entry point — one scanner thread per target month, plus a URL monitor.

Scanners only book; they never trigger a CAPTCHA. The URL monitor is the only thing
that can re-mint a session, and it solves synchronously in its own thread, which is
what prevents overlapping solves without a lock. A watchdog restarts either.

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
    URL_CHECK_INTERVAL, URL_REFRESH_INTERVAL, LOG_FILE,
    LOG_MAX_BYTES, LOG_BACKUPS, TARGET_DATES, EMAIL, PRIORITY_HOTELS,
)
import book_hotels
from book_hotels import R, G, Y, C, B, X
from book_hotels import log as url_log

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

MONTH_ABBR = ['', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
              'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

# How often the watchdog checks that every worker thread is still alive.
WATCHDOG_INTERVAL = 30


def group_dates_by_month(dates):
    """Group date strings by YYYY-MM. Returns {month_str: [dates]}."""
    months = {}
    for d in dates:
        m = d[:7]
        months.setdefault(m, []).append(d)
    return months


def check_cached_url():
    """Test whether the cached calendar URL is still valid (HTTP 200).

    Returns (url, confirmed_valid):
      - (url, True)  — 200, session confirmed active
      - (url, False) — 5xx or connection failure; session assumed valid
      - (None, False) — no cached URL, or session expired (302, 4xx, etc.)
    """
    url = book_hotels._read_cached_url()
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
        # A server-side blip, not an expired session: a local failure to run curl
        # says nothing about the token, and discarding a working URL over it would
        # force a needless CAPTCHA solve.
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
    """Monitor the calendar URL cache and solve the CAPTCHA when needed.

    Runs forever. Proactively re-solves every URL_REFRESH_INTERVAL even when the
    current URL still works. The solve is synchronous, which is what blocks a second
    one from starting without needing a lock.
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
                # A proactive refresh replaces a token that still works, and a
                # booking holds the old one across a ~10-request chain. A repair
                # (url is None) is never deferred — nothing is left to protect.
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
            # The only thread that can re-solve the CAPTCHA. If it dies, booking
            # stops forever while the process still looks healthy; the watchdog
            # would restart it, but not losing it is cheaper.
            url_log(f"{R}URL monitor error: {e!r}{X}")

        time.sleep(URL_CHECK_INTERVAL)


def _rotate_log():
    """Roll LOG_FILE over once it passes LOG_MAX_BYTES, keeping LOG_BACKUPS.

    At startup rather than mid-write, so no log handler needs a lock.
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

    Every worker is a daemon and main() never joins any of them, so a thread that
    dies leaves the process running with one fewer month scanned — or, for the URL
    monitor, with nothing left that can re-mint a session. Both are invisible
    without a watchdog.
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
