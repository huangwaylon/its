# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

ITS Calendar Booker — polls the ITS facility calendar (as.its-kenpo.or.jp) for
vacancies on target dates and, when one appears, drives the 空き照会申込 flow over
curl. Cloudflare Turnstile guards the entry point, so a real Chrome solves it and
curl replays the resulting `s=` session token until it expires.

**`send_complete` is not a reservation.** The official flow has nine steps. The curl
chain in `book_hotels.py` implements six and ends at `send_complete` —
「メール送信を完了しました」, a confirmation email dispatched. What that secures is the
**30-minute hold** taken at step 7. `confirm_booking.py` then runs the official
steps 7–9 off the emailed link — applicant form → 申込する → 確認 — and only that
yields a 申込受付番号 and 予約確定. Verified end to end against the live site on
2026-08-19 (ブルーベリーヒル勝浦, 2026-09-01, 申込受付番号 10287126).

**But one request on that leg does not work over curl.** `POST /apply/confirm`
(申込する) is answered `302 → /service_category/index` regardless of what is sent,
while real Chrome — same URL, same 15 fields, same cookies — gets through. It is the
*client* that is refused, not the request, and the measurement was taken from a
sandbox whose HTTPS proxy intercepts. See Finding 6 in `docs/BOOKING_VIA_CURL.md`
before touching anything on that request, and **check whether it reproduces off a
proxied network first.**

So `confirm_booking` tries curl and, when curl is refused, hands that one leg to
`browser_apply` — real Chrome, driven the same way `captcha_solver` drives it. curl
stays the primary path, so if the cause turns out to be environmental the fallback
simply stops firing with nothing to undo. `BROWSER_CONFIRM=False` restores the
curl-only behaviour. Without `pydoll-python`, or if the browser fails too, a booking
still ends with the hold taken and the mail sent, and the log says `HUMAN NEEDED`
with the minutes remaining.

## Setup & Running

```bash
uv run main.py                       # normal operation: solve CAPTCHA, then book forever
.venv/bin/python captcha_solver.py   # solve only; writes calendar_url_cache.txt
```

`book_hotels.py` is stdlib + curl; `captcha_solver.py` and `browser_apply.py` need
`pydoll-python` (real Chrome over CDP — Turnstile rejects headless, so the window is
visible but minimised); `display.py` needs `rich`. No linter or formatter configured.
`make_s_token.py` builds an `s=` token layer by layer, for inspection.
`check_env.py` is the preflight: `.env` completeness, IMAP reachability, and which
target dates the confirmation gate would auto-confirm.

## Booking rules that constrain the design

Full detail and sources in `docs/ITS_RULES.md`. What bears on the code:

- **Two channels:** 抽選申込 (monthly lottery, by chance, five applications per
  insurance number per month) and 空き照会申込 (first-come vacancy). Only the second
  is automated.
- **Release instant:** whatever the lottery did not fill drops at 00:00 JST on the
  照会開始日 — FY2026, stay months 8月…3月: 6/27, 7/27, 8/27, 9/27, 10/27, 11/27,
  12/27, 1/27.
- **Cutoffs:** applications close 4 days before use for 直営 (トスラブ 3 館),
  ブルーベリーヒル勝浦, 日光千姫物語, 熱海後楽園ホテル and all 夏季/冬季 facilities,
  10 days for other 通年 facilities. `_future_dates()` drops only strictly-past
  dates, so dead dates are still polled and `TARGET_DATES` needs curating by hand.
  `PRIORITY_HOTELS = ['NAGU']` is NAGU勝浦, 夏季: 4-day cutoff, never listed past
  9/30.
- **Cancellations are the only supply after the release instant, and they are
  structured:** web cancellation is possible only until 10 days before use, so
  pressure peaks at D−10 — 22% of observed availability episodes start exactly
  there, 57% within D−9…D−14. Later cancellations are phone-only, 9:00–17:00
  Mon–Fri. No waitlist exists.
- **Episodes are not seconds long:** of 104 measured intra-episode gaps 38 are
  20–21 s (still there a full scan cycle later) and 66 are ≥1800 s. Design for a
  half-hour window, not 200 ms.
- Max 2 nights per stay per facility, no overlapping applications at one facility,
  max 10 rooms. Vacancy search is suspended during lottery processing, which the bot
  does not know, so it polls a switched-off service.

## Architecture

`config.py` (settings) · `main.py` (orchestration) · `book_hotels.py` (booking
engine) · `captcha_solver.py` (Turnstile) · `confirm_booking.py` (the emailed leg) ·
`display.py` (TUI) · `check_env.py` (`.env` / IMAP preflight).

### `main.py`

One `_Worker` per target month plus the URL monitor, then a watchdog, then blocks on
the display. `_rotate_log()` runs at startup only, so no log write needs a lock.

- **URL monitor.** `check_cached_url()` curls the cached URL; on anything but 200 it
  runs `asyncio.run(get_calendar_url())` **synchronously in this thread**, which is
  what prevents overlapping solves without a lock. A *proactive* refresh is deferred
  while `book_hotels.active_bookings()` is non-zero — a booking carries one `s=`
  token across a ~7-request chain — but a *repair* never is.
- **Watchdog.** Restarts a `_Worker` whose thread died: workers are daemons and
  `main()` never joins them, so a dead scanner, or the URL monitor which alone can
  re-mint a session, would otherwise be invisible behind a display that keeps drawing.
- **Display mode.** The Rich TUI runs only when `sys.stdout.isatty()`; under
  nohup/launchd/a pipe (the normal case) it falls back to plain lines, since the
  escape sequences would only corrupt a piped log.

### `book_hotels.py`

Never solves a CAPTCHA; all URL access goes through `_read_cached_url()`.

**Scanner** (`scan_and_book_month`) — one thread per month, forever, whole loop body
guarded because nothing may end it. Per cycle: read the cached URL, GET the calendar
(skippable while `SCAN_REUSE_SESSION` can reuse a live csrf/`s` pair), POST
`calendar_select` for the month — one response carries every date's state — then
spawn a booking thread per available date. Sleep is
`min(RETRY_DELAY * 2**failures, SCAN_BACKOFF_MAX)` plus
`random.uniform(0, SCAN_JITTER)`, the jitter keeping the per-month scanners out of
lockstep; the cookie jar is truncated only after a failure.

**Per date** (`book_all_hotels_for_date` → `_book_date_once`) outcomes are
`'ok'`/`'done'`, `'retry'` (5xx, transport failure, dead session), `'unavailable'`,
`'failed'`. Each pass opens a fresh session, POSTs `service_group_select` for the
hotel list, filters skipped/booked/attempted/cooling-off names, puts
`PRIORITY_HOTELS` first, then books hotels one at a time on a fresh session each;
only setup requests are retried. That list holds **only facilities with vacancy on
that date** (header 「{date}に空きがある施設です」) — typically about three, not the
24-facility roster.

Invariants that are not obvious from the code:

- `is_available()` matches `empty` **and** `a_little`, both clickable on the site;
  matching `empty` alone silently skipped every limited-availability date. Shared
  with `_open_calendar_session` so scan and re-check cannot disagree.
- `_is_session_dead()` is a 302 to `/service_category/index` — what a stale `s=`
  token answers, and the largest failure class on disk. `_is_retryable()` excludes
  302 so a dead session stays distinguishable from flow progress.
- `curl()` never raises; a transport failure is `(0, '', None)`, because the scan
  loop has no `except` around its calls.
- Headers are merged in Python, never appended as extra `-H` flags: curl emits every
  header it is handed, so a default plus a per-call `Accept` sends both.
- `_read_cached_url()` catches `OSError`, not just `FileNotFoundError` — a raise
  there kills that month's scanner for the rest of the run.
- `_write_bookings()` writes a same-directory temp file, fsyncs, then renames, since
  `open(..., 'w')` truncates first. A present-but-unparseable file is renamed to
  `bookings.json.corrupt.<ts>.<seq>`, never overwritten, because a failed read
  returning `{}` plus a normal save would rewrite it with only the newest entry; an
  `OSError` is not corruption, and the write is refused instead.
- Hotel names match after casefolding, unescaping and collapsing whitespace, so a
  full-width space cannot defeat a skip entry. Anything on neither list is eligible.
- CSRF tokens are **not** single-use: Rails compares statelessly against the session,
  and reusing one `authenticity_token` across two POSTs is how the verified booking
  was made. `__token__` (40 hex, email form) is a separate app-level nonce, plausibly
  consumable, re-extracted every time; do not conflate them.

**The hold, and what `retry=False` protects.** `book_one_hotel()` is steps 3–9:
select hotel → select service (302) → GET booking form → AJAX room search → POST
`/apply/empty_create` → agree to rules → POST the email form, expecting
`send_complete`.

- `empty_create` is 「予約手続きに進む」: it takes the **30-minute hold** and is the
  point of no return. It still goes out with curl's retry enabled, so a `--max-time`
  expiry there can take two holds.
- The email POST is the only request sent with `retry=False`: it is not idempotent
  (consumes `__token__`, dispatches mail) and `--max-time` can expire after the
  server accepted it, so a repeat would submit twice with no way to tell. Status 0 is
  logged as `outcome unknown` and the hotel abandoned.
- **Nothing here releases a hold.** Any failure after `empty_create` leaks a held
  room and re-attempts stack holds, after which the bot reads its own holds back as
  「空き部屋がございません」.
- `attempted` lives only for one `book_all_hotels_for_date` call, so the
  per-(date, hotel) cooldowns are what bound re-attempts; unbounded, a date available
  for half an hour with two failing facilities cost ~2,000 requests in 30 minutes.

**The 503s are IP bans, and they last about 24 hours.** Every one of the 467 non-empty
recovered dumps is the same page — `<title>セキュリティアラート</title>`, served with a
503 — and its body says so outright:

> ご利用のIPアドレス（…）から、アクセス過多を検知しました。システムセキュリティの
> 観点より、**約24時間**、一時的にシステムへのアクセスを遮断します。お急ぎの場合、
> 別のネットワークからアクセスしてください。

This is notice 144's 一定時間の遮断 being enforced. Three separate episodes are on
record — 2026-04-07, 2026-04-12 and 2026-08-17 — across three egress IPs, every one
served on the scanner's `calendar_get`, which is simply the most frequent request. The
detector's own reason string is `time error`.

Two consequences. **Request volume is the dominant operational risk**, not a nuisance:
one ban costs a whole day of every target date at once, so trading load for detection
latency is a bad bet, and any budget freed by `SCAN_REUSE_SESSION` should be banked
rather than respent on a shorter interval. And **the dump corpus is not a census** —
`DEBUG_DUMP_KEEP` prunes oldest-first, so the surviving sample is survivorship-biased;
an earlier reading of it concluded these 503s clustered in office hours and were
therefore ordinary load. They are not. Do not repeat that inference from dump
timestamps alone: read the bodies.

**Debug dumps** (`_dump_debug`) write a redacted body plus redacted response headers to
`DEBUG_DIR`, throttled per `(label, step)` and pruned to `DEBUG_DUMP_KEEP`. Redaction
is a **whitelist**: anything unrecognised becomes `[len=N sha256=xxxxxxxx]`, so a
future session-bearing header cannot leak by default, and the digest is stable so two
dumps can be compared without either holding a session id.

### `confirm_booking.py`

The official steps 7–9, off the link in the site's confirmation mail: poll IMAP for
the mail → `GET /apply/new?c=<uuid>` → fill the applicant form → 申込する → 確認 →
申込受付番号. Called from `book_hotels._finish_from_email`, which imports it lazily and
swallows every failure: a hold plus a sent mail is already worth keeping, and the
human fallback works from exactly that state.

- **`bh.confirm_allowed()` is consulted immediately before each committing POST**, not
  once at the top. Free web cancellation ends at D−10, so anything nearer than
  `AUTO_CONFIRM_MIN_DAYS` is left for a person.
- **The form is filled from what the form declares**, never from hard-coded names.
  `map_fields` matches the live controls against `config.APPLICANT`; anything it
  cannot place is `unmapped`, and a caller finding `unmapped` non-empty must not
  submit — these are 資格認証のキー and a half-filled form is a rejected application
  plus a wasted hold, not a near miss.
- **`required` cannot be trusted and neither can labels.** The live form marks nothing
  `required` and never says 必須 (it uses `<img src=".../must-*.png">`), and it labels
  by *proximity*: `apply[month]`'s preceding text is the tail of the year dropdown's
  options. So `_match_rule` tries **field name first and label only as a fallback**,
  and a name match wins even when no value is configured — otherwise `apply[address]`
  with `ITS_ADDR` unset matched the 〒 a few characters before it and would have
  submitted the postcode as the street address.
- **`_URL_RE` is built from `BASE`**, not hard-coded. The host restriction is a safety
  property — the mail body is the one input here nobody in this repo authors — but
  baking the literal host in also made the whole leg untestable, because
  `extract_apply_link` returned None for every fake.
- Mail is matched on **arrival time**, never the stay date: the mail names only the
  date it was sent, so a stay-date filter can never match.
- `parse_receipt` searches the **tag-stripped** text. The live page is
  `<strong>申込受付番号：  10287126</strong>` — one tag around both — so a raw-markup
  regex works by luck; one tag between them and it captures `strong`, writing a
  made-up number into the only record that a real reservation exists.
- 確認 is the one request here sent with `retry=False`. Status 0 is
  `confirm outcome unknown` and it says to check the mailbox before trying again.

### `browser_apply.py` and `chrome_guard.py`

`browser_apply.submit()` is the 申込する/確認 leg in real Chrome, reached only after
curl's POST was refused. It is the sequence that was verified live on 2026-08-19:
open the emailed link → write every value back **and read it back** → 申込する →
require 申込内容確認画面 on screen → 確認 → parse the receipt.

- **The read-back is not belt-and-braces.** A `<select>` handed a value that is not
  among its options keeps its old one silently, so without verifying, a mismatched
  都道府県 or 生年月日 would be *filed blank* against somebody's insurance record. Any
  field the page did not accept aborts before 申込する.
- **`allow_commit` is a callable, not a boolean.** Filling a form in a browser takes
  tens of seconds and the free-cancellation gate can close inside that window, so it
  is re-evaluated on the 申込内容確認画面 immediately before the commit.
- **A timeout does not reap the browser.** It may have landed after 確認 was accepted,
  and SIGKILL cannot un-file an application; the outcome is reported unknown and the
  mailbox is the authority.
- Field values are never logged — they are 資格認証のキー. Only names, and only when
  the page rejected one.
- The budget is `min(BROWSER_CONFIRM_TIMEOUT, hold remaining)`, so this can never
  outlive the room it is trying to keep.

`chrome_guard` exists because **`captcha_solver._kill_stray_chrome()` reaps by
`pgrep -f remote-debugging-port`**, which matches an application-filing browser as
readily as a Turnstile-solving one. A solve that timed out while an application was
in flight would SIGKILL it somewhere between 申込する and 確認, with no way to learn
which side of the commit it died on. One lock serialises the two, so the reaper only
runs when nothing else of ours has a Chrome open and anything `pgrep` finds really is
an orphan. Both sides take it with a timeout — the solve thread is the only thing
that can re-mint a session, and the booking side is holding a room — and a refused
solve simply waits for the next `URL_CHECK_INTERVAL`.

### `captcha_solver.py`


pydoll drives real Chrome over CDP (Playwright trips the bot detection).
`get_calendar_url()` opens the homepage, clicks カレンダーから探す, solves Turnstile,
submits, records Chrome's UA to `USER_AGENT_CACHE`, and caches the calendar URL. The
solve clicks ~28 px in from the `.cf-turnstile` box's left edge with CDP mouse events
(which cross the cross-origin iframe boundary), then polls
`input[name="cf-turnstile-response"]`.

- **Hard deadline:** `_solve_and_cache()` runs under
  `asyncio.wait_for(..., CAPTCHA_TIMEOUT)` because it occupies the one thread that
  can re-mint a session, and an untimed Chrome hang would stop all booking behind a
  healthy-looking display. On timeout `_kill_stray_chrome()` reaps processes matching
  `remote-debugging-port`, which a cancelled `async with Chrome(...)` cannot always do.
- **Never caches a bad URL:** a post-submit URL without `calendar_select` is
  discarded. Caching it poisoned the cache, because a non-calendar URL still answers
  200, so the session looked healthy and no re-solve ever fired.
- Nothing logs a URL or token verbatim; `redact_url`/`token_summary` come from
  `book_hotels`, which imports only `config`, so there is no cycle.

### `display.py`

Two bounded `deque`s (left: URL monitor/CAPTCHA; right: booking) plus a URL bar,
rendered into a Rich `Layout` under `Live` at 4 Hz. Appending is lock-free, since
`deque.append` with a `maxlen` and a `str` assignment are atomic under the GIL. The
panel holds no state, so the refresh *rate* is 4 Hz but the update *cadence* is
however often something logs — a quiet panel is the steady state, not a stall.

**`_visual_lines()` must measure, never estimate.** It asks Rich for the wrapped
height (`Text.wrap`) rather than computing `ceil(cell_len / width)`: Rich breaks on
word boundaries, so the ceiling estimate *underestimates*, `_render_panel` then
selects one message too many, and `Panel` crops the overflow off the **bottom**, where
the newest lines are. Real log lines mis-measure at 80 and 160 columns.

## The `s=` token

```
s= → base64 → reverse → base64 → "service_category_id=1&verify_expires=<10 digits>"
```

47 bytes of printable ASCII with nothing left over: no signature, no MAC, so nothing
binds the token to the Turnstile solve that produced it. The payload is two fields and
one is constant, so printing `verify_expires` in full is equivalent to printing the
token — hence `token_summary()` renders timestamp-shaped fields as a relative delta
(`verify_expires=+1h29m`), masks other values as `<N chars>`, always shows field
*names* so a new field is visible without leaking, and is strict, since a truncated
token still base64-decodes into plausible bytes.

`verify_expires` is **not yet usable for scheduling refreshes**: one live sample
carried a timestamp ten days in its own past. Watch it across several solves first.

## Concurrency Model

1 URL monitor (daemon), 1 watchdog (daemon), N scanners (daemon, one per month), plus
temporary booking threads from each scanner's `ThreadPoolExecutor`, one per available
date. Long-lived threads are wrapped in `main._Worker` for the watchdog, and their
loop bodies are guarded internally as the first line of defence.

**Chrome is single-threaded across the process**, by `chrome_guard`: the URL monitor's
CAPTCHA solve and a booking thread's `browser_apply` submit take one lock, because the
solve's stray-process reaper cannot tell the two browsers apart.

Shared state: `calendar_url_cache.txt` (written in the URL monitor thread, read
everywhere — POSIX atomicity for a small file, and readers treat stale or empty data
as "no URL"); `bookings.json` (only via `save_booking()`, `get_booked_hotels()`,
`booked_hotels_checked()`, all under the non-reentrant `_bookings_lock` that
`_load_bookings()` assumes is held); the `(date, hotel)` cooldown map under
`_cooldown_lock`; one cookie-jar temp file per thread, never shared.

## Data Files

- `calendar_url_cache.txt` — current session URL. **Gitignored: the `s=` token is a
  live credential.**
- `chrome_user_agent.txt` — UA of the Chrome that minted the current token, so curl
  replays it consistently. Gitignored; `FALLBACK_USER_AGENT` applies until the first
  solve.
- `bookings.json` — `{date: [hotel_names]}`, and those are **holds, not
  reservations**: each expired 30 minutes later unless a human followed the emailed
  link. Still the only thing preventing duplicate applications, so losing it is worse
  than a crash.
- `its_booking.log` / `.log.N` — rotated at startup, gitignored under both patterns
  (`*.log` misses the rotated names). **Credential-bearing:** the `s=` token has no
  MAC, so any decoded field is equivalent to the token.
- `debug_responses/` — failure dumps, bodies redacted because they embed `s=` tokens
  in form actions. Gitignored, but tracked until 2026-08-18, so earlier files remain
  in the public remote's history. **Redaction now also covers `config.APPLICANT`** —
  申込内容確認画面 echoes 記号/番号/カナ氏名/生年月日/電話/住所 straight back, and
  `config.py` claimed those were in the redaction list when they were not. Two passes:
  every `value="…"` on an input whose *name* looks like an identity field, plus each
  configured value found literally (including 生年月日 in the site's own
  `2000年3月4日` rendering). `sex`/`zokugara` are deliberately left alone — 男/女 and
  本人 identify nobody and appear in the form's `<option>` labels regardless.
- `reservations.json` — `{date: ["hotel\t申込受付番号"]}`. Unlike `bookings.json` these
  are **confirmed reservations**, so an entry means a real cancellation liability
  exists. Gitignored.

## Tests

The two server suites are stdlib-only against a throwaway localhost server; the
display suite needs `rich`. None touches ITS.

```bash
.venv/bin/python test_http_layer.py      # curl + redaction layer
.venv/bin/python test_booking_flow.py    # booking flow end to end
.venv/bin/python test_display.py         # TUI rendering
```

- **`test_http_layer.py`** — header merging (a duplicate `-H` would let the server
  pick) and redaction (a dump must never contain a cookie or token value).
- **`test_booking_flow.py`** — the part that wins or loses a room. `FakeITS` replays
  the real markup from `docs/BOOKING_VIA_CURL.md` and the dumps, **including the
  escaped quotes AJAX responses arrive in**, because the extractors are markup-exact.
  `STATE.fail_once` injects the production failures (503, the 302 to
  `/service_category/index`, 404, the 「空き部屋がございません」 page, `HANGUP` for
  curl status 0), and the fake calendar serves an `empty` date *and* an `a_little` one
  so an `empty`-only filter fails loudly. Also covers priority ordering, skip
  normalisation, the final submit never being repeated, `bookings.json` atomicity and
  corruption handling, the watchdog and the CAPTCHA timeout.
- **The emailed leg must stay stubbed.** `confirm_from_email` polls IMAP for
  `CONFIRM_MAIL_TIMEOUT` seconds, so when `AUTO_CONFIRM` reached the suite every
  successful booking blocked 180 s against the operator's real Gmail: over an hour,
  printing nothing, and only if credentials happened to be present. `Env` therefore
  sets `AUTO_CONFIRM=False` by default and injects `confirm_booking._mail_source`
  (a callable returning message text) for the tests that want the leg. Nothing in
  any suite may open a socket to `imap.gmail.com`.
- **`confirm_booking` coverage** replays the *live* applicant form: `_method="true"`,
  no `utf8`, nothing marked `required`, the 必須 markers as `<img name="*_img">`,
  labels only adjacent to their control, and `man`/`woman`/`myself` option values.
  Plus the expired-hold page, the 申込する rejection, `確認` never being repeated,
  an unfillable form deferring without submitting, and that a dump of these pages
  carries no 記号/番号/カナ氏名/生年月日/電話/住所.
- **The Chrome fallback is stubbed, never launched.** `Env` replaces
  `confirm_booking._browser_submit` with a recorder whose default result is what the
  curl-only path used to return, so the tests written before the fallback existed keep
  asserting what they meant. Covered: it fires only after curl is refused, gets the
  emailed link and the 13 applicant fields with the hidden ones stripped, gets a
  *live* `allow_commit` callable, records the reservation on success, is skippable via
  `BROWSER_CONFIRM`, and still reaches `HUMAN NEEDED` when it fails too. A separate
  test asserts `chrome_guard` refuses a second Chrome and that a busy Chrome defers a
  solve rather than disabling it — deliberately run in the calling thread, because a
  background thread that outlives the assertion goes on to open a real browser.
- **`test_display.py`** — that the newest logged line is on screen: measures
  `_visual_lines` against what Rich really renders, then renders layouts at six
  terminal sizes using real log lines as fixtures.

Pass a substring for a subset and `-v` for the flow's own logging:
`.venv/bin/python test_booking_flow.py -v test_503`.

The server suites bind `127.0.0.1`, which the Apple Claude Code sandbox denies;
`.claude/apple/tool_allowlist.csv` allowlists both by name. **Run each in its own Bash
call** — the allowlist matches the whole command string, so chaining a test behind
`&&` falls through to the sandbox and fails with `PermissionError: [Errno 1]`. Both
clear proxy settings for loopback, since a local proxy would rewrite responses.

## Configuration (`config.py`)

| Setting | Default | Notes |
|---|---|---|
| `TARGET_DATES` | — | One scanner thread per distinct month; curate against the cutoffs. |
| `EMAIL`, `NUM_GUESTS` | — | Email form / room search. |
| `SKIP_PAST_DATES` | `True` | Drops strictly-past dates only. |
| `RETRY_DELAY` | 20 | Base scan interval, ×`2**failures`. |
| `SCAN_BACKOFF_MAX` / `SCAN_JITTER` | 300 / 5 | Backoff ceiling; random 0..N s per sleep, breaking lockstep. |
| `SCAN_REUSE_SESSION` / `_MAX_FAILURES` | `True` / 3 | Reuse a live csrf/`s` pair to skip the scan GET; self-disables after N rejections. |
| `CURL_MAX_ATTEMPTS` | 3 | Attempts per request; bypassed by `retry=False`. |
| `CURL_RETRY_BACKOFF` / `_MAX` | 0.5 / 8.0 | Doubling retry delay; `Retry-After` wins, capped. |
| `CURL_TIMEOUT` | 30 | curl `--max-time`; `subprocess.run` gets +10 s so a stuck curl cannot wedge a thread. |
| `BOOK_MAX_ATTEMPTS` / `BOOK_RETRY_DELAY` | 3 / 2.0 | Re-attempts of a date on a fresh session. |
| `HOTEL_RETRY_COOLDOWN` / `HOTEL_HOLD_COOLDOWN` | 300 / 1800 | Per-(date, hotel) hold-off before / after a hold was taken; the latter must not undercut 30 min. |
| `MAX_BOOKINGS_PER_DATE` | 0 (off) | Cap on holds per date; off because several hedge against missing the email window. |
| `URL_CHECK_INTERVAL` / `URL_REFRESH_INTERVAL` | 60 / 1800 | Validity check; proactive re-solve, skipped mid-booking. |
| `CAPTCHA_TIMEOUT` | 180 | Hard ceiling on one solve. |
| `AUTO_CONFIRM` / `AUTO_CONFIRM_MIN_DAYS` | `True` / 11 | Run the emailed leg; never inside the free-cancellation window (D−10 plus a day of margin). |
| `CONFIRM_MAIL_TIMEOUT` / `_HOLD_SECONDS` / `_HOLD_MARGIN` | 180 / 1800 / 300 | IMAP poll budget; the site's hold; how much of it to leave unspent. |
| `BROWSER_CONFIRM` / `_TIMEOUT` | `True` / 240 | Finish 申込する in real Chrome when curl is refused; never longer than the hold has left. |
| `PRIORITY_HOTELS` | `['NAGU']` | Substrings attempted first; hotels book sequentially at ~7 requests each. |
| `SKIP_HOTELS` | list | Never booked, matched normalised. The "keep" list is only a comment — anything on neither list is eligible. |
| `DEBUG_DUMP_INTERVAL` / `_KEEP` | 300 / 400 | Throttle per label+step; file cap for `DEBUG_DIR`. |
| `LOG_MAX_BYTES` / `LOG_BACKUPS` | 32 MB / 3 | Rotation at startup. |
| `IDLE_LOG_INTERVAL` | 300 | One idle line per N s. |
| `BROWSER_HEADERS`, `ACCEPT`, `ACCEPT_LANGUAGE`, `FALLBACK_USER_AGENT` | — | Browser-like headers; the fallback UA applies until the first solve. |

**Deliberately not sent** as browser headers:

- `Origin` — absent means Rails skips its origin check; sending it opts into a check
  that fails if the app sees `http` behind the ALB, producing an
  `InvalidAuthenticityToken` redirect indistinguishable from the bug being chased.
- `Sec-Fetch-*`, `sec-ch-ua`, `Upgrade-Insecure-Requests` — one static value must
  contradict one of the two request classes made here (navigation POST vs XHR), and a
  self-inconsistent set is a stronger bot signal than none.
- `Accept-Encoding` / `--compressed` — this curl build has no brotli or zstd, and a
  decode failure yields an empty body, manufacturing more 0-byte responses.
- A mobile UA or non-Japanese `Accept-Language` — the site may serve a different
  template, breaking the markup-exact extractors and `SKIP_HOTELS` matching, and an
  unmatched name books a hotel meant to be skipped.

The TLS fingerprint is still curl's, so a browser UA is a UA/TLS mismatch.

## Logging

`book_hotels.log()` (`HH:MM:SS`, ANSI colour) is imported by `main.py`;
`captcha_solver.log()` (`[elapsed]`) is independent. `main.url_log()` routes through
`_url_sink` (TUI panel or stdout), and everything also reaches `LOG_FILE` with ANSI
stripped. Reserve red for what is actually wrong: 「空き部屋がございません」 is an
ordinary lost race, so it is yellow with no dump.
