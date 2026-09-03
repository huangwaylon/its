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
  dispatched mail — then hands the hold to the **confirm worker**, which runs the emailed
  leg (申込する → 確認) off-thread and serially. Only that yields a 申込受付番号 and 予約確定.
- **curl reads the applicant form; only Chrome commits it.** `POST /apply/confirm` is
  refused for the *client*, not the request — 0 for 3 in the logs against Chrome's 2 for 2 —
  so `browser.submit` is the only committer and curl no longer tries. Without
  `pydoll-python`, or if Chrome fails, a booking still ends with the hold taken and the mail
  sent, and the log says `HUMAN NEEDED`. **That measurement was taken behind an intercepting
  HTTPS proxy — check whether it reproduces off one before restoring a curl path**
  (docs/SITE.md §5).
- **The bot does not track the 30-minute hold, deliberately.** The site enforces it: a
  second application at a facility we already hold is refused with 「空き部屋がございません」,
  so a taken hold removes itself from what we are offered. Do not reintroduce hold
  accounting.

## Setup & Running

```bash
uv run main.py                  # normal operation: solve CAPTCHA, then book forever
uv run main.py --check          # preflight: .env, IMAP, the confirmation gate
.venv/bin/python chrome.py      # solve only; writes calendar_url_cache.txt
.venv/bin/python test_its.py    # the whole suite (substring selects, -v for logs)
```

Everything but `chrome.py` is stdlib + curl; `chrome.py` needs `pydoll-python`, the only
dependency, imported inside the functions that use it so the rest of the program runs
without it. Two runtime requirements the packaging cannot express: the `curl` binary and
`pgrep`. No linter or formatter, and deliberately no TUI.

## Architecture

Six files. `config.py` (settings) · `main.py` (orchestration, the URL monitor, and
`--check`) · `book_hotels.py` (the curl layer, scan + hold) · `confirm_booking.py` (the hold
queue + the emailed leg) · `chrome.py` (the one Chrome: Turnstile *and* the filing leg) ·
`test_its.py`. `book_hotels.log()` is the one line formatter for the whole program
(`main.py` imports it as `url_log`).

Booking and confirming are **decoupled**: a booking thread takes the hold, records it,
enqueues it and returns; one confirm worker drains that queue serially. Not tidiness — two
holds taken in the same second used to race for the same confirmation mail and both consumed
the same `c=` link, filing one application against an unknown date and wasting the other
hold. Safe to move because the emailed link establishes its own session, so the leg needs
none of the booking thread's cookies.

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

**The hold.** `empty_create` takes it and is the point of no return; it still goes out with
curl's retry enabled, so a `--max-time` expiry there can take two holds. The email POST is
the only request here sent with `retry=False`. **Nothing releases a hold.** Re-attempts are
bounded by `SKIP_HOTELS`, the `holds.json` filter, the per-call `attempted` set and the
site's own refusal; **there is deliberately no retry cooldown** — read `config.py` first.

**Request volume is the dominant operational risk**: the 503s are ~24-hour IP bans, and all
three recorded bans hit the scanner's `calendar_get`, so the risk lives in the *poll*
cadence, not in booking retries. Budget freed by `SCAN_REUSE_SESSION` is banked, not
respent.

**Debug dumps are written verbatim** — there is no redaction layer. A dump therefore holds live
session cookies, `s=` tokens, and, for anything on the emailed leg, the applicant's
記号/番号/カナ氏名/生年月日 (申込内容確認画面 echoes them all back). `debug_responses/` is gitignored;
treat a dump as credential-bearing and do not paste one anywhere.

### The other modules — their docstrings are the reference

- **`confirm_booking.py`** owns `_pending` and `_claimed`; `worker()` loops `drain_once()`.
  Holds pair **oldest-to-oldest** with unclaimed links — newest-wins caused the collision —
  and legs run one at a time. No mail within `CONFIRM_MAIL_TIMEOUT` drops the hold with
  `HUMAN NEEDED`; there is no hold clock, the site stays the authority via
  「ご利用のURLは無効となりました」. `confirm_allowed()` is checked at enqueue, before handing
  off to Chrome, and again by Chrome on the 申込内容確認画面; **`unmapped` non-empty must never
  submit**; every non-confirmed outcome logs `HUMAN NEEDED`. IMAP timeouts go to
  `IMAP4_SSL(...)`, never `socket.setdefaulttimeout()`.
- **`chrome.py`** owns the one Chrome and both jobs that need it. `submit()` is **the only
  thing that can file an application**; *every* field `:missing` there means a consumed link,
  not a fill bug. The solve runs under a hard `CAPTCHA_TIMEOUT` (it occupies the one thread
  that can re-mint a session) and **never caches a URL without `calendar_select`** — a
  non-calendar URL answers 200 too, so caching one poisons the cache with a healthy-looking
  session. The two are serialised by `hold()` because `_kill_stray_chrome()` reaps by
  `pgrep -f remote-debugging-port`, matching a filing browser as readily as a solving one — a
  timed-out solve would otherwise SIGKILL it between 申込する and 確認.

**The `s=` token** decodes to `service_category_id=1&verify_expires=<10 digits>`, unsigned. URLs
are logged and dumped **verbatim** — both files are gitignored and the token expires anyway.

## Concurrency Model

1 URL monitor, 1 confirm worker, 1 watchdog, N scanners (one per month) — all daemons — plus
temporary booking threads from each scanner's `ThreadPoolExecutor`, one per available date.
Loop bodies are guarded internally. **Chrome is single-threaded across the process** by
`chrome.hold()`; with one confirm worker only ever one emailed leg is in flight, so the
only contention left is the CAPTCHA solve. Shared state: `calendar_url_cache.txt` (written in
the URL monitor thread, read everywhere — POSIX atomicity for a small file, and readers treat
stale or empty data as "no URL"); `holds.json` and `reservations.json` (only via
`save_booking()`/`get_booked_hotels()` under `_bookings_lock`); `_pending` and `_claimed`
under `_pending_lock`; one cookie-jar temp file per thread, never shared.

## Data Files

All gitignored: `calendar_url_cache.txt` (the `s=` token — a live credential),
`chrome_user_agent.txt`, `its_booking.log` / `.log.N` (both carry `s=` tokens verbatim),
`debug_responses/` (tracked until 2026-08-18, so earlier files are in the public remote's
history). `holds.json` is `{date: [hotel_names]}` and those are **holds, not reservations** — each
lapses unless the emailed link was followed, but it is still the only thing preventing
duplicate applications, so losing it is worse than a crash. `reservations.json` is
`{date: ["hotel\t申込受付番号"]}`; those **are** confirmed, so an entry means a real
cancellation liability.

## Tests

`test_its.py` is stdlib-only against a throwaway localhost server and never touches ITS. Pass a
substring for a subset, `-v` for the flow's logging: `test_its.py -v test_503`. It binds
`127.0.0.1`, which the Apple Claude Code sandbox denies, so `.claude/apple/tool_allowlist.csv`
allowlists it by name — the allowlist matches the whole command string, so do not chain it
behind `&&`. It clears proxy settings for loopback, since a local proxy would rewrite responses.

- **The part that wins or loses a room.** `FakeITS` replays the real markup **including the
  escaped quotes AJAX responses arrive in**, because the extractors are markup-exact;
  `STATE.fail_once` injects the production failures; the calendar serves an `empty` date *and*
  an `a_little` one so an `empty`-only filter fails loudly; and `/apply/confirm` is a
  **tripwire** — anything reaching it means curl regressed into committing.
- **The curl layer underneath it** — header merging, UA injection, mobile-UA rejection, and the
  debug dumps — runs against the same fake, on its `/ok` and `/raw302` routes.
- **Tests are discovered, not hand-listed**, and arguments are injected by parameter name: a
  test declares `port` and/or `tmpdir` to receive them. An empty selection is an error, so a
  typo'd filter cannot read as green.
- **`Env` restores by reflection**, snapshotting every module-level setting rather than a
  hand-listed tuple, so a new knob in `config.py` cannot leak across tests.
- **Every date the flow sees is derived from `date.today()`**, never written out: `TARGET`
  and friends sit 33-66 days ahead, so the suite cannot start failing because the calendar
  moved past a literal or inside `AUTO_CONFIRM_MIN_DAYS`. Hardcoded `today=` arguments are
  fine — those tests never call `date.today()`.
- **The emailed leg must stay stubbed and the Chrome fallback never launched.** `Env` sets
  `AUTO_CONFIRM=False`, injects `_mail_source`/`_browser_submit` and clears `_pending` and
  `_claimed`; nothing may open a socket to `imap.gmail.com`. Drive the worker with
  `drain_once()`, never `worker()` — the loop would outlive the assertion. The `hold()`
  test runs in the calling thread for the same reason.

## Configuration (`config.py`)

Every setting is documented at its definition with its reason; that file is the reference.

- `TARGET_DATES` drives one scanner thread per distinct month. `_future_dates()` drops only
  strictly-past dates, so **dates past their cutoff are still polled** — curate it by hand
  against docs/SITE.md §1.
- `EMAIL` must be the mailbox IMAP reads; `AUTO_CONFIRM_MIN_DAYS = 11` is D−10 plus margin. Secrets come from the
  environment only, and `_load_dotenv` treats an exported-but-empty variable as unset.
- `Origin`, `Sec-Fetch-*`, `sec-ch-ua`, `Upgrade-Insecure-Requests` and `Accept-Encoding` are
  **deliberately not sent**; a mobile UA or non-Japanese `Accept-Language` risks a different
  template that breaks the extractors and `SKIP_HOTELS` matching. The TLS fingerprint is curl's,
  so a browser UA is a UA/TLS mismatch.
