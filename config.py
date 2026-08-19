"""User-configurable settings for the ITS Calendar Booker."""
import os
import re

# ── Paths ────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    """Read `KEY=value` lines from `path` into the environment.

    Loaded here rather than left to `source .env`, because forgetting to source it
    does not fail loudly: `EMAIL` would quietly fall back to its default, the
    site's confirmation mail would go to a mailbox we do not read, and every
    booking would stall at the email step for a reason nothing in the log explains.

    A real environment variable wins over the file, so an override on the command
    line still works — but only a *non-empty* one. An exported-but-empty variable
    counts as unset, because that is a placeholder rather than a decision, and
    `setdefault` semantics would otherwise let one stale `export ITS_KIGOU=` in a
    shell profile shadow the file permanently and silently.

    `export ` prefixes, surrounding quotes, comments and blank lines are all
    tolerated so the same file can also be sourced by a shell.
    """
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.readlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip().lstrip('﻿')
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, sep, value = line.partition('=')
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in '"\''
        if quoted:
            value = value[1:-1]
        else:
            # Strip a trailing comment, but only one introduced by whitespace, so
            # a value that legitimately contains '#' survives. Without this an
            # annotated line like `ITS_ZIP=1420051   # 郵便番号` silently sets the
            # postcode to "1420051   # 郵便番号", which the site then rejects.
            value = re.split(r'\s#', value, maxsplit=1)[0].rstrip()
        if not os.environ.get(key):
            os.environ[key] = value


_load_dotenv(os.path.join(_DIR, '.env'))

CALENDAR_URL_CACHE = os.path.join(_DIR, 'calendar_url_cache.txt')
HOLDS_FILE = os.path.join(_DIR, 'holds.json')
LOG_FILE = os.path.join(_DIR, 'its_booking.log')
DEBUG_DIR = os.path.join(_DIR, 'debug_responses')
USER_AGENT_CACHE = os.path.join(_DIR, 'chrome_user_agent.txt')

# ── Booking settings ─────────────────────────────────────────────────
TARGET_DATES = [
    # "2026-09-16",
    "2026-09-05",
    "2026-09-16",
    "2026-09-19",
    "2026-09-20",
    "2026-09-21",
    "2026-09-22",
    "2026-09-26",
]
NUM_GUESTS = '2'

SKIP_PAST_DATES = True    # stop polling dates that have already passed

# ── Network tuning ───────────────────────────────────────────────────
RETRY_DELAY = 20          # seconds between scan retry attempts
CURL_MAX_ATTEMPTS = 3     # max attempts per curl request (1 = no retry)
URL_CHECK_INTERVAL = 60   # seconds between URL validity checks
URL_REFRESH_INTERVAL = 1800  # seconds between proactive URL refreshes

# Delay before a curl retry, doubling per attempt (0.5s, 1s, 2s, ...). The site
# answers 503 under load — 1072 of them in the last log — and the old zero-delay
# retry put both attempts in the same millisecond, so it never once helped.
CURL_RETRY_BACKOFF = 0.5
CURL_RETRY_BACKOFF_MAX = 8.0
# curl's own --max-time. subprocess.run gets this plus a margin, so a curl that
# ignores its deadline still cannot wedge a thread for the rest of the week.
CURL_TIMEOUT = 30

# A date the scanner just saw as available is worth more than one request. On a
# transient failure (503, connection reset, or an expired-session 302) the whole
# date attempt is retried on a completely fresh session rather than abandoned
# until the next scan cycle, which is RETRY_DELAY seconds of a contested slot.
BOOK_MAX_ATTEMPTS = 3
BOOK_RETRY_DELAY = 2.0    # seconds before re-attempting a date

# The scan cycle is one POST that carries availability for the whole month, plus
# a calendar GET whose only purpose is to mint the csrf/s pair that POST needs. A
# Rails authenticity_token is valid for the life of the session — validation is a
# stateless unmask against session[:_csrf_token], with no nonce store — so the
# GET is skippable while the session lasts, halving the steady-state scan to one
# request. See docs/SITE.md §4.
#
# Reuse is self-limiting: a rejected token is detected by response *shape*, the
# tokens are re-minted in the same cycle, and after this many consecutive
# rejections reuse switches off for the rest of the process rather than emitting
# a steady stream of rejected POSTs.
SCAN_REUSE_SESSION = True
SCAN_REUSE_MAX_FAILURES = 3

# Consecutive scan failures multiply RETRY_DELAY up to this ceiling, so a site
# outage is not met with the same request rate for hours.
SCAN_BACKOFF_MAX = 300
# Random 0..N seconds added to each scan sleep. Without it the per-month
# scanners settle into lockstep and hit the site in bursts.
SCAN_JITTER = 5

# Hard ceiling on one CAPTCHA solve. pydoll/Chrome can hang, and the solve runs
# synchronously in the URL monitor thread — the only thing that re-mints a
# session. An untimed hang there stops all booking while the process still looks
# healthy.
CAPTCHA_TIMEOUT = 180

# There is deliberately no per-hotel retry cooldown. One existed, on the theory
# that a date staying available for half an hour with a hotel that always fails
# would be re-attempted every scan cycle — nominally ~2,000 requests in 30
# minutes. The logs do not bear that out: the longest observed run is **three**
# consecutive cycles (2026-08-22, 「No services for」 on 鳴子温泉 湯元 吉祥, NAGU 勝浦
# and トスラブ箱根ビオーレ, ~20 s apart), after which the date stopped being listed.
# Under 20 requests, not 2,000, and the cooldown itself fired exactly once in
# 153k log lines — after a *success*, not a failure loop.
#
# Repetition is bounded by what actually does the work: SKIP_HOTELS, the
# already-booked filter reading holds.json, the per-call `attempted` set, and the
# site refusing a second application at a facility we already hold (the room
# search answers 空き部屋がございません). Note also that all three recorded IP bans
# were served on the scanner's calendar_get — the poll cadence, not this path —
# so volume risk lives in RETRY_DELAY, not in hotel re-attempts.

# ── Emailed confirmation ─────────────────────────────────────────────
# `send_complete` only dispatches the confirmation email. The reservation exists
# only after the emailed link is opened, the applicant form submitted, and 確認
# pressed — which yields a 申込受付番号 and 予約確定. That last POST is the first
# irreversible, money-bearing action in this program.
AUTO_CONFIRM = True

# Never complete an application for a stay fewer than this many days away.
#
# Free cancellation is web-only and ends at D−10 (「利用日の10日前まではWEB上で
# キャンセルが行えます」); from D−9 it costs 50% of the fee and must be arranged by
# telephone during office hours, and the full amount on the day. So a booking
# confirmed inside that window cannot be undone cheaply, or possibly at all.
#
# 11 rather than 10: at D−11 there is still a whole further day (D−10) in which to
# cancel free, which is the margin for clock skew, a JST/local timezone edge, and
# an application that fires just before midnight. Dates inside the window are NOT
# abandoned — the hold is still taken and the email still sent, and a human
# finishes from the emailed link. See confirm_allowed().
AUTO_CONFIRM_MIN_DAYS = 11

# How long to wait for the site's confirmation email before giving up on it.
CONFIRM_MAIL_TIMEOUT = 180     # seconds to poll IMAP for the message

# Finish the application in real Chrome when curl's 申込する POST is refused.
#
# `POST /apply/confirm` answers 302 → /service_category/index for curl whatever it
# sends, while the identical POST from Chrome succeeds: the site refuses the
# *client*, not the request. docs/SITE.md §5 has the bisect matrix
# and the open question — that measurement was taken through an intercepting HTTPS
# proxy, so check whether it reproduces off one. curl is still tried first, so if
# the cause is environmental this simply stops firing.
#
# Needs `pydoll-python`. Without it the booking still takes its hold and sends its
# mail, and the log asks for a human.
BROWSER_CONFIRM = True
# Hard ceiling on one browser submit. Chrome can hang, and this runs inside a
# booking thread holding a room.
BROWSER_CONFIRM_TIMEOUT = 240

# ── Secrets, from the environment only ───────────────────────────────
# Never hard-coded and never written to a tracked file: this repository's history
# is on a public remote. 記号/番号/カナ氏名/生年月日 are 資格認証のキー — identity
# credentials for the insurance record — so they are also added to the debug-dump
# redaction list, not merely kept out of git.
IMAP_HOST = os.environ.get('ITS_IMAP_HOST', 'imap.gmail.com')
IMAP_PORT = int(os.environ.get('ITS_IMAP_PORT', '993'))
IMAP_USER = os.environ.get('ITS_IMAP_USER', '')
# Google displays an app password as four groups of four. The spaces are purely
# cosmetic and IMAP rejects them, so strip rather than make that a support call.
IMAP_APP_PASSWORD = os.environ.get('ITS_IMAP_APP_PASSWORD', '').replace(' ', '')
MAIL_FROM = os.environ.get('ITS_MAIL_FROM', 'noreply@mail.its-kenpo.or.jp')

# The address submitted to the site must be the mailbox we read, or the
# confirmation link is delivered somewhere we cannot see it.
EMAIL = os.environ.get('ITS_EMAIL') or IMAP_USER or 'wwaylonhuang@gmail.com'

APPLICANT = {
    'kigou': os.environ.get('ITS_KIGOU', ''),
    'bangou': os.environ.get('ITS_BANGOU', ''),
    'kana_sei': os.environ.get('ITS_KANA_SEI', ''),
    'kana_mei': os.environ.get('ITS_KANA_MEI', ''),
    # The live form has ONE 「カナ氏名」 box, not separate 姓/名. Left blank here it
    # is derived as "SEI　MEI" with a full-width space; set it to override.
    'kana_name': os.environ.get('ITS_KANA_NAME', ''),
    'name_sei': os.environ.get('ITS_NAME_SEI', ''),
    'name_mei': os.environ.get('ITS_NAME_MEI', ''),
    'birth': os.environ.get('ITS_BIRTH', ''),
    'sex': os.environ.get('ITS_SEX', ''),
    'zokugara': os.environ.get('ITS_ZOKUGARA', ''),
    'tel': os.environ.get('ITS_TEL', ''),
    # 事業所名 — asked for by name on the applicant form (apply[office_name]).
    'office': os.environ.get('ITS_OFFICE_NAME', ''),
    'zip': os.environ.get('ITS_ZIP', ''),
    # 都道府県, matched against the form's dropdown by its label, e.g. 東京都.
    'state': os.environ.get('ITS_STATE', ''),
    'addr': os.environ.get('ITS_ADDR', ''),
}

RESERVATIONS_FILE = os.path.join(_DIR, 'reservations.json')

# ── Debug dumps ──────────────────────────────────────────────────────
# Dumps are throttled per (label, step) and the directory is pruned to a fixed
# size, so a failure that repeats every cycle for a week cannot fill the disk.
DEBUG_DUMP_INTERVAL = 300   # seconds between dumps of the same label+step
DEBUG_DUMP_KEEP = 400       # max files retained in DEBUG_DIR

# ── Logging ──────────────────────────────────────────────────────────
LOG_MAX_BYTES = 32 * 1024 * 1024   # rotate the log past this size
LOG_BACKUPS = 3                    # keep this many rotated logs
# "no dates available" is 116k of the last 150k log lines. Only log an unchanged
# idle scan result this often; availability and errors are never suppressed.
IDLE_LOG_INTERVAL = 300

# ── HTTP fingerprint ─────────────────────────────────────────────────
# The calendar session token is minted in real Chrome by captcha_solver, then
# replayed by curl. With this off, those replays identify as `curl/8.x`, an
# obvious mid-session client switch. On, they carry the UA of the Chrome that
# actually solved the CAPTCHA (recorded to USER_AGENT_CACHE at solve time).
#
# Deliberately NOT sent: Origin (absent = Rails skips its origin check
# entirely), Sec-Fetch-* and sec-ch-ua (a single static value contradicts one
# of the two request classes we make), and Accept-Encoding (this curl build
# has no brotli/zstd, and a decode failure yields an empty body — the exact
# symptom being diagnosed).
# Note the TLS fingerprint is still curl's, so a browser UA is a UA/TLS
# mismatch. If the failure rate gets worse after this, flip BROWSER_HEADERS
# off and compare.
BROWSER_HEADERS = True
ACCEPT = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
ACCEPT_LANGUAGE = 'ja-JP,ja;q=0.9'
# Used only until the first CAPTCHA solve records Chrome's real UA.
FALLBACK_USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
)

# ── Hotel priority ───────────────────────────────────────────────────
# Hotels matching these substrings are attempted FIRST, in this order, before
# any other non-skipped hotel. Matching is case-insensitive and ignores spacing,
# so 'NAGU' catches "NAGU 勝浦".
#
# This is about the race, not about preference. Hotels are booked sequentially at
# ~10 requests each, so a hotel the site lists last is attempted some seconds
# after the slot was spotted. Anything named here jumps the queue. Note the list
# a date offers holds only facilities with vacancy on it — 「{date}に空きがある
# 施設です」 — so it is usually about three entries, not the whole roster.
PRIORITY_HOTELS = [
    'NAGU',
]

# ── Hotel skip list ──────────────────────────────────────────────────
# Matched after normalizing case, HTML entities, and full-width vs half-width
# spaces, so an entry cannot silently miss on whitespace alone.
SKIP_HOTELS = [
    "ブルーベリーヒル勝浦",
    "ホテル日航プリンセス京都",
    "ホテルハーヴェスト南紀田辺",
    "草津温泉　ホテルヴィレッジ",
    "ホテルハーヴェスト伊東",
    "ホテルハーヴェスト　スキージャム勝山",
    "ホテル琵琶レイクオーツカ",
    "ホテルハーヴェスト有馬六彩",
    "ホテルハーヴェスト浜名湖",
    "ゆふいん山水館",
    "ホテル日航アリビラ",
    "ラビスタ函館ベイANNEX",
    "ホテルハーヴェスト斑尾",
    "ホテルハーヴェスト京都鷹峯",
    "和倉温泉 あえの風",
    "鳴子温泉　湯元　吉祥",
    "ホテルオークラ東京ベイ",  # also listed under Keep below; skipping wins
    "NASPAニューオータニ",
    "トスラブ館山ルアーナ",
    "ホテルハーヴェスト旧軽井沢",
    "伊香保温泉 ホテル天坊",
    "蓼科東急ホテル",
]
