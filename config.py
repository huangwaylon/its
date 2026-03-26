# -*- coding: utf-8 -*-
"""Configuration constants for ITS Calendar Scanner."""

# File paths
CALENDAR_URL_CACHE = "calendar_url_cache.txt"
CALENDAR_URL_HISTORY = "calendar_url_history.csv"
BOOKINGS_FILE = "bookings.json"

# URLs
MAIN_URL = "https://as.its-kenpo.or.jp"

# User configuration
TARGET_EMAIL = "wwaylonhuang@gmail.com"
NUM_GUESTS = 2

# Scanning configuration
SCAN_INTERVAL_SECONDS = 0
NUM_MONTHS_TO_SKIP = 1  # Number of months to skip from current month before scanning
NUM_MONTHS_TO_SCAN = 1

# Target dates to check for availability (YYYY-MM-DD format)
# Add specific dates you want to monitor and book
TARGET_DATES = [
    "2026-04-13",
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

# Weekday names mapping
WEEKDAY_NAMES = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}

# Booking mode
AUTO_BOOK = True

# Hotel filtering
SKIP_HOTELS = [
    # "ブルーベリーヒル勝浦",
    # "ホテル日航プリンセス京都",
    # "ホテルハーヴェスト南紀田辺",
    # "草津温泉　ホテルヴィレッジ",
    # "ホテルハーヴェスト伊東",
    # "ホテルハーヴェスト　スキージャム勝山",
    # "ホテル琵琶レイクオーツカ",
    # "ホテルハーヴェスト南紀田辺",
    # "ホテルハーヴェスト有馬六彩",
    # "リソルの森",
    # "ホテルハーヴェスト浜名湖",
    # "ゆふいん山水館",
    # "ホテル日航アリビラ",
    # "ラビスタ函館ベイANNEX",

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
    # "鳴子温泉　湯元　吉祥",
    # "ホテルオークラ東京ベイ",
    # "熱海後楽園ホテル",
    # "ラビスタ横須賀観音崎テラス",
    # "ラビスタ熱海テラス",
    # "ホテルハーヴェスト鬼怒川",
    # "蓼科東急ホテル",
    # "NASPAニューオータニ",
    # "NAGU 勝浦",
]

# Chrome browser arguments
CHROME_ARGUMENTS = [
    "-no-first-run",
    "-force-color-profile=srgb",
    "-metrics-recording-only",
    "-password-store=basic",
    "-use-mock-keychain",
    "-export-tagged-pdf",
    "-no-default-browser-check",
    "-disable-background-mode",
    "-enable-features=NetworkService,NetworkServiceInProcess",
    "-disable-features=FlashDeprecationWarning",
    "-deny-permission-prompts",
    "-accept-lang=ja-JP",
    "--disable-usage-stats",
    "--disable-crash-reporter",
]

# Timeouts (seconds)
BROWSER_START_TIMEOUT = 30
DEFAULT_TIMEOUT = 3
EXTENDED_TIMEOUT = 5

# Sleep durations (seconds)
SLEEP_SHORT = 0.2
SLEEP_STANDARD = 0.5
SLEEP_MONTH_NAV = 0.5  # Reduced from 2.0s, with polling for page load

# Month navigation polling configuration
MONTH_NAV_POLL_ATTEMPTS = 10  # Verification polling attempts (5 × 0.3s = 1.5s max)
MONTH_NAV_POLL_INTERVAL = 0.3  # Time between polls

# HTML selectors and attributes
TAG_INPUT = "input"
TAG_ANCHOR = "a"
TAG_TD = "td"
TAG_PARAGRAPH = "p"
CLASS_MONTH = "month"
ATTR_DATA_JOIN_TIME = "data-join-time"
ID_NEXT_MONTH = "nextMonth"

# Input field names
INPUT_NAME_STAY_PERSONS = "stay_persons"
INPUT_NAME_EMAIL = "email"
INPUT_NAME_NO_NAME = "no-name"
INPUT_NAME_ROOM_PREFIX = "apply[coma["

# JavaScript selectors
RECAPTCHA_IFRAME_SELECTOR = 'iframe[src*="recaptcha/api2/anchor"]'
FORM_SUBMIT_SCRIPT = "document.querySelector('form').submit();"
WINDOW_LOCATION_SCRIPT = "return window.location.href"

# URL path components
URL_CALENDAR_APPLY = "calendar_apply"
URL_CALENDAR_SELECT = "calendar_select"
URL_SERVICE_GROUP_SELECT = "service_group_select"
URL_APPLY_SERVICE_SELECT = "apply_service_select"
URL_APPLY_EMPTY_NEW = "apply/empty_new"
URL_APPLY_RULE = "apply/rule"
URL_APPLY_EMAIL_INPUT = "apply/email_input"
URL_SEND_COMPLETE = "send_complete"

# URL protocols
PROTOCOL_JAVASCRIPT = "javascript:"
PROTOCOL_HTTP = "http://"
PROTOCOL_HTTPS = "https://"

# Japanese UI text
TEXT_CALENDAR_SEARCH = "カレンダーから探す"
TEXT_NEXT_BUTTON = "次へ"
TEXT_NEXT_MONTH = "翌月"
TEXT_SEARCH_AVAILABILITY = "空き検索"
TEXT_PROCEED_TO_BOOKING = "予約手続きに進む"
TEXT_AGREE = "同意"
TEXT_SUBMIT = "送信"

# Skip link texts
SKIP_LINK_TEXTS = ["ページ先頭", "関東ITソフトウェア", "健康保険組合", "公式サイト"]
SKIP_LINK_TEXTS_SERVICE = ["ページ先頭", "関東ITソフトウェア", "健康保険組合"]

# Status indicators
ICON_AVAILABLE = "○"
STATUS_AVAILABLE = "Available"
STATUS_FULL = "Full"
STATUS_UNKNOWN = "Unknown"

# Display constants
SEPARATOR_WIDTH = 60
URL_TRUNCATE_LENGTH = 80
TEXT_TRUNCATE_LENGTH = 50
MIN_LINK_TEXT_LENGTH = 3
SCROLL_DOWN_DISTANCE = 100
SCROLL_UP_DISTANCE = 50

# Logging symbols
LOG_ARROW = "→"
LOG_SUCCESS = "✓"
LOG_ERROR = "✗"
LOG_WARNING = "⚠"
LOG_SKIP = "⊗"
LOG_SEPARATOR = "─"
LOG_EQUALS = "="

# ANSI color codes
COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_RESET = "\033[0m"
