#!/usr/bin/env python3
"""Booking engine — scans calendar months and books all available hotels per date.

Pure booking logic only. Reads the calendar URL from calendar_url_cache.txt each
cycle. If the URL is missing or expired, it simply waits for the next cycle
(the URL monitor in main.py handles CAPTCHA solving separately).
"""
import subprocess, re, urllib.parse, os, json, tempfile, threading, time, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from config import (
    CALENDAR_URL_CACHE, TARGET_DATES, EMAIL, NUM_GUESTS,
    BOOKINGS_FILE, RETRY_DELAY, CURL_MAX_ATTEMPTS, SKIP_HOTELS,
    DEBUG_DIR, USER_AGENT_CACHE, BROWSER_HEADERS, ACCEPT, ACCEPT_LANGUAGE,
    FALLBACK_USER_AGENT,
)

BASE = 'https://as.its-kenpo.or.jp'

# Minimum seconds between month-nav failure dumps per scanner. The scan runs
# every RETRY_DELAY seconds, so an unthrottled dump would flood the disk.
NAV_DUMP_INTERVAL = 300

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
        with open(CALENDAR_URL_CACHE) as f:
            url = f.read().strip()
        return url or None
    except FileNotFoundError:
        return None


def _load_bookings():
    if not os.path.exists(BOOKINGS_FILE):
        return {}
    try:
        with open(BOOKINGS_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except (json.JSONDecodeError, OSError) as e:
        log(f"{R}Warning: failed to load {BOOKINGS_FILE}: {e}{X}")
        return {}


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


class Response(str):
    """The response body, with its own headers attached.

    Behaves exactly like the plain `str` every call site already treats it as,
    so `_dump_debug` can record the headers that belong to *this* body without
    widening the 3-tuple that 17 call sites unpack.
    """
    __slots__ = ('headers', 'location', 'request')

    def __new__(cls, body, headers='', location=None, request=''):
        obj = super().__new__(cls, body)
        obj.headers = headers
        obj.location = location
        obj.request = request
        return obj


# Chrome's real user agent, recorded by captcha_solver at each solve.
_ua_lock = threading.Lock()
_ua_cache = (None, None)  # ((path, mtime), user_agent)

# A UA must name a desktop platform: a mobile UA can make the site serve a
# different template, which would break the markup-exact extractors and the
# SKIP_HOTELS name matching — and an unmatched skip name means booking a hotel
# that was meant to be skipped.
_UA_PLATFORMS = ('Macintosh', 'Windows NT', 'X11')


def _user_agent():
    """Read the UA of the Chrome that minted the current session token.

    Re-reads when captcha_solver rewrites the file, so the UA can never drift
    out of sync with the session it was captured alongside. Never raises:
    callers sit outside curl()'s try block, and an exception here would kill a
    scanner thread or — worse — the URL monitor, which would stop CAPTCHA
    solving for good while the process went on looking healthy.
    """
    global _ua_cache
    with _ua_lock:
        try:
            key = (USER_AGENT_CACHE, os.path.getmtime(USER_AGENT_CACHE))
            if _ua_cache[0] == key:
                return _ua_cache[1]
            with open(USER_AGENT_CACHE, encoding='utf-8', errors='replace') as f:
                raw = f.read()
        except Exception:
            return FALLBACK_USER_AGENT
        # First line only: this value is spliced into a curl -H flag, so an
        # interior newline would inject an arbitrary extra header.
        ua = (raw.splitlines() or [''])[0].strip()
        if not ua or not any(p in ua for p in _UA_PLATFORMS):
            return FALLBACK_USER_AGENT  # deliberately not cached
        _ua_cache = (key, ua)
        return ua


def _merge_headers(headers):
    """Merge per-call headers over the browser defaults.

    Merging here rather than appending `-H` flags matters: curl emits every
    user-supplied header it is given, so a default `Accept` plus a per-call
    `Accept` would send both and let the server pick. Comparison is
    case-insensitive so a case variant cannot produce a duplicate line either.
    """
    merged = {}
    if BROWSER_HEADERS:
        merged['User-Agent'] = _user_agent()
        merged['Accept'] = ACCEPT
        merged['Accept-Language'] = ACCEPT_LANGUAGE
    for k, v in (headers or {}).items():
        if v is None:
            continue  # ex() found no match; `X-CSRF-Token: None` would 422
        for existing in [e for e in merged if e.lower() == k.lower()]:
            del merged[existing]
        merged[k] = v
    return merged


def header_args(headers=None):
    """curl `-H` flags for the merged header set."""
    args = []
    for k, v in _merge_headers(headers).items():
        args.extend(['-H', f'{k}: {v}'])
    return args


def curl(cookie_file, method, url, data=None, headers=None):
    cmd = ['curl', '-s', '-c', cookie_file, '-b', cookie_file,
           '-D', '/dev/stderr', '--max-redirs', '0', '--max-time', '30']
    if method == 'POST':
        cmd.extend(['-X', 'POST'])
    cmd.extend(header_args(headers))
    if data:
        # An `ex()` that found nothing yields None. Sending the literal string
        # "None" is never right; an empty value is what a browser submits for
        # an unfilled hidden field. Log it, because a missing token here is the
        # real cause of the rejection that follows.
        missing = [k for k, v in data.items() if v is None]
        if missing:
            log(f"  {Y}{method} {url.split('?')[0]}: no value extracted for "
                f"{', '.join(missing)}{X}")
        for k, v in data.items():
            cmd.extend(['--data-urlencode', f'{k}={"" if v is None else v}'])
    cmd.append(url)
    attempts = max(1, CURL_MAX_ATTEMPTS)
    status, body, hdrs = 0, '', ''
    for attempt in range(attempts):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
        except Exception as e:
            # A transport-level failure must not kill a scanner thread: the
            # scan loop has no except around its curl calls.
            status, body, hdrs = 0, '', ''
            log(f"  {R}curl {method} raised: {e}{X}")
        else:
            body = r.stdout
            hdrs = r.stderr
            st = re.findall(r'HTTP/\S+ (\d+)', hdrs)
            status = int(st[-1]) if st else 0
        if (status != 0 and status < 500) or attempt + 1 == attempts:
            break
        log(f"  {Y}curl {method} failed ({status}), retrying...{X}")
    loc = re.search(r'location: (.+)', hdrs, re.IGNORECASE)
    location = loc.group(1).strip() if loc else None
    return status, Response(body, hdrs, location, f'{method} {redact_url(url)}'), location


def ex(html, pat):
    m = re.search(pat, html)
    return m.group(1) if m else None


# Headers written verbatim to debug dumps. Everything else has its value
# fingerprinted — a whitelist, so a future session-bearing header cannot leak
# by default. `debug_responses/` is gitignored, but these files still get read,
# pasted and shared, so no cookie or token value is ever written to disk.
# Anything ambiguous fails closed (redacted) rather than open.
_SAFE_HEADERS = frozenset("""
    accept-ranges age alt-svc cache-control connection content-encoding
    content-language content-length content-security-policy content-type date
    etag expires keep-alive last-modified pragma referrer-policy server status
    strict-transport-security transfer-encoding upgrade vary via x-cache
    x-content-type-options x-frame-options x-permitted-cross-domain-policies
    x-powered-by x-request-id x-runtime x-xss-protection
    cf-cache-status cf-ray
""".split())

# Set-Cookie attributes, kept verbatim. Anything else in the attribute list is
# a second comma-joined cookie (or a value that broke across a `;`), so it gets
# fingerprinted instead of passed through.
_COOKIE_ATTRS = frozenset(
    'expires max-age domain path samesite priority'.split())
_COOKIE_FLAGS = frozenset('secure httponly partitioned'.split())

_MAX_PLAIN = 12       # query values / fragments longer than this are secrets
_MAX_SEGMENT = 32     # path segments longer than this are secrets
                      # (the longest real ITS segment is check_apply_service_coma, 24)


def _fingerprint(value):
    """Stable, non-reversible stand-in for a secret value.

    The digest prefix is what makes a dump useful: it answers "is this the same
    `_src_session` as the previous request, or a fresh one?" across two files
    without either file containing the session id.
    """
    digest = hashlib.sha256(value.encode('utf-8', 'replace')).hexdigest()[:8]
    return f'[len={len(value)} sha256={digest}]'


def _redact_query_part(pair):
    key, eq, value = pair.partition('=')
    if not eq:  # a bare `?TOKEN` component
        return key if len(key) <= _MAX_PLAIN else _fingerprint(key)
    return f'{key}={value}' if len(value) <= _MAX_PLAIN else f'{key}={_fingerprint(value)}'


def redact_url(url):
    """Keep scheme, host and path shape; fingerprint anything token-length.

    The path is the whole point (`/service_category/index` means the session
    was thrown away, `/apply/empty_new` means the flow advanced); the `s=`
    token is a live credential wherever it appears — query, path or fragment.

    Never raises: this runs on every request (via `curl`) and on every dump, so
    a malformed `Location` must not take out a booking attempt or lose the very
    artifact being written.
    """
    if not url:
        return ''
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return _fingerprint(url)
    netloc = parts.netloc
    if '@' in netloc:  # strip any user:password@
        netloc = f'[redacted]@{netloc.rpartition("@")[2]}'
    path = '/'.join(seg if len(seg) <= _MAX_SEGMENT else _fingerprint(seg)
                    for seg in parts.path.split('/'))
    query = '&'.join(_redact_query_part(p) for p in parts.query.split('&')) \
        if parts.query else ''
    fragment = parts.fragment if len(parts.fragment) <= _MAX_PLAIN \
        else _fingerprint(parts.fragment)
    try:
        return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, fragment))
    except ValueError:
        return _fingerprint(url)


def _redact_set_cookie(value):
    """Fingerprint the cookie value, keep its name and its real attributes.

    The attributes are load-bearing: `Max-Age=0` or a past `Expires` is how a
    session *reset* is told apart from a session *re-issue*.
    """
    out = []
    for i, part in enumerate(value.split(';')):
        name, eq, val = part.partition('=')
        attr = name.strip().lower()
        if i == 0:  # the cookie itself
            out.append(f'{name}={_fingerprint(val)}' if eq else _fingerprint(part))
        elif eq and attr in _COOKIE_ATTRS:
            # Only an RFC-1123 Expires date may legitimately contain a comma;
            # anywhere else a comma is the join between two cookies on one
            # header line, so the tail is a second cookie's name=value.
            if ',' in val and not (attr == 'expires' and val.strip().lower().endswith('gmt')):
                head, _, tail = val.partition(',')
                out.append(f'{name}={head},{_fingerprint(tail)}')
            else:
                out.append(part)
        elif not eq and attr in _COOKIE_FLAGS:
            out.append(part)
        elif eq:  # a comma-joined second cookie
            out.append(f'{name}={_fingerprint(val)}')
        else:  # a value that broke across the `;`
            out.append(_fingerprint(part))
    return ';'.join(out)


def _redact_headers(hdrs):
    """Redact a raw curl `-D` header dump for safe storage."""
    out = []
    for raw in (hdrs or '').splitlines():
        line = raw.rstrip('\r')
        if not line or line.startswith('HTTP/'):
            out.append(line)
            continue
        if raw[:1] in (' ', '\t'):
            # An obs-fold continuation — the tail of the header above, which
            # may well be the tail of a Set-Cookie value.
            out.append(f'# folded: {_fingerprint(line.strip())}')
            continue
        name, sep, value = line.partition(':')
        if not sep:
            out.append(f'# unparsed: {_fingerprint(line)}')
            continue
        key, value = name.strip().lower(), value.strip()
        if key == 'set-cookie':
            out.append(f'{name}: {_redact_set_cookie(value)}')
        elif key == 'location':
            out.append(f'{name}: {redact_url(value)}')
        elif key in _SAFE_HEADERS:
            out.append(f'{name}: {value}')
        else:
            out.append(f'{name}: {_fingerprint(value)}')
    return '\n'.join(out).strip() or '(no headers captured)'


# Credentials embedded in response bodies. The dump is kept for its markup —
# form actions, CSS classes, error text — none of which needs a token's value.
# Each pattern appears twice: AJAX responses are Rails-UJS JavaScript, in which
# the markup arrives with backslash-escaped quotes.
_BODY_SECRETS = (
    r'name=\\?"authenticity_token\\?"[^>]*?value=\\?"([^"\\]+)',
    r'csrf-token\\?"[^>]*?content=\\?"([^"\\]+)',
    r"coma_search\(\\?'([^'\\]+)",
    r'[?&](?:amp;)?s=([^"\'&<>\s\\]{13,})',
)


def _redact_body(body):
    """Fingerprint credentials embedded in a dumped response body."""
    def replace(m):
        # Splice by span, not by str.replace: a short captured value can also
        # occur in the surrounding markup, and replacing every occurrence would
        # shred the tags the dump exists to show.
        start, end = m.start(1) - m.start(0), m.end(1) - m.start(0)
        whole = m.group(0)
        return whole[:start] + _fingerprint(m.group(1)) + whole[end:]
    for pat in _BODY_SECRETS:
        body = re.sub(pat, replace, body)
    return body


def _headers_section(resp):
    """The `# request` / `# body` preamble plus redacted headers for a response."""
    return (f'# request: {getattr(resp, "request", "") or "(unknown)"}\n'
            f'# body: {len(str(resp).encode("utf-8", "replace"))} bytes\n\n'
            f'{_redact_headers(getattr(resp, "headers", ""))}\n')


def _dump_debug(label, step, status, body, via=None):
    """Save an unexpected HTTP response for later debugging.

    Writes the redacted body to `<stem>.html` and the redacted response headers
    to `<stem>.headers.txt`. The headers are the diagnostic payload: whether a
    302 carries `x-runtime` (Rails generated it) or not (Apache/ALB/WAF did),
    whether `content-length` is 0 by intent or the body was truncated in
    transit, and whether `set-cookie` re-issued the session.

    `via` is the response *before* a redirect was followed. Without it, a dump
    taken after `if s == 302: c('GET', loc)` records the follow-up GET's headers
    and throws away the 302's — which is exactly the response worth reading.
    """
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_label = label.replace(' ', '_').replace('/', '-')
        stem = os.path.join(DEBUG_DIR, f'{ts}_{safe_label}_{step}_status{status}')
        with open(stem + '.html', 'w', encoding='utf-8') as f:
            f.write(_redact_body(body))
        with open(stem + '.headers.txt', 'w', encoding='utf-8') as f:
            f.write(_headers_section(body))
            if via is not None:
                f.write('\n# ── preceding response (the redirect that led here) ──\n')
                f.write(_headers_section(via))
        loc = redact_url(getattr(via if via is not None else body, 'location', None) or '')
        log(f"  {Y}Debug response saved: {os.path.basename(stem)}.html"
            f" — location: {loc or '(none)'}{X}")
    except Exception as e:
        log(f"  {R}Failed to save debug response: {e}{X}")


def _date_css_class(body, date):
    """Extract the CSS class for a date cell from escaped JS response."""
    return ex(body, rf'class=\\"([^"\\]*)\\\"[^>]*data-join-time=\\"{date}\\"') or ''


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
        _dump_debug(f"{target_date}_{hotel_name}", 'step3_service_select', s, body)
        return False
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')

    # STEP 4: Select service (302)
    service_id = services[0][0]
    s, body, loc = c('POST', BASE + '/calendar_apply/check_apply_service_coma',
        {'utf8': '\u2713', 'authenticity_token': auth,
         'join_time': target_date, 's': s_param, 'apply_service_id': service_id})
    if not loc or 'empty_new' not in loc:
        log(f"{tag}   {R}Step 4 redirect failed{X}")
        _dump_debug(f"{target_date}_{hotel_name}", 'step4_check_coma', s, body)
        return False

    # STEP 5: Load booking form
    referer_url = loc
    s, body, _ = c('GET', loc)
    csrf = ex(body, r'csrf-token.*?content="(.*?)"')
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')
    form_action = ex(body, r'action="(/apply/empty_create\?s=[^"]+)"')
    coma_s = ex(body, r"coma_search\('([^']+)'\)")
    if not form_action or not coma_s:
        missing = [p for p, v in [('form_action', form_action), ('coma_s', coma_s),
                   ('csrf', csrf), ('auth', auth)] if not v]
        title = ex(body, r'<title>(.*?)</title>') or '(no title)'
        snippet = re.sub(r'<[^>]+>', '', body)[:200].strip()
        log(f"{tag}   {R}Missing form params on booking page (status {s}, missing: {', '.join(missing)}){X}")
        log(f"{tag}   {R}  url: {loc}{X}")
        log(f"{tag}   {R}  title: {title}{X}")
        log(f"{tag}   {R}  snippet: {snippet}{X}")
        _dump_debug(f"{target_date}_{hotel_name}", 'step5_booking_form', s, body)
        return False

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
        _dump_debug(f"{target_date}_{hotel_name}", 'step6_room_search', s, body)
        return False
    rooms = re.findall(r'name=\\"apply\[coma\[(\d+)\]\]\\".*?value=\\"(\d+)\\"', body)
    guid = ex(body, r'apply_session_guid.*?value=\\"([^"\\]+)\\"')
    if not rooms:
        log(f"{tag}   {R}No rooms available{X}")
        _dump_debug(f"{target_date}_{hotel_name}", 'step6_no_rooms', s, body)
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
    via = None
    if s == 302 and loc:
        via, (s, body, _) = body, c('GET', loc)

    # STEP 8: Agree to rules
    if '\u540c\u610f' not in body:
        log(f"{tag}   {R}Not on rules page{X}")
        _dump_debug(f"{target_date}_{hotel_name}", 'step8_rules', s, body, via)
        return False
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')
    form_act = ex(body, r'<form[^>]*action="([^"]*)"[^>]*method="post"')
    s_rule_m = re.search(r'name="s"[^>]*value="([^"]*)"', body)
    s_rule = s_rule_m.group(1) if s_rule_m else None
    rule_url = BASE + form_act if form_act else None
    if not rule_url:
        log(f"{tag}   {R}Missing rules form action{X}")
        _dump_debug(f"{target_date}_{hotel_name}", 'step8_rules_form', s, body)
        return False
    post_data = {'utf8': '\u2713', 'authenticity_token': auth}
    if s_rule:
        post_data['s'] = s_rule
    s, body, loc = c('POST', rule_url, post_data)
    via = None
    if s == 302 and loc:
        via, (s, body, _) = body, c('GET', loc)

    # STEP 9: Submit email
    if 'email' not in body.lower():
        log(f"{tag}   {R}Not on email page{X}")
        _dump_debug(f"{target_date}_{hotel_name}", 'step9_email_page', s, body, via)
        return False
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')
    form_act = ex(body, r'<form[^>]*action="([^"]*)"[^>]*method="post"')
    token_field = ex(body, r'name="__token__"[^>]*value="([^"]*)"')
    email_url = BASE + form_act if form_act else None
    if not email_url:
        log(f"{tag}   {R}Missing email form action{X}")
        _dump_debug(f"{target_date}_{hotel_name}", 'step9_email_form', s, body)
        return False
    post_data = {
        'utf8': '\u2713', 'authenticity_token': auth,
        'email': EMAIL, 'commit': '\u9001\u4fe1',
    }
    if token_field:
        post_data['__token__'] = token_field
    s, body, loc = c('POST', email_url, post_data)
    via = None
    if s == 302 and loc:
        via, (s, body, _) = body, c('GET', loc)

    if 'send_complete' in body:
        log(f"{tag}   {B}{G}BOOKED: {hotel_name}{X}")
        save_booking(target_date, hotel_name)
        return True

    log(f"{tag}   {R}Final page not send_complete{X}")
    _dump_debug(f"{target_date}_{hotel_name}", 'step9_final', s, body, via)
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
    open(cookie_file, 'w').close()  # truncate

    def c(method, url, data=None, headers=None):
        return curl(cookie_file, method, url, data, headers)

    try:
        # STEP 1: Load calendar
        s, body, _ = c('GET', url)
        if s != 200:
            log(f"{tag} {Y}URL expired ({s}), skipping{X}")
            _dump_debug(label, 'calendar_get', s, body)
            return target_date, booked
        csrf = ex(body, r'csrf-token.*?content="(.*?)"')
        auth = ex(body, r'name="authenticity_token" value="(.*?)"')
        s_param = ex(body, r'name="s" id="s" value="(.*?)"')

        # Navigate to target month if needed
        if f'data-join-time="{target_date}"' not in body:
            target_ym = f"{target_date[:4]}-{target_date[5:7]}-01"
            s_nav, body_nav, _ = c('POST', BASE + '/calendar_apply/calendar_select',
                {'join_date': target_ym, 's': s_param},
                {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
                 'Accept': 'text/javascript, application/javascript, */*; q=0.01',
                 'Referer': url})
            if s_nav != 200:
                log(f"{tag} {Y}month nav returned {s_nav}{X}")
                _dump_debug(label, 'calendar_select', s_nav, body_nav)
                return target_date, booked
            cls = _date_css_class(body_nav, target_date)
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
            _dump_debug(label, 'service_group_select', s, body)
            return target_date, booked
        auth = ex(body, r'name="authenticity_token" value="(.*?)"')

        log(f"{tag} {C}Found {len(all_hotels)} hotels: {', '.join(n for _, n in all_hotels)}{X}")

        # Filter: skip list + already booked
        already_booked = get_booked_hotels(target_date)
        skipped = [n for _, n in all_hotels if n in SKIP_HOTELS]
        already = [n for _, n in all_hotels if n in already_booked]
        hotels = [(gid, name) for gid, name in all_hotels
                  if name not in SKIP_HOTELS and name not in already_booked]

        if not hotels:
            reasons = []
            if skipped:
                reasons.append(f"skip list: {', '.join(skipped)}")
            if already:
                reasons.append(f"already booked: {', '.join(already)}")
            log(f"{tag} {Y}All hotels filtered out ({'; '.join(reasons)}){X}")
            return target_date, booked

        log(f"{tag} {C}{len(hotels)} to book: {', '.join(n for _, n in hotels)}{X}")

        # Book each hotel
        for i, (hotel_id, hotel_name) in enumerate(hotels):
            if i > 0:
                # Fresh session for next hotel
                open(cookie_file, 'w').close()  # truncate
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
    open(cookie_file, 'w').close()  # truncate

    def c(method, url, data=None, headers=None):
        return curl(cookie_file, method, url, data, headers)

    try:
        attempt = 0
        last_nav_dump = float('-inf')
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
            open(cookie_file, 'w').close()  # truncate
            s, body, _ = c('GET', url)
            if s != 200:
                log(f"{tag} {Y}[{attempt}] URL returned {s}, waiting...{X}")
                continue
            csrf = ex(body, r'csrf-token.*?content="(.*?)"')
            s_param = ex(body, r'name="s" id="s" value="(.*?)"')

            # SCAN: Navigate to target month (1 POST)
            s_nav, body_nav, _ = c('POST', BASE + '/calendar_apply/calendar_select',
                {'join_date': month_ym, 's': s_param},
                {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
                 'Accept': 'text/javascript, application/javascript, */*; q=0.01',
                 'Referer': url})
            if s_nav != 200:
                # Without this the scan reports "no dates available" forever
                # while the URL monitor's plain GET still returns 200, so no
                # re-solve fires and nothing ever gets booked again.
                log(f"{tag} {Y}[{attempt}] month nav returned {s_nav}, waiting...{X}")
                # Throttled on elapsed time, not on the status: an upstream that
                # flaps between two statuses would otherwise dump every cycle.
                if time.monotonic() - last_nav_dump >= NAV_DUMP_INTERVAL:
                    last_nav_dump = time.monotonic()
                    _dump_debug(label, 'calendar_select', s_nav, body_nav)
                continue

            # Check all target dates for availability
            available = []
            for td in target_dates:
                cls = _date_css_class(body_nav, td)
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
                    try:
                        td, booked_list = future.result()
                    except Exception as e:
                        log(f"{tag} {R}Booking thread failed: {e}{X}")
                        continue
                    if booked_list:
                        log(f"{tag} {G}Booked for {td}: {', '.join(booked_list)}{X}")

    finally:
        os.unlink(cookie_file)
