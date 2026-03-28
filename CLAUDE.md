# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ITS Calendar Booker — an automated booking system for ITS Health Insurance Facility Calendar (as.its-kenpo.or.jp). It monitors available dates at Japanese hospitality facilities, books hotels using parallel HTTP requests via curl, and automatically re-solves CAPTCHAs when session tokens expire.

## Setup & Running

```bash
# Indefinite mode (recommended): auto-solves CAPTCHA, books forever
.venv/bin/python run.py

# Manual burst mode: one-shot booking with existing cached URL
python3 main.py

# Solve CAPTCHA only (saves URL to calendar_url_cache.txt)
.venv/bin/python captcha_solver.py

# Test CAPTCHA solver against 2captcha demo
.venv/bin/python captcha_solver.py --demo
```

**Dependencies**:
- `main.py`: stdlib only + curl (no pip packages)
- `captcha_solver.py`: `playwright`, `Pillow`, ollama running locally with `qwen3-vl:8b` model
- `run.py`: combines both, needs all of the above

There are no tests, linting, or formatting tools configured.

## Architecture

Three scripts, each usable independently:

### `run.py` — Indefinite booking orchestrator (primary entry point)

Outer loop that runs forever:
1. Check if `calendar_url_cache.txt` has a valid URL (quick curl GET, check for 200)
2. If not valid, call `captcha_solver.get_calendar_url()` to solve CAPTCHA and get a fresh URL
3. Group `TARGET_DATES` by month, spawn one scanner thread per month
4. Each scanner polls its month's calendar (1 GET + 1 POST per cycle = 2 requests regardless of how many dates)
5. When a scanner finds availability, it spawns parallel booking threads (one per available date)
6. Wait for all scanner threads to finish
7. If any thread reported URL expiry → back to step 2
8. If no expiry → back to step 3 with same URL

Bridges async captcha solver into sync context via `asyncio.run()`. Gives up after 5 consecutive CAPTCHA failures (30s backoff between retries).

### `main.py` — Booking engine (also works standalone)

**Month scanner** (`scan_and_book_month()`): One thread per month. Each cycle:
1. GET calendar page, extract CSRF tokens (1 request)
2. POST to navigate to target month (1 request) — response contains availability for ALL dates
3. Check each target date's availability via regex on the response
4. For each available date, spawn a booking thread (`book_all_hotels_for_date` with `max_attempts=1`)
5. Wait for booking threads, then loop back to step 1

**Booking flow per date** (`book_all_hotels_for_date()`):
1. GET calendar page, extract CSRF/auth tokens via regex
2. POST to navigate to target month if date not visible
3. POST to `service_group_select` to get hotel list
4. Filter out `SKIP_HOTELS` + already-booked hotels
5. For each hotel, `book_one_hotel()`: select hotel → select service → load form → search rooms → submit room → agree to rules → submit email

When used standalone (`python3 main.py`), spawns one thread per date with infinite retries (original burst mode).

**URL expiry handling**: When GET returns non-200, the thread sets `expired = True` and returns. The caller decides what to do.

**Key functions**:
- `scan_and_book_month(month_str, target_dates, label, calendar_url)` — month scanner loop, spawns booking threads on availability
- `book_all_hotels_for_date(target_date, label, calendar_url, max_attempts)` — booking loop per date (`max_attempts=0` for infinite, `1` for single-pass)
- `book_one_hotel(tag, c, target_date, s_param, auth, hotel_id, hotel_name)` — books one hotel (steps 3-9), returns True/False
- `curl(cookie_file, method, url, data, headers)` — raw HTTP via curl subprocess
- `ex(html, pat)` — regex extraction helper

**Threading model** (via `run.py`):
- 1 scanner thread per month (e.g., APR, MAY) — polls with 2 requests/cycle
- On availability: spawns N booking threads (one per available date) — each fully independent with own cookie file
- Booking threads run single-pass (`max_attempts=1`), scanner resumes after they complete
- Only shared state is `bookings.json` (protected by `threading.Lock`)
- Standalone mode (`python3 main.py`): 1 thread per date, infinite retries, no scanner

### `captcha_solver.py` — reCAPTCHA v2 solver

Uses Playwright (non-headless Chromium) + ollama vision model to solve reCAPTCHA v2 image challenges.

**Key functions**:
- `get_calendar_url()` — async. Navigates ITS site → clicks "カレンダーから探す" → solves CAPTCHA → clicks "次へ" → saves URL to `calendar_url_cache.txt`. Returns URL string or None.
- `solve_recaptcha(page, max_attempts=8)` — async. Solves reCAPTCHA on any page. Returns g-recaptcha-response token or None.
- `ask_vision(http, image_b64, prompt)` — sends image to ollama via curl subprocess.

**CAPTCHA solving strategy**:
1. Click reCAPTCHA checkbox
2. If challenge appears, detect grid type (3x3 or 4x4; skips 4x4)
3. Strategy 1: screenshot full grid, ask vision model to identify matching tiles
4. Strategy 2 (fallback): screenshot each tile individually, classify in parallel
5. Click matching tiles, handle dynamic replacements, click verify
6. Retry up to 8 challenges

**Config**: `OLLAMA_URL = 'http://localhost:11434'`, `OLLAMA_MODEL = 'qwen3-vl:8b'`

## Data Files

- `calendar_url_cache.txt` — Current calendar session URL. Written by captcha_solver, read by main.py and run.py. Contains a URL with an `s=` session token that expires periodically.
- `bookings.json` — Records successful bookings as `{date: [hotel_names]}`. Thread-safe writes via `_bookings_lock`.

## Configuration (in `main.py`)

- `TARGET_DATES` — List of date strings to book
- `EMAIL` — Email for booking confirmation
- `NUM_GUESTS` — Number of guests per booking
- `RETRY_DELAY` — Seconds between retry attempts (default 10)
- `SKIP_HOTELS` — Hotels to never book (commented-out section = "keep" list)

## Logging

All output uses ANSI color codes: red (errors/failures), green (success/booked), yellow (waiting/warnings), cyan (info), bold (headers/totals).
