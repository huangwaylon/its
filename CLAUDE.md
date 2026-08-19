# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

ITS Calendar Booker — polls the ITS facility calendar (as.its-kenpo.or.jp) for vacancies on
target dates and, when one appears, drives the 空き照会申込 flow over curl. Cloudflare
Turnstile guards the entry, so a real Chrome solves it and curl replays the resulting `s=`
session token until it expires.

**`docs/SITE.md` is the single record of what the site does** — published rules, cutoffs,
the request chain, applicant-form traps, the 503 IP ban, measured availability stats. None
of it is recoverable from the code; read it before changing a request or extractor. Each
module's docstrings carry its own reasons; this file is the map between them. Three facts
govern the design:

- **`send_complete` is not a reservation.** `book_hotels.py` ends there — a hold plus a
  dispatched mail. `confirm_booking.py` runs the emailed leg (申込する → 確認), and only that
  yields a 申込受付番号 and 予約確定.
- **One request on that leg does not work over curl.** `POST /apply/confirm` is refused for
  the *client*, not the request, so that leg goes to real Chrome (`browser.submit`); curl
  stays primary, so if the cause is environmental the fallback stops firing with nothing to
  undo. Failing that, a booking still ends with the hold taken and the mail sent, and the
  log says `HUMAN NEEDED`. **That measurement was taken behind an intercepting HTTPS proxy
  — check whether it reproduces off one before touching the request** (docs/SITE.md §5).
- **The bot does not track the 30-minute hold, deliberately.** The site enforces it: a
  second application at a facility we already hold is refused with 「空き部屋がございません」,
  so a taken hold removes itself from what we are offered. Do not reintroduce hold
  accounting.

## Setup & Running

```bash
uv run main.py                       # normal operation: solve CAPTCHA, then book forever
.venv/bin/python captcha_solver.py   # solve only; writes calendar_url_cache.txt
.venv/bin/python check_env.py        # preflight: .env, IMAP, the confirmation gate
.venv/bin/python test_http_layer.py      # curl + redaction layer
.venv/bin/python test_booking_flow.py    # booking flow end to end
```

`book_hotels.py` is stdlib + curl; `captcha_solver.py` and `browser.py` need
`pydoll-python`, the only dependency. Two runtime requirements the packaging cannot
express: the `curl` binary and `pgrep`. No linter or formatter. Logging is plain lines to
stdout plus `LOG_FILE`; deliberately no TUI, since an unattended run under a pipe is normal.

## Architecture

`config.py` (settings) · `main.py` (orchestration) · `book_hotels.py` (scan + book) ·
`captcha_solver.py` (Turnstile) · `confirm_booking.py` (the emailed leg) · `browser.py`
(the one Chrome, and the leg that needs it) · `check_env.py` (preflight).

`book_hotels.log()` is the one line formatter (`main.py` imports it as `url_log`);
`captcha_solver.log()` is independent. Reserve red for what is actually wrong:
「空き部屋がございません」 is an ordinary lost race, so it is yellow with no dump.

**`main.py`** starts one `_Worker` per target month plus the URL monitor, then a watchdog,
then blocks forever. The monitor solves **synchronously in its own thread**, which is what
prevents overlapping solves without a lock; a *proactive* refresh is deferred while
`book_hotels.active_bookings()` is non-zero — a booking carries one `s=` token across a
~10-request chain — but a *repair* never is. The watchdog exists because workers are
daemons and `main()` never joins them, so a dead scanner (or the monitor, which alone can
re-mint a session) would be invisible in a healthy-looking process.

### `book_hotels.py`

Never solves a CAPTCHA; all URL access goes through `_read_cached_url()`.

**Scanner** (`scan_and_book_month`) — one thread per month, forever, whole loop body
guarded because nothing may end it. Per cycle: read the cached URL, GET the calendar
(skippable while `SCAN_REUSE_SESSION` can reuse a live csrf/`s` pair), POST
`calendar_select` for the month — one response carries every date's state — then spawn a
booking thread per available date. The backoff jitter keeps the per-month scanners out of
lockstep; the cookie jar is truncated only after a failure.

**Per date** (`book_all_hotels_for_date` → `_book_date_once`) outcomes are `'done'`,
`'retry'` (5xx, transport failure, dead session), `'unavailable'`, `'failed'`. Each pass
opens a fresh session, lists the hotels, drops skipped and already-booked/attempted names,
puts `PRIORITY_HOTELS` first, then books them one at a time on a fresh session each; only
setup requests are retried. Invariants that span call sites:

- `is_available()` matches `empty` **and** `a_little`; `empty` alone silently skipped
  every limited-availability date.
- `_month_rendered()` gates the month nav on the response *shape*, not its status, and
  **both** the scanner and `_open_calendar_session` use it so they cannot disagree.
- `_is_session_dead()` is a 302 to `/service_category/index`; `_is_retryable()` excludes
  302, so a dead session stays distinguishable from flow progress.
- `curl()` never raises — a transport failure is `(0, '', None)`, because the scan loop
  has no `except` around its calls. Headers are merged in Python, never appended as extra
  `-H` flags, since curl emits every header it is handed.
- `_load_bookings()` returns `(data, ok)` and a write is **refused** while `ok` is False.
  `save_booking(..., path=)` must stay a parameter, not a swapped global — both files are
  written from concurrent booking threads.
- Hotel names match after casefolding, unescaping and collapsing whitespace, so a
  full-width space cannot defeat a skip entry. Anything on neither list is eligible.

**The hold.** `empty_create` is 「予約手続きに進む」: it takes the hold and is the point of
no return, and it still goes out with curl's retry enabled, so a `--max-time` expiry there
can take two holds. The email POST is the only request here sent with `retry=False`.
**Nothing releases a hold**, so any failure past `empty_create` leaks a held room.
Re-attempts are bounded by `SKIP_HOTELS`, the already-booked filter over `holds.json`, the
per-call `attempted` set, and the site's own refusal of a second application at a facility
we hold. **There is deliberately no retry cooldown** — read the note in `config.py` before
adding one back.

**Request volume is the dominant operational risk**: the 503s are ~24-hour IP bans, and all
three recorded bans hit the scanner's `calendar_get`, so the risk lives in the *poll*
cadence, not in booking retries. Budget freed by `SCAN_REUSE_SESSION` is banked, not
respent.

**Debug dump redaction is a whitelist.** Anything unrecognised becomes
`[len=N sha256=xxxxxxxx]`, so a future session-bearing header cannot leak by default, and the
digest is stable so two dumps compare without holding a session id. It also covers
`config.APPLICANT`, since 申込内容確認画面 echoes those values back.

### The other modules — their docstrings are the reference

- **`confirm_booking.py`** is called from `book_hotels._finish_from_email`, which imports it
  lazily and swallows every failure: a hold plus a sent mail is worth keeping, and the human
  fallback works from that state. `bh.confirm_allowed()` is consulted immediately before
  **each** committing POST. **A caller finding `unmapped` non-empty must not submit** —
  those are 資格認証のキー, and a half-filled form is a rejected application plus a wasted
  hold. IMAP timeouts go to `IMAP4_SSL(...)`, never `socket.setdefaulttimeout()`, which is
  process-wide and would retime pydoll's CDP websocket.
- **`browser.py`** owns the process's one Chrome, serialised because
  `captcha_solver._kill_stray_chrome()` reaps by `pgrep -f remote-debugging-port`, which
  matches an application-filing browser as readily as a solving one — a timed-out solve
  would SIGKILL it between 申込する and 確認, with no way to learn which side it died on.
- **`captcha_solver.py`** runs under a hard `CAPTCHA_TIMEOUT` because the solve occupies
  the one thread that can re-mint a session, and **never caches a URL without
  `calendar_select`** — a non-calendar URL still answers 200, so caching one poisons the
  cache with a session that looks healthy and never triggers a re-solve.

**The `s=` token** is 47 unsigned bytes decoding to
`service_category_id=1&verify_expires=<10 digits>`. Two fields, one constant, so **any
decoded field is equivalent to the token**. Nothing decodes or logs it; `redact_url()`
fingerprints it, and a decoder would only reintroduce a way to leak it.

## Concurrency Model

1 URL monitor, 1 watchdog, N scanners (one per month) — all daemons — plus temporary booking
threads from each scanner's `ThreadPoolExecutor`, one per available date. Loop bodies are
guarded internally. **Chrome is single-threaded across the process**, by `browser.chrome()`.
Shared state: `calendar_url_cache.txt` (written in the URL monitor thread, read everywhere —
POSIX atomicity for a small file, and readers treat stale or empty data as "no URL");
`holds.json` and `reservations.json` (only via `save_booking()` and `get_booked_hotels()`,
under `_bookings_lock`, which `_load_bookings()` assumes is held); one cookie-jar temp file
per thread, never shared.

## Data Files

All gitignored: `calendar_url_cache.txt` (the `s=` token — a live credential),
`chrome_user_agent.txt` (UA of the Chrome that minted it), `its_booking.log` / `.log.N`
(credential-bearing), `debug_responses/` (bodies redacted, but tracked until 2026-08-18, so
earlier files remain in the public remote's history). `holds.json` is `{date: [hotel_names]}` and those are **holds, not reservations** — each
lapses unless the emailed link was followed, but it is still the only thing preventing
duplicate applications, so losing it is worse than a crash. `reservations.json` is
`{date: ["hotel\t申込受付番号"]}`; those **are** confirmed, so an entry means a real
cancellation liability.

## Tests

Both suites are stdlib-only against a throwaway localhost server; neither touches ITS. Pass
a substring for a subset, `-v` for the flow's logging: `test_booking_flow.py -v test_503`.
They bind `127.0.0.1`, which the Apple Claude Code sandbox denies, so
`.claude/apple/tool_allowlist.csv` allowlists both by name. **Run each in its own Bash call**
— the allowlist matches the whole command string, so chaining one behind `&&` falls through
to the sandbox and fails with `PermissionError: [Errno 1]`. Both clear proxy settings for
loopback, since a local proxy would rewrite responses.

- **`test_http_layer.py`** — header merging (a duplicate `-H` would let the server pick),
  UA newline injection, mobile-UA rejection, and redaction.
- **`test_booking_flow.py`** — the part that wins or loses a room. `FakeITS` replays the
  real markup, **including the escaped quotes AJAX responses arrive in**, because the
  extractors are markup-exact; `STATE.fail_once` injects the production failures; and the
  fake calendar serves an `empty` date *and* an `a_little` one so an `empty`-only filter
  fails loudly.
- **Tests are discovered, not hand-listed.** One needing the fake server declares a `port`
  parameter (`test_http_layer.py` injects `srv`/`tmpdir` likewise); adding a `def test_*` is
  enough to make it run.
- **The emailed leg must stay stubbed and the Chrome fallback never launched.** `Env` sets
  `AUTO_CONFIRM=False` and injects `_mail_source` and `_browser_submit`; nothing may open a
  socket to `imap.gmail.com`. The `chrome()` test runs in the calling thread deliberately —
  a background thread outliving the assertion opens a real browser.

## Configuration (`config.py`)

Every setting is documented at its definition with its reason; that file is the reference.

- `TARGET_DATES` drives one scanner thread per distinct month. `_future_dates()` drops
  only strictly-past dates, so **dates past their cutoff are still polled** and the list
  needs curating by hand against docs/SITE.md §1.
- `EMAIL` must be the mailbox IMAP reads, or the confirmation link lands out of reach.
  `AUTO_CONFIRM_MIN_DAYS = 11` is D−10 plus a day of margin. Secrets come from the
  environment only, and `_load_dotenv` treats an exported-but-empty variable as unset.
- `Origin`, `Sec-Fetch-*`, `sec-ch-ua`, `Upgrade-Insecure-Requests` and `Accept-Encoding`
  are **deliberately not sent**, and a mobile UA or non-Japanese `Accept-Language` risks a
  different template that would break the markup-exact extractors and `SKIP_HOTELS`
  matching. The TLS fingerprint is still curl's, so a browser UA is a UA/TLS mismatch.
