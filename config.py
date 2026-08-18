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

# ── Network tuning ───────────────────────────────────────────────────
RETRY_DELAY = 20          # seconds between scan retry attempts
CURL_MAX_ATTEMPTS = 2     # max attempts per curl request (1 = no retry)
URL_CHECK_INTERVAL = 60   # seconds between URL validity checks
URL_REFRESH_INTERVAL = 1800  # seconds between proactive URL refreshes

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

# ── Hotel skip list ──────────────────────────────────────────────────
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
    "ホテルオークラ東京ベイ",
    "NASPAニューオータニ",


    "トスラブ館山ルアーナ",
    # ------------------------- Keep -------------------------
    # "リソルの森",
    # "トスラブ箱根ビオーレ",
    # "トスラブ箱根和奏林",
    # "ホテルハーヴェスト那須",
    "ホテルハーヴェスト斑尾",
    "ホテルハーヴェスト旧軽井沢",
    # "日光千姫物語",
    "伊香保温泉 ホテル天坊",
    "和倉温泉 あえの風",
    # "ラビスタ富士河口湖",
    "鳴子温泉　湯元　吉祥",
    # "ホテルオークラ東京ベイ",
    # "熱海後楽園ホテル",
    # "ラビスタ横須賀観音崎テラス",
    # "ラビスタ熱海テラス",
    # "ホテルハーヴェスト鬼怒川",
    "蓼科東急ホテル",
    # "NAGU 勝浦",
]
