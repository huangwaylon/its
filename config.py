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
SCAN_INTERVAL_SECONDS = 5
NUM_MONTHS_TO_SKIP = 1  # Number of months to skip from current month before scanning
NUM_MONTHS_TO_SCAN = 1

# Target weekdays (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun)
TARGET_WEEKDAYS = [5]  # Sat

# Holiday booking configuration
INCLUDE_HOLIDAYS = True  # Check for national holidays (Fridays and Sundays before Mondays)

# Date skip list (YYYY-MM-DD format for specific dates)
DATE_SKIP_LIST = [
    "2025-12-13",
    "2025-12-20",
    "2025-12-27",
    "2026-01-24"
]

# Exceptional dates to book (creates extended weekend breaks)
EXCEPTIONAL_DATES = {
    '2026-01-02': 'Apple Holiday Shutdown',
    '2026-01-11': 'Coming of Age Day',
    '2026-02-22': "Emperor's Birthday",
    '2026-03-20': 'Spring Equinox',
    '2026-05-03': 'Greenery Day',
    '2026-05-04': 'Childrens Day',
    '2026-05-05': 'Constitution Day',
    '2026-07-19': 'Marine Day',
    '2026-09-20': 'Respect-for-the-Aged Day',
    '2026-09-21': 'National Day',
    '2026-09-22': 'Autumn Equinox',
    '2026-10-11': 'Sports Day',
    '2026-11-22': 'Labour Thanksgiving Day',
}

# Weekday names mapping
WEEKDAY_NAMES = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday"
}

# Booking mode
AUTO_BOOK = True

# Hotel filtering
SKIP_HOTELS = [
    "ブルーベリーヒル勝浦"
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
MONTH_NAV_POLL_ATTEMPTS = 5         # Verification polling attempts (5 × 0.3s = 1.5s max)
MONTH_NAV_POLL_INTERVAL = 0.3       # Time between polls

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
