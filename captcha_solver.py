#!/usr/bin/env python3
"""reCAPTCHA v2 solver using Playwright browser automation + ollama vision AI.

Usage:
    # Solve ITS captcha and save calendar URL
    .venv/bin/python captcha_solver.py

    # As module
    from captcha_solver import solve_recaptcha, get_calendar_url
    token = await solve_recaptcha(page)
    url = await get_calendar_url()
"""

import asyncio
import base64
import io
import json
import os
import re
import tempfile
import time
from PIL import Image
from playwright.async_api import async_playwright, Page

from config import CALENDAR_URL_CACHE

# ── Config ──────────────────────────────────────────────────────────
OLLAMA_URL = 'http://localhost:11434'
OLLAMA_MODEL = 'qwen3-vl:8b'
DEBUG_DIR = '/tmp/captcha_debug'
MAX_ATTEMPT_SECONDS = 90  # reCAPTCHA sessions expire ~120s; bail at 90s to leave margin

# Japanese object names → English translations for common reCAPTCHA categories
CAPTCHA_TRANSLATIONS = {
    'バス': 'buses', '自動車': 'cars', '信号機': 'traffic lights',
    '横断歩道': 'crosswalks', '自転車': 'bicycles', '消火栓': 'fire hydrants',
    'オートバイ': 'motorcycles', '山': 'mountains', '煙突': 'chimneys',
    'ヤシの木': 'palm trees', '橋': 'bridges', '階段': 'stairs',
    'トラクター': 'tractors', 'タクシー': 'taxis', '船': 'boats',
    '駐車メーター': 'parking meters', 'バイク': 'motorcycles',
}

# ── reCAPTCHA selectors ─────────────────────────────────────────────
ANCHOR_IFRAME = 'iframe[title="reCAPTCHA"]'
BFRAME_IFRAME = 'iframe[src*="bframe"]'
CHECKBOX = '.recaptcha-checkbox-border'
CHECKBOX_CHECKED = '.recaptcha-checkbox-checked'
PROMPT_DESC = '.rc-imageselect-desc, .rc-imageselect-desc-no-canonical'
GRID_44 = 'table.rc-imageselect-table-44'
TILES = 'td.rc-imageselect-tile'
VERIFY_BTN = '#recaptcha-verify-button'
RELOAD_BTN = '#recaptcha-reload-button'
ERR_SELECT_MORE = '.rc-imageselect-error-select-more'
ERR_INCORRECT = '.rc-imageselect-incorrect-response'
DYNAMIC_SELECTED = '.rc-imageselect-dynamic-selected'
NEW_TILE_IMG = 'img.rc-image-tile-11'
CHALLENGE_AREA = '.rc-imageselect-challenge'

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


# ── Vision model ────────────────────────────────────────────────────

def _screenshot_to_b64(png_bytes, max_dim=None):
    """Convert PNG screenshot bytes to JPEG base64 (much smaller payload for ollama).

    Args:
        png_bytes: Raw PNG screenshot bytes from Playwright.
        max_dim: If set, resize so the longest side is at most this many pixels.
    """
    img = Image.open(io.BytesIO(png_bytes))
    if max_dim:
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=85)
    return base64.b64encode(buf.getvalue()).decode()


# Limit concurrent ollama requests to avoid overwhelming it
_vision_semaphore = None
_vision_semaphore_loop = None


def _get_vision_semaphore():
    """Get or recreate the semaphore for the current event loop."""
    global _vision_semaphore, _vision_semaphore_loop
    loop = asyncio.get_running_loop()
    if _vision_semaphore_loop is not loop:
        _vision_semaphore = asyncio.Semaphore(4)
        _vision_semaphore_loop = loop
    return _vision_semaphore


async def ask_vision(image_b64, prompt, no_think=False):
    """Send image + text prompt to ollama vision model via curl subprocess."""
    if no_think:
        prompt = prompt + '\n/no_think'
    num_predict = 500 if no_think else 2000
    payload = json.dumps({
        'model': OLLAMA_MODEL,
        'messages': [{'role': 'user', 'content': prompt, 'images': [image_b64]}],
        'stream': False,
        'options': {'temperature': 0.1, 'num_predict': num_predict},
    })
    async with _get_vision_semaphore():
        for attempt in range(2):  # Max 2 attempts (fail fast)
            t = time.time()
            tmp = None
            try:
                # Write payload to temp file to avoid stdin pipe truncation
                tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
                tmp.write(payload)
                tmp.close()
                proc = await asyncio.create_subprocess_exec(
                    'curl', '-s', '-X', 'POST',
                    f'{OLLAMA_URL}/api/chat',
                    '-H', 'Content-Type: application/json',
                    '-d', f'@{tmp.name}',
                    '--max-time', '30',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=35)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    raise RuntimeError('ollama took >35s, killed')
                if proc.returncode != 0:
                    raise RuntimeError(f'curl exit {proc.returncode}: {stderr.decode()[:200]}')
                result = json.loads(stdout)
                answer = result['message']['content']
                if '</think>' in answer:
                    answer = answer.split('</think>')[-1]
                log(f'  [vision] {time.time()-t:.1f}s → {answer.strip()[:120]}')
                return answer.strip()
            except Exception as e:
                if attempt < 1:
                    log(f'  [vision] retry after error: {e}')
                    await asyncio.sleep(2)
                else:
                    log(f'  [vision] failed after 2 attempts: {e}')
                    return ''
            finally:
                if tmp:
                    try:
                        os.unlink(tmp.name)
                    except OSError:
                        pass


def parse_tile_numbers(text, max_tile):
    """Extract tile numbers from model response like [1, 4, 7]."""
    m = re.search(r'\[[\d\s,]*\]', text)
    if m:
        try:
            nums = json.loads(m.group())
            return sorted(set(n for n in nums if 1 <= n <= max_tile))
        except (json.JSONDecodeError, TypeError):
            pass
    nums = [int(n) for n in re.findall(r'\b(\d+)\b', text)]
    return sorted(set(n for n in nums if 1 <= n <= max_tile))


def _translate_prompt(prompt_text):
    """Extract the object name from Japanese reCAPTCHA prompt and return English version.

    Returns (english_object, original_prompt) — english_object is None if no translation found.
    """
    for jp, en in CAPTCHA_TRANSLATIONS.items():
        if jp in prompt_text:
            return en, prompt_text
    return None, prompt_text


def build_grid_prompt(prompt_text, grid_type, total_tiles):
    """Build vision prompt for full-grid analysis."""
    rows = 4 if grid_type == '4x4' else 3
    layout = '\n'.join(
        f'  Row {r+1}: tiles {r*rows+1} through {r*rows+rows}'
        for r in range(rows)
    )
    en_obj, _ = _translate_prompt(prompt_text)
    if en_obj:
        object_desc = f'Select all tiles that contain {en_obj}.'
    else:
        object_desc = f'The challenge says: "{prompt_text}"'
    return (
        f'You are solving a CAPTCHA image challenge.\n\n'
        f'{object_desc}\n\n'
        f'The image shows a {grid_type} grid of tiles. '
        f'Tiles are numbered 1-{total_tiles}, left to right, top to bottom:\n'
        f'{layout}\n\n'
        f'Select every tile that contains ANY part of the target object, '
        f'even if only a small portion is visible.\n\n'
        f'Which tiles match? Reply with ONLY a JSON array of tile numbers.\n'
        f'Example: [1, 4, 7]\n'
        f'If no tiles match: []\n'
        f'Output ONLY the JSON array, nothing else.'
    )


def build_tile_prompt(prompt_text):
    """Build vision prompt for individual tile classification."""
    en_obj, _ = _translate_prompt(prompt_text)
    if en_obj:
        object_desc = f'Does this image contain {en_obj} (or any part of one)?'
    else:
        object_desc = f'The challenge says: "{prompt_text}"\nDoes this tile contain the target object (or any part of it)?'
    return (
        f'You are solving a CAPTCHA.\n'
        f'{object_desc}\n'
        f'Reply ONLY "yes" or "no".'
    )


def parse_yes_no(text):
    """Parse a yes/no response from the model."""
    return bool(re.search(r'\byes\b', text.lower()))


# ── Solver ──────────────────────────────────────────────────────────

async def solve_recaptcha(page: Page, max_attempts=8):
    """Solve reCAPTCHA v2 on the given Playwright page.

    Args:
        page: A Playwright Page object with a page containing reCAPTCHA.
        max_attempts: Maximum number of challenge attempts.

    Returns:
        The g-recaptcha-response token string, or None on failure.
    """
    # ── Step 1: Click the checkbox ──
    log('Looking for reCAPTCHA checkbox...')
    anchor = page.frame_locator(ANCHOR_IFRAME)
    checkbox = anchor.locator(CHECKBOX)
    await checkbox.wait_for(state='visible', timeout=15000)
    log('Checkbox found, clicking...')
    await checkbox.click()
    await asyncio.sleep(2)

    # Check if solved without challenge
    try:
        await anchor.locator(CHECKBOX_CHECKED).wait_for(state='attached', timeout=4000)
        log('Solved by checkbox click alone!')
        return await _get_token(page)
    except Exception:
        log('Challenge required, proceeding to image solver...')

    # ── Step 2: Solve image challenges ──
    attempt = 0
    skips_4x4 = 0
    while attempt < max_attempts:
        attempt += 1
        attempt_start = time.time()
        log(f'═══ Challenge attempt {attempt}/{max_attempts} ═══')

        bframe = page.frame_locator(BFRAME_IFRAME)

        # Extract prompt text
        prompt_text = ''
        try:
            prompt_loc = bframe.locator(PROMPT_DESC).first
            await prompt_loc.wait_for(state='visible', timeout=10000)
            prompt_text = (await prompt_loc.inner_text()).strip()
        except Exception as e:
            log(f'Warning: could not get prompt text: {e}')
        en_obj, _ = _translate_prompt(prompt_text)
        log(f'Prompt: "{prompt_text}"' + (f' → {en_obj}' if en_obj else ''))

        # Detect grid type
        grid_type = '4x4' if await bframe.locator(GRID_44).count() > 0 else '3x3'
        total_tiles = 16 if grid_type == '4x4' else 9
        tiles = bframe.locator(TILES)
        tile_count = await tiles.count()
        if tile_count != total_tiles:
            log(f'Warning: expected {total_tiles} tiles, found {tile_count}')
            total_tiles = tile_count
        log(f'Grid: {grid_type} ({total_tiles} tiles)')

        # Skip 4x4 challenges — don't count against attempt limit (up to 5 free skips)
        if grid_type == '4x4':
            skips_4x4 += 1
            log(f'4x4 grid detected, skipping (skip {skips_4x4}). Reloading...')
            await _reload_challenge(bframe)
            if skips_4x4 <= 5:
                attempt -= 1  # Don't count this against the attempt limit
            continue

        if total_tiles == 0:
            log('No tiles found (page not loaded?), reloading...')
            await _reload_challenge(bframe)
            continue

        # Save debug screenshot of challenge
        try:
            challenge = bframe.locator(CHALLENGE_AREA)
            await challenge.wait_for(state='visible', timeout=5000)
            await challenge.screenshot(path=_debug_path(f'challenge_{attempt}.png'))
            log(f'Debug screenshot: {_debug_path(f"challenge_{attempt}.png")}')
        except Exception:
            pass

        # ── Classify tiles ──
        log('Classifying tiles...')
        t_classify = time.time()
        matching = await _classify_tiles(bframe, prompt_text, grid_type, total_tiles, tiles)
        log(f'Classification done in {time.time()-t_classify:.1f}s → matches: {matching}')

        if not matching:
            log('No matches found, reloading challenge...')
            await _reload_challenge(bframe)
            continue

        # ── Click matching tiles ──
        await _scroll_bframe_into_view(page)
        log(f'Clicking {len(matching)} tiles: {matching}')
        for idx in matching:
            if 1 <= idx <= tile_count:
                tile = tiles.nth(idx - 1)
                await _click_tile(tile, f'Tile {idx}')
                await asyncio.sleep(0.15)

        # ── Handle dynamic tiles (with time budget) ──
        await asyncio.sleep(1.0)
        has_replacements = await bframe.locator(NEW_TILE_IMG).count() > 0
        has_animating = await bframe.locator(DYNAMIC_SELECTED).count() > 0
        if has_replacements or has_animating:
            log(f'Dynamic replacements detected (new={has_replacements}, animating={has_animating})')
            deadline = attempt_start + MAX_ATTEMPT_SECONDS
            await _handle_dynamic_tiles(page, bframe, prompt_text, deadline)

        # ── Time budget check before verify ──
        elapsed = time.time() - attempt_start
        if elapsed > MAX_ATTEMPT_SECONDS:
            log(f'Time budget exceeded ({elapsed:.0f}s > {MAX_ATTEMPT_SECONDS}s), reloading...')
            await _reload_challenge(bframe)
            continue

        # ── Click verify ──
        log('Clicking verify...')
        await _scroll_bframe_into_view(page)
        try:
            await bframe.locator(VERIFY_BTN).click(timeout=5000)
        except Exception as e:
            log(f'Verify click failed (session expired?): {e}')
            return None
        await asyncio.sleep(2)

        # ── Check if solved ──
        try:
            await anchor.locator(CHECKBOX_CHECKED).wait_for(state='attached', timeout=5000)
            log('SOLVED!')
            token = await _get_token(page)
            log(f'Token: {token[:60] if token else "None"}...')
            return token
        except Exception:
            log('Not solved yet...')

        # ── On "select more" or wrong answer, just reload (re-analysis is too slow) ──
        error = await _check_error(bframe)
        if error:
            log(f'Error: "{error}"')

        # Check if a new challenge auto-appeared
        try:
            new_text = (await bframe.locator(PROMPT_DESC).first.inner_text(timeout=2000)).strip()
            if new_text and new_text != prompt_text:
                log(f'New challenge appeared: "{new_text[:50]}", retrying...')
                continue
        except Exception:
            pass

        log('Reloading challenge...')
        await _reload_challenge(bframe)

    log('Failed after all attempts')
    return None


# ── Internal helpers ────────────────────────────────────────────────

async def _scroll_bframe_into_view(page):
    """Scroll the bframe iframe into the viewport on the outer page."""
    try:
        await page.locator(BFRAME_IFRAME).scroll_into_view_if_needed()
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def _click_tile(tile, label="tile"):
    """Click a tile, falling back to dispatch_event on viewport errors."""
    try:
        await tile.click(force=True)
    except Exception as e:
        if 'outside of the viewport' in str(e):
            log(f'  {label}: viewport error, using dispatch_event fallback')
            await tile.dispatch_event('click')
        else:
            raise


async def _classify_tiles(bframe, prompt_text, grid_type, total_tiles, tiles):
    """Classify tiles using vision model. Returns list of 1-indexed matching tile numbers."""
    # Strategy 1: Full grid screenshot with no_think (fast, one API call)
    # Only try once — retries waste too much time and ollama degrades over time
    try:
        challenge = bframe.locator(CHALLENGE_AREA)
        await challenge.wait_for(state='visible', timeout=5000)
        screenshot_bytes = await challenge.screenshot()
        with open(_debug_path('strategy1.png'), 'wb') as f:
            f.write(screenshot_bytes)
        challenge_b64 = _screenshot_to_b64(screenshot_bytes, max_dim=450)
        prompt = build_grid_prompt(prompt_text, grid_type, total_tiles)
        log('Strategy 1: full grid 450px (single attempt)...')
        answer = await ask_vision(challenge_b64, prompt, no_think=True)
        if answer:
            log(f'Strategy 1 raw: "{answer[:200]}"')
            matching = parse_tile_numbers(answer, total_tiles)
            if matching:
                log(f'Strategy 1 result: {matching}')
                return matching
            log('Strategy 1 returned empty array')
        else:
            log('Strategy 1 got no response')
    except Exception as e:
        log(f'Strategy 1 failed: {e}')

    # Strategy 2: Individual tile screenshots in parallel (reliable)
    log(f'Strategy 2: per-tile analysis ({total_tiles} tiles)...')
    return await _classify_each_tile(tiles, total_tiles, prompt_text)


async def _classify_each_tile(tiles, total_tiles, prompt_text):
    """Screenshot all tiles, then classify in parallel via asyncio.gather."""
    tile_prompt = build_tile_prompt(prompt_text)

    # Phase 1: capture all screenshots
    log(f'  Screenshotting {total_tiles} tiles...')
    tile_images = []
    for i in range(total_tiles):
        tile_bytes = await tiles.nth(i).screenshot()
        tile_images.append(_screenshot_to_b64(tile_bytes))
        # Save debug images
        with open(_debug_path(f'tile_{i+1}.png'), 'wb') as f:
            f.write(tile_bytes)

    # Phase 2: classify all tiles in parallel
    log(f'  Sending {total_tiles} tiles to vision model in parallel...')
    t = time.time()

    async def _check(idx, b64):
        answer = await ask_vision(b64, tile_prompt)
        result = parse_yes_no(answer)
        log(f'  Tile {idx+1}: {"YES" if result else "no"} (raw: "{answer[:60]}")')
        return (idx + 1, result)

    results = await asyncio.gather(*[_check(i, b64) for i, b64 in enumerate(tile_images)])
    matching = sorted(num for num, matched in results if matched)
    log(f'  Parallel classification done in {time.time()-t:.1f}s → {matching}')
    return matching


async def _handle_dynamic_tiles(page, bframe, prompt_text, deadline=None):
    """Handle dynamic challenges where selected tiles get replaced with new images.

    Limited to 2 rounds to avoid session timeout. Respects deadline if provided.
    """
    tile_prompt = build_tile_prompt(prompt_text)

    for round_num in range(2):
        # Check time budget
        if deadline and time.time() > deadline:
            log(f'  Dynamic tiles: time budget exceeded, stopping')
            break

        log(f'  Dynamic round {round_num+1}: waiting for animation...')
        for _ in range(8):
            if await bframe.locator(DYNAMIC_SELECTED).count() > 0:
                await asyncio.sleep(0.4)
            else:
                break
        await asyncio.sleep(1.0)

        # Find new replacement tiles
        tiles = bframe.locator(TILES)
        tile_count = await tiles.count()
        to_check = []
        try:
            for i in range(tile_count):
                tile = tiles.nth(i)
                if await tile.locator(NEW_TILE_IMG).count() > 0:
                    tile_bytes = await tile.screenshot(timeout=5000)
                    b64 = _screenshot_to_b64(tile_bytes)
                    to_check.append((i, b64))
        except Exception as e:
            log(f'  Screenshot failed (session expired?): {e}')
            break

        if not to_check:
            log('  No new replacement tiles, done')
            break

        log(f'  Classifying {len(to_check)} replacement tiles...')
        t = time.time()

        async def _check(idx, b64):
            answer = await ask_vision(b64, tile_prompt)
            result = parse_yes_no(answer)
            log(f'    Dynamic tile {idx+1}: {"YES" if result else "no"}')
            return (idx, result)

        results = await asyncio.gather(*[_check(i, b64) for i, b64 in to_check])
        matches = [idx for idx, matched in results if matched]
        log(f'  Dynamic round {round_num+1} done in {time.time()-t:.1f}s → matches: {[m+1 for m in matches]}')

        if not matches:
            log('  No more dynamic matches, ready to verify')
            break

        await _scroll_bframe_into_view(page)
        for idx in matches:
            await _click_tile(tiles.nth(idx), f'Dynamic tile {idx+1}')
            await asyncio.sleep(0.15)

        await asyncio.sleep(1.0)


async def _reload_challenge(bframe):
    """Click the reload button for a fresh challenge."""
    try:
        await bframe.locator(RELOAD_BTN).click()
        await asyncio.sleep(2)
    except Exception as e:
        log(f'Reload failed: {e}')


async def _check_error(bframe):
    """Check for visible error messages in the challenge frame."""
    for sel in [ERR_SELECT_MORE, ERR_INCORRECT]:
        try:
            loc = bframe.locator(sel)
            if await loc.count() > 0 and await loc.is_visible():
                return await loc.inner_text()
        except Exception:
            pass
    return None


async def _get_token(page: Page):
    """Extract the g-recaptcha-response token from the host page."""
    try:
        return await page.evaluate(
            'document.querySelector(\'textarea[name="g-recaptcha-response"]\').value'
        )
    except Exception:
        return None


# ── ITS Calendar URL getter ────────────────────────────────────────


async def get_calendar_url():
    """Navigate ITS site, solve CAPTCHA, click 次へ, and save the calendar URL."""
    log('Starting Playwright browser...')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--headless=new',
            ],
        )
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 1400},
            locale='ja-JP',
        )
        page = await context.new_page()

        try:
            # Step 1: Go to ITS homepage
            log('Navigating to ITS homepage...')
            response = await page.goto('https://as.its-kenpo.or.jp/', timeout=30000)
            if not response or response.status >= 400:
                log(f'Homepage returned HTTP {response.status if response else "no response"}')
                return None
            await asyncio.sleep(3)

            # Step 2: Click "カレンダーから探す"
            log('Looking for カレンダーから探す link...')
            calendar_link = page.locator('a:has-text("カレンダーから探す")')
            try:
                await calendar_link.wait_for(state='visible', timeout=15000)
            except Exception:
                await page.screenshot(path=_debug_path('homepage_link_not_found.png'))
                raise
            log('Found link, clicking...')
            await calendar_link.click()
            await page.wait_for_load_state('networkidle', timeout=30000)
            await asyncio.sleep(3)

            log(f'On captcha page: {page.url}')
            await page.screenshot(path=_debug_path('its_captcha_page.png'))

            # Step 3: Solve reCAPTCHA
            token = await solve_recaptcha(page)
            if not token:
                log('FAILED to solve captcha')
                return None

            log(f'CAPTCHA solved! Token: {token[:60]}...')

            # Step 4: Click 次へ button
            log('Clicking 次へ button...')
            next_btn = page.locator('input[value="次へ"], button:has-text("次へ"), a:has-text("次へ")')
            await next_btn.wait_for(state='visible', timeout=10000)
            await next_btn.click()
            await page.wait_for_load_state('networkidle', timeout=30000)
            await asyncio.sleep(3)

            # Step 5: Extract and save calendar URL
            calendar_url = page.url
            log(f'Calendar URL: {calendar_url}')
            await page.screenshot(path=_debug_path('its_calendar_page.png'))

            if 'calendar_select' not in calendar_url:
                log(f'WARNING: URL does not look like a calendar page: {calendar_url}')

            tmp_path = CALENDAR_URL_CACHE + '.tmp'
            with open(tmp_path, 'w') as f:
                f.write(calendar_url + '\n')
            os.replace(tmp_path, CALENDAR_URL_CACHE)
            log(f'Saved to {CALENDAR_URL_CACHE}')

            return calendar_url

        finally:
            await browser.close()
            log('Browser closed')


if __name__ == '__main__':
    asyncio.run(get_calendar_url())
