#!/usr/bin/env python3
"""Cloudflare Turnstile solver using pydoll (CDP) browser automation.

Usage:
    # Solve ITS captcha and save calendar URL
    .venv/bin/python captcha_solver.py

    # As module
    from captcha_solver import get_calendar_url
    url = await get_calendar_url()
"""

import asyncio
import os
import signal
import subprocess
import time

from pydoll.browser.chromium.chrome import Chrome
from pydoll.browser.options import ChromiumOptions
from pydoll.commands.input_commands import InputCommands
from pydoll.protocol.input.types import MouseEventType, MouseButton

from config import CALENDAR_URL_CACHE, USER_AGENT_CACHE, CAPTCHA_TIMEOUT
import chrome_guard
# Shared with the booking engine so there is one implementation of "make this
# safe to write down". book_hotels imports only config, so this is not a cycle.
from book_hotels import redact_url, token_summary

# ── Config ──────────────────────────────────────────────────────────
DEBUG_DIR = '/tmp/captcha_debug'
MAX_ATTEMPTS = 3          # Turnstile retries before giving up
TOKEN_POLL_INTERVAL = 2   # seconds between token checks
TOKEN_TIMEOUT = 30        # max seconds to wait for token after click

# ── Logging ─────────────────────────────────────────────────────────
_t0 = time.time()

_log_handler = None  # Set externally for display routing


def log(msg):
    elapsed = time.time() - _t0
    formatted = f'[{elapsed:6.1f}s] {msg}'
    if _log_handler:
        _log_handler(formatted)
    else:
        print(formatted, flush=True)


def _debug_path(name):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    return os.path.join(DEBUG_DIR, name)


def _script_value(result):
    """Unwrap a CDP Runtime.evaluate result to its plain string value."""
    if isinstance(result, dict):
        result = result.get('result', {}).get('result', {}).get('value', '')
    return result if isinstance(result, str) else ''


async def _save_user_agent(tab):
    """Record Chrome's user agent alongside the session token it minted.

    book_hotels replays the token with curl; sending the UA of the browser
    that actually solved the CAPTCHA keeps the two from disagreeing.
    """
    try:
        ua = _script_value(await tab.execute_script('return navigator.userAgent'))
        ua = (ua.splitlines() or [''])[0].strip()
        if not ua or 'Headless' in ua:
            log(f'Not recording user agent: {ua or "(empty)"}')
            return
        tmp_path = USER_AGENT_CACHE + '.tmp'
        with open(tmp_path, 'w') as f:
            f.write(ua + '\n')
        os.replace(tmp_path, USER_AGENT_CACHE)
        log(f'Recorded Chrome user agent: {ua}')
    except Exception as e:
        log(f'Could not record user agent: {e}')


# ── Turnstile solver ────────────────────────────────────────────────

async def _click_turnstile_checkbox(tab):
    """Find the Turnstile widget and click its checkbox via CDP mouse events.

    Returns the cf-turnstile-response token string, or None on failure.
    """
    # Find the cf-turnstile container element
    cf = await tab.find(class_name='cf-turnstile', timeout=10, raise_exc=False)
    if not cf:
        log('No Cloudflare Turnstile widget found on page')
        return None

    # Get bounds of the Turnstile container div
    bounds = await cf.get_bounds_using_js()
    log(f'Turnstile widget bounds: x={bounds["x"]:.0f} y={bounds["y"]:.0f} '
        f'w={bounds["width"]:.0f} h={bounds["height"]:.0f}')

    # The checkbox is rendered inside a cross-origin iframe at ~28px from
    # the left edge, vertically centered. CDP mouse events reach across
    # iframe boundaries.
    checkbox_x = int(bounds['x'] + 28)
    checkbox_y = int(bounds['y'] + bounds['height'] / 2)
    log(f'Clicking Turnstile checkbox at ({checkbox_x}, {checkbox_y})...')

    press_cmd = InputCommands.dispatch_mouse_event(
        type=MouseEventType.MOUSE_PRESSED,
        x=checkbox_x, y=checkbox_y,
        button=MouseButton.LEFT, click_count=1,
    )
    release_cmd = InputCommands.dispatch_mouse_event(
        type=MouseEventType.MOUSE_RELEASED,
        x=checkbox_x, y=checkbox_y,
        button=MouseButton.LEFT, click_count=1,
    )
    await tab._connection_handler.execute_command(press_cmd)
    await asyncio.sleep(0.1)
    await tab._connection_handler.execute_command(release_cmd)

    # Poll for the token to appear
    deadline = time.time() + TOKEN_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(TOKEN_POLL_INTERVAL)
        token_result = await tab.execute_script(
            'return document.querySelector(\'input[name="cf-turnstile-response"]\')?.value || ""'
        )
        token = _script_value(token_result)
        if token:
            # Length only. This is Cloudflare's single-use response token; a
            # 60-character prefix in the log was never diagnostic of anything.
            log(f'Turnstile token obtained ({len(token)} chars)')
            return token
        elapsed = time.time() - (deadline - TOKEN_TIMEOUT)
        log(f'  Waiting for token ({elapsed:.0f}s)...')

    log('Turnstile token not generated within timeout')
    return None


async def solve_turnstile(tab, max_attempts=MAX_ATTEMPTS):
    """Solve Cloudflare Turnstile on the given pydoll tab.

    Returns the cf-turnstile-response token string, or None on failure.
    """
    for attempt in range(1, max_attempts + 1):
        log(f'═══ Turnstile attempt {attempt}/{max_attempts} ═══')
        token = await _click_turnstile_checkbox(tab)
        if token:
            return token

        if attempt < max_attempts:
            # Reload the page to get a fresh Turnstile widget
            log('Reloading page for fresh Turnstile challenge...')
            await tab.refresh()
            await asyncio.sleep(5)

    log('Failed to solve Turnstile after all attempts')
    return None


# ── ITS Calendar URL getter ────────────────────────────────────────

async def get_calendar_url():
    """Solve the CAPTCHA and cache a fresh calendar URL, under a hard deadline.

    The solve runs synchronously inside main.py's URL monitor thread, and that
    thread is the only thing that ever re-mints a session. A pydoll or Chrome
    hang there stops all booking indefinitely while the process keeps rendering
    its display and looks perfectly healthy, so the deadline is not optional.

    Held under `chrome_guard` because `browser_apply` drives Chrome too, and
    `_kill_stray_chrome()` below reaps by `pgrep -f remote-debugging-port` — which
    matches that browser as readily as this one. Serialised, a timeout here can only
    fire while nothing else of ours has a Chrome open, so the reaper cannot kill a
    browser that is midway through filing an application against a room hold.

    Returns the URL string, or None on failure or timeout.
    """
    with chrome_guard.chrome() as owned:
        if not owned:
            log('Chrome is busy filing an application; deferring this solve')
            return None
        try:
            return await asyncio.wait_for(_solve_and_cache(),
                                          timeout=CAPTCHA_TIMEOUT)
        except asyncio.TimeoutError:
            log(f'TIMEOUT: solve exceeded {CAPTCHA_TIMEOUT}s, abandoning this attempt')
            _kill_stray_chrome()
            return None
        except Exception as e:
            log(f'Solve failed: {e!r}')
            return None


def _kill_stray_chrome():
    """Kill Chrome processes left behind by an abandoned solve.

    On a timeout the `async with Chrome(...)` block is cancelled mid-await, and
    pydoll cannot always complete its own teardown. Over weeks of solves every
    orphan keeps its profile directory and a few hundred MB of RSS, so they are
    reaped here rather than accumulating until the machine runs out of memory.
    Matched narrowly on the flags pydoll launches with, so a Chrome the user is
    browsing in is never a candidate.
    """
    try:
        out = subprocess.run(['pgrep', '-f', 'remote-debugging-port'],
                             capture_output=True, text=True, timeout=10).stdout
        pids = [int(p) for p in out.split() if p.isdigit()]
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
                log(f'Killed stray Chrome pid {pid}')
            except (ProcessLookupError, PermissionError):
                pass
    except Exception as e:
        log(f'Could not reap stray Chrome: {e}')


async def _solve_and_cache():
    """Navigate ITS site, solve Turnstile CAPTCHA, click 次へ, and save the calendar URL."""
    options = ChromiumOptions()
    options.headless = False
    options.add_argument('--no-sandbox')
    options.add_argument('--lang=ja-JP')
    options.add_argument('--window-size=1280,1600')

    log('Starting Chrome browser via pydoll...')
    async with Chrome(options=options) as browser:
        tab = await browser.start()

        try:
            # Minimize window via CDP - hidden but still renders normally
            try:
                win = await tab._connection_handler.execute_command(
                    {'method': 'Browser.getWindowForTarget', 'params': {}}
                )
                window_id = win.get('result', win).get('windowId')
                if window_id:
                    await tab._connection_handler.execute_command({
                        'method': 'Browser.setWindowBounds',
                        'params': {
                            'windowId': window_id,
                            'bounds': {'windowState': 'minimized'},
                        },
                    })
                    log('Browser window minimized via CDP')
            except Exception as e:
                log(f'Could not minimize window: {e}')

            log('Navigating to ITS homepage...')
            await tab.go_to('https://as.its-kenpo.or.jp/', timeout=30)
            await asyncio.sleep(3)

            await _save_user_agent(tab)

            log('Looking for カレンダーから探す link...')
            await tab.execute_script("""
                const links = document.querySelectorAll('a');
                for (const a of links) {
                    if (a.textContent.includes('カレンダーから探す')) {
                        a.click();
                        return true;
                    }
                }
                return false;
            """)
            await asyncio.sleep(5)

            url = await tab.current_url
            log(f'On captcha page: {redact_url(url)}')

            if 'calendar_apply' not in url:
                log(f'WARNING: unexpected URL (expected calendar_apply): '
                    f'{redact_url(url)}')
                await tab.take_screenshot(path=_debug_path('unexpected_url.png'))

            token = await solve_turnstile(tab)
            if not token:
                log('FAILED to solve Turnstile')
                await tab.take_screenshot(path=_debug_path('turnstile_failed.png'))
                return None

            log('Turnstile solved! Submitting form...')
            await tab.execute_script("""
                const btn = document.querySelector('input[value="次へ"]');
                if (btn) { btn.disabled = false; btn.form.submit(); }
            """)
            await asyncio.sleep(5)

            calendar_url = await tab.current_url
            # The decoded token fields, never the URL: this line ran on all 647
            # solves in the previous log and wrote the complete `s=` token to
            # disk every time.
            log(f'Calendar URL obtained — {token_summary(calendar_url)}')

            # Refuse to cache anything that is not a calendar session. Saving it
            # anyway used to poison the cache: every scanner would then replay a
            # non-calendar URL that can still answer 200, so check_cached_url
            # called the session healthy and no re-solve ever fired. Returning
            # None leaves the previous URL in place and retries next cycle.
            if 'calendar_select' not in (calendar_url or ''):
                log(f'FAILED: not a calendar URL, not caching: '
                    f'{redact_url(calendar_url)}')
                await tab.take_screenshot(path=_debug_path('not_calendar.png'))
                return None

            tmp_path = CALENDAR_URL_CACHE + '.tmp'
            with open(tmp_path, 'w') as f:
                f.write(calendar_url + '\n')
            os.replace(tmp_path, CALENDAR_URL_CACHE)
            log(f'Saved to {CALENDAR_URL_CACHE}')

            return calendar_url

        finally:
            # `async with Chrome(...)` stops the browser on exit too, so this is
            # the second call. Guarded because a raise from a finally would
            # replace a perfectly good return value with an exception.
            try:
                await browser.stop()
            except Exception as e:
                log(f'Browser stop: {e}')
            log('Browser closed')


if __name__ == '__main__':
    asyncio.run(get_calendar_url())
