# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ITS Calendar Scanner — an automated booking system for ITS Health Insurance Facility Calendar (as.its-kenpo.or.jp). It monitors available dates at Japanese hospitality facilities and attempts automated bookings. Three execution modes exist: continuous browser scanning, blitz mode (multi-tab parallel browser), and direct HTTP bypass.

## Setup & Running

```bash
# Install dependencies
uv pip install -r requirements.txt

# Mode 1: Continuous scanner (browser-based, loops forever)
uv run main.py

# Mode 2: Blitz mode (fast multi-tab browser, for midnight rush)
uv run blitz.py                    # Normal blitz
uv run blitz.py --dry-run          # Scan only, no booking
uv run blitz.py --wait-until 00:00 # Wait until midnight then start

# Mode 3: Direct HTTP booking (fastest, no browser needed)
# Use http_booking.py as a library — see HTTPBooker class
```

There are no tests, linting, or formatting tools configured.

## Project Structure

```
its/
├── main.py                  # Entry point: continuous browser scanning loop
├── blitz.py                 # Entry point: multi-tab parallel blitz mode
├── http_booking.py          # Library: direct HTTP booking bypass (fastest)
├── config.py                # All constants: user config, selectors, timeouts, hotel filters
├── browser.py               # Chrome options, script value extraction, network blocking
├── cache.py                 # URL caching (txt), URL history (csv), booking history (json)
├── navigation.py            # Main page navigation, CAPTCHA iframe handling
├── calendar_scanner.py      # Date availability detection, month navigation (original)
├── booking.py               # Full booking workflow via browser (original)
├── blitz_scanner.py         # Batch JS scanning — single JS call scans all cells
├── blitz_booking.py         # Streamlined browser booking with minimal waits
├── requirements.txt         # Dependencies: pydoll-python (aiohttp is a transitive dep)
├── bookings.json            # Records successful bookings (date → hotel list)
├── calendar_url_cache.txt   # Cached calendar URL (expires periodically)
├── calendar_url_history.csv # Historical URLs with timestamps
└── captured_requests.json   # Network capture of booking HTTP request chain (reference)
```

## Architecture

### Three execution modes

**Mode 1: `main.py` — Continuous scanner (original)**
- Infinite loop: `main()` → `scan_once()` → `scan_calendar_and_book()` → `scan_and_book_one()`
- Single browser tab, sequential scan + book
- ~5s per scan cycle

**Mode 2: `blitz.py` — Midnight rush mode**
- Designed for the 27th at midnight when next month's availability opens
- Uses batch JS scanning (one JS call scans all cells vs N round-trips)
- Opens multiple browser tabs for parallel booking via `asyncio.gather()`
- `--wait-until HH:MM` flag to pre-position before start time
- ~3s per booking attempt, ~1s per scan cycle

**Mode 3: `http_booking.py` — Direct HTTP bypass (fastest)**
- Skips browser entirely; pure `aiohttp` HTTP requests
- Each booking step is a single HTTP POST (~100ms) vs browser page load (~300ms)
- Extracts CSRF `authenticity_token` from each response's HTML
- Uses `X-CSRF-Token` header with meta tag token for XHR room search
- ~0.8s per complete booking (3.7x faster than browser)

### Module responsibilities

- `config.py` — All constants: user config (email, guests, target dates), CSS selectors, timeouts, hotel skip list, UI strings
- `browser.py` — Chrome options setup (`fast=True` enables GPU disable + network blocking), `extract_script_value()` for CDP results, `setup_network_blocking()` via CDP
- `cache.py` — URL caching (text file), URL history (CSV), booking history (JSON)
- `navigation.py` — Main page navigation, calendar link discovery, CAPTCHA iframe handling (requires visible browser)
- `calendar_scanner.py` — Original per-cell date scanning, month navigation with polling
- `booking.py` — Original browser booking workflow: date selection, hotel filtering, form filling, submission
- `blitz_scanner.py` — Batch JS scanning: `BATCH_SCAN_JS` scans all cells in one call, `fast_navigate_next_month()` with JS-based click
- `blitz_booking.py` — Streamlined booking: each step (hotel click, service select, form fill, room select, agree, email) is a single JS execution
- `http_booking.py` — `HTTPBooker` class: direct HTTP POST chain, HTML parsing for tokens/IDs, XHR room search

### ITS site booking flow (7 steps)

Each mode traverses this server-side page sequence:

1. `calendar_select` — Calendar page (date grid)
2. `service_group_select` — Hotel list for selected date
3. `apply_service_select` — Service type selection for hotel
4. `check_apply_service_coma` → `empty_new` — Booking form (redirects, new session `s` param)
5. `empty_new` XHR POST — Room search (returns JavaScript with room checkboxes)
6. `empty_create` — Room selection + proceed
7. `rule` → `email_input` — Agreement + email submission → `send_complete`

### Key technical details

- The site uses Rails CSRF protection: every POST requires `authenticity_token` from the previous page
- Two different tokens exist: form hidden input token (for form POSTs) and meta `csrf-token` (for XHR via `X-CSRF-Token` header)
- The session parameter `s=` in URLs changes at steps 4 and 7
- Hotel IDs are in `data-service-group-id` attributes, service IDs in `data-apply-service-id`
- Room search (step 5) is a jQuery AJAX call returning `text/javascript` that injects room checkboxes and `apply_session_guid`
- Calendar URL (`s=` param) expires periodically and must be refreshed via CAPTCHA bypass

### Key patterns

- Async/await throughout (asyncio)
- Browser automation via `pydoll` (Chrome DevTools Protocol wrapper)
- JavaScript execution for complex DOM interactions
- Polling strategy for month navigation (15 attempts, 0.15s intervals in blitz)
- All configuration centralized in `config.py`
- `Network.setBlockedURLs` via raw CDP command for blocking fonts/analytics
