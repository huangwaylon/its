"""User-configurable settings for the ITS Calendar Booker."""
import os

# ── Paths ────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
CALENDAR_URL_CACHE = os.path.join(_DIR, 'calendar_url_cache.txt')
BOOKINGS_FILE = os.path.join(_DIR, 'bookings.json')
LOG_FILE = os.path.join(_DIR, 'its_booking.log')

# ── Booking settings ─────────────────────────────────────────────────
TARGET_DATES = [
    "2026-04-18",
    "2026-04-29",
    "2026-04-30",
    "2026-05-01",
    "2026-05-02",
    "2026-05-03",
    "2026-05-04",
    "2026-05-05",
    "2026-05-06",
    "2026-05-09",
    "2026-05-16",
    "2026-05-23",
]
EMAIL = 'wwaylonhuang@gmail.com'
NUM_GUESTS = '2'

# ── Network tuning ───────────────────────────────────────────────────
RETRY_DELAY = 20          # seconds between scan retry attempts
CURL_MAX_ATTEMPTS = 2     # max attempts per curl request (1 = no retry)
URL_CHECK_INTERVAL = 60   # seconds between URL validity checks
URL_REFRESH_INTERVAL = 1  # seconds between proactive URL refreshes

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
    "リソルの森",
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

    # ------------------------- Keep -------------------------
    # "トスラブ箱根ビオーレ",
    # "トスラブ箱根和奏林",
    # "トスラブ館山ルアーナ",
    # "ホテルハーヴェスト那須",
    # "ホテルハーヴェスト斑尾",
    # "ホテルハーヴェスト旧軽井沢",
    # "ホテルハーヴェスト京都鷹峯",
    # "日光千姫物語",
    # "伊香保温泉 ホテル天坊",
    # "和倉温泉 あえの風",
    # "ラビスタ富士河口湖",
    # "鳴子温泉　湯元　吉祥",
    # "ホテルオークラ東京ベイ",
    # "熱海後楽園ホテル",
    # "ラビスタ横須賀観音崎テラス",
    # "ラビスタ熱海テラス",
    # "ホテルハーヴェスト鬼怒川",
    # "蓼科東急ホテル",
    # "NASPAニューオータニ",
    # "NAGU 勝浦",
]
