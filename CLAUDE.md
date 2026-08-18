# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ITS Calendar Booker — an automated booking system for ITS Health Insurance Facility Calendar (as.its-kenpo.or.jp). It monitors available dates at Japanese hospitality facilities, books hotels using parallel HTTP requests via curl, and automatically re-solves CAPTCHAs when session tokens expire.

## Setup & Running

```bash
# Start the system (recommended): auto-solves CAPTCHA, books forever
uv run main.py

# Solve CAPTCHA only (saves URL to calendar_url_cache.txt)
.venv/bin/python captcha_solver.py
```

**Dependencies**:
- `book_hotels.py`: stdlib only + curl (no pip packages)
- `captcha_solver.py`: `pydoll-python` (CDP browser automation with real Chrome)
- `main.py`: combines both, needs all of the above

There are no tests, linting, or formatting tools configured.

## Architecture

Three scripts with clear separation of concerns:

### `config.py` — User-configurable settings

Central configuration file for all tunable constants. Stdlib only (`import os`). Contains:
- Paths: `CALENDAR_URL_CACHE`, `BOOKINGS_FILE`
- Booking settings: `TARGET_DATES`, `EMAIL`, `NUM_GUESTS`
- Network tuning: `RETRY_DELAY`, `CURL_MAX_ATTEMPTS`, `URL_CHECK_INTERVAL`, `URL_REFRESH_INTERVAL`
- HTTP fingerprint: `BROWSER_HEADERS`, `ACCEPT_LANGUAGE`, `FALLBACK_USER_AGENT`, `USER_AGENT_CACHE`
- Hotel skip list: `SKIP_HOTELS` (with commented-out "keep" list for reference)

Imported by `book_hotels.py`, `captcha_solver.py`, and `main.py`.

### `main.py` — Entry point and orchestrator

Starts two kinds of threads and waits forever (Ctrl+C to stop):

**Booking scanner threads** (1 per month, daemon):
- Groups `TARGET_DATES` by month, spawns one `threading.Thread` per month
- Each calls `book_hotels.scan_and_book_month()` which loops indefinitely
- Reads URL from `calendar_url_cache.txt` each cycle
- If URL is missing or expired, logs and waits for next cycle (never triggers CAPTCHA)

**URL monitor thread** (1 total, daemon):
- `url_monitor()` loops forever
- Each cycle: `check_cached_url()` reads the cache file and makes a curl GET to verify HTTP 200
- If valid → sleep `URL_CHECK_INTERVAL` (10s)
- If invalid/missing → call `asyncio.run(get_calendar_url())` synchronously in this thread
- Synchronous design naturally prevents re-triggering while a solve is in progress (no locks needed)
- On failure, sleeps and retries indefinitely

**Key functions**:
- `main()` — starts monitor thread, starts scanner threads, joins scanners
- `url_monitor()` — cache validity loop, triggers CAPTCHA solve when needed
- `check_cached_url()` — reads cache file, curl GET to test validity, returns URL or None
- `group_dates_by_month(dates)` — groups `YYYY-MM-DD` strings by `YYYY-MM`

### `book_hotels.py` — Booking engine

Pure booking logic. Never triggers CAPTCHA solving. All calendar URL access goes through `_read_cached_url()` which reads `CALENDAR_URL_CACHE` (imported from `config`).

**Month scanner** (`scan_and_book_month(month_str, target_dates, label)`): One thread per month, runs forever. Each cycle:
1. Read URL from cache via `_read_cached_url()`. If missing → log, sleep `RETRY_DELAY`, retry
2. GET calendar page, extract CSRF tokens (1 request). If non-200 → log, sleep, retry
3. POST to navigate to target month (1 request) — response contains availability for ALL dates in that month
4. Check each target date's CSS class for `empty` (available) via regex
5. For each available date, spawn a booking thread via `ThreadPoolExecutor`
6. Wait for all booking threads to complete, then loop back to step 1

**Booking flow per date** (`book_all_hotels_for_date(target_date, label)`): Single-pass, returns `(date, list_of_booked_hotels)`.
1. Read URL from cache. If missing → return `(date, [])`
2. GET calendar page, extract CSRF/auth tokens via regex. If non-200 → return
3. POST to navigate to target month if date not visible in current view
4. POST to `service_group_select` to get hotel list
5. Filter out `SKIP_HOTELS` + already-booked hotels (from `bookings.json`)
6. For each hotel sequentially, call `book_one_hotel()` (fresh session per hotel)

**Single hotel booking** (`book_one_hotel(tag, c, target_date, s_param, auth, hotel_id, hotel_name)`): Steps 3-9 of the booking flow. Returns True/False.
1. POST to select hotel → extract services list
2. POST to select service → follow 302 redirect
3. GET booking form → extract CSRF, auth, form action, coma search param
4. POST to search rooms (AJAX) → extract room IDs and session GUID
5. POST to submit room selection → follow 302
6. POST to agree to rules page → follow 302
7. POST to submit email confirmation → check for `send_complete` in response

**Key functions**:
- `scan_and_book_month(month_str, target_dates, label)` — month scanner loop
- `book_all_hotels_for_date(target_date, label)` — single-pass booking for one date
- `book_one_hotel(tag, c, target_date, s_param, auth, hotel_id, hotel_name)` — books one hotel
- `curl(cookie_file, method, url, data, headers)` — raw HTTP via curl subprocess, returns `(status, body, location)`. `body` is a `Response` (a `str` subclass carrying `.headers`, `.location`, `.request`), so call sites treat it as a plain string. Never raises: a transport failure returns `(0, '', None)`, because the scan loop has no `except` around its curl calls.
- `header_args(headers)` — curl `-H` flags for the merged header set; also used by `main.check_cached_url()`
- `_merge_headers(headers)` — per-call headers over the browser defaults, case-insensitive. Merging in Python (rather than appending `-H` flags) is required: curl emits every header it is given, so a default plus a per-call `Accept` would send both.
- `_user_agent()` — reads `USER_AGENT_CACHE`, re-reading on mtime change; falls back to `FALLBACK_USER_AGENT`
- `redact_url(url)` / `_redact_headers(hdrs)` / `_redact_set_cookie(v)` / `_redact_body(b)` / `_fingerprint(v)` — strip session material from anything written to `debug_responses/`. All fail closed: anything unrecognized is fingerprinted rather than passed through.
- `ex(html, pat)` — regex group(1) extraction helper
- `save_booking(date, hotel_name)` — thread-safe append to `bookings.json`
- `get_booked_hotels(date)` — thread-safe read from `bookings.json`
- `_read_cached_url()` — reads `CALENDAR_URL_CACHE`, returns URL string or None
- `_load_bookings()` — private, reads `bookings.json`, must only be called under `_bookings_lock`

**Debug dumps** (`_dump_debug`): on an unexpected response, writes two sibling files to `DEBUG_DIR` — `<stem>.html` (body) and `<stem>.headers.txt` (redacted response headers). The headers are the diagnostic payload: `x-runtime` present means Rails generated the response, absent means Apache/ALB/WAF did; `content-length: 0` with an empty body means the server sent nothing by intent rather than the body being truncated; `set-cookie` shows whether the session was re-issued or reset (`Max-Age=0`). Redaction is a **whitelist** — unrecognized header values are replaced with `[len=N sha256=xxxxxxxx]`, so a future session-bearing header cannot leak by default. Cookie names and attributes are kept, values fingerprinted; the digest prefix is stable, so two dumps can be compared to tell a fresh `_src_session` from a reused one without either file containing the id.

### `captcha_solver.py` — Cloudflare Turnstile solver

Uses pydoll (CDP-based Chrome automation) to solve Cloudflare Turnstile CAPTCHA. Pydoll drives real Chrome via DevTools Protocol, avoiding bot detection that Playwright triggers. Requires non-headless mode (visible Chrome window) since headless Chrome gets rejected by Turnstile. Has its own `log()` function (elapsed-seconds format) separate from `book_hotels.log()` (wall-clock format).

**Key functions**:
- `get_calendar_url()` — async. Full flow: launch Chrome via pydoll → navigate to ITS homepage → click "カレンダーから探す" → solve Turnstile → submit form → save resulting URL to `CALENDAR_URL_CACHE`. Returns URL string or None.
- `_save_user_agent(tab)` — async. Records Chrome's `navigator.userAgent` to `USER_AGENT_CACHE` so the curl requests that replay the session token carry the UA of the browser that minted it. Skips a `Headless` UA.
- `_script_value(result)` — unwraps a CDP `Runtime.evaluate` result to its plain value
- `solve_turnstile(tab, max_attempts=3)` — async. Solves Cloudflare Turnstile on the given pydoll tab. Returns cf-turnstile-response token or None.
- `_click_turnstile_checkbox(tab)` — async. Finds the `.cf-turnstile` widget, calculates checkbox coordinates, dispatches CDP mouse events to click it, then polls for the response token.

**Turnstile solving strategy**:
1. Find the `.cf-turnstile` container div and get its bounding box.
2. Calculate checkbox position (~28px from left edge, vertically centered).
3. Dispatch CDP `Input.dispatchMouseEvent` (press+release) at those coordinates — CDP mouse events cross iframe boundaries, reaching the Cloudflare iframe.
4. Poll `input[name="cf-turnstile-response"]` value every 2s until token appears (timeout 30s).
5. If token not generated, reload page and retry (up to 3 attempts).

**Config**: `DEBUG_DIR = '/tmp/captcha_debug'`, `MAX_ATTEMPTS = 3`, `TOKEN_TIMEOUT = 30`

## Concurrency Model

**Threads at runtime** (when started via `main.py`):
- 1 URL monitor thread (daemon)
- N scanner threads (daemon, 1 per month — currently 2: APR, MAY)
- Temporary booking threads (spawned by scanners via `ThreadPoolExecutor`, 1 per available date)

**Shared state**:
- `calendar_url_cache.txt` — Written by captcha solver (in URL monitor thread), read by all scanner/booking threads. Safe via POSIX file atomicity for small files. Readers handle stale/empty data gracefully (return None, retry next cycle).
- `bookings.json` — All access goes through `save_booking()` and `get_booked_hotels()`, both protected by `_bookings_lock` (`threading.Lock`). `_load_bookings()` is private and must only be called under this lock (non-reentrant lock would deadlock if added directly).
- Cookie files — Each thread creates its own temp file (`cookies_scan_*` for scanners, `cookies_*` for booking threads). No sharing.

## Data Files

- `calendar_url_cache.txt` — Current calendar session URL. Written by `captcha_solver.get_calendar_url()`, read by `book_hotels._read_cached_url()` and `main.check_cached_url()`. Contains a URL with an `s=` session token that expires periodically. Path defined once in `config.CALENDAR_URL_CACHE`. **Gitignored** — the `s=` token is a live credential.
- `chrome_user_agent.txt` — UA of the Chrome that solved the most recent CAPTCHA. Written by `captcha_solver._save_user_agent()`, read by `book_hotels._user_agent()`. Gitignored. Absent until the first solve, in which case `FALLBACK_USER_AGENT` is used.
- `bookings.json` — Records successful bookings as `{date: [hotel_names]}`. Thread-safe via `_bookings_lock`.
- `debug_responses/` — Failure dumps from `_dump_debug` (`.html` body + `.headers.txt` redacted headers). **Gitignored**: response bodies embed `s=` tokens in form actions. It was tracked until 2026-08-18; the pre-existing 380 files remain in the history of the (public) GitHub remote. The `.html` body is redacted too (`_redact_body`), since that is the file people actually open.

## Tests

`test_http_layer.py` covers the curl/debug-dump layer — header merging and redaction — against a throwaway localhost HTTP server. Stdlib only; makes no requests to ITS.

```bash
.venv/bin/python test_http_layer.py
```

It needs to bind `127.0.0.1`, which the Apple Claude Code sandbox denies; `.claude/apple/tool_allowlist.csv` allowlists it. It also sets `NO_PROXY` for loopback, since a local HTTP proxy would otherwise intercept and rewrite the responses.

## Configuration (in `config.py`)

- `TARGET_DATES` — List of `YYYY-MM-DD` date strings to book
- `EMAIL` — Email for booking confirmation
- `NUM_GUESTS` — Number of guests per booking (string)
- `BOOKINGS_FILE` — Path to bookings JSON file (default `'bookings.json'`)
- `RETRY_DELAY` — Seconds between scan retry attempts (default 10)
- `CURL_MAX_ATTEMPTS` — Max attempts per curl request (default 2, 1 = no retry)
- `SKIP_HOTELS` — Hotels to never book (commented-out section = "keep" list for reference)
- `URL_CHECK_INTERVAL` — Seconds between URL validity checks (default 10)
- `URL_REFRESH_INTERVAL` — Seconds between proactive URL refreshes (default 600)
- `BROWSER_HEADERS` — Send browser-like headers on the curl requests (default True)
- `ACCEPT_LANGUAGE` — `Accept-Language` sent when `BROWSER_HEADERS` is on
- `FALLBACK_USER_AGENT` — UA used only until the first CAPTCHA solve records Chrome's real one

**Deliberately not sent** as browser headers, each for a specific reason:
- `Origin` — absent means Rails skips its origin check entirely; sending it opts into a check that fails if the app sees `http` behind the ALB, producing an `InvalidAuthenticityToken` redirect indistinguishable from the bug being diagnosed.
- `Sec-Fetch-*`, `sec-ch-ua`, `Upgrade-Insecure-Requests` — one static value must contradict one of the two request classes made here (navigation form POST vs XHR). A self-inconsistent set is a stronger bot signal than sending none.
- `Accept-Encoding` / `--compressed` — this curl build has no brotli or zstd, and a content-encoding failure yields an empty body, i.e. it would manufacture more of the exact 0-byte responses under investigation.
- A mobile UA or a non-Japanese `Accept-Language` — the site may serve a different template, which would break the markup-exact extractors and, worse, the `SKIP_HOTELS` name matching (unmatched names mean booking hotels meant to be skipped).

## Logging

- `book_hotels.log()`: `HH:MM:SS message` format with ANSI colors. Used by `main.py` too (imported).
- `captcha_solver.log()`: `[elapsed_seconds] message` format (elapsed since module import). Independent.
- ANSI color codes: red `R` (errors/failures), green `G` (success/booked), yellow `Y` (waiting/warnings), cyan `C` (info), bold `B` (headers/totals), reset `X`.
