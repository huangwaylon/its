# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ITS Calendar Scanner — an automated booking system for ITS Health Insurance Facility Calendar (as.its-kenpo.or.jp). It continuously monitors available dates at Japanese hospitality facilities and attempts automated bookings using Chromium browser automation.

## Setup & Running

```bash
# Install dependencies (only pydoll-python)
uv pip install -r requirements.txt

# Run the scanner
uv run main.py
```

There are no tests, linting, or formatting tools configured.

## Architecture

The system follows a two-phase approach: scan all target months to collect available days, then process each available day and attempt booking.

**Entry point**: `main.py` — continuous loop calling `scan_once()` → `scan_calendar_and_book()` → `scan_and_book_one()`

**Module responsibilities**:
- `config.py` — All constants: user config, selectors, timeouts, hotel filters, UI strings
- `calendar_scanner.py` — Date availability detection, month navigation with polling, target date filtering
- `navigation.py` — Main page navigation, calendar link discovery, CAPTCHA iframe handling
- `booking.py` — Full booking workflow: date selection, hotel filtering, form filling, submission
- `browser.py` — Chrome options setup, script execution result extraction
- `cache.py` — URL caching (text file), URL history (CSV), booking history (JSON)

**Key patterns**:
- Async/await throughout (asyncio)
- Browser automation via `pydoll` (Chromium wrapper)
- JavaScript execution for complex DOM interactions
- Polling strategy for month navigation (10 attempts, 0.3s intervals) instead of fixed waits
- All configuration centralized in `config.py`

**Data files**:
- `bookings.json` — Records successful bookings (date → hotel list)
- `calendar_url_cache.txt` — Cached calendar URL
- `calendar_url_history.csv` — Historical URLs with timestamps
