#!/usr/bin/env python3
"""The one Chrome this program drives, and the leg that needs it.

Two users, one browser, serialised by `chrome()`: `captcha_solver._kill_stray_chrome()`
reaps by `pgrep -f remote-debugging-port`, which matches an application-filing browser
as readily as a solving one, so a timed-out solve would SIGKILL it between 申込する and
確認 with no way to learn which side of the commit it died on. Under the lock, anything
`pgrep` finds really is an orphan.

`submit()` is the 申込する/確認 leg, reached only after curl's POST was refused
(docs/SITE.md §5). **It presses 確認**, past which a real reservation exists, so:

  - `allow_commit` is a **callable**, re-consulted immediately before the commit —
    filling a form takes tens of seconds and the gate can close inside that window;
  - every field is written back and *read back*. A `<select>` given a value not among
    its options keeps its old one silently, so an unverified mismatch would file a
    blank 都道府県 or 生年月日 against somebody's insurance record;
  - 申込内容確認画面 must be on screen before 確認 is looked for.

A timeout does **not** reap the browser — it may have landed after 確認 was accepted,
and SIGKILL cannot un-file an application, so the outcome is unknown and the mailbox is
the authority. Field values are never logged; they are 資格認証のキー.
"""
import asyncio
import contextlib
import json
import threading

from config import BROWSER_CONFIRM_TIMEOUT
import book_hotels as bh

_CONFIRM_TEXT = '申込内容確認'

# ── one Chrome at a time ─────────────────────────────────────────────
# Long enough to sit through a solve plus teardown, short enough that a wedged holder
# cannot cost a whole booking.
DEFAULT_WAIT = 210

_lock = threading.Lock()


@contextlib.contextmanager
def chrome(timeout=None):
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


# ── Chrome plumbing, shared with captcha_solver ──────────────────────

def value(result):
    """Unwrap a CDP Runtime.evaluate result to its plain string value."""
    if isinstance(result, dict):
        result = result.get('result', {}).get('result', {}).get('value', '')
    return result if isinstance(result, str) else ''


def launch():
    """A real, non-headless Chrome — Turnstile rejects headless, and so may /apply.

    pydoll is imported here, not at module scope: it is optional, and a booking with a
    hold and a sent mail must not be lost to its absence.
    """
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

    with chrome(timeout=max(1, budget)) as owned:
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

    browser = launch()
    async with browser:
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


async def _click(tab, predicate):
    """Click the first input/button matching a JS predicate on `e`."""
    return value(await tab.execute_script("""
        const b = [...document.querySelectorAll('input,button')].find(e => %s);
        if (!b) return 'missing';
        b.click();
        return 'clicked';
    """ % predicate))
