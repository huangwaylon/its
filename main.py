#!/usr/bin/env python3
"""Parallel curl booking for multiple dates -- books all available hotels per date."""
import subprocess, re, urllib.parse, sys, os, json, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = 'https://as.its-kenpo.or.jp'
try:
    CALENDAR_URL = open('calendar_url_cache.txt').read().strip()
except FileNotFoundError:
    CALENDAR_URL = None

# ANSI colors
R = '\033[91m'   # red
G = '\033[92m'   # green
Y = '\033[93m'   # yellow
C = '\033[96m'   # cyan
B = '\033[1m'    # bold
X = '\033[0m'    # reset
TARGET_DATES = [
    "2026-04-18",
    "2026-04-29",
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
BOOKINGS_FILE = 'bookings.json'
RETRY_DELAY = 10  # seconds
SKIP_HOTELS = [
    "ブルーベリーヒル勝浦",
    "ホテル日航プリンセス京都",
    "ホテルハーヴェスト南紀田辺",
    "草津温泉　ホテルヴィレッジ",
    "ホテルハーヴェスト伊東",
    "ホテルハーヴェスト　スキージャム勝山",
    "ホテル琵琶レイクオーツカ",
    "ホテルハーヴェスト南紀田辺",
    "ホテルハーヴェスト有馬六彩",
    "リソルの森",
    "ホテルハーヴェスト浜名湖",
    "ゆふいん山水館",
    "ホテル日航アリビラ",
    "ラビスタ函館ベイANNEX",
    "ホテルハーヴェスト斑尾",
    "ホテルハーヴェスト京都鷹峯",
    "和倉温泉 あえの風",
    "鳴子温泉　湯元　吉祥",
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

# Thread-safe bookings access
_bookings_lock = threading.Lock()


def load_bookings():
    if not os.path.exists(BOOKINGS_FILE):
        return {}
    with open(BOOKINGS_FILE, 'r', encoding='utf-8') as f:
        content = f.read().strip()
        return json.loads(content) if content else {}


def save_booking(date, hotel_name):
    with _bookings_lock:
        bookings = load_bookings()
        if date not in bookings:
            bookings[date] = []
        if hotel_name not in bookings[date]:
            bookings[date].append(hotel_name)
            with open(BOOKINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(bookings, f, ensure_ascii=False, indent=2)


def get_booked_hotels(date):
    with _bookings_lock:
        return load_bookings().get(date, [])


def curl(cookie_file, method, url, data=None, headers=None):
    cmd = ['curl', '-s', '-c', cookie_file, '-b', cookie_file,
           '-D', '/dev/stderr', '--max-redirs', '0']
    if method == 'POST':
        cmd.extend(['-X', 'POST'])
    if headers:
        for k, v in headers.items():
            cmd.extend(['-H', f'{k}: {v}'])
    if data:
        for k, v in data.items():
            cmd.extend(['--data-urlencode', f'{k}={v}'])
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True)
    body = r.stdout
    hdrs = r.stderr
    st = re.findall(r'HTTP/\S+ (\d+)', hdrs)
    status = int(st[-1]) if st else 0
    loc = re.search(r'location: (.+)', hdrs, re.IGNORECASE)
    location = loc.group(1).strip() if loc else None
    return status, body, location


def ex(html, pat):
    m = re.search(pat, html)
    return m.group(1) if m else None


def book_one_hotel(tag, c, target_date, s_param, auth, hotel_id, hotel_name):
    """Book a single hotel for a date. Steps 3-9. Returns True on success."""
    # STEP 3: Select hotel
    print(f"{tag} {C}Booking: {hotel_name}{X}")
    s, body, _ = c('POST', BASE + '/calendar_apply/apply_service_select',
        {'utf8': '\u2713', 'authenticity_token': auth, 'empty': '',
         'join_time': target_date, 's': s_param, 'service_group_id': hotel_id})
    services = re.findall(r'data-apply-service-id="(\d+)".*?>(.*?)</a>', body)
    if not services:
        print(f"{tag}   {R}No services for {hotel_name}{X}")
        return False
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')

    # STEP 4: Select service (302)
    service_id = services[0][0]
    s, body, loc = c('POST', BASE + '/calendar_apply/check_apply_service_coma',
        {'utf8': '\u2713', 'authenticity_token': auth,
         'join_time': target_date, 's': s_param, 'apply_service_id': service_id})
    if not loc or 'empty_new' not in loc:
        print(f"{tag}   {R}Step 4 redirect failed{X}")
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
        print(f"{tag}   {R}Session expired at room search{X}")
        return False
    rooms = re.findall(r'name=\\"apply\[coma\[(\d+)\]\]\\".*?value=\\"(\d+)\\"', body)
    guid = ex(body, r'apply_session_guid.*?value=\\"([^"\\]+)\\"')
    if not rooms:
        print(f"{tag}   {R}No rooms available{X}")
        return False
    print(f"{tag}   {C}{len(rooms)} rooms -> selecting room{X}")

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
        print(f"{tag}   {R}Not on rules page{X}")
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
        print(f"{tag}   {R}Not on email page{X}")
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
        print(f"{tag}   {B}{G}BOOKED: {hotel_name}{X}")
        save_booking(target_date, hotel_name)
        return True

    print(f"{tag}   {R}Final page not send_complete{X}")
    return False


def book_all_hotels_for_date(target_date, label, calendar_url=None):
    """Loop through all available hotels for a date, booking each one.
    Retries indefinitely until URL expires.
    Returns (date, list_of_booked_hotels, expired)."""
    url = calendar_url or CALENDAR_URL
    tag = f"[{label}]"
    booked = []
    expired = False

    cookie_fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix=f'cookies_{target_date}_')
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

            # STEP 1: Load calendar
            open(cookie_file, 'w').close()
            s, body, _ = c('GET', url)
            if s != 200:
                print(f"{tag} {R}FATAL: token expired ({s}), stopping{X}")
                expired = True
                return target_date, booked, expired
            csrf = ex(body, r'csrf-token.*?content="(.*?)"')
            auth = ex(body, r'name="authenticity_token" value="(.*?)"')
            s_param = ex(body, r'name="s" id="s" value="(.*?)"')

            # Navigate to target month if needed
            if f'data-join-time="{target_date}"' not in body:
                target_ym = f"{target_date[:4]}-{target_date[5:7]}-01"
                s2, body_nav, _ = c('POST', BASE + '/calendar_apply/calendar_select',
                    {'join_date': target_ym, 's': s_param},
                    {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
                     'Accept': 'text/javascript, application/javascript, */*; q=0.01',
                     'Referer': url})
                cls = ex(body_nav, rf'class=\\"([^"\\]*)\\"[^>]*data-join-time=\\"{target_date}\\"') or ''
                if 'empty' not in cls:
                    print(f"{tag} {Y}[{attempt}] date not available, waiting...{X}")
                    continue

            # STEP 2: Select date -> get hotel list
            s, body, _ = c('POST', BASE + '/calendar_apply/service_group_select',
                {'utf8': '\u2713', 'authenticity_token': auth,
                 'join_time': target_date, 's': s_param})
            all_hotels = re.findall(r'data-service-group-id="(\d+)".*?>(.*?)</a>', body)
            if not all_hotels:
                print(f"{tag} {Y}[{attempt}] no hotels listed, waiting...{X}")
                continue
            auth = ex(body, r'name="authenticity_token" value="(.*?)"')

            # Filter: skip list + already booked
            already_booked = get_booked_hotels(target_date)
            hotels = []
            for gid, name in all_hotels:
                if name in SKIP_HOTELS:
                    pass
                elif name in already_booked:
                    pass
                else:
                    hotels.append((gid, name))

            if not hotels:
                if attempt == 1:
                    skipped = [n for _, n in all_hotels if n in SKIP_HOTELS]
                    prev_booked = [n for _, n in all_hotels if n in already_booked]
                    if skipped:
                        print(f"{tag} {Y}Skipped: {', '.join(skipped)}{X}")
                    if prev_booked:
                        print(f"{tag} {Y}Already booked: {', '.join(prev_booked)}{X}")
                print(f"{tag} {Y}[{attempt}] nothing new to book, waiting...{X}")
                continue

            print(f"{tag} {C}[{attempt}] {len(hotels)} to book: {', '.join(n for _, n in hotels)}{X}")

            # Loop through each hotel
            for i, (hotel_id, hotel_name) in enumerate(hotels):
                if i > 0:
                    # Fresh session for next hotel
                    open(cookie_file, 'w').close()
                    s, body, _ = c('GET', url)
                    if s != 200:
                        print(f"{tag} {R}Calendar reload failed, stopping hotel loop{X}")
                        expired = True
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
                    print(f"{tag} {B}{G}=== Total booked for {target_date}: {len(booked)} ({', '.join(booked)}){X}")

            if expired:
                return target_date, booked, expired

    finally:
        os.unlink(cookie_file)


def main():
    if not CALENDAR_URL:
        print(f"{R}ERROR: calendar_url_cache.txt not found. Run captcha_solver.py first.{X}")
        sys.exit(1)
    print(f"{B}Booking {len(TARGET_DATES)} dates in parallel: {', '.join(TARGET_DATES)}{X}")
    print(f"Email: {EMAIL}")
    print(f"Calendar URL: {CALENDAR_URL[:60]}...")
    print("=" * 60)

    with ThreadPoolExecutor(max_workers=len(TARGET_DATES)) as pool:
        futures = {}
        for i, date in enumerate(TARGET_DATES):
            label = f"D{i+1} {date}"
            futures[pool.submit(book_all_hotels_for_date, date, label)] = date

        results = {}
        for future in as_completed(futures):
            date, booked_list, _ = future.result()
            results[date] = booked_list

    print("\n" + "=" * 60)
    print(f"{B}RESULTS{X}")
    print("=" * 60)
    for date in TARGET_DATES:
        booked_list = results.get(date, [])
        if booked_list:
            print(f"  {G}{date}: {len(booked_list)} booked{X}")
            for h in booked_list:
                print(f"    {G}- {h}{X}")
        else:
            print(f"  {date}: none booked")


if __name__ == '__main__':
    main()
