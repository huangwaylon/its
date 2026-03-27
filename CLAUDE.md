# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ITS Calendar Booker — an automated booking system for ITS Health Insurance Facility Calendar (as.its-kenpo.or.jp). It monitors available dates at Japanese hospitality facilities and books hotels using parallel HTTP requests via curl.

## Setup & Running

```bash
# No dependencies required (stdlib only + curl)
python3 main.py
```

Requires a valid calendar URL in `calendar_url_cache.txt` (obtained separately via CAPTCHA).

There are no tests, linting, or formatting tools configured.

## Architecture

Single self-contained script: `main.py`

**Strategy**: Spawns one thread per target date using `ThreadPoolExecutor`. Each thread retries up to `MAX_RETRIES` (300) times, polling for availability and booking all eligible hotels per date.

**Booking flow per date** (`book_all_hotels_for_date()`):
1. GET calendar page, extract CSRF/auth tokens via regex
2. POST to navigate to target month if date not visible
3. POST to `service_group_select` to get hotel list
4. Filter out `SKIP_HOTELS` + already-booked hotels
5. For each hotel, `book_one_hotel()`: select hotel → select service → load form → search rooms → submit room → agree to rules → submit email

**Key patterns**:
- Raw HTTP via `curl` subprocess (no browser rendering)
- HTML parsing with regex for token/form extraction
- Thread-safe bookings access with `threading.Lock`
- Cookie files per thread, cleared between hotel bookings

**Data files**:
- `bookings.json` — Records successful bookings (date → hotel list)
- `calendar_url_cache.txt` — Calendar URL (must exist before running)
- `calendar_url_history.csv` — Historical URLs with timestamps
