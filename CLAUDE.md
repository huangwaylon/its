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
- Network tuning: `RETRY_DELAY`, `CURL_MAX_ATTEMPTS`, `CURL_TIMEOUT`, `URL_CHECK_INTERVAL`, `URL_REFRESH_INTERVAL`
- Retry/backoff: `CURL_RETRY_BACKOFF`, `CURL_RETRY_BACKOFF_MAX`, `BOOK_MAX_ATTEMPTS`, `BOOK_RETRY_DELAY`, `SCAN_BACKOFF_MAX`, `SCAN_JITTER`, `CAPTCHA_TIMEOUT`
- Housekeeping: `DEBUG_DUMP_INTERVAL`, `DEBUG_DUMP_KEEP`, `LOG_MAX_BYTES`, `LOG_BACKUPS`, `IDLE_LOG_INTERVAL`, `SKIP_PAST_DATES`
- HTTP fingerprint: `BROWSER_HEADERS`, `ACCEPT_LANGUAGE`, `FALLBACK_USER_AGENT`, `USER_AGENT_CACHE`
- Hotel policy: `PRIORITY_HOTELS` (attempted first), `SKIP_HOTELS` (never booked; "keep" list kept as a comment)

Imported by `book_hotels.py`, `captcha_solver.py`, and `main.py`.

### `main.py` — Entry point and orchestrator

Starts two kinds of threads and waits forever (Ctrl+C to stop):

**Booking scanner threads** (1 per month, daemon):
- Groups `TARGET_DATES` by month, spawns one `threading.Thread` per month
- Each calls `book_hotels.scan_and_book_month()` which loops indefinitely
- Reads URL from `calendar_url_cache.txt` each cycle
- If URL is missing or expired, logs and waits for next cycle (never triggers CAPTCHA)

**URL monitor thread** (1 total, daemon):
- `url_monitor()` loops forever, with the whole body guarded so nothing can end the thread
- Each cycle: `check_cached_url()` reads the cache file and makes a curl GET to verify HTTP 200
- If valid → sleep `URL_CHECK_INTERVAL`
- If invalid/missing → call `asyncio.run(get_calendar_url())` synchronously in this thread
- Synchronous design naturally prevents re-triggering while a solve is in progress (no locks needed)
- A **proactive** refresh is deferred while `book_hotels.active_bookings()` is non-zero: a booking holds one `s=` token across a ~7-request chain, and swapping it underneath loses the slot. A **repair** (no valid URL at all) is never deferred.
- On failure, sleeps and retries indefinitely

**Watchdog thread** (1 total, daemon):
- Every `WATCHDOG_INTERVAL` (30s), restarts any `_Worker` whose thread is no longer alive.
- Needed because every worker is a daemon and `main()` blocks on the display rather than joining them, so a dead scanner (or, worse, a dead URL monitor) is otherwise completely invisible — the process keeps rendering and looks healthy.

**Display mode**: the Rich full-screen TUI is used only when `sys.stdout.isatty()`. Under `nohup`/launchd/a pipe it falls back to plain line logging on stdout, since the escape sequences have nothing to draw on and only corrupt a piped log.

**Log rotation**: `_rotate_log()` runs at startup and rolls `LOG_FILE` past `LOG_MAX_BYTES`, keeping `LOG_BACKUPS`. Rotation at startup rather than mid-write means no log handler needs a lock.

**Key functions**:
- `main()` — starts monitor thread, starts scanner threads, joins scanners
- `url_monitor()` — cache validity loop, triggers CAPTCHA solve when needed
- `check_cached_url()` — reads cache file, curl GET to test validity, returns URL or None
- `group_dates_by_month(dates)` — groups `YYYY-MM-DD` strings by `YYYY-MM`

### `book_hotels.py` — Booking engine

Pure booking logic. Never triggers CAPTCHA solving. All calendar URL access goes through `_read_cached_url()` which reads `CALENDAR_URL_CACHE` (imported from `config`).

**Month scanner** (`scan_and_book_month(month_str, target_dates, label, stop_event=None)`): One thread per month, runs forever. **The entire loop body is wrapped in `try/except`** — nothing may end this thread, because it is the only thing scanning its month, it is a daemon, and `main()` blocks on the display rather than joining it. Each cycle:
1. Drop past dates via `_future_dates()`; if none remain, idle
2. Read URL from cache via `_read_cached_url()`. If missing → log, back off, retry
3. GET calendar page, extract CSRF tokens (1 request). If non-200 → reset the cookie jar, back off, retry
4. POST to navigate to target month (1 request) — response contains availability for ALL dates in that month
5. Check each target date's CSS class for `empty` (available) via regex
6. For each available date, spawn a booking thread via `ThreadPoolExecutor`
7. Wait for all booking threads to complete, then loop back to step 1

Sleep between cycles is `min(RETRY_DELAY * 2**consecutive_failures, SCAN_BACKOFF_MAX)` plus `random.uniform(0, SCAN_JITTER)`. The jitter is not optional: without it the per-month scanners settle into lockstep and arrive as a burst, the likeliest cause of the 1072 x 503 in the last log. The cookie jar is reset **only after a failure** — truncating it every cycle asked the site for a brand-new session ~4,300 times a day and threw a working one away each time.

`stop_event` is an optional `threading.Event` for cooperative shutdown. Production leaves it `None`; the tests set it so a finished test's scanner cannot keep booking into the next one's fixtures.

**Booking flow per date** (`book_all_hotels_for_date(target_date, label)`): retries up to `BOOK_MAX_ATTEMPTS`, returns `(date, list_of_booked_hotels)`. Wraps `_book_date_once()` in `_BookingInFlight` (which feeds `active_bookings()`) and re-attempts on a `'retry'` outcome. **Only the setup requests are retried** — once `book_one_hotel` is under way it either completes or is left alone, and `attempted` carries across attempts, so nothing can be applied for twice.

`_book_date_once(...)` returns one of `'ok'`/`'done'`, `'retry'` (transient: 5xx, transport failure, or a dead session), `'unavailable'` (date genuinely not open), `'failed'` (a response repeating will not fix):
1. Read URL from cache. If missing → `'failed'`
2. `_open_calendar_session()` — fresh cookie jar, GET calendar, extract CSRF/auth/`s`, navigate to the target month, confirm the date is `empty`
3. `_select_date()` — POST `service_group_select` to get the hotel list, HTML-unescaping each name
4. Filter out `SKIP_HOTELS`, already-booked, and already-attempted hotels; then `order_hotels()` puts `PRIORITY_HOTELS` first
5. For each hotel sequentially, call `book_one_hotel()`, re-running steps 2–3 first (fresh session per hotel)

`_open_calendar_session()` and `_select_date()` are shared by the first attempt and by every subsequent hotel. The per-hotel copy of that logic used to be inline and had already drifted — it dropped the availability check and ignored the navigation's status.

**Single hotel booking** (`book_one_hotel(tag, c, target_date, s_param, auth, hotel_id, hotel_name)`): Steps 3-9 of the booking flow. Returns True/False.
1. POST to select hotel → extract services list
2. POST to select service → follow 302 redirect
3. GET booking form → extract CSRF, auth, form action, coma search param
4. POST to search rooms (AJAX) → extract room IDs and session GUID
5. POST to submit room selection → follow 302
6. POST to agree to rules page → follow 302
7. POST to submit email confirmation → check for `send_complete` in response

**Key functions**:
- `scan_and_book_month(month_str, target_dates, label, stop_event=None)` — month scanner loop
- `book_all_hotels_for_date(target_date, label)` — retrying booking for one date
- `_book_date_once(target_date, label, tag, booked, attempted)` — one pass; returns an outcome string
- `_open_calendar_session(...)` / `_select_date(...)` — shared session setup and date selection
- `_is_retryable(status)` — true for 0, 429, and 5xx. 302 is excluded; `_is_session_dead()` tells a dead session apart from flow progress
- `_is_session_dead(status, location)` — 302 to `/service_category/index`. **Confirmed live**: that is exactly what a stale `s=` token answers (302, 0 bytes), and it is the signature of 302 of the 380 dumps on disk
- `order_hotels(hotels)` / `is_skipped(name)` / `_norm_hotel(name)` — `PRIORITY_HOTELS` first (stable sort), skip matching after casefolding, HTML-unescaping and collapsing whitespace so a full-width space cannot silently defeat a skip entry
- `active_bookings()` — count of dates mid-booking; read by `main.url_monitor()` to defer a proactive refresh
- `_future_dates(dates)` — drops dates that have already passed (`SKIP_PAST_DATES`)
- `_retry_after(hdrs)` — seconds from a `Retry-After` header, capped at `CURL_RETRY_BACKOFF_MAX`
- `book_one_hotel(tag, c, target_date, s_param, auth, hotel_id, hotel_name)` — books one hotel
- `curl(cookie_file, method, url, data, headers)` — raw HTTP via curl subprocess, returns `(status, body, location)`. `body` is a `Response` (a `str` subclass carrying `.headers`, `.location`, `.request`), so call sites treat it as a plain string. Never raises: a transport failure returns `(0, '', None)`, because the scan loop has no `except` around its curl calls.
- `header_args(headers)` — curl `-H` flags for the merged header set; also used by `main.check_cached_url()`
- `_merge_headers(headers)` — per-call headers over the browser defaults, case-insensitive. Merging in Python (rather than appending `-H` flags) is required: curl emits every header it is given, so a default plus a per-call `Accept` would send both.
- `_user_agent()` — reads `USER_AGENT_CACHE`, re-reading on mtime change; falls back to `FALLBACK_USER_AGENT`
- `redact_url(url)` / `_redact_headers(hdrs)` / `_redact_set_cookie(v)` / `_redact_body(b)` / `_fingerprint(v)` — strip session material from anything written to `debug_responses/`. All fail closed: anything unrecognized is fingerprinted rather than passed through.
- `token_summary(url, now=None)` / `decode_s_token(token)` / `_relative(seconds)` — decode the `s=` token for logging. See **The `s=` token** below. `token_summary` never raises; it runs in the URL monitor's logging path.
- `ex(html, pat)` — regex group(1) extraction helper
- `save_booking(date_str, hotel_name)` — thread-safe, **atomic**, and never destructive. `_write_bookings()` writes a same-directory temp file, fsyncs, then renames; a plain `open(..., 'w')` truncates first, so a crash midway leaves a file that parses as nothing. If the existing file was unreadable, it is renamed to `bookings.json.corrupt.<ts>.<seq>` rather than overwritten — a bad read returning `{}` followed by a normal save would otherwise rewrite the file with only the one new entry. The last log has 5 bookings for 2026-08-22 that no longer appear in `bookings.json`, which is that failure having already happened.
- `get_booked_hotels(date_str)` — thread-safe read from `bookings.json`
- `_read_cached_url()` — reads `CALENDAR_URL_CACHE`, returns URL string or None. Catches `OSError`, not just `FileNotFoundError`: a raise here would kill a scanner thread
- `_load_bookings()` — private, reads `bookings.json`, must only be called under `_bookings_lock`. Sets `_bookings_unreadable` on a parse failure or a non-object payload

**Debug dumps** (`_dump_debug`): on an unexpected response, writes two sibling files to `DEBUG_DIR` — `<stem>.html` (body) and `<stem>.headers.txt` (redacted response headers). The headers are the diagnostic payload: `x-runtime` present means Rails generated the response, absent means Apache/ALB/WAF did; `content-length: 0` with an empty body means the server sent nothing by intent rather than the body being truncated; `set-cookie` shows whether the session was re-issued or reset (`Max-Age=0`). Redaction is a **whitelist** — unrecognized header values are replaced with `[len=N sha256=xxxxxxxx]`, so a future session-bearing header cannot leak by default. Cookie names and attributes are kept, values fingerprinted; the digest prefix is stable, so two dumps can be compared to tell a fresh `_src_session` from a reused one without either file containing the id.

Dumps are **throttled** per `(label, step)` to one per `DEBUG_DUMP_INTERVAL`, and `DEBUG_DIR` is **pruned** to `DEBUG_DUMP_KEEP` files (oldest first). Without that, a failure repeating every cycle wrote a file pair per cycle for as long as it lasted — 83 of the 380 existing dumps are one date's `service_group_select`. The `.html` is skipped entirely when the body is empty (302 of the 380 are 0 bytes); the headers file records the size either way. Filenames carry a `_dump_seq()` counter because the timestamp is only second-resolution and several dates book in parallel — without it the second of two simultaneous dumps silently overwrote the first.

### `captcha_solver.py` — Cloudflare Turnstile solver

Uses pydoll (CDP-based Chrome automation) to solve Cloudflare Turnstile CAPTCHA. Pydoll drives real Chrome via DevTools Protocol, avoiding bot detection that Playwright triggers. Requires non-headless mode (visible Chrome window) since headless Chrome gets rejected by Turnstile. Has its own `log()` function (elapsed-seconds format) separate from `book_hotels.log()` (wall-clock format).

Imports `redact_url` and `token_summary` from `book_hotels` so there is one implementation of "make this safe to write down". `book_hotels` imports only `config`, so this is not a cycle. Nothing here logs a URL or token verbatim any more: the pre-solve URL goes through `redact_url`, the resulting calendar URL through `token_summary`, and the Turnstile response token is reported as a length. The old `Calendar URL: {calendar_url}` line wrote the complete `s=` token to disk on all 647 solves in the previous log.

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

**Config**: `DEBUG_DIR = '/tmp/captcha_debug'`, `MAX_ATTEMPTS = 3`, `TOKEN_TIMEOUT = 30`, `config.CAPTCHA_TIMEOUT = 180`

**Hard deadline**: `get_calendar_url()` is a thin wrapper that runs `_solve_and_cache()` under `asyncio.wait_for(..., CAPTCHA_TIMEOUT)` and returns `None` on timeout or any exception. The solve runs synchronously inside the URL monitor thread, and that thread is the only thing that ever re-mints a session — an untimed pydoll/Chrome hang there stops all booking indefinitely while the process keeps rendering its display and looks healthy. On timeout, `_kill_stray_chrome()` reaps Chrome processes matching `remote-debugging-port`, since a cancelled `async with Chrome(...)` cannot always finish its own teardown and each orphan holds a profile directory and a few hundred MB.

**Never caches a bad URL**: if the post-submit URL does not contain `calendar_select`, `_solve_and_cache()` screenshots it and returns `None`, leaving the previous cache entry alone. It used to save it anyway, which poisoned the cache — every scanner would then replay a non-calendar URL that can still answer 200, so `check_cached_url()` called the session healthy and no re-solve ever fired.

## The `s=` token

Not opaque. Decoded from a live 88-character token:

```
s= → base64 → reverse → base64 → "service_category_id=1&verify_expires=<10 digits>"
```

47 bytes of printable ASCII with **nothing left over** — no signature, no MAC. Nothing cryptographically binds the token to the Turnstile solve that produced it. The reversal is why every token looks like it shares a suffix: the recurring `VURPM0VU` is the constant `service_category_id=1&verify_` appearing mirrored, and the field that varies (647 distinct tokens across 647 solves) sits at the front of the outer encoding, which maps to the *end* of the payload.

**Consequence for logging.** Because the payload is only those two fields and one is constant, printing `verify_expires` in full is equivalent to printing the token. So `token_summary()`:
- renders timestamp-shaped fields as a **relative delta** (`verify_expires=+1h29m`), never an absolute value;
- masks every other value as `<N chars>`;
- always shows field **names**, so a field the site adds later becomes visible without its value leaking;
- is deliberately **strict** — a truncated token still base64-decodes into plausible bytes, so anything that is not clean printable ASCII in `k=v&k=v` shape is reported as `token does not decode (N chars)` rather than as a payload.

**`verify_expires` is not yet usable for scheduling refreshes.** One live sample contradicted the obvious reading: a token minted 2026-08-18 13:33 carried 2026-08-08 19:12:52, ten days in its own past. Since all 647 tokens differ and there are only two fields, *something* in there varies per solve and this is the only candidate — so either it is not a per-token expiry, or it is not an epoch at all. Every token in the previous log was truncated at 80 characters and cannot be decoded, which is why the field is now logged on each solve. Watch it across a few refreshes before letting `url_monitor` schedule against it.

**Do not mint tokens.** The absent MAC means one could be forged with an arbitrary expiry, skipping Turnstile entirely. That is defeating the anti-automation control rather than solving it, and it would break silently and completely the moment a signature is added.

## Concurrency Model
**Threads at runtime** (when started via `main.py`):
- 1 URL monitor thread (daemon)
- 1 watchdog thread (daemon) — restarts any of the above that dies
- N scanner threads (daemon, 1 per month — currently 2: AUG, SEP)
- Temporary booking threads (spawned by scanners via `ThreadPoolExecutor`, 1 per available date)

All long-lived threads are wrapped in `main._Worker`, which the watchdog can restart. The scanner and URL-monitor loop bodies are additionally guarded internally, so a restart is the second line of defence rather than the first.

**Shared state**:
- `calendar_url_cache.txt` — Written by captcha solver (in URL monitor thread), read by all scanner/booking threads. Safe via POSIX file atomicity for small files. Readers handle stale/empty data gracefully (return None, retry next cycle).
- `bookings.json` — All access goes through `save_booking()` and `get_booked_hotels()`, both protected by `_bookings_lock` (`threading.Lock`). `_load_bookings()` is private and must only be called under this lock (non-reentrant lock would deadlock if added directly).
- Cookie files — Each thread creates its own temp file (`cookies_scan_*` for scanners, `cookies_*` for booking threads). No sharing.

## Data Files

- `calendar_url_cache.txt` — Current calendar session URL. Written by `captcha_solver.get_calendar_url()`, read by `book_hotels._read_cached_url()` and `main.check_cached_url()`. Contains a URL with an `s=` session token that expires periodically. Path defined once in `config.CALENDAR_URL_CACHE`. **Gitignored** — the `s=` token is a live credential.
- `chrome_user_agent.txt` — UA of the Chrome that solved the most recent CAPTCHA. Written by `captcha_solver._save_user_agent()`, read by `book_hotels._user_agent()`. Gitignored. Absent until the first solve, in which case `FALLBACK_USER_AGENT` is used.
- `bookings.json` — Records successful bookings as `{date: [hotel_names]}`. Thread-safe via `_bookings_lock`, written atomically (temp file + `fsync` + rename). An unparseable file is renamed to `bookings.json.corrupt.<ts>.<seq>` instead of being overwritten. This file is the only thing preventing duplicate applications, so losing it is worse than a crash.
- `its_booking.log` / `its_booking.log.N` — rotated at startup past `LOG_MAX_BYTES`, keeping `LOG_BACKUPS`. Both patterns are gitignored (`*.log` misses the rotated names). **Treat this file as credential-bearing.** The `s=` token has no MAC, so any decoded field is equivalent to the token; the log now records only relative deltas, but the trust boundary is the same as `calendar_url_cache.txt`.
- `debug_responses/` — Failure dumps from `_dump_debug` (`.html` body + `.headers.txt` redacted headers). **Gitignored**: response bodies embed `s=` tokens in form actions. It was tracked until 2026-08-18; the pre-existing 380 files remain in the history of the (public) GitHub remote. The `.html` body is redacted too (`_redact_body`), since that is the file people actually open.

## Tests

Two suites, both stdlib-only, both against a throwaway localhost HTTP server. Neither makes a request to ITS.

```bash
.venv/bin/python test_http_layer.py      # 128 checks — curl + redaction layer
.venv/bin/python test_booking_flow.py    # 145 checks — booking flow end to end
```

- **`test_http_layer.py`** — header merging (curl emits every `-H` it is given, so a duplicate would let the server pick) and redaction (a dump must never contain a cookie or token value).
- **`test_booking_flow.py`** — the part that wins or loses a slot. A `FakeITS` server replays the real markup shapes from `docs/BOOKING_VIA_CURL.md` and the dumps in `debug_responses/`, **including the escaped-quote form AJAX responses arrive in** — the extractors are markup-exact, so a fake that prettied the markup up would test nothing. `STATE.fail_once` injects statuses per path to reproduce the production failures: a 503 on the calendar GET, a 302 to `/service_category/index` out of `service_group_select` (both mid-flow and between hotels), a 404, and the site's "no vacant rooms" page. Also covers priority ordering, skip-list normalization, `bookings.json` atomicity and corruption handling, the watchdog, log rotation, and the CAPTCHA timeout wrapper.

Pass a substring to run a subset, and `-v` to see the flow's own logging:

```bash
.venv/bin/python test_booking_flow.py -v test_503
```

Both need to bind `127.0.0.1`, which the Apple Claude Code sandbox denies; `.claude/apple/tool_allowlist.csv` allowlists both by name. **Run each in its own Bash call** — the allowlist matches on the whole command string, so chaining a test behind `&&` after an unrelated command can fall through to the sandbox and fail with `PermissionError: [Errno 1]`. Both clear proxy settings for loopback, since a local HTTP proxy would otherwise intercept and rewrite the responses.

## Configuration (in `config.py`)

- `TARGET_DATES` — List of `YYYY-MM-DD` date strings to book
- `EMAIL` — Email for booking confirmation
- `NUM_GUESTS` — Number of guests per booking (string)
- `BOOKINGS_FILE` — Path to bookings JSON file (default `'bookings.json'`)
- `RETRY_DELAY` — Base seconds between scan cycles (default 20); multiplied by `2**consecutive_failures` up to `SCAN_BACKOFF_MAX`
- `CURL_MAX_ATTEMPTS` — Max attempts per curl request (default 3, 1 = no retry)
- `CURL_RETRY_BACKOFF` / `CURL_RETRY_BACKOFF_MAX` — Delay before a curl retry, doubling per attempt (default 0.5s / 8s). The old zero-delay retry put both attempts in the same millisecond against a server that was answering 503 *because* it was being asked too often, so it never once helped. A `Retry-After` header wins when present, capped at the max.
- `CURL_TIMEOUT` — curl's `--max-time` (default 30); `subprocess.run` gets this plus 10s so a curl ignoring its own deadline cannot wedge a thread for the week
- `BOOK_MAX_ATTEMPTS` / `BOOK_RETRY_DELAY` — Re-attempts of a whole date on a fresh session (default 3 / 2s). A date the scanner just saw as available is worth more than one request; a single 503 used to abandon it until the next cycle.
- `SCAN_BACKOFF_MAX` — Ceiling on the scan backoff (default 300)
- `SCAN_JITTER` — Random 0..N seconds added to every scan sleep (default 5), to stop the per-month scanners settling into lockstep
- `CAPTCHA_TIMEOUT` — Hard ceiling on one solve (default 180)
- `PRIORITY_HOTELS` — Substrings matched first, in order (default `['NAGU']`). About the race, not preference: hotels are booked sequentially at ~7 requests each, so in the site's own ordering a hotel listed last is attempted tens of seconds after the slot was spotted.
- `SKIP_HOTELS` — Hotels to never book. Matched after casefolding, HTML-unescaping and collapsing whitespace, so a full-width vs half-width space cannot silently miss. The "keep" list is a plain comment block; **anything on neither list is eligible to be booked**, including hotels the site adds later.
- `SKIP_PAST_DATES` — Stop polling dates that have passed (default True)
- `DEBUG_DUMP_INTERVAL` / `DEBUG_DUMP_KEEP` — Dump throttle per label+step, and file cap for `DEBUG_DIR` (default 300s / 400)
- `LOG_MAX_BYTES` / `LOG_BACKUPS` — Log rotation at startup (default 32 MB / 3)
- `IDLE_LOG_INTERVAL` — Collapse an unchanged "no dates available" streak to one line per N seconds (default 300). That message was 116k of the last log's 150k lines.
- `URL_CHECK_INTERVAL` — Seconds between URL validity checks (default 60)
- `URL_REFRESH_INTERVAL` — Seconds between proactive URL refreshes (default 1800); a proactive refresh is skipped while any booking is in flight
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
- `main.url_log()` routes through `_url_sink`, set by `main()` to either the TUI's left panel or plain stdout. Everything also goes to `LOG_FILE` with ANSI stripped.
- Reserve red for things that are actually wrong. The site's "no vacant rooms" page is an ordinary outcome of losing a race, so it is logged yellow with no debug dump; it used to produce four red lines and a dump every time.
