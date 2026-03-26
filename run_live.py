#!/usr/bin/env python3
"""Parallel curl booking for multiple dates -- books all available hotels per date."""
import subprocess, re, urllib.parse, sys, os, json, tempfile, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = 'https://as.its-kenpo.or.jp'
CALENDAR_URL = open('calendar_url_cache.txt').read().strip()
TARGET_DATES = ['2026-04-13', '2026-04-14']
EMAIL = 'wwaylonhuang@gmail.com'
NUM_GUESTS = '2'
BOOKINGS_FILE = 'bookings.json'
SKIP_HOTELS = [
    "ブルーベリーヒル勝浦",
    # "ホテル日航プリンセス京都",
    # "ホテルハーヴェスト南紀田辺",
    # "草津温泉　ホテルヴィレッジ",
    # "ホテルハーヴェスト伊東",
    # "ホテルハーヴェスト　スキージャム勝山",
    # "ホテル琵琶レイクオーツカ",
    # "ホテルハーヴェスト有馬六彩",
    # "リソルの森",
    # "ホテルハーヴェスト浜名湖",
    # "ゆふいん山水館",
    # "ホテル日航アリビラ",
    # "ラビスタ函館ベイANNEX",
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
    print(f"{tag} Booking: {hotel_name}")
    s, body, _ = c('POST', BASE + '/calendar_apply/apply_service_select',
        {'utf8': '\u2713', 'authenticity_token': auth, 'empty': '',
         'join_time': target_date, 's': s_param, 'service_group_id': hotel_id})
    services = re.findall(r'data-apply-service-id="(\d+)".*?>(.*?)</a>', body)
    if not services:
        print(f"{tag}   No services for {hotel_name}")
        return False
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')

    # STEP 4: Select service (302)
    service_id = services[0][0]
    s, body, loc = c('POST', BASE + '/calendar_apply/check_apply_service_coma',
        {'utf8': '\u2713', 'authenticity_token': auth,
         'join_time': target_date, 's': s_param, 'apply_service_id': service_id})
    if not loc or 'empty_new' not in loc:
        print(f"{tag}   Step 4 redirect failed")
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
        print(f"{tag}   Session expired at room search")
        return False
    rooms = re.findall(r'name=\\"apply\[coma\[(\d+)\]\]\\".*?value=\\"(\d+)\\"', body)
    guid = ex(body, r'apply_session_guid.*?value=\\"([^"\\]+)\\"')
    if not rooms:
        print(f"{tag}   No rooms available")
        return False
    print(f"{tag}   {len(rooms)} rooms -> selecting room")

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
        print(f"{tag}   Not on rules page")
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
        print(f"{tag}   Not on email page")
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
        print(f"{tag}   BOOKED: {hotel_name}")
        save_booking(target_date, hotel_name)
        return True

    print(f"{tag}   Final page not send_complete")
    return False


def book_all_hotels_for_date(target_date, label):
    """Loop through all available hotels for a date, booking each one.
    Returns (date, list_of_booked_hotels)."""
    tag = f"[{label}]"
    booked = []

    cookie_fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix=f'cookies_{target_date}_')
    os.close(cookie_fd)
    open(cookie_file, 'w').close()

    def c(method, url, data=None, headers=None):
        return curl(cookie_file, method, url, data, headers)

    try:
        # STEP 1: Load calendar
        print(f"{tag} Step 1: Load calendar")
        s, body, _ = c('GET', CALENDAR_URL)
        if s != 200:
            print(f"{tag} FAILED - token expired ({s})")
            return target_date, booked
        csrf = ex(body, r'csrf-token.*?content="(.*?)"')
        auth = ex(body, r'name="authenticity_token" value="(.*?)"')
        s_param = ex(body, r'name="s" id="s" value="(.*?)"')

        # Navigate to target month if needed
        if f'data-join-time="{target_date}"' not in body:
            next_date = ex(body, r"toNextMonth\('([^']+)'")
            target_ym = f"{target_date[:4]}-{target_date[5:7]}-01"
            s2, body_nav, _ = c('POST', BASE + '/calendar_apply/calendar_select',
                {'join_date': next_date or target_ym, 's': s_param},
                {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
                 'Accept': 'text/javascript, application/javascript, */*; q=0.01',
                 'Referer': CALENDAR_URL})
            cls = ex(body_nav, rf'class=\\"([^"\\]*)\\"[^>]*data-join-time=\\"{target_date}\\"') or ''
            if 'empty' not in cls:
                print(f"{tag} {target_date}: NOT AVAILABLE")
                return target_date, booked

        # STEP 2: Select date -> get hotel list
        print(f"{tag} Step 2: Select date {target_date}")
        s, body, _ = c('POST', BASE + '/calendar_apply/service_group_select',
            {'utf8': '\u2713', 'authenticity_token': auth,
             'join_time': target_date, 's': s_param})
        all_hotels = re.findall(r'data-service-group-id="(\d+)".*?>(.*?)</a>', body)
        if not all_hotels:
            print(f"{tag} No hotels for {target_date}")
            return target_date, booked
        auth = ex(body, r'name="authenticity_token" value="(.*?)"')

        # Filter: skip list + already booked
        already_booked = get_booked_hotels(target_date)
        hotels = []
        for gid, name in all_hotels:
            if name in SKIP_HOTELS:
                print(f"{tag}   SKIP (config): {name}")
            elif name in already_booked:
                print(f"{tag}   SKIP (already booked): {name}")
            else:
                hotels.append((gid, name))

        if not hotels:
            print(f"{tag} No hotels to book for {target_date}")
            return target_date, booked

        print(f"{tag} {len(hotels)} hotels to book")

        # Loop through each hotel
        for i, (hotel_id, hotel_name) in enumerate(hotels):
            # For hotel after the first, we need to navigate back to the
            # service_group_select page to pick the next hotel.
            if i > 0:
                # Fresh session for each hotel: reload calendar -> select date
                open(cookie_file, 'w').close()
                s, body, _ = c('GET', CALENDAR_URL)
                if s != 200:
                    print(f"{tag} Calendar reload failed, stopping")
                    break
                csrf = ex(body, r'csrf-token.*?content="(.*?)"')
                auth = ex(body, r'name="authenticity_token" value="(.*?)"')
                s_param = ex(body, r'name="s" id="s" value="(.*?)"')

                # Navigate to month again
                if f'data-join-time="{target_date}"' not in body:
                    next_date = ex(body, r"toNextMonth\('([^']+)'")
                    target_ym = f"{target_date[:4]}-{target_date[5:7]}-01"
                    c('POST', BASE + '/calendar_apply/calendar_select',
                        {'join_date': next_date or target_ym, 's': s_param},
                        {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
                         'Accept': 'text/javascript, application/javascript, */*; q=0.01',
                         'Referer': CALENDAR_URL})

                # Select date again
                s, body, _ = c('POST', BASE + '/calendar_apply/service_group_select',
                    {'utf8': '\u2713', 'authenticity_token': auth,
                     'join_time': target_date, 's': s_param})
                auth = ex(body, r'name="authenticity_token" value="(.*?)"')

            success = book_one_hotel(tag, c, target_date, s_param, auth,
                                    hotel_id, hotel_name)
            if success:
                booked.append(hotel_name)

        return target_date, booked

    finally:
        os.unlink(cookie_file)


def main():
    print(f"Booking {len(TARGET_DATES)} dates in parallel: {', '.join(TARGET_DATES)}")
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
            date, booked_list = future.result()
            results[date] = booked_list

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for date in TARGET_DATES:
        booked_list = results.get(date, [])
        if booked_list:
            print(f"  {date}: {len(booked_list)} booked")
            for h in booked_list:
                print(f"    - {h}")
        else:
            print(f"  {date}: none booked")


if __name__ == '__main__':
    main()
