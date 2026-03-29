#!/usr/bin/env python3
"""Booking engine — scans calendar months and books all available hotels per date.

Pure booking logic only. Reads the calendar URL from calendar_url_cache.txt each
cycle. If the URL is missing or expired, it simply waits for the next cycle
(the URL monitor in main.py handles CAPTCHA solving separately).
"""
import subprocess, re, urllib.parse, os, json, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from config import (
    CALENDAR_URL_CACHE, TARGET_DATES, EMAIL, NUM_GUESTS,
    BOOKINGS_FILE, RETRY_DELAY, CURL_MAX_ATTEMPTS, SKIP_HOTELS,
)

BASE = 'https://as.its-kenpo.or.jp'

# ANSI colors
R = '\033[91m'   # red
G = '\033[92m'   # green
Y = '\033[93m'   # yellow
C = '\033[96m'   # cyan
B = '\033[1m'    # bold
X = '\033[0m'    # reset


_log_handler = None  # Set externally for display routing

def log(msg=''):
    ts = datetime.now().strftime('%H:%M:%S')
    formatted = f'{ts} {msg}'
    if _log_handler:
        _log_handler(formatted)
    else:
        print(formatted, flush=True)


# Thread-safe bookings access
_bookings_lock = threading.Lock()


def _read_cached_url():
    """Read the current calendar URL from cache file."""
    try:
        url = open(CALENDAR_URL_CACHE).read().strip()
        return url or None
    except FileNotFoundError:
        return None


def _load_bookings():
    if not os.path.exists(BOOKINGS_FILE):
        return {}
    with open(BOOKINGS_FILE, 'r', encoding='utf-8') as f:
        content = f.read().strip()
        return json.loads(content) if content else {}


def save_booking(date, hotel_name):
    with _bookings_lock:
        bookings = _load_bookings()
        if date not in bookings:
            bookings[date] = []
        if hotel_name not in bookings[date]:
            bookings[date].append(hotel_name)
            with open(BOOKINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(bookings, f, ensure_ascii=False, indent=2)


def get_booked_hotels(date):
    with _bookings_lock:
        return _load_bookings().get(date, [])


def curl(cookie_file, method, url, data=None, headers=None):
    cmd = ['curl', '-s', '-c', cookie_file, '-b', cookie_file,
           '-D', '/dev/stderr', '--max-redirs', '0', '--max-time', '30']
    if method == 'POST':
        cmd.extend(['-X', 'POST'])
    if headers:
        for k, v in headers.items():
            cmd.extend(['-H', f'{k}: {v}'])
    if data:
        for k, v in data.items():
            cmd.extend(['--data-urlencode', f'{k}={v}'])
    cmd.append(url)
    for attempt in range(CURL_MAX_ATTEMPTS):
        r = subprocess.run(cmd, capture_output=True, text=True)
        body = r.stdout
        hdrs = r.stderr
        st = re.findall(r'HTTP/\S+ (\d+)', hdrs)
        status = int(st[-1]) if st else 0
        if (status != 0 and status < 500) or attempt + 1 == CURL_MAX_ATTEMPTS:
            break
        log(f"  {Y}curl {method} failed ({status}), retrying...{X}")
    loc = re.search(r'location: (.+)', hdrs, re.IGNORECASE)
    location = loc.group(1).strip() if loc else None
    return status, body, location


def ex(html, pat):
    m = re.search(pat, html)
    return m.group(1) if m else None


def book_one_hotel(tag, c, target_date, s_param, auth, hotel_id, hotel_name):
    """Book a single hotel for a date. Steps 3-9. Returns True on success."""
    # STEP 3: Select hotel
    log(f"{tag} {C}Booking: {hotel_name}{X}")
    s, body, _ = c('POST', BASE + '/calendar_apply/apply_service_select',
        {'utf8': '\u2713', 'authenticity_token': auth, 'empty': '',
         'join_time': target_date, 's': s_param, 'service_group_id': hotel_id})
    services = re.findall(r'data-apply-service-id="(\d+)".*?>(.*?)</a>', body)
    if not services:
        log(f"{tag}   {R}No services for {hotel_name}{X}")
        return False
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')

    # STEP 4: Select service (302)
    service_id = services[0][0]
    s, body, loc = c('POST', BASE + '/calendar_apply/check_apply_service_coma',
        {'utf8': '\u2713', 'authenticity_token': auth,
         'join_time': target_date, 's': s_param, 'apply_service_id': service_id})
    if not loc or 'empty_new' not in loc:
        log(f"{tag}   {R}Step 4 redirect failed{X}")
        return False

    # STEP 5: Load booking form
    referer_url = loc
    s, body, _ = c('GET', loc)
    csrf = ex(body, r'csrf-token.*?content="(.*?)"')
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')
    form_action = ex(body, r'action="(/apply/empty_create\?s=[^"]+)"')
    coma_s = ex(body, r"coma_search\('([^']+)'\)")

    # STEP 6: Search rooms
    s, body, _ = c('POST',
        BASE + '/apply/empty_new?s=' + urllib.parse.quote(coma_s, safe=''),
        {'utf8': '\u2713', 'authenticity_token': auth,
         'apply[join_time]': target_date, 'apply[night_count]': '1',
         'apply[stay_persons]': NUM_GUESTS, 'apply[hope_rooms]': '1'},
        {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
         'Accept': 'text/javascript, application/javascript, */*; q=0.01',
         'Referer': referer_url})
    if 'service_category' in body:
        log(f"{tag}   {R}Session expired at room search{X}")
        return False
    rooms = re.findall(r'name=\\"apply\[coma\[(\d+)\]\]\\".*?value=\\"(\d+)\\"', body)
    guid = ex(body, r'apply_session_guid.*?value=\\"([^"\\]+)\\"')
    if not rooms:
        log(f"{tag}   {R}No rooms available{X}")
        return False
    log(f"{tag}   {C}{len(rooms)} rooms -> selecting room{X}")

    # STEP 7: Submit room
    room_id = rooms[0][0]
    s, body, loc = c('POST', BASE + form_action,
        {'utf8': '\u2713', 'authenticity_token': auth,
         'apply[join_time]': target_date, 'apply[night_count]': '1',
         'apply[stay_persons]': NUM_GUESTS, 'apply[hope_rooms]': '1',
         'apply_session_guid': guid, f'apply[coma[{room_id}]]': room_id},
        {'Referer': referer_url})
    if s == 302 and loc:
        s, body, _ = c('GET', loc)

    # STEP 8: Agree to rules
    if '\u540c\u610f' not in body:
        log(f"{tag}   {R}Not on rules page{X}")
        return False
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')
    form_act = ex(body, r'<form[^>]*action="([^"]*)"[^>]*method="post"')
    s_rule_m = re.search(r'name="s"[^>]*value="([^"]*)"', body)
    s_rule = s_rule_m.group(1) if s_rule_m else None
    rule_url = BASE + form_act if form_act else None
    post_data = {'utf8': '\u2713', 'authenticity_token': auth}
    if s_rule:
        post_data['s'] = s_rule
    s, body, loc = c('POST', rule_url, post_data)
    if s == 302 and loc:
        s, body, _ = c('GET', loc)

    # STEP 9: Submit email
    if 'email' not in body.lower():
        log(f"{tag}   {R}Not on email page{X}")
        return False
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')
    form_act = ex(body, r'<form[^>]*action="([^"]*)"[^>]*method="post"')
    token_field = ex(body, r'name="__token__"[^>]*value="([^"]*)"')
    email_url = BASE + form_act if form_act else None
    post_data = {
        'utf8': '\u2713', 'authenticity_token': auth,
        'email': EMAIL, 'commit': '\u9001\u4fe1',
    }
    if token_field:
        post_data['__token__'] = token_field
    s, body, loc = c('POST', email_url, post_data)
    if s == 302 and loc:
        s, body, _ = c('GET', loc)

    if 'send_complete' in body:
        log(f"{tag}   {B}{G}BOOKED: {hotel_name}{X}")
        save_booking(target_date, hotel_name)
        return True

    log(f"{tag}   {R}Final page not send_complete{X}")
    return False


def book_all_hotels_for_date(target_date, label):
    """Book all available hotels for a single date (single attempt).

    Reads URL from cache. If URL is missing or expired, returns immediately.
    Returns (date, list_of_booked_hotels).
    """
    url = _read_cached_url()
    if not url:
        return target_date, []

    tag = f"[{label}]"
    booked = []

    cookie_fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix=f'cookies_{target_date}_')
    os.close(cookie_fd)
    open(cookie_file, 'w').close()

    def c(method, url, data=None, headers=None):
        return curl(cookie_file, method, url, data, headers)

    try:
        # STEP 1: Load calendar
        s, body, _ = c('GET', url)
        if s != 200:
            log(f"{tag} {Y}URL expired ({s}), skipping{X}")
            return target_date, booked
        csrf = ex(body, r'csrf-token.*?content="(.*?)"')
        auth = ex(body, r'name="authenticity_token" value="(.*?)"')
        s_param = ex(body, r'name="s" id="s" value="(.*?)"')

        # Navigate to target month if needed
        if f'data-join-time="{target_date}"' not in body:
            target_ym = f"{target_date[:4]}-{target_date[5:7]}-01"
            _, body_nav, _ = c('POST', BASE + '/calendar_apply/calendar_select',
                {'join_date': target_ym, 's': s_param},
                {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
                 'Accept': 'text/javascript, application/javascript, */*; q=0.01',
                 'Referer': url})
            cls = ex(body_nav, rf'class=\\"([^"\\]*)\\\"[^>]*data-join-time=\\"{target_date}\\"') or ''
            if 'empty' not in cls:
                log(f"{tag} {Y}date not available{X}")
                return target_date, booked

        # STEP 2: Select date -> get hotel list
        s, body, _ = c('POST', BASE + '/calendar_apply/service_group_select',
            {'utf8': '\u2713', 'authenticity_token': auth,
             'join_time': target_date, 's': s_param})
        all_hotels = re.findall(r'data-service-group-id="(\d+)".*?>(.*?)</a>', body)
        if not all_hotels:
            log(f"{tag} {Y}no hotels listed{X}")
            return target_date, booked
        auth = ex(body, r'name="authenticity_token" value="(.*?)"')

        # Filter: skip list + already booked
        already_booked = get_booked_hotels(target_date)
        hotels = [(gid, name) for gid, name in all_hotels
                  if name not in SKIP_HOTELS and name not in already_booked]

        if not hotels:
            return target_date, booked

        log(f"{tag} {C}{len(hotels)} to book: {', '.join(n for _, n in hotels)}{X}")

        # Book each hotel
        for i, (hotel_id, hotel_name) in enumerate(hotels):
            if i > 0:
                # Fresh session for next hotel
                open(cookie_file, 'w').close()
                s, body, _ = c('GET', url)
                if s != 200:
                    log(f"{tag} {Y}URL expired during hotel loop, stopping{X}")
                    break
                csrf = ex(body, r'csrf-token.*?content="(.*?)"')
                auth = ex(body, r'name="authenticity_token" value="(.*?)"')
                s_param = ex(body, r'name="s" id="s" value="(.*?)"')

                if f'data-join-time="{target_date}"' not in body:
                    target_ym = f"{target_date[:4]}-{target_date[5:7]}-01"
                    c('POST', BASE + '/calendar_apply/calendar_select',
                        {'join_date': target_ym, 's': s_param},
                        {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
                         'Accept': 'text/javascript, application/javascript, */*; q=0.01',
                         'Referer': url})

                s, body, _ = c('POST', BASE + '/calendar_apply/service_group_select',
                    {'utf8': '\u2713', 'authenticity_token': auth,
                     'join_time': target_date, 's': s_param})
                auth = ex(body, r'name="authenticity_token" value="(.*?)"')

            success = book_one_hotel(tag, c, target_date, s_param, auth,
                                    hotel_id, hotel_name)
            if success:
                booked.append(hotel_name)
                log(f"{tag} {B}{G}=== Total booked for {target_date}: {len(booked)} ({', '.join(booked)}){X}")

        return target_date, booked

    finally:
        os.unlink(cookie_file)


def scan_and_book_month(month_str, target_dates, label):
    """Scan a month's calendar for availability, spawn booking threads per date.

    Runs indefinitely. Each cycle: 1 GET + 1 POST checks ALL target dates.
    When availability is found, spawns parallel booking threads (one per date).
    If URL is missing or expired, logs and waits for next cycle.
    """
    tag = f"[{label}]"
    month_ym = f"{month_str}-01"

    cookie_fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix=f'cookies_scan_{month_str}_')
    os.close(cookie_fd)
    open(cookie_file, 'w').close()

    def c(method, url, data=None, headers=None):
        return curl(cookie_file, method, url, data, headers)

    try:
        attempt = 0
        while True:
            attempt += 1
            if attempt > 1:
                time.sleep(RETRY_DELAY)

            # Get current URL from cache
            url = _read_cached_url()
            if not url:
                log(f"{tag} {Y}[{attempt}] no URL available, waiting...{X}")
                continue

            # SCAN: Load calendar (1 GET)
            open(cookie_file, 'w').close()
            s, body, _ = c('GET', url)
            if s != 200:
                log(f"{tag} {Y}[{attempt}] URL returned {s}, waiting...{X}")
                continue
            csrf = ex(body, r'csrf-token.*?content="(.*?)"')
            s_param = ex(body, r'name="s" id="s" value="(.*?)"')

            # SCAN: Navigate to target month (1 POST)
            _, body_nav, _ = c('POST', BASE + '/calendar_apply/calendar_select',
                {'join_date': month_ym, 's': s_param},
                {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
                 'Accept': 'text/javascript, application/javascript, */*; q=0.01',
                 'Referer': url})

            # Check all target dates for availability
            available = []
            for td in target_dates:
                cls = ex(body_nav, rf'class=\\"([^"\\]*)\\\"[^>]*data-join-time=\\"{td}\\"') or ''
                if 'empty' in cls:
                    available.append(td)

            if not available:
                log(f"{tag} {Y}[{attempt}] no dates available ({len(target_dates)} checked), waiting...{X}")
                continue

            log(f"{tag} {C}[{attempt}] {len(available)}/{len(target_dates)} dates available: {', '.join(d[5:] for d in available)}{X}")

            # BOOK: Spawn parallel threads, one per available date
            with ThreadPoolExecutor(max_workers=len(available)) as pool:
                futures = {}
                for td in available:
                    dlabel = f"{label} {td[5:]}"
                    futures[pool.submit(book_all_hotels_for_date, td, dlabel)] = td

                for future in as_completed(futures):
                    td, booked_list = future.result()
                    if booked_list:
                        log(f"{tag} {G}Booked for {td}: {', '.join(booked_list)}{X}")

    finally:
        os.unlink(cookie_file)
