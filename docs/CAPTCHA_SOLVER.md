# CAPTCHA solver — Cloudflare Turnstile via pydoll

`captcha_solver.py` mints a calendar session for `as.its-kenpo.or.jp`: the one step
of the flow curl cannot do. What it does, and why each guard exists, is in the
module's own docstrings and in `CLAUDE.md` — this file records only what is written
down nowhere else.

## Why pydoll rather than Playwright — and what it costs

pydoll drives an ordinary Chrome install over the DevTools Protocol. Playwright
drives its own bundled Chromium with its own instrumentation, and Turnstile
detects that: challenges that pass under pydoll fail under Playwright. Chrome
being the same binary a person would use is the whole point.

The cost is that pydoll is a thinner library, so the flow reaches for CDP
**directly** in three places — mouse dispatch, window bounds, and
`Runtime.evaluate` — and does it through the **private `tab._connection_handler`**.
That is an unsupported internal: a pydoll upgrade can rename or restructure it
with no deprecation and no type error, and every one of those three call sites
breaks at once. Check it first after any dependency bump.

## Minimising, and why headless is not an option

Headless Chrome is rejected by Turnstile — the widget either never issues a token
or issues one the site refuses. So `options.headless` is `False` and the window
is real. To keep that from being a nuisance on a machine running this for weeks,
the window is minimised immediately after launch via CDP:
`Browser.getWindowForTarget` for the `windowId`, then `Browser.setWindowBounds`
with `windowState: minimized`. **A minimised window still renders and still
passes Turnstile; headless does not.** Failing to minimise is non-fatal.

For the same reason `_save_user_agent()` **refuses to record a UA containing
`Headless`** — that string in `chrome_user_agent.txt` would make every subsequent
curl request advertise a browser the site rejects, long after the solve that
wrote it.

## Serialisation with the other Chrome

`browser_apply` files the applicant form in Chrome too, and the two are serialised
by `chrome_guard`, so a solve arriving while an application is in flight is
**deferred, not queued behind it** — `get_calendar_url()` returns `None` and the
URL monitor retries on the next `URL_CHECK_INTERVAL`.

## Constants that `config.py` does not hold

| Name | Default | Purpose |
|---|---|---|
| `MAX_ATTEMPTS` | 3 | Turnstile retries before giving up |
| `TOKEN_POLL_INTERVAL` | 2s | Gap between token checks |
| `TOKEN_TIMEOUT` | 30s | Wait for a token after the click |
| `SCREENSHOT_DIR` | `/tmp/captcha_debug` | Failure screenshots (unrelated to `config.DEBUG_DIR`) |

Worst case is `MAX_ATTEMPTS` × (`TOKEN_TIMEOUT` + 5s reload) ≈ 105s plus
navigation, and **that has to fit inside `config.CAPTCHA_TIMEOUT`** (180s).
Raising either knob without raising `CAPTCHA_TIMEOUT` makes the deadline, rather
than the retry count, the thing that ends a solve — so the last attempt is cut
off mid-poll instead of reported as a failure.

## Known limitations

- **Fixed sleeps.** 3s after the homepage, 5s after the link click, 5s after the
  form submit — none waiting on a condition. On a slow network a step runs against
  a page that has not finished loading; the symptom is a `calendar_apply` or
  `calendar_select` check failing on a URL that would have been correct a second
  later.
- **Hard-coded 28px checkbox offset**, plus the private pydoll internals above.
  Either can break on an upgrade or a widget redesign with no warning, and a
  mis-aimed click presents as **three consecutive 30-second token timeouts** —
  identical in the log to Cloudflare simply refusing.
- **Only the checkbox path is handled.** If Cloudflare escalates to an interactive
  challenge the token never appears; the solve fails cleanly rather than solving.
