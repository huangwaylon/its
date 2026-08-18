"""User-configurable settings for the ITS Calendar Booker."""
import os

# ── Paths ────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
CALENDAR_URL_CACHE = os.path.join(_DIR, 'calendar_url_cache.txt')
BOOKINGS_FILE = os.path.join(_DIR, 'bookings.json')
LOG_FILE = os.path.join(_DIR, 'its_booking.log')
DEBUG_DIR = os.path.join(_DIR, 'debug_responses')
USER_AGENT_CACHE = os.path.join(_DIR, 'chrome_user_agent.txt')

# ── Booking settings ─────────────────────────────────────────────────
TARGET_DATES = [
    "2026-08-22",
    "2026-08-29",
    "2026-09-05",
    "2026-09-19",
    "2026-09-20",
    "2026-09-21",
    "2026-09-22",
    "2026-09-26",
]
EMAIL = 'wwaylonhuang@gmail.com'
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

# Consecutive scan failures multiply RETRY_DELAY up to this ceiling, so a site
# outage is not met with the same request rate for hours.
SCAN_BACKOFF_MAX = 300
# Random 0..N seconds added to each scan sleep. Without it the per-month
# scanners settle into lockstep and hit the site in bursts.
SCAN_JITTER = 5

# Hard ceiling on one CAPTCHA solve. pydoll/Chrome can hang, and the solve runs
# synchronously in the URL monitor thread — the only thing that re-mints a
# session. An untimed hang there stops all booking while the process still looks
# healthy and the display keeps refreshing.
CAPTCHA_TIMEOUT = 180

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
# This is about the race, not about preference. Hotels are booked sequentially
# and one hotel is ~7 requests, so in the site's own ordering a hotel listed
# last is attempted tens of seconds after the slot was spotted — long enough to
# lose it. Anything named here jumps the queue.
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

# ── Keep (reference only — these are NOT skipped) ────────────────────
# Kept as a comment so the intent behind the skip list above stays readable.
# Nothing here is code; anything not in SKIP_HOTELS is eligible to be booked,
# including hotels the site adds that appear on neither list.
#   NAGU 勝浦                  ← see PRIORITY_HOTELS
#   リソルの森
#   トスラブ箱根ビオーレ
#   トスラブ箱根和奏林
#   ホテルハーヴェスト那須
#   日光千姫物語
#   ラビスタ富士河口湖
#   熱海後楽園ホテル
#   ラビスタ横須賀観音崎テラス
#   ラビスタ熱海テラス
#   ホテルハーヴェスト鬼怒川
