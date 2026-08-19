#!/usr/bin/env python3
"""The one Chrome this program drives, and the two jobs that need it.

Two users, one browser: the Turnstile solver that mints a session, and the
申込する/確認 leg that files an application. They are serialised by `hold()`, because
`_kill_stray_chrome()` reaps by `pgrep -f remote-debugging-port` and that matches a
filing browser as readily as a solving one — a timed-out solve would otherwise SIGKILL
a browser between 申込する and 確認, with no way to learn which side of the commit it
died on. Under the lock, anything `pgrep` finds really is an orphan.

**Turnstile** rejects headless Chrome and detects Playwright's bundled Chromium;
pydoll driving real Chrome over CDP passes, and a minimised window still passes. The
checkbox sits in a cross-origin iframe reachable only by CDP mouse events. See
docs/SITE.md §6.

**`submit()`** is reached only after curl's POST was refused (docs/SITE.md §5). **It
presses 確認**, past which a real reservation exists, so:

  - `allow_commit` is a **callable**, re-consulted immediately before the commit —
    filling a form takes tens of seconds and the gate can close inside that window;
  - every field is written back and *read back*. A `<select>` given a value not among
    its options keeps its old one silently, so an unverified mismatch would file a
    blank 都道府県 or 生年月日 against somebody's insurance record;
  - 申込内容確認画面 must be on screen before 確認 is looked for.

A timeout does **not** reap the browser — it may have landed after 確認 was accepted,
and SIGKILL cannot un-file an application, so the outcome is unknown and the mailbox is
the authority. Field values are never logged; they are 資格認証のキー.

pydoll is imported inside the functions that need it, never at module scope: it is the
one dependency, and a booking that already holds a room and has had its mail sent must
not be lost to its absence.

    .venv/bin/python chrome.py       # solve, cache the calendar URL
"""
import asyncio
import contextlib
import json
import os
import signal
import subprocess
import threading
import time

from config import (
    BROWSER_CONFIRM_TIMEOUT, CALENDAR_URL_CACHE, CAPTCHA_TIMEOUT, USER_AGENT_CACHE,
)
import book_hotels as bh
from book_hotels import log

MAX_ATTEMPTS = 3          # Turnstile retries before giving up
TOKEN_POLL_INTERVAL = 2   # seconds between token checks
TOKEN_TIMEOUT = 30        # max seconds to wait for token after click
                          # MAX_ATTEMPTS * TOKEN_TIMEOUT must fit in CAPTCHA_TIMEOUT

_CONFIRM_TEXT = '申込内容確認'


# ── one Chrome at a time ─────────────────────────────────────────────
# Long enough to sit through a solve plus teardown, short enough that a wedged holder
# cannot cost a whole booking.
DEFAULT_WAIT = 210

_lock = threading.Lock()


@contextlib.contextmanager
def hold(timeout=None):
    """Own the browser for the duration of the block.

    Yields True when the lock was taken, False when not — callers decide, because a
    solve can wait for the next cycle while a booking on a hold may have seconds.
    `timeout=None` reads `DEFAULT_WAIT` at call time, so a test can shorten it.
    """
    wait = DEFAULT_WAIT if timeout is None else timeout
    acquired = _lock.acquire(timeout=wait)
    try:
        yield acquired
    finally:
        if acquired:
            _lock.release()


# ── Chrome plumbing ──────────────────────────────────────────────────

def value(result):
    """Unwrap a CDP Runtime.evaluate result to its plain string value."""
    if isinstance(result, dict):
        result = result.get('result', {}).get('result', {}).get('value', '')
    return result if isinstance(result, str) else ''


def launch():
    """A real, non-headless Chrome — Turnstile rejects headless, and so may /apply."""
    from pydoll.browser.chromium.chrome import Chrome
    from pydoll.browser.options import ChromiumOptions

    options = ChromiumOptions()
    options.headless = False
    options.add_argument('--no-sandbox')
    options.add_argument('--lang=ja-JP')
    options.add_argument('--window-size=1280,1600')
    return Chrome(options=options)


async def minimise(tab):
    """Hide the window without going headless — minimised still renders, and passes."""
    try:
        win = await tab._connection_handler.execute_command(
            {'method': 'Browser.getWindowForTarget', 'params': {}})
        window_id = win.get('result', win).get('windowId')
        if window_id:
            await tab._connection_handler.execute_command({
                'method': 'Browser.setWindowBounds',
                'params': {'windowId': window_id,
                           'bounds': {'windowState': 'minimized'}},
            })
    except Exception:
        pass   # cosmetic only; never worth losing a hold over


async def html(tab):
    return value(await tab.execute_script('return document.documentElement.outerHTML'))


async def _click(tab, predicate):
    """Click the first input/button matching a JS predicate on `e`."""
    return value(await tab.execute_script("""
        const b = [...document.querySelectorAll('input,button')].find(e => %s);
        if (!b) return 'missing';
        b.click();
        return 'clicked';
    """ % predicate))


def _kill_stray_chrome():
    """Kill Chrome processes left behind by an abandoned solve — a cancelled
    `async with Chrome(...)` cannot always finish pydoll's teardown, and each orphan
    keeps a profile and a few hundred MB. Only safe under `hold()`: the match is on
    launch flags an application-filing browser carries too."""
    try:
        out = subprocess.run(['pgrep', '-f', 'remote-debugging-port'],
                             capture_output=True, text=True, timeout=10).stdout
        for pid in [int(p) for p in out.split() if p.isdigit()]:
            try:
                os.kill(pid, signal.SIGKILL)
                log(f'Killed stray Chrome pid {pid}')
            except (ProcessLookupError, PermissionError):
                pass
    except Exception as e:
        log(f'Could not reap stray Chrome: {e}')


# ── Turnstile ────────────────────────────────────────────────────────

async def _save_user_agent(tab):
    """Record Chrome's UA alongside the session token it minted, so curl's replays and
    the browser that solved the CAPTCHA cannot disagree."""
    try:
        ua = value(await tab.execute_script('return navigator.userAgent'))
        ua = (ua.splitlines() or [''])[0].strip()
        if not ua or 'Headless' in ua:
            log(f'Not recording user agent: {ua or "(empty)"}')
            return
        with open(USER_AGENT_CACHE + '.tmp', 'w') as f:
            f.write(ua + '\n')
        os.replace(USER_AGENT_CACHE + '.tmp', USER_AGENT_CACHE)
        log(f'Recorded Chrome user agent: {ua}')
    except Exception as e:
        log(f'Could not record user agent: {e}')


async def _click_turnstile_checkbox(tab):
    """Find the Turnstile widget and click its checkbox via CDP mouse events.

    Returns the cf-turnstile-response token string, or None on failure.
    """
    from pydoll.commands.input_commands import InputCommands
    from pydoll.protocol.input.types import MouseEventType, MouseButton

    cf = await tab.find(class_name='cf-turnstile', timeout=10, raise_exc=False)
    if not cf:
        log('No Cloudflare Turnstile widget found on page')
        return None

    bounds = await cf.get_bounds_using_js()
    log(f'Turnstile widget bounds: x={bounds["x"]:.0f} y={bounds["y"]:.0f} '
        f'w={bounds["width"]:.0f} h={bounds["height"]:.0f}')

    # The checkbox sits in a cross-origin iframe ~28px from the left edge,
    # vertically centred. CDP mouse events cross iframe boundaries; clicks do not.
    x = int(bounds['x'] + 28)
    y = int(bounds['y'] + bounds['height'] / 2)
    log(f'Clicking Turnstile checkbox at ({x}, {y})...')

    for event in (MouseEventType.MOUSE_PRESSED, MouseEventType.MOUSE_RELEASED):
        await tab._connection_handler.execute_command(
            InputCommands.dispatch_mouse_event(
                type=event, x=x, y=y, button=MouseButton.LEFT, click_count=1))
        await asyncio.sleep(0.1)

    deadline = time.time() + TOKEN_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(TOKEN_POLL_INTERVAL)
        token = value(await tab.execute_script(
            'return document.querySelector'
            '(\'input[name="cf-turnstile-response"]\')?.value || ""'))
        if token:
            # Length only: this is Cloudflare's single-use response token.
            log(f'Turnstile token obtained ({len(token)} chars)')
            return token
        log(f'  Waiting for token ({TOKEN_TIMEOUT - (deadline - time.time()):.0f}s)...')

    log('Turnstile token not generated within timeout')
    return None


async def solve_turnstile(tab):
    """Solve Cloudflare Turnstile on the given pydoll tab. The token, or None."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(f'═══ Turnstile attempt {attempt}/{MAX_ATTEMPTS} ═══')
        token = await _click_turnstile_checkbox(tab)
        if token:
            return token
        if attempt < MAX_ATTEMPTS:
            log('Reloading page for fresh Turnstile challenge...')
            await tab.refresh()
            await asyncio.sleep(5)

    log('Failed to solve Turnstile after all attempts')
    return None


async def get_calendar_url():
    """Solve the CAPTCHA and cache a fresh calendar URL, under a hard deadline.

    The solve occupies the one thread that can re-mint a session, so an untimed Chrome
    hang would stop all booking behind a healthy-looking process. Serialised against
    the application-filing browser. None on failure or timeout.
    """
    with hold() as owned:
        if not owned:
            log('Chrome is busy filing an application; deferring this solve')
            return None
        try:
            return await asyncio.wait_for(_solve_and_cache(), timeout=CAPTCHA_TIMEOUT)
        except asyncio.TimeoutError:
            log(f'TIMEOUT: solve exceeded {CAPTCHA_TIMEOUT}s, abandoning this attempt')
            _kill_stray_chrome()
            return None
        except Exception as e:
            log(f'Solve failed: {e!r}')
            return None


async def _solve_and_cache():
    """Navigate ITS, solve Turnstile, click 次へ, and cache the calendar URL."""
    log('Starting Chrome browser via pydoll...')
    async with launch() as browser:
        tab = await browser.start()
        await minimise(tab)

        log('Navigating to ITS homepage...')
        await tab.go_to('https://as.its-kenpo.or.jp/', timeout=30)
        await asyncio.sleep(3)

        await _save_user_agent(tab)

        log('Looking for カレンダーから探す link...')
        await tab.execute_script("""
            for (const a of document.querySelectorAll('a')) {
                if (a.textContent.includes('カレンダーから探す')) { a.click(); return true; }
            }
            return false;
        """)
        await asyncio.sleep(5)

        url = await tab.current_url
        log(f'On captcha page: {url}')
        if 'calendar_apply' not in url:
            log(f'WARNING: unexpected URL (expected calendar_apply): {url}')

        if not await solve_turnstile(tab):
            log('FAILED to solve Turnstile')
            return None

        log('Turnstile solved! Submitting form...')
        await tab.execute_script("""
            const btn = document.querySelector('input[value="次へ"]');
            if (btn) { btn.disabled = false; btn.form.submit(); }
        """)
        await asyncio.sleep(5)

        calendar_url = await tab.current_url
        log(f'Calendar URL obtained — {calendar_url}')

        # A non-calendar URL still answers 200, so caching one makes the session
        # look healthy forever and no re-solve ever fires.
        if 'calendar_select' not in (calendar_url or ''):
            log(f'FAILED: not a calendar URL, not caching: {calendar_url}')
            return None

        with open(CALENDAR_URL_CACHE + '.tmp', 'w') as f:
            f.write(calendar_url + '\n')
        os.replace(CALENDAR_URL_CACHE + '.tmp', CALENDAR_URL_CACHE)
        log(f'Saved to {CALENDAR_URL_CACHE}')
        return calendar_url


# ── the 申込する / 確認 leg ───────────────────────────────────────────

def submit(link, values, log, tag, allow_commit):
    """Fill the emailed applicant form, press 申込する, then 確認.

    `values` is `map_fields`' post body without the hidden fields — the DOM already has
    `_method` and `authenticity_token`, and overwriting them with copies from a
    different page load is how a browser submit fails for a reason that looks like the
    bug it works around. Returns `(status, detail)` in `confirm_from_email`'s
    vocabulary.
    """
    budget = BROWSER_CONFIRM_TIMEOUT

    with hold(timeout=max(1, budget)) as owned:
        if not owned:
            log(f'{tag}   Chrome is busy re-minting a session; '
                f'not waiting any longer with a hold running')
            return 'failed', 'browser busy'
        try:
            return asyncio.run(
                asyncio.wait_for(_run(link, values, log, tag, allow_commit),
                                 timeout=budget))
        except asyncio.TimeoutError:
            log(f'{tag}   Browser submit exceeded {budget:.0f}s: outcome UNKNOWN. '
                f'Check the mailbox for a 申込完了メール before retrying.')
            return 'failed', 'browser submit outcome unknown'
        except Exception as e:
            log(f'{tag}   Browser submit failed: {e!r}')
            return 'failed', f'browser submit error: {type(e).__name__}'


async def _run(link, values, log, tag, allow_commit):
    import confirm_booking

    async with launch() as browser:
        tab = await browser.start()
        try:
            await minimise(tab)
            await tab.go_to(link, timeout=30)
            await asyncio.sleep(3)

            page = await html(tab)
            if confirm_booking._EXPIRED_TEXT in page:
                log(f'{tag}   The hold expired before the browser reached the form')
                return 'failed', 'hold expired'
            if bh._NO_ROOMS_TEXT in page:
                return 'failed', 'room taken'

            # Write every value back and read it back out — see the module note.
            report = value(await tab.execute_script("""
                const V = %s;
                const bad = [];
                let ok = 0;
                for (const [name, v] of Object.entries(V)) {
                    const el = document.getElementsByName(name)[0];
                    if (!el) { bad.push(name + ':missing'); continue; }
                    el.value = v;
                    el.dispatchEvent(new Event('input',  {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    if (el.value === v) { ok++; } else { bad.push(name + ':rejected'); }
                }
                return JSON.stringify({ok: ok, bad: bad});
            """ % json.dumps(values, ensure_ascii=False)))
            try:
                filled = json.loads(report or '{}')
            except ValueError:
                filled = {}
            bad = filled.get('bad') or []
            # Every field missing means this is not the applicant form at all — a
            # consumed link renders something else, and calling that "could not fill"
            # sends the operator hunting for a form-mapping bug that is not there.
            if values and all(b.endswith(':missing') for b in bad) \
                    and len(bad) == len(values):
                log(f'{tag}   The emailed link no longer shows the applicant form '
                    f'(no such fields on the page) — it was probably already used')
                return 'failed', 'link no longer shows the applicant form'
            # Field *names* only. The values are 資格認証のキー.
            log(f'{tag}   Browser filled {filled.get("ok", 0)}/{len(values)} fields'
                + (f', not accepted: {", ".join(bad)}' if bad else ''))
            if bad or filled.get('ok') != len(values):
                log(f'{tag}   Not submitting a form the page did not accept in full')
                return 'deferred', f'browser could not fill: {", ".join(bad) or "?"}'

            if await _click(tab, "(e.value || e.textContent || '').includes('申込する')") \
                    != 'clicked':
                return 'failed', 'no 申込する control on the page'
            await asyncio.sleep(6)

            page = await html(tab)
            if _CONFIRM_TEXT not in page:
                if bh._NO_ROOMS_TEXT in page:
                    return 'failed', 'room taken'
                log(f'{tag}   申込する did not reach 申込内容確認画面 even in Chrome')
                return 'failed', 'browser apply did not reach the confirm screen'

            # Re-checked here, on the screen before the commit, because filling the
            # form took time and the free-cancellation gate can have closed.
            allowed, why = allow_commit()
            if not allowed:
                log(f'{tag}   Gate closed before 確認 in the browser: {why}')
                return 'deferred', why

            log(f'{tag}   確認 (browser): filing the application')
            if await _click(tab, "['確認', '確定'].includes("
                                 "(e.value || e.textContent || '').trim())") != 'clicked':
                return 'failed', 'no 確認 control on 申込内容確認画面'
            await asyncio.sleep(8)

            page = await html(tab)
            receipt = confirm_booking.parse_receipt(page)
            if receipt or '申込完了' in page:
                return 'confirmed', receipt
            return 'failed', 'browser 確認 did not produce a completion page'
        finally:
            try:
                await browser.stop()
            except Exception:
                pass


if __name__ == '__main__':
    asyncio.run(get_calendar_url())
