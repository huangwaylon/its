#!/usr/bin/env python3
"""Live curl booking run."""
import subprocess, re, urllib.parse, sys

BASE = 'https://as.its-kenpo.or.jp'
COOKIES = 'cookies_live.txt'
CALENDAR_URL = open('calendar_url_cache.txt').read().strip()
TARGET_DATE = '2026-04-13'

def curl(method, url, data=None, headers=None):
    cmd = ['curl', '-s', '-c', COOKIES, '-b', COOKIES, '-D', '/dev/stderr', '--max-redirs', '0']
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

open(COOKIES, 'w').close()

# STEP 1
print("STEP 1: Load calendar")
s, body, _ = curl('GET', CALENDAR_URL)
print(f"  [{s}] {len(body)}b")
if s != 200:
    print("  FAILED - token expired")
    sys.exit(1)
csrf = ex(body, r'csrf-token.*?content="(.*?)"')
auth = ex(body, r'name="authenticity_token" value="(.*?)"')
s_param = ex(body, r'name="s" id="s" value="(.*?)"')
month = ex(body, r'<span class="month">(.*?)</span>')
print(f"  Month: {month}")

# Navigate to April if needed
if f'data-join-time="{TARGET_DATE}"' not in body:
    print("  Navigating to April...")
    next_date = ex(body, r"toNextMonth\('([^']+)'")
    s2, body2, _ = curl('POST', BASE + '/calendar_apply/calendar_select',
        {'join_date': next_date or '2026-04-01', 's': s_param},
        {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
         'Accept': 'text/javascript, application/javascript, */*; q=0.01',
         'Referer': CALENDAR_URL})
    print(f"  [{s2}] {len(body2)}b")
    cls = ex(body2, rf'class=\\"([^"\\]*)\\"[^>]*data-join-time=\\"{TARGET_DATE}\\"') or ''
    avail = 'empty' in cls
    print(f"  {TARGET_DATE}: {'AVAILABLE' if avail else 'NOT AVAILABLE'}")
    if not avail:
        sys.exit(0)

# STEP 2
print("\nSTEP 2: Select date")
s, body, _ = curl('POST', BASE + '/calendar_apply/service_group_select',
    {'utf8': '\u2713', 'authenticity_token': auth, 'join_time': TARGET_DATE, 's': s_param})
print(f"  [{s}] {len(body)}b")
hotels = re.findall(r'data-service-group-id="(\d+)".*?>(.*?)</a>', body)
for gid, name in hotels:
    print(f"  Hotel: {name} (id={gid})")
auth = ex(body, r'name="authenticity_token" value="(.*?)"')

# STEP 3
hotel_id, hotel_name = hotels[0]
print(f"\nSTEP 3: Select hotel: {hotel_name}")
s, body, _ = curl('POST', BASE + '/calendar_apply/apply_service_select',
    {'utf8': '\u2713', 'authenticity_token': auth, 'empty': '',
     'join_time': TARGET_DATE, 's': s_param, 'service_group_id': hotel_id})
print(f"  [{s}] {len(body)}b")
services = re.findall(r'data-apply-service-id="(\d+)".*?>(.*?)</a>', body)
for sid, name in services:
    print(f"  Service: {name} (id={sid})")
auth = ex(body, r'name="authenticity_token" value="(.*?)"')

# STEP 4
service_id = services[0][0]
print(f"\nSTEP 4: Select service (302 redirect)")
s, body, loc = curl('POST', BASE + '/calendar_apply/check_apply_service_coma',
    {'utf8': '\u2713', 'authenticity_token': auth,
     'join_time': TARGET_DATE, 's': s_param, 'apply_service_id': service_id})
print(f"  [{s}] -> {loc[:80] if loc else 'NONE'}")

# STEP 5
print(f"\nSTEP 5: Load booking form")
referer_url = loc
s, body, _ = curl('GET', loc)
print(f"  [{s}] {len(body)}b")
timeout = '\u30bb\u30c3\u30b7\u30e7\u30f3\u304c\u30bf\u30a4\u30e0\u30a2\u30a6\u30c8' in body
print(f"  Session timeout: {timeout}")
csrf = ex(body, r'csrf-token.*?content="(.*?)"')
auth = ex(body, r'name="authenticity_token" value="(.*?)"')
form_action = ex(body, r'action="(/apply/empty_create\?s=[^"]+)"')
coma_s = ex(body, r"coma_search\('([^']+)'\)")
print(f"  Form action: {form_action[:60] if form_action else 'NONE'}")

# STEP 6
print(f"\nSTEP 6: Search rooms (2 guests)")
s, body, _ = curl('POST', BASE + '/apply/empty_new?s=' + urllib.parse.quote(coma_s, safe=''),
    {'utf8': '\u2713', 'authenticity_token': auth,
     'apply[join_time]': TARGET_DATE, 'apply[night_count]': '1',
     'apply[stay_persons]': '2', 'apply[hope_rooms]': '1'},
    {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
     'Accept': 'text/javascript, application/javascript, */*; q=0.01',
     'Referer': referer_url})
print(f"  [{s}] {len(body)}b")

if 'service_category' in body:
    print("  ERROR: Session expired")
    sys.exit(1)

rooms = re.findall(r'name=\\"apply\[coma\[(\d+)\]\]\\".*?value=\\"(\d+)\\"', body)
guid = ex(body, r'apply_session_guid.*?value=\\"([^"\\]+)\\"')
for m2 in re.finditer(
    r'name=\\"apply\[coma\[(\d+)\]\]\\"[^>]*>.*?<\\/td>\s*<td[^>]*>(\d+)<\\/td>\s*<td[^>]*>([^<]+)<\\/td>\s*<td[^>]*>([^<]+)<\\/td>\s*<td[^>]*>([^<]+)<\\/td>',
    body, re.DOTALL):
    print(f"  Room {m2.group(2)}: {m2.group(4)} ({m2.group(5).strip()}) [coma={m2.group(1)}]")
print(f"  GUID: {guid}")
print(f"  Total: {len(rooms)} rooms")

if not rooms:
    print("  No rooms!")
    sys.exit(0)

# STEP 7
room_id = rooms[0][0]
print(f"\nSTEP 7: Submit room {room_id}")
s, body, loc = curl('POST', BASE + form_action,
    {'utf8': '\u2713', 'authenticity_token': auth,
     'apply[join_time]': TARGET_DATE, 'apply[night_count]': '1',
     'apply[stay_persons]': '2', 'apply[hope_rooms]': '1',
     'apply_session_guid': guid, f'apply[coma[{room_id}]]': room_id},
    {'Referer': referer_url})
print(f"  [{s}] Redirect: {loc}")
if s == 302 and loc:
    s, body, _ = curl('GET', loc)
    print(f"  Followed -> [{s}] {len(body)}b")
open('step7_live.html', 'w').write(body)

# Check what page we landed on
page_title = ex(body, r'<h1[^>]*>(.*?)</h1>')
print(f"  Page: {page_title}")

if '\u540c\u610f' in body:
    print("  -> RULES PAGE")
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')
    form_act = ex(body, r'<form[^>]*action="([^"]*)"[^>]*method="post"')
    # The s param is in a hidden field inside the form
    s_rule_m = re.search(r'name="s"[^>]*value="([^"]*)"', body)
    s_rule = s_rule_m.group(1) if s_rule_m else None
    print(f"  Form: {form_act}")
    print(f"  S (rule): {s_rule[:40] if s_rule else 'NONE'}...")

    # STEP 8
    print(f"\nSTEP 8: Agree to rules")
    rule_url = BASE + form_act if form_act and not form_act.startswith('http') else form_act
    post_data = {'utf8': '\u2713', 'authenticity_token': auth}
    if s_rule:
        post_data['s'] = s_rule
    s, body, loc = curl('POST', rule_url, post_data)
    print(f"  [{s}] Redirect: {loc}")
    if s == 302 and loc:
        s, body, _ = curl('GET', loc)
        print(f"  Followed -> [{s}] {len(body)}b")
    open('step8_live.html', 'w').write(body)

    page_title = ex(body, r'<h1[^>]*>(.*?)</h1>')
    print(f"  Page: {page_title}")

    if 'email' in (loc or '').lower() or '\u30e1\u30fc\u30eb' in body or 'email' in body.lower():
        print("  -> EMAIL PAGE")
        auth = ex(body, r'name="authenticity_token" value="(.*?)"')
        form_act = ex(body, r'<form[^>]*action="([^"]*)"[^>]*method="post"')
        email_field = ex(body, r'name="([^"]*email[^"]*)"')
        token_field = ex(body, r'name="__token__"[^>]*value="([^"]*)"')
        print(f"  Form: {form_act}")
        print(f"  Email field: {email_field}")
        print(f"  __token__: {token_field}")

        # STEP 9
        print(f"\nSTEP 9: Submit email")
        email_url = BASE + form_act if form_act and not form_act.startswith('http') else form_act
        post_data = {
            'utf8': '\u2713',
            'authenticity_token': auth,
            email_field or 'email': 'wwaylonhuang@gmail.com',
            'commit': '\u9001\u4fe1',
        }
        if token_field:
            post_data['__token__'] = token_field
        s, body, loc = curl('POST', email_url, post_data)
        print(f"  [{s}] Redirect: {loc}")
        if s == 302 and loc:
            s, body, _ = curl('GET', loc)
            print(f"  Followed -> [{s}] {len(body)}b")
        open('step9_live.html', 'w').write(body)

        if 'send_complete' in (loc or '') or 'send_complete' in body:
            print("\n" + "=" * 60)
            print("BOOKING COMPLETE!")
            print("=" * 60)
        else:
            page_title = ex(body, r'<h1[^>]*>(.*?)</h1>')
            print(f"  Page: {page_title}")
