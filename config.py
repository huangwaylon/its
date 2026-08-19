"""User-configurable settings for the ITS Calendar Booker."""
import os
import re

# ── Paths ────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    """Read `KEY=value` lines from `path` into the environment.

    Done here, not left to `source .env`: forgetting to source it fails silently,
    with mail going to a mailbox nobody reads. A real env var wins, but only a
    *non-empty* one — an exported-but-empty value is a placeholder, and `setdefault`
    would let one stale `export ITS_KIGOU=` shadow the file forever.

    `export `, quotes, comments and blank lines are tolerated so a shell can source
    the same file.
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
            # Only a whitespace-introduced comment, so a value containing '#'
            # survives; `ITS_ZIP=1420051  # note` must not set the whole string.
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
    "2026-09-05",
    "2026-09-19",
    "2026-09-20",
    "2026-09-21",
    "2026-09-22",
    "2026-09-26",
]
NUM_GUESTS = '2'

SKIP_PAST_DATES = True    # stop polling dates that have already passed

# ── Network tuning ───────────────────────────────────────────────────
RETRY_DELAY = 15          # seconds between scan retry attempts
CURL_MAX_ATTEMPTS = 3     # max attempts per curl request (1 = no retry)
URL_CHECK_INTERVAL = 60   # seconds between URL validity checks
URL_REFRESH_INTERVAL = 1800  # seconds between proactive URL refreshes

# Doubling delay before a curl retry. Must be non-zero: retrying a 503 in the same
# millisecond never once helped.
CURL_RETRY_BACKOFF = 0.5
CURL_RETRY_BACKOFF_MAX = 8.0
# curl's --max-time. subprocess.run gets this plus a margin, so a curl ignoring its
# deadline cannot wedge a thread.
CURL_TIMEOUT = 30

# A date just seen available is worth more than one request, so a transient failure
# retries the whole date on a fresh session rather than waiting a scan cycle.
BOOK_MAX_ATTEMPTS = 3
BOOK_RETRY_DELAY = 2.0    # seconds before re-attempting a date

# A csrf/`s` pair is valid for the life of the session (docs/SITE.md §4), so the
# calendar GET that mints it is skippable — halving the steady-state scan to one
# request. Self-limiting: rejection is detected by response *shape*, tokens are
# re-minted in the same cycle, and reuse switches off after this many rejections.
SCAN_REUSE_SESSION = True
SCAN_REUSE_MAX_FAILURES = 3

# Consecutive failures multiply RETRY_DELAY up to this ceiling.
SCAN_BACKOFF_MAX = 300
# Random 0..N s per sleep; without it the per-month scanners arrive as a burst.
SCAN_JITTER = 1

# Hard ceiling on one solve: it occupies the one thread that can re-mint a session,
# so an untimed Chrome hang stops all booking behind a healthy-looking process.
CAPTCHA_TIMEOUT = 180

# **No per-hotel retry cooldown, deliberately.** One existed against a claimed
# ~2,000 requests / 30 min; the logs show the longest real run is three consecutive
# cycles, under 20 requests. Repetition is bounded by SKIP_HOTELS, the holds.json
# filter, the per-call `attempted` set and the site's own refusal. All three recorded
# IP bans hit the scanner's calendar_get, so volume risk lives in RETRY_DELAY.

# ── Emailed confirmation ─────────────────────────────────────────────
# Run the emailed leg. Its last POST is the first irreversible, money-bearing action
# in this program.
AUTO_CONFIRM = True

# Never complete an application for a stay nearer than this. Free web cancellation
# ends at D−10; from D−9 it costs 50% and is phone-only. 11 rather than 10 leaves a
# whole further day to cancel free — margin for clock skew and a near-midnight
# application. Nearer dates are still held and mailed, for a human to finish.
AUTO_CONFIRM_MIN_DAYS = 11

CONFIRM_MAIL_TIMEOUT = 180     # give up on a hold's mail this long after the hold
CONFIRM_POLL_INTERVAL = 10     # worker cycle; one IMAP trip serves the whole queue

# The emailed leg is filed in real Chrome, always: curl's 申込する POST is answered
# 302 → /service_category/index whatever it sends, because the site refuses the
# *client*, not the request (docs/SITE.md §5 — including the open question of whether
# that reproduces off an intercepting proxy). Needs `pydoll-python`; without it the hold
# and mail still stand and the log asks for a human.
BROWSER_CONFIRM_TIMEOUT = 240  # Chrome can hang, and a room is held meanwhile

# ── Secrets, from the environment only ───────────────────────────────
# Never hard-coded, never in a tracked file: this history is on a public remote.
# 記号/番号/カナ氏名/生年月日 are 資格認証のキー, so they are also redacted from dumps.
IMAP_HOST = os.environ.get('ITS_IMAP_HOST', 'imap.gmail.com')
IMAP_PORT = int(os.environ.get('ITS_IMAP_PORT', '993'))
IMAP_USER = os.environ.get('ITS_IMAP_USER', '')
# Google shows app passwords in groups of four; the spaces are cosmetic and IMAP
# rejects them.
IMAP_APP_PASSWORD = os.environ.get('ITS_IMAP_APP_PASSWORD', '').replace(' ', '')
MAIL_FROM = os.environ.get('ITS_MAIL_FROM', 'noreply@mail.its-kenpo.or.jp')

# Must be the mailbox IMAP reads, or the confirmation link lands out of reach.
EMAIL = os.environ.get('ITS_EMAIL') or IMAP_USER or 'umeway1122@gmail.com'

APPLICANT = {
    'kigou': os.environ.get('ITS_KIGOU', ''),
    'bangou': os.environ.get('ITS_BANGOU', ''),
    'kana_sei': os.environ.get('ITS_KANA_SEI', ''),
    'kana_mei': os.environ.get('ITS_KANA_MEI', ''),
    # The live form has ONE 「カナ氏名」 box. Blank here derives "SEI　MEI".
    'kana_name': os.environ.get('ITS_KANA_NAME', ''),
    'name_sei': os.environ.get('ITS_NAME_SEI', ''),
    'name_mei': os.environ.get('ITS_NAME_MEI', ''),
    'birth': os.environ.get('ITS_BIRTH', ''),
    'sex': os.environ.get('ITS_SEX', ''),
    'zokugara': os.environ.get('ITS_ZOKUGARA', ''),
    'tel': os.environ.get('ITS_TEL', ''),
    # 事業所名, asked for by name (apply[office_name]).
    'office': os.environ.get('ITS_OFFICE_NAME', ''),
    'zip': os.environ.get('ITS_ZIP', ''),
    # 都道府県, matched against the dropdown by label, e.g. 東京都.
    'state': os.environ.get('ITS_STATE', ''),
    'addr': os.environ.get('ITS_ADDR', ''),
}

RESERVATIONS_FILE = os.path.join(_DIR, 'reservations.json')

# ── Debug dumps ──────────────────────────────────────────────────────
# Throttled and pruned, so a failure repeating every cycle cannot fill the disk.
DEBUG_DUMP_INTERVAL = 300   # seconds between dumps of the same label+step
DEBUG_DUMP_KEEP = 400       # max files retained in DEBUG_DIR

# ── Logging ──────────────────────────────────────────────────────────
# Every scan cycle logs, so a healthy day is ~4,100 "no dates available" lines and
# rotation is what bounds that.
LOG_MAX_BYTES = 32 * 1024 * 1024   # rotate the log past this size
LOG_BACKUPS = 3                    # keep this many rotated logs

# ── HTTP fingerprint ─────────────────────────────────────────────────
# On, curl replays carry the UA of the Chrome that solved the CAPTCHA; off, they
# identify as curl/8.x — an obvious mid-session client switch.
#
# Deliberately NOT sent: `Origin` (absent = Rails skips its origin check),
# `Sec-Fetch-*` / `sec-ch-ua` (one static value must contradict one of the two
# request classes we make), `Accept-Encoding` (no brotli/zstd here, and a decode
# failure yields an empty body). The TLS fingerprint is still curl's, so a browser UA
# is a UA/TLS mismatch.
BROWSER_HEADERS = True
ACCEPT = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
ACCEPT_LANGUAGE = 'ja-JP,ja;q=0.9'
# Used only until the first CAPTCHA solve records Chrome's real UA.
FALLBACK_USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
)

# ── Hotel priority ───────────────────────────────────────────────────
# Substrings attempted first, in this order; matched ignoring case and spacing, so
# 'NAGU' catches "NAGU 勝浦". About the race, not preference: hotels are booked
# sequentially at ~10 requests each, so anything named here jumps the queue.
PRIORITY_HOTELS = [
    'NAGU',
]

# ── Hotel skip list ──────────────────────────────────────────────────
# Normalized for case, entities and full-width spaces, then compared for **exact
# membership** — not a substring test. A facility the site spells differently from
# the roster silently never matches, and an unmatched skip entry books a hotel meant
# to be skipped: `service_group_select` says 「グランドメルキュール伊勢志摩」 where the
# roster says 「グランドメルキュール 伊勢志摩リゾート＆スパ」.
#
# ✓ = the exact string observed in `service_group_select`, so safe. The rest come
# from the roster pages (read 2026-08-19) and are unverified against live markup;
# check one by reading a 「Found N hotels: …」 line in the log.
#
# The whole roster is here for reference, commented where not skipped. Cutoffs
# (docs/SITE.md §1): 直営, ブルーベリーヒル勝浦, 日光千姫物語, 熱海後楽園ホテル and all
# 夏季/冬季 are D−4; other 通年 are D−10.
SKIP_HOTELS = [
    # ── 直営 (3) — D−4 ──────────────────────────────────────────────
    # "トスラブ箱根ビオーレ",                     # ✓
    # "トスラブ箱根和奏林",                       # ✓
    "トスラブ館山ルアーナ",                        # ✓

    # ── 通年 (24) — D−10 unless marked ──────────────────────────────
    # "ラビスタ熱海テラス",
    # "ホテルハーヴェスト鬼怒川",
    "鳴子温泉　湯元　吉祥",                        # ✓ full-width spaces on the site
    # "ホテルハーヴェスト那須",                    # ✓
    # "日光千姫物語",                             # D−4
    "草津温泉　ホテルヴィレッジ",                   # ✓ full-width space
    "伊香保温泉 ホテル天坊",
    # "ラビスタ横須賀観音崎テラス",                 # ✓
    "ホテルオークラ東京ベイ",                      # ✓
    # "リソルの森",                               # ✓
    "ブルーベリーヒル勝浦",                        # ✓ D−4
    "和倉温泉 あえの風",
    # "ラビスタ富士河口湖",                        # ✓
    "ホテルハーヴェスト斑尾",                      # ✓
    "ホテルハーヴェスト旧軽井沢",                   # ✓ roster writes it with a space
    # "熱海後楽園ホテル",                          # D−4
    "ホテルハーヴェスト伊東",                      # ✓
    "ホテルハーヴェスト浜名湖",
    "ホテル琵琶レイクオーツカ",
    "ホテル日航プリンセス京都",                     # ✓
    "ホテルハーヴェスト京都鷹峯",
    "ホテルハーヴェスト有馬六彩",
    "ホテルハーヴェスト南紀田辺",                   # ✓
    "ゆふいん山水館",

    # ── 夏季 (13) — D−4, none listed for a stay past 9/30 ───────────
    # "グランドメルキュール伊勢志摩",               # ✓ NOT the roster's longer name
    # "スパリゾートハワイアンズ モノリスタワー",
    # "フルーツパーク富士屋ホテル",                 # ✓
    # "定山渓 ゆらく草庵",                         # ✓ half-width space
    # "鎌倉パークホテル",
    # "NAGU 勝浦",           # ✓ in PRIORITY_HOTELS — skipping WINS, so leave commented
    # "プレジャーリゾート伊豆赤沢温泉",
    # "軽井沢マリオットホテル",
    "蓼科東急ホテル",                             # ✓ also 冬季
    # "高山グリーンホテル",
    "NASPAニューオータニ",                        # ✓ also 冬季
    # "アオアヲナルトリゾート",                     # ✓
    "ホテル日航アリビラ",                          # ✓ also 冬季

    # ── 冬季 (5) — D−4. Four overlap 夏季 above; only this one is unique ──
    "ラビスタ函館ベイANNEX",

    # No longer on any ITS roster (直営/通年/夏季/冬季/提携 all checked 2026-08-19).
    # Kept because skipping a facility that never appears costs nothing, while
    # dropping it would book the place if ITS re-contracts it.
    "ホテルハーヴェスト　スキージャム勝山",
]
