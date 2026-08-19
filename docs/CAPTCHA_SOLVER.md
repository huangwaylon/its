# CAPTCHA solver — Cloudflare Turnstile via pydoll

`captcha_solver.py` mints a calendar session for `as.its-kenpo.or.jp`. That is
the one step of the booking flow curl cannot do: the site gates
`/calendar_apply` behind Cloudflare Turnstile, and passing it requires a real
browser. Everything downstream (`docs/BOOKING_VIA_CURL.md`) is plain HTTP.

The product is a URL of the form
`https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s=…`, written to
`config.CALENDAR_URL_CACHE`. `main.py`'s URL monitor thread calls
`get_calendar_url()` whenever that cache is missing, stale or invalid; nothing
else in the program ever solves a CAPTCHA.

> Historical note: the site previously used Google reCAPTCHA v2, and this
> document previously described a Playwright + local vision-model solver for it.
> That solver is gone. `docs/BOOKING_VIA_CURL.md` still says reCAPTCHA in
> places; it is stale on that point.

## Why pydoll rather than Playwright

pydoll drives an ordinary Chrome install over the DevTools Protocol. Playwright
drives its own bundled Chromium with its own instrumentation, and Turnstile
detects that — challenges that pass under pydoll fail under Playwright. Chrome
being the same binary a person would use is the whole point.

The cost is that pydoll is a thinner library: the flow reaches for CDP directly
in three places (mouse dispatch, window bounds, `Runtime.evaluate`), and depends
on the private `tab._connection_handler` to do it.

## Why the browser cannot be headless

Headless Chrome is rejected by Turnstile. The widget either never issues a token
or issues one the site refuses, so `options.headless` is `False` and the window
is real.

To keep a visible window from being a nuisance on a machine running this for
weeks, the window is **minimised via CDP** immediately after launch
(`Browser.getWindowForTarget` then `Browser.setWindowBounds` with
`windowState: minimized`). A minimised window still renders and still passes;
`headless` does not. Failing to minimise is non-fatal and only logged.

For the same reason `_save_user_agent()` refuses to record a UA containing
`Headless` — a headless UA in `chrome_user_agent.txt` would make every
subsequent curl request advertise a browser the site rejects.

## How Turnstile is solved

Turnstile renders its checkbox inside a cross-origin iframe, so no amount of
DOM querying from the host page reaches it. The trick is that CDP input events
are dispatched at the browser level and cross iframe boundaries.

`_click_turnstile_checkbox(tab)`:

1. Find the host page's `.cf-turnstile` container (10s timeout) and read its
   bounding box with `get_bounds_using_js()`.
2. Compute the checkbox position from that box: **28px in from the left edge,
   vertically centred**. The checkbox is at a fixed offset inside the widget, so
   the container's geometry is enough.
3. Dispatch `Input.dispatchMouseEvent` `mousePressed`, sleep 100ms, then
   `mouseReleased` at that point.
4. Poll `input[name="cf-turnstile-response"]` on the host page every
   `TOKEN_POLL_INTERVAL` (2s) for up to `TOKEN_TIMEOUT` (30s). A non-empty value
   is the token.

`solve_turnstile()` wraps that in up to `MAX_ATTEMPTS` (3) tries, reloading the
page and waiting 5s between them to get a fresh widget.

Only the token's **length** is logged. It is Cloudflare's single-use response
token; a prefix in the log was never diagnostic of anything.

## The rest of the flow

`_solve_and_cache()` launches Chrome (`--no-sandbox`, `--lang=ja-JP`,
`--window-size=1280,1600`) and minimises it, navigates to
`https://as.its-kenpo.or.jp/`, records the user agent to
`config.USER_AGENT_CACHE`, then clicks the 「カレンダーから探す」 link by scanning
`<a>` text content — the link has no stable selector. It checks the resulting URL
contains `calendar_apply`, screenshotting `unexpected_url.png` if not but
carrying on, since the solve may still work.

After Turnstile it submits the form. The 「次へ」 button is disabled until
Turnstile completes, so the script clears `disabled` and calls
`btn.form.submit()` directly. The cache write is a temp file plus `os.replace()`,
so a reader never sees a half-written URL.

## Never cache a bad URL

If the post-submit URL does not contain `calendar_select`, `_solve_and_cache()`
screenshots `not_calendar.png` and returns `None`, leaving the previous cache
entry untouched.

This matters more than it looks. Saving the URL unconditionally poisoned the
cache: every scanner would then replay a non-calendar URL that still answers
HTTP 200, so `main.check_cached_url()` judged the session healthy and no
re-solve ever fired. The booker sat there polling a URL that could not possibly
show availability. Returning `None` keeps the old (possibly still valid) URL and
retries on the next monitor cycle.

## Hard deadline and stray Chrome

`get_calendar_url()` is a thin wrapper: it runs `_solve_and_cache()` under
`asyncio.wait_for(..., config.CAPTCHA_TIMEOUT)` (180s) and returns `None` on
timeout or on any exception.

The deadline is not optional. The solve runs synchronously inside `main.py`'s URL
monitor thread, and that thread is the only thing in the program that re-mints a
session. A pydoll or Chrome hang there stops all booking indefinitely while the
process keeps refreshing its display and looks perfectly healthy.

On timeout, `_kill_stray_chrome()` runs. A cancelled `async with Chrome(...)` is
interrupted mid-await and pydoll cannot always finish its own teardown, so the
Chrome it launched survives — each orphan holding a profile directory and a few
hundred MB of RSS. Over weeks of solves that is how the machine runs out of
memory. The reaper matches `pgrep -f remote-debugging-port` and sends `SIGKILL`,
which is narrow enough that a Chrome the user is browsing in is never a
candidate.

`browser.stop()` is also called in a `finally`, guarded — `async with
Chrome(...)` stops the browser on exit too, so it is the second call, and a
raise from a `finally` would replace a good return value with an exception.

The wrapper's three behaviours (timeout returns `None`, Chrome is reaped, an
exception does not propagate) are covered by `test_captcha_timeout_wrapper` in
`test_booking_flow.py`.

## Logging and debug output

`log()` prefixes `[elapsed_seconds]`, measured from module import — deliberately
different from `book_hotels.log()`'s wall clock, so a solve's internal timings
read as durations. `_log_handler` is set by `main()` to route into the TUI's
left panel; unset, it prints.

Nothing here logs a URL or a token verbatim. `redact_url()` handles the
pre-solve URL and `token_summary()` the resulting calendar URL, both imported
from `book_hotels` so there is one implementation of "make this safe to write
down" (`book_hotels` imports only `config`, so this is not a cycle). The line
this replaced wrote the complete `s=` token to disk on all 647 solves in the
previous log; see the `s=` token section of `CLAUDE.md` for why a decoded field
is equivalent to the token itself.

Screenshots go to `DEBUG_DIR` and are written only on the three failure paths:
`unexpected_url.png`, `turnstile_failed.png`, `not_calendar.png`.

## Configuration

| Name | Where | Default | Purpose |
|---|---|---|---|
| `MAX_ATTEMPTS` | `captcha_solver.py` | 3 | Turnstile retries before giving up |
| `TOKEN_POLL_INTERVAL` | `captcha_solver.py` | 2s | Gap between token checks |
| `TOKEN_TIMEOUT` | `captcha_solver.py` | 30s | Wait for a token after the click |
| `DEBUG_DIR` | `captcha_solver.py` | `/tmp/captcha_debug` | Failure screenshots |
| `CAPTCHA_TIMEOUT` | `config.py` | 180s | Hard ceiling on one whole solve |

Worst case is `MAX_ATTEMPTS` × (`TOKEN_TIMEOUT` + 5s reload) ≈ 105s plus
navigation, which fits inside `CAPTCHA_TIMEOUT` with room to spare. Raising
`MAX_ATTEMPTS` or `TOKEN_TIMEOUT` without raising `CAPTCHA_TIMEOUT` makes the
deadline, rather than the retry count, the thing that ends a solve.

## Running it

Needs `pydoll-python` and a real Chrome install. Either solve once —

```bash
.venv/bin/python captcha_solver.py
```

— or let `main.py` solve on demand, forever, with `uv run main.py`. As a module,
`await get_calendar_url()` returns the URL string or `None`.

## Known limitations

- **Fixed sleeps.** The flow waits 3s after the homepage, 5s after the link
  click and 5s after the form submit rather than waiting on a condition. On a
  slow network a step can run against a page that has not finished loading; the
  symptom is a `calendar_apply` or `calendar_select` check failing on a URL that
  would have been correct a second later.
- **Private pydoll internals**, and a **hard-coded 28px checkbox offset**. Either
  can break on an upgrade or a widget redesign with no warning; a mis-aimed click
  presents as three consecutive 30-second token timeouts.
- **Only the checkbox path is handled.** If Cloudflare escalates to an
  interactive challenge the token never appears and the solve fails cleanly
  rather than being solved.
