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
- `captcha_solver.py`: `playwright`, `Pillow`, ollama running locally with `qwen3-vl:8b` model
- `main.py`: combines both, needs all of the above

There are no tests, linting, or formatting tools configured.

## Architecture

Three scripts with clear separation of concerns:

### `config.py` — User-configurable settings

Central configuration file for all tunable constants. Stdlib only (`import os`). Contains:
- Paths: `CALENDAR_URL_CACHE`, `BOOKINGS_FILE`
- Booking settings: `TARGET_DATES`, `EMAIL`, `NUM_GUESTS`
- Network tuning: `RETRY_DELAY`, `CURL_MAX_ATTEMPTS`, `URL_CHECK_INTERVAL`, `URL_REFRESH_INTERVAL`
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
- `curl(cookie_file, method, url, data, headers)` — raw HTTP via curl subprocess, returns `(status, body, location)`
- `ex(html, pat)` — regex group(1) extraction helper
- `save_booking(date, hotel_name)` — thread-safe append to `bookings.json`
- `get_booked_hotels(date)` — thread-safe read from `bookings.json`
- `_read_cached_url()` — reads `CALENDAR_URL_CACHE`, returns URL string or None
- `_load_bookings()` — private, reads `bookings.json`, must only be called under `_bookings_lock`

### `captcha_solver.py` — reCAPTCHA v2 solver

Uses Playwright (Chromium with `--headless=new`) + ollama vision model to solve reCAPTCHA v2 image challenges. Has its own `log()` function (elapsed-seconds format) separate from `book_hotels.log()` (wall-clock format).

**Key functions**:
- `get_calendar_url()` — async. Full flow: launch browser → navigate to ITS homepage → click "カレンダーから探す" → solve CAPTCHA → click "次へ" → save resulting URL to `CALENDAR_URL_CACHE`. Returns URL string or None.
- `solve_recaptcha(page, max_attempts=8)` — async. Generic reCAPTCHA v2 solver for any page. Returns g-recaptcha-response token or None.
- `ask_vision(image_b64, prompt, no_think=False)` — async. Sends image+prompt to ollama via curl subprocess. Uses temp file for payload. Semaphore limits to 2 concurrent requests. Retries once on failure.

**CAPTCHA solving strategy** (in `solve_recaptcha`):
1. Click reCAPTCHA checkbox. If solved immediately, return token.
2. For each challenge attempt (up to `max_attempts`):
   - Detect grid type (3x3 or 4x4). Skip 4x4 (too slow for ollama).
   - **Strategy 1**: Screenshot full challenge area (300px), ask vision model for matching tile numbers in one call (`no_think=True` for speed).
   - **Strategy 2** (fallback): Screenshot each tile individually, classify all in parallel via `asyncio.gather`.
   - Click matching tiles. Handle dynamic replacement tiles (up to 5 rounds).
   - Click verify. Check if solved. Handle "select more" errors with re-analysis.
   - If not solved, reload challenge and retry.

**Config**: `OLLAMA_URL = 'http://localhost:11434'`, `OLLAMA_MODEL = 'qwen3-vl:8b'`, `DEBUG_DIR = '/tmp/captcha_debug'`

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

- `calendar_url_cache.txt` — Current calendar session URL. Written by `captcha_solver.get_calendar_url()`, read by `book_hotels._read_cached_url()` and `main.check_cached_url()`. Contains a URL with an `s=` session token that expires periodically. Path defined once in `config.CALENDAR_URL_CACHE`.
- `bookings.json` — Records successful bookings as `{date: [hotel_names]}`. Thread-safe via `_bookings_lock`.

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

## Logging

- `book_hotels.log()`: `HH:MM:SS message` format with ANSI colors. Used by `main.py` too (imported).
- `captcha_solver.log()`: `[elapsed_seconds] message` format (elapsed since module import). Independent.
- ANSI color codes: red `R` (errors/failures), green `G` (success/booked), yellow `Y` (waiting/warnings), cyan `C` (info), bold `B` (headers/totals), reset `X`.
