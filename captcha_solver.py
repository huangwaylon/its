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
import time

from pydoll.browser.chromium.chrome import Chrome
from pydoll.browser.options import ChromiumOptions
from pydoll.commands.input_commands import InputCommands
from pydoll.protocol.input.types import MouseEventType, MouseButton

from config import CALENDAR_URL_CACHE

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
        token = ''
        if isinstance(token_result, dict):
            token = token_result.get('result', {}).get('result', {}).get('value', '')
        if token:
            log(f'Turnstile token obtained: {token[:60]}...')
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
            log(f'Calendar URL: {calendar_url}')

            if 'calendar_select' not in calendar_url:
                log(f'WARNING: URL does not look like a calendar page: {calendar_url}')
                await tab.take_screenshot(path=_debug_path('not_calendar.png'))

            tmp_path = CALENDAR_URL_CACHE + '.tmp'
            with open(tmp_path, 'w') as f:
                f.write(calendar_url + '\n')
            os.replace(tmp_path, CALENDAR_URL_CACHE)
            log(f'Saved to {CALENDAR_URL_CACHE}')

            return calendar_url

        finally:
            await browser.stop()
            log('Browser closed')


if __name__ == '__main__':
    asyncio.run(get_calendar_url())
