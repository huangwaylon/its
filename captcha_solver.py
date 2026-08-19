#!/usr/bin/env python3
"""Cloudflare Turnstile solver — real Chrome over CDP, via pydoll.

Playwright's bundled Chromium trips the bot detection; headless is rejected. See
docs/SITE.md §6 for the site's behaviour and why the click lands where it does.

`_click_turnstile_checkbox` and `minimise` reach CDP through the **private**
`tab._connection_handler`, because mouse events must cross a cross-origin iframe
boundary where a DOM click cannot. A pydoll upgrade can break those two call sites.

    .venv/bin/python captcha_solver.py       # solve, cache the calendar URL
"""

import asyncio
import os
import signal
import subprocess
import time

from pydoll.commands.input_commands import InputCommands
from pydoll.protocol.input.types import MouseEventType, MouseButton

from config import CALENDAR_URL_CACHE, USER_AGENT_CACHE, CAPTCHA_TIMEOUT
import browser
from browser import value as _script_value

MAX_ATTEMPTS = 3          # Turnstile retries before giving up
TOKEN_POLL_INTERVAL = 2   # seconds between token checks
TOKEN_TIMEOUT = 30        # max seconds to wait for token after click
                          # MAX_ATTEMPTS * TOKEN_TIMEOUT must fit in CAPTCHA_TIMEOUT

# ── Logging ─────────────────────────────────────────────────────────
_t0 = time.time()

_log_handler = None  # main() routes this to stdout + LOG_FILE


def log(msg):
    elapsed = time.time() - _t0
    formatted = f'[{elapsed:6.1f}s] {msg}'
    if _log_handler:
        _log_handler(formatted)
    else:
        print(formatted, flush=True)


async def _save_user_agent(tab):
    """Record Chrome's UA alongside the session token it minted, so curl's replays and
    the browser that solved the CAPTCHA cannot disagree."""
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

    # The checkbox sits in a cross-origin iframe ~28px from the left edge,
    # vertically centred. CDP mouse events cross iframe boundaries; clicks do not.
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
            # Length only: this is Cloudflare's single-use response token.
            log(f'Turnstile token obtained ({len(token)} chars)')
            return token
        elapsed = time.time() - (deadline - TOKEN_TIMEOUT)
        log(f'  Waiting for token ({elapsed:.0f}s)...')

    log('Turnstile token not generated within timeout')
    return None


async def solve_turnstile(tab):
    """Solve Cloudflare Turnstile on the given pydoll tab.

    Returns the cf-turnstile-response token string, or None on failure.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(f'═══ Turnstile attempt {attempt}/{MAX_ATTEMPTS} ═══')
        token = await _click_turnstile_checkbox(tab)
        if token:
            return token

        if attempt < MAX_ATTEMPTS:
            # Reload the page to get a fresh Turnstile widget
            log('Reloading page for fresh Turnstile challenge...')
            await tab.refresh()
            await asyncio.sleep(5)

    log('Failed to solve Turnstile after all attempts')
    return None


# ── ITS Calendar URL getter ────────────────────────────────────────

async def get_calendar_url():
    """Solve the CAPTCHA and cache a fresh calendar URL, under a hard deadline.

    The solve occupies the one thread that can re-mint a session, so an untimed Chrome
    hang would stop all booking behind a healthy-looking process. Serialised against
    the application-filing browser. None on failure or timeout.
    """
    with browser.chrome() as owned:
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
    """Kill Chrome processes left behind by an abandoned solve — a cancelled
    `async with Chrome(...)` cannot always finish pydoll's teardown, and each orphan
    keeps a profile and a few hundred MB. Only safe under `browser.chrome()`: the match
    is on launch flags an application-filing browser carries too."""
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
    """Navigate ITS, solve Turnstile, click 次へ, and cache the calendar URL."""
    log('Starting Chrome browser via pydoll...')
    async with browser.launch() as chrome:
        tab = await chrome.start()
        await browser.minimise(tab)

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
        log(f'On captcha page: {url}')
        if 'calendar_apply' not in url:
            log(f'WARNING: unexpected URL (expected calendar_apply): {url}')

        token = await solve_turnstile(tab)
        if not token:
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

        tmp_path = CALENDAR_URL_CACHE + '.tmp'
        with open(tmp_path, 'w') as f:
            f.write(calendar_url + '\n')
        os.replace(tmp_path, CALENDAR_URL_CACHE)
        log(f'Saved to {CALENDAR_URL_CACHE}')

        return calendar_url


if __name__ == '__main__':
    asyncio.run(get_calendar_url())
