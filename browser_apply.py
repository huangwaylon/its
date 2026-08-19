#!/usr/bin/env python3
"""File the applicant form in real Chrome, when curl cannot.

`POST /apply/confirm` (申込する) is answered `302 → /service_category/index` for curl
no matter what it sends, while the identical POST from Chrome succeeds: the site
refuses the *client*, not the request. Finding 6 in docs/BOOKING_VIA_CURL.md has the
matrix. This is deliberately not the primary path — curl is tried first and this runs
only when curl was refused, so if the cause turns out to be environmental the fast
path resumes on its own with nothing to undo.

**This module presses 確認.** Past that a real reservation exists with a real
cancellation liability, so three things guard it:

  - `confirm_allowed()` is re-checked immediately before the commit, via the
    `allow_commit` callable — filling a form takes tens of seconds and the
    free-cancellation gate can close inside that window;
  - every field is written back and *verified*: a `<select>` handed a value that is
    not among its options keeps its old one silently, so an unverified mismatch would
    file a blank 都道府県 or 生年月日 against somebody's insurance record. One value
    the form did not accept aborts before 申込する;
  - 申込内容確認画面 must actually be on screen before 確認 is looked for.

A timeout does not reap the browser: it may have landed after 確認 was accepted, and
SIGKILL cannot un-file an application, so the outcome is reported unknown and the
mailbox is the authority. Values are never logged — they are 資格認証のキー.
"""
import asyncio

from config import BROWSER_CONFIRM_TIMEOUT
import chrome_guard

# Imported lazily in `_run`: pydoll is only needed on this path, and a booking that
# has a hold and a sent mail must not be lost to a missing optional dependency.
_CONFIRM_TEXT = '申込内容確認'
_EXPIRED_TEXT = 'ご利用のURLは無効となりました'
_NO_ROOMS_TEXT = '空き部屋がございません'


def _value(result):
    """Unwrap a CDP Runtime.evaluate result to its plain string value."""
    if isinstance(result, dict):
        result = result.get('result', {}).get('result', {}).get('value', '')
    return result if isinstance(result, str) else ''


def submit(link, values, log, tag, allow_commit):
    """Fill the emailed applicant form, press 申込する, then 確認.

    `values` is `confirm_booking.map_fields`' post body with the hidden fields
    removed — the DOM already carries `_method` and `authenticity_token`, and
    overwriting them with our copies is both pointless and a way to get them wrong.

    `allow_commit` is a **callable** re-consulted immediately before 確認, not a
    boolean captured earlier: filling the form takes tens of seconds and the
    cancellation gate can close inside that window.

    Returns `(status, detail)` with the same vocabulary as
    `confirm_booking.confirm_from_email`: 'confirmed' / 'deferred' / 'failed'.
    """
    budget = BROWSER_CONFIRM_TIMEOUT

    with chrome_guard.chrome(timeout=max(1, budget)) as owned:
        if not owned:
            log(f'{tag}   Chrome is busy re-minting a session; '
                f'not waiting any longer with a hold running')
            return 'failed', 'browser busy'
        try:
            return asyncio.run(
                asyncio.wait_for(_run(link, values, log, tag, allow_commit),
                                 timeout=budget))
        except asyncio.TimeoutError:
            # Deliberately not reaped here. The timeout may have landed after 確認
            # was accepted, and a SIGKILL cannot un-file an application — so the
            # outcome is reported as unknown and the mailbox is the authority.
            log(f'{tag}   Browser submit exceeded {budget:.0f}s: outcome UNKNOWN. '
                f'Check the mailbox for a 申込完了メール before retrying.')
            return 'failed', 'browser submit outcome unknown'
        except Exception as e:
            log(f'{tag}   Browser submit failed: {e!r}')
            return 'failed', f'browser submit error: {type(e).__name__}'


async def _open():
    """A minimised real Chrome, configured as captcha_solver configures its own."""
    from pydoll.browser.chromium.chrome import Chrome
    from pydoll.browser.options import ChromiumOptions

    options = ChromiumOptions()
    options.headless = False      # Turnstile rejects headless, and so may this flow
    options.add_argument('--no-sandbox')
    options.add_argument('--lang=ja-JP')
    options.add_argument('--window-size=1280,1600')
    return Chrome(options=options)


async def _minimise(tab):
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


async def _html(tab):
    return _value(await tab.execute_script(
        'return document.documentElement.outerHTML'))


async def _run(link, values, log, tag, allow_commit):
    import json

    browser = await _open()
    async with browser:
        tab = await browser.start()
        try:
            await _minimise(tab)
            await tab.go_to(link, timeout=30)
            await asyncio.sleep(3)

            html = await _html(tab)
            if _EXPIRED_TEXT in html:
                log(f'{tag}   The hold expired before the browser reached the form')
                return 'failed', 'hold expired'
            if _NO_ROOMS_TEXT in html:
                return 'failed', 'room taken'

            # Write every value back and read it back out — see the module note.
            report = _value(await tab.execute_script("""
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
            # Field *names* only. The values are 資格認証のキー.
            log(f'{tag}   Browser filled {filled.get("ok", 0)}/{len(values)} fields'
                + (f', not accepted: {", ".join(bad)}' if bad else ''))
            if bad or filled.get('ok') != len(values):
                log(f'{tag}   Not submitting a form the page did not accept in full')
                return 'deferred', f'browser could not fill: {", ".join(bad) or "?"}'

            clicked = _value(await tab.execute_script("""
                const b = [...document.querySelectorAll('input,button')]
                    .find(e => (e.value || e.textContent || '').includes('申込する'));
                if (!b) return 'missing';
                b.click();
                return 'clicked';
            """))
            if clicked != 'clicked':
                return 'failed', 'no 申込する control on the page'
            await asyncio.sleep(6)

            html = await _html(tab)
            if _CONFIRM_TEXT not in html:
                if _NO_ROOMS_TEXT in html:
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
            clicked = _value(await tab.execute_script("""
                const b = [...document.querySelectorAll('input,button')]
                    .find(e => {
                        const t = (e.value || e.textContent || '').trim();
                        return t === '確認' || t === '確定';
                    });
                if (!b) return 'missing';
                b.click();
                return 'clicked';
            """))
            if clicked != 'clicked':
                return 'failed', 'no 確認 control on 申込内容確認画面'
            await asyncio.sleep(8)

            html = await _html(tab)
            import confirm_booking
            receipt = confirm_booking.parse_receipt(html)
            if receipt or '申込完了' in html:
                return 'confirmed', receipt
            return 'failed', 'browser 確認 did not produce a completion page'
        finally:
            try:
                await browser.stop()
            except Exception:
                pass
