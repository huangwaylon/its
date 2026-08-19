#!/usr/bin/env python3
"""Entry point — one scanner per target month, a URL monitor, a confirm worker.

Scanners only take holds; they never trigger a CAPTCHA. The URL monitor is the only
thing that can re-mint a session and solves synchronously in its own thread, which is
what prevents overlapping solves without a lock. A watchdog restarts any of them.

`--check` validates `.env` instead of booking anything — the site checks those values
mid-hold with a contested slot on the line, so check them at your desk.

    uv run main.py
    uv run main.py --check
"""
import asyncio
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import date, datetime

from chrome import get_calendar_url
import config
from config import (
    URL_CHECK_INTERVAL, URL_REFRESH_INTERVAL, LOG_FILE,
    LOG_MAX_BYTES, LOG_BACKUPS, TARGET_DATES, EMAIL, PRIORITY_HOTELS,
)
import book_hotels
import confirm_booking
from book_hotels import R, G, Y, C, B, X
from book_hotels import log as url_log

DIM = '\033[2m'

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

MONTH_ABBR = ['', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
              'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

# How often the watchdog checks that every worker thread is still alive.
WATCHDOG_INTERVAL = 30


def check_cached_url():
    """`(url, confirmed_valid)`: `(url, True)` on 200; `(url, False)` on 5xx or a
    connection failure, where the session is assumed still valid; `(None, False)` when
    there is no cached URL or the session expired."""
    url = book_hotels._read_cached_url()
    if not url:
        return None, False
    try:
        r = subprocess.run(
            ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10']
            + book_hotels.header_args() + [url],
            capture_output=True, text=True, timeout=30,
        )
        status = r.stdout.strip()
    except Exception as e:
        # A local failure to run curl says nothing about the token, and discarding a
        # working URL over it would force a needless solve.
        url_log(f"{Y}URL check: could not run curl ({e!r}), will retry{X}")
        return url, False
    if status == '200':
        url_log(f"{C}URL check: valid (200){X}")
        return url, True
    if not status or status == '000' or status.startswith('5'):
        url_log(f"{Y}URL check: server error ({status}), will retry{X}")
        return url, False
    url_log(f"{Y}URL check: session invalid ({status}){X}")
    return None, False


def url_monitor():
    """Monitor the calendar URL cache and solve the CAPTCHA when needed. Runs forever,
    re-solving proactively every URL_REFRESH_INTERVAL even when the URL still works;
    the solve is synchronous, which is what blocks a second one without a lock."""
    last_solve = time.time()
    while True:
        try:
            url, confirmed = check_cached_url()
            due_for_refresh = time.time() - last_solve >= URL_REFRESH_INTERVAL

            if url and (not due_for_refresh or not confirmed):
                time.sleep(URL_CHECK_INTERVAL)
                continue

            if url:
                # A proactive refresh replaces a working token, and a booking carries
                # the old one across a ~10-request chain. A repair is never deferred.
                active = book_hotels.active_bookings()
                if active:
                    url_log(f"{Y}Proactive refresh deferred "
                            f"({active} booking(s) in flight){X}")
                    time.sleep(URL_CHECK_INTERVAL)
                    continue
                url_log(f"{Y}Proactive refresh "
                        f"({int(time.time() - last_solve)}s since last solve)...{X}")
            else:
                url_log(f"{B}URL invalid or missing, solving CAPTCHA...{X}")

            try:
                new_url = asyncio.run(get_calendar_url())
                if new_url:
                    last_solve = time.time()
                    url_log(f"{G}New URL saved — {new_url}{X}")
                    continue
                url_log(f"{R}CAPTCHA solve failed, will retry next cycle{X}")
            except Exception as e:
                url_log(f"{R}CAPTCHA solve error: {e!r}{X}")
            if url:  # Had a valid URL; reset the timer to avoid spamming retries
                last_solve = time.time()

        except Exception as e:
            # The only thread that can re-solve the CAPTCHA; the watchdog would
            # restart it, but not losing it is cheaper.
            url_log(f"{R}URL monitor error: {e!r}{X}")

        time.sleep(URL_CHECK_INTERVAL)


def _rotate_log():
    """Roll LOG_FILE over past LOG_MAX_BYTES, keeping LOG_BACKUPS. At startup rather
    than mid-write, so no log handler needs a lock."""
    try:
        if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) < LOG_MAX_BYTES:
            return
        for i in range(LOG_BACKUPS - 1, 0, -1):
            src, dst = f'{LOG_FILE}.{i}', f'{LOG_FILE}.{i + 1}'
            if os.path.exists(src):
                os.replace(src, dst)
        os.replace(LOG_FILE, f'{LOG_FILE}.1')
    except OSError as e:
        print(f'Could not rotate {LOG_FILE}: {e}', file=sys.stderr)


class _Worker:
    """A named restartable daemon thread. main() never joins any of them, so a thread
    that dies leaves the process short one scanner — or with nothing that can re-mint a
    session, or nothing that confirms a hold. All invisible without a watchdog."""

    def __init__(self, name, target, args=()):
        self.name, self.target, self.args = name, target, args
        self.thread = None
        self.restarts = 0

    def start(self):
        self.thread = threading.Thread(target=self.target, args=self.args,
                                       name=self.name, daemon=True)
        self.thread.start()

    def ensure_alive(self):
        if self.thread is not None and self.thread.is_alive():
            return False
        self.restarts += 1
        self.start()
        return True


def watchdog(workers):
    """Restart any worker thread that has stopped. Runs forever."""
    while True:
        time.sleep(WATCHDOG_INTERVAL)
        for w in workers:
            try:
                if w.ensure_alive():
                    url_log(f"{R}Worker '{w.name}' died - restarted "
                            f"(restart #{w.restarts}){X}")
            except Exception as e:
                url_log(f"{R}Watchdog could not restart '{w.name}': {e!r}{X}")


# ── Environment check (`main.py --check`) ────────────────────────────
#
# The ITS site validates every value here at the moment it matters least:
# mid booking, inside a 30-minute hold, with a contested slot on the line.
# Check them at your desk instead.

_problems = []


def _pad(text, width):
    """Left-justify by display width, not character count: 記号 occupies four
    columns for two characters, so `str.ljust` leaves the output ragged."""
    cells = sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)
    return text + ' ' * max(0, width - cells)


def _report(label, value, ok, detail=''):
    mark = f'{G}ok{X}' if ok else f'{R}FAIL{X}'
    print(f'  [{mark}] {_pad(label, 24)} {value}'
          + (f'  {DIM}{detail}{X}' if detail else ''))
    if not ok:
        _problems.append(f'{label}: {detail or "not set"}')


# Full-width katakana, plus the long vowel mark and the middle dot used in names.
_KATAKANA = re.compile(r'\A[ァ-ヺー・　 ]+\Z')
_HIRAGANA = re.compile(r'[ぁ-ゖ]')
_KANJI = re.compile(r'[一-鿿]')


def _check_applicant():
    print(f'\n{DIM}申込代表者 (from 保険証等){X}')
    a = config.APPLICANT

    for key, label in (('kigou', '記号 (ITS_KIGOU)'), ('bangou', '番号 (ITS_BANGOU)')):
        v = a[key]
        # Length is safe to show; the value itself is an identity credential.
        _report(label, f'{len(v)} chars' if v else '(empty)', bool(v),
               'required — on the front of the card')

    for key, label in (('kana_sei', 'カナ姓 (ITS_KANA_SEI)'),
                       ('kana_mei', 'カナ名 (ITS_KANA_MEI)')):
        v = a[key]
        if not v:
            _report(label, '(empty)', False, 'required, katakana')
        elif _HIRAGANA.search(v):
            _report(label, f'{len(v)} chars', False, 'looks like hiragana, not katakana')
        elif _KANJI.search(v):
            _report(label, f'{len(v)} chars', False, 'looks like kanji — this field wants カナ')
        elif not _KATAKANA.match(v):
            _report(label, f'{len(v)} chars', False,
                   'not full-width katakana (half-width ﾊﾝｶｸ will not match either)')
        else:
            _report(label, f'{len(v)} chars katakana', True)

    v = a['birth']
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v or ''):
        _report('生年月日 (ITS_BIRTH)', v or '(empty)', False, 'must be YYYY-MM-DD')
    else:
        try:
            born = date.fromisoformat(v)
        except ValueError:
            _report('生年月日 (ITS_BIRTH)', v, False, 'not a real date')
        else:
            age = (date.today() - born).days // 365
            plausible = 0 < age < 120
            _report('生年月日 (ITS_BIRTH)', v, plausible,
                   f'age {age}' if plausible else f'implausible age {age}')

    v = a['sex']
    _report('性別 (ITS_SEX)', v or '(empty)', v in ('男', '女'), "must be 男 or 女")

    v = a['zokugara']
    _report('続柄 (ITS_ZOKUGARA)', v or '(empty)', bool(v),
           '本人 for the insured member')

    v = a['tel']
    digits = re.sub(r'\D', '', v or '')
    _report('電話番号 (ITS_TEL)', v or '(empty)', 10 <= len(digits) <= 11,
           f'{len(digits)} digits — expected 10 or 11')

    optional = [k for k in ('name_sei', 'name_mei', 'zip', 'addr') if a[k]]
    print(f'  {DIM}optional set: {", ".join(optional) or "none"} '
          f'(only used if the form asks){X}')


def _check_mail_config():
    print(f'\n{DIM}Gmail{X}')
    user = config.IMAP_USER
    _report('ITS_IMAP_USER', user or '(empty)', '@' in user, 'must be the mailbox we read')

    pw = config.IMAP_APP_PASSWORD
    if not pw:
        _report('ITS_IMAP_APP_PASSWORD', '(empty)', False,
               'https://myaccount.google.com/apppasswords')
    else:
        _report('ITS_IMAP_APP_PASSWORD', f'{len(pw)} chars', len(pw) == 16,
               'Google app passwords are 16 characters'
               if len(pw) != 16 else 'spaces stripped automatically')

    same = config.EMAIL == user and bool(user)
    _report('EMAIL submitted to ITS', config.EMAIL, same,
           '' if same else (f'differs from the mailbox we poll '
                            f'({user or "unset"}) — the confirmation link would '
                            f'be delivered out of reach'))
    print(f'  {DIM}expecting mail from {config.MAIL_FROM}{X}')


def _check_imap_login():
    print(f'\n{DIM}IMAP connection{X}')
    if not (config.IMAP_USER and config.IMAP_APP_PASSWORD):
        _report('login', 'skipped', False, 'user or app password missing')
        return
    import imaplib
    try:
        with imaplib.IMAP4_SSL(config.IMAP_HOST, config.IMAP_PORT, timeout=20) as m:
            try:
                m.login(config.IMAP_USER, config.IMAP_APP_PASSWORD)
            except imaplib.IMAP4.error as e:
                msg = str(e)
                hint = 'wrong app password, or 2-Step Verification is off'
                if 'Application-specific password required' in msg:
                    hint = ('this account requires an app password — a normal '
                            'account password will not work')
                elif 'Invalid credentials' in msg:
                    hint = 'invalid credentials — regenerate the app password'
                _report('login', 'rejected', False, hint)
                return
            _report('login', config.IMAP_USER, True)

            # All Mail, not INBOX: a filter or Google's own classifier can put the
            # confirmation somewhere else, and losing the 30-minute hold to a
            # mailbox-selection detail would be an absurd way to lose a room.
            box = '"[Gmail]/All Mail"'
            typ, _ = m.select(box, readonly=True)
            if typ != 'OK':
                box = 'INBOX'
                typ, _ = m.select(box, readonly=True)
            _report('mailbox', box.strip('"'), typ == 'OK',
                   '' if typ == 'OK' else 'could not select a mailbox')

    except OSError as e:
        _report('connection', f'{config.IMAP_HOST}:{config.IMAP_PORT}', False,
               f'{type(e).__name__}: {e} — IMAP disabled, or the network blocks 993')


def _check_gate():
    print(f'\n{DIM}Confirmation gate{X}')
    bh = book_hotels
    print(f'  AUTO_CONFIRM={config.AUTO_CONFIRM}  '
          f'AUTO_CONFIRM_MIN_DAYS={config.AUTO_CONFIRM_MIN_DAYS}')
    if not config.TARGET_DATES:
        print(f'  {Y}TARGET_DATES is empty{X}')
    for d in sorted(config.TARGET_DATES):
        left = bh.days_until(d)
        ok, _why = bh.confirm_allowed(d)
        if left is not None and left < 0:
            verdict = f'{DIM}past{X}'
        elif ok:
            verdict = f'{G}auto-confirm{X}'
        else:
            verdict = f'{Y}hold+email only, human confirms{X}'
        print(f'    {d}  {str(left) + "d":>5}  {verdict}')



def check_env():
    """Validate .env before it matters — the site checks these mid-hold, with a
    contested slot on the line. Prints no secrets. Returns a process exit code."""
    print(f'\nITS booker — environment check  {DIM}(no secrets printed){X}')
    _check_mail_config()
    _check_applicant()
    _check_imap_login()
    _check_gate()

    print()
    if _problems:
        print(f'{R}{len(_problems)} problem(s) to fix:{X}')
        for p in _problems:
            print(f'  - {p}')
        return 1
    print(f'{G}Ready.{X}')
    return 0


def main():
    _rotate_log()
    log_file = open(LOG_FILE, 'a', encoding='utf-8')
    log_file.write(f'\n=== Session started {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===\n')
    log_file.flush()

    def sink(msg):
        plain = _ANSI_RE.sub('', msg)
        print(plain, flush=True)
        try:
            log_file.write(plain + '\n')
            log_file.flush()
        except OSError:
            pass  # a full disk must not take down a booking thread

    book_hotels._log_handler = sink
    url_log("=" * 60)
    url_log(f"{B}ITS BOOKING SYSTEM{X}")
    url_log(f"Email: {EMAIL}")
    url_log(f"Dates: {', '.join(TARGET_DATES)}")
    url_log(f"Priority hotels: {', '.join(PRIORITY_HOTELS) or '(none)'}")
    url_log("=" * 60)

    months = {}
    for d in TARGET_DATES:
        months.setdefault(d[:7], []).append(d)
    # The confirm worker owns every emailed leg, serially. Under the watchdog because
    # it is the only thing that turns a hold into a reservation.
    workers = [_Worker('url-monitor', url_monitor),
               _Worker('confirm', confirm_booking.worker)]
    for month_str, dates in months.items():
        label = MONTH_ABBR[int(month_str[5:7])]
        workers.append(_Worker(f'scan-{label}', book_hotels.scan_and_book_month,
                               (month_str, dates, label)))

    url_log(f"{B}Starting {len(months)} scanner threads "
            f"({', '.join(MONTH_ABBR[int(m[5:7])] for m in months)}) "
            f"for {len(TARGET_DATES)} dates{X}")
    url_log("=" * 60)

    for w in workers:
        w.start()

    threading.Thread(target=watchdog, args=(workers,),
                     name='watchdog', daemon=True).start()
    url_log(f"{C}Watchdog started ({len(workers)} workers, "
            f"{WATCHDOG_INTERVAL}s interval){X}")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    print(f"{Y}Interrupted, exiting.{X}")


if __name__ == '__main__':
    if sys.argv[1:] == ['--check']:
        sys.exit(check_env())
    if sys.argv[1:]:
        sys.exit(f'usage: {sys.argv[0]} [--check]')
    main()
