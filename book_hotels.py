#!/usr/bin/env python3
"""Booking engine — scans calendar months and books all available hotels per date.

Pure booking logic only. Reads the calendar URL from calendar_url_cache.txt each
cycle. If the URL is missing or expired, it simply waits for the next cycle
(the URL monitor in main.py handles CAPTCHA solving separately).
"""
import subprocess, re, urllib.parse, os, json, tempfile, threading, time, hashlib
import html as _html
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date

from config import (
    CALENDAR_URL_CACHE, EMAIL, NUM_GUESTS,
    BOOKINGS_FILE, RETRY_DELAY, CURL_MAX_ATTEMPTS, SKIP_HOTELS,
    DEBUG_DIR, USER_AGENT_CACHE, BROWSER_HEADERS, ACCEPT, ACCEPT_LANGUAGE,
    FALLBACK_USER_AGENT, PRIORITY_HOTELS,
    CURL_RETRY_BACKOFF, CURL_RETRY_BACKOFF_MAX, CURL_TIMEOUT,
    BOOK_MAX_ATTEMPTS, BOOK_RETRY_DELAY,
    SCAN_BACKOFF_MAX, SCAN_JITTER,
    SCAN_REUSE_SESSION, SCAN_REUSE_MAX_FAILURES,
    HOTEL_RETRY_COOLDOWN,
    AUTO_CONFIRM, AUTO_CONFIRM_MIN_DAYS,
    DEBUG_DUMP_INTERVAL, DEBUG_DUMP_KEEP, IDLE_LOG_INTERVAL, SKIP_PAST_DATES,
    APPLICANT,
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

# Number of dates currently mid-booking. main.py's URL monitor reads this to hold
# off a *proactive* CAPTCHA refresh: a booking holds one `s=` token across a
# ~7-request chain, and replacing it underneath is a needless way to lose a slot.
_active_lock = threading.Lock()
_active_bookings = 0


def active_bookings():
    with _active_lock:
        return _active_bookings


class _BookingInFlight:
    def __enter__(self):
        global _active_bookings
        with _active_lock:
            _active_bookings += 1

    def __exit__(self, *exc):
        global _active_bookings
        with _active_lock:
            _active_bookings -= 1
        return False


def _read_cached_url():
    """Read the current calendar URL from cache file.

    Never raises. A scanner's loop body has no `except` around this, and an
    OSError here (not just a missing file) would kill the thread for that month
    permanently while the rest of the process went on looking healthy.
    """
    try:
        with open(CALENDAR_URL_CACHE) as f:
            url = f.read().strip()
        return url or None
    except OSError:
        return None


def _load_bookings(path):
    """`(bookings, ok)`. `ok` is False when the file's contents are unknown.

    Callers must not treat `not ok` as "nothing is booked": a bad read returning
    {} followed by a normal save would rewrite the file with only the one new
    entry and destroy every prior booking. The last log has 5 bookings for
    2026-08-22 that no longer appear in bookings.json, which is exactly that
    failure having already happened. Losing the record of one application risks a
    duplicate attempt later; losing the file risks duplicating every application
    ever made.
    """
    if not os.path.exists(path):
        return {}, True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.loads(f.read().strip() or '{}')
    except (OSError, ValueError, UnicodeDecodeError) as e:
        log(f"{R}Warning: could not read {path}: {e}{X}")
        return {}, False
    if not isinstance(data, dict):
        log(f"{R}Warning: could not read {path}: not a JSON object{X}")
        return {}, False
    return data, True


def save_booking(date_str, hotel_name, path=None):
    """Record a successful booking. Thread-safe, atomic, never destructive.

    `path` defaults to BOOKINGS_FILE; `confirm_booking` passes RESERVATIONS_FILE.
    It is a parameter rather than a swapped module global because both files are
    written from booking threads that run concurrently.
    """
    path = path or BOOKINGS_FILE
    with _bookings_lock:
        bookings, ok = _load_bookings(path)
        if not ok:
            log(f"{R}BOOKED {hotel_name} for {date_str} but {path} could "
                f"not be read — not recording, refusing to overwrite it{X}")
            return
        if hotel_name in bookings.get(date_str, []):
            return
        bookings.setdefault(date_str, []).append(hotel_name)
        try:
            _write_bookings(bookings, path)
        except OSError as e:
            # The booking itself succeeded on the site; losing the record only
            # risks a duplicate attempt later, so this must not raise into the
            # booking thread and abort the remaining hotels.
            log(f"{R}BOOKED but failed to record {hotel_name} for {date_str}: {e}{X}")


def _write_bookings(bookings, path):
    """Write the bookings file atomically — same directory, then rename.

    A plain `open(..., 'w')` truncates first, so a crash or a full disk midway
    through leaves a half-written file that parses as nothing. This process is
    meant to run unattended for weeks; the record of what is already booked is
    the only thing preventing duplicate applications.
    """
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.bookings_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(bookings, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_booked_hotels(date_str):
    with _bookings_lock:
        return _load_bookings(BOOKINGS_FILE)[0].get(date_str, [])



# ── Per-(date, hotel) cooldowns ──────────────────────────────────────
# `attempted` in book_all_hotels_for_date only lives for one call, so without
# this a date that stays available keeps re-attempting the same failing hotels
# every scan cycle. See HOTEL_RETRY_COOLDOWN in config.
_cooldown_lock = threading.Lock()
_cooldowns = {}   # (date_str, normalized hotel name) -> monotonic expiry


def set_cooldown(date_str, hotel_name, seconds):
    """Hold off on this (date, hotel) for `seconds`. Later calls win.

    `hotel_name=None` cools off the whole date. A per-hotel cooldown alone still
    left the date paying the three setup requests every cycle just to rediscover
    that every hotel on it is cooling off, which is most of what the cooldown was
    supposed to save.
    """
    key = (date_str, _norm_hotel(hotel_name))
    with _cooldown_lock:
        _cooldowns[key] = time.monotonic() + seconds


def in_cooldown(date_str, hotel_name):
    """True while this (date, hotel) is still cooling off."""
    key = (date_str, _norm_hotel(hotel_name))
    now = time.monotonic()
    with _cooldown_lock:
        expiry = _cooldowns.get(key)
        if expiry is None:
            return False
        if expiry <= now:
            del _cooldowns[key]
            return False
        return True


def cooldown_remaining(date_str, hotel_name):
    """Seconds left on this (date, hotel)'s cooldown, 0 if none. For logging."""
    key = (date_str, _norm_hotel(hotel_name))
    with _cooldown_lock:
        return max(0.0, _cooldowns.get(key, 0.0) - time.monotonic())



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


def _is_retryable(status):
    """True for statuses worth sending the same request again.

    5xx and 0 (transport failure) are the site under load; 429 is explicit rate
    limiting. Any other 4xx is a rejected request — repeating it verbatim only
    adds load. 302 is deliberately excluded: it is either progress through the
    flow or a dead session, and `_is_session_dead` tells those apart.
    """
    return status == 0 or status == 429 or status >= 500


def curl(cookie_file, method, url, data=None, headers=None, retry=True):
    """Make one HTTP request via curl. Returns `(status, Response, location)`.

    `retry=False` disables the `CURL_MAX_ATTEMPTS` loop for a request that must
    never be repeated — see the final submit in `book_one_hotel`. Retrying is
    safe for everything that only *reads* or navigates, and unsafe for the one
    request that files an application.
    """
    cmd = ['curl', '-s', '-c', cookie_file, '-b', cookie_file,
           '-D', '/dev/stderr', '--max-redirs', '0', '--max-time', str(CURL_TIMEOUT)]
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
    attempts = max(1, CURL_MAX_ATTEMPTS) if retry else 1
    status, body, hdrs = 0, '', ''
    for attempt in range(attempts):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=CURL_TIMEOUT + 10)
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
        if not _is_retryable(status) or attempt + 1 == attempts:
            break
        # Wait before retrying. Retrying a 503 with no delay — as this did
        # before — puts both attempts inside the same millisecond, against a
        # server that is answering 503 precisely because it is being asked too
        # often. `Retry-After` wins when the server states a figure.
        delay = min(CURL_RETRY_BACKOFF * (2 ** attempt), CURL_RETRY_BACKOFF_MAX)
        delay = max(delay, _retry_after(hdrs))
        log(f"  {Y}curl {method} failed ({status}), retrying in {delay:.1f}s...{X}")
        time.sleep(delay)
    loc = re.search(r'location: (.+)', hdrs, re.IGNORECASE)
    location = loc.group(1).strip() if loc else None
    return status, Response(body, hdrs, location, f'{method} {redact_url(url)}'), location


def _retry_after(hdrs):
    """Seconds requested by a `Retry-After` header, capped. 0 if absent.

    Capped because the site is free to name a figure longer than the slot will
    survive; past the cap it is better to come back on our own schedule.
    """
    m = re.search(r'^retry-after:\s*(\d+)\s*$', hdrs or '', re.IGNORECASE | re.MULTILINE)
    if not m:
        return 0.0
    return min(float(m.group(1)), CURL_RETRY_BACKOFF_MAX)


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
    return _redact_applicant(body)


# Inputs whose *name* marks them as carrying 資格認証のキー. Matched on the name so a
# value the site reformats — 070-8999-3499 for a stored 07089993499, 生年月日 split
# across three selects — is still caught, which exact-value matching cannot do.
_PII_FIELD_NAME = re.compile(
    r'sign_no|kigou|insured_no|bangou|kana|birth|\[year\]|\[month\]|\[day\]'
    r'|\btel\b|phone|denwa|postal|\bzip\b|post_?code|address|juu?sho'
    r'|office_name|jigyou?sho|\bmail\b', re.I)

_PII_INPUT = re.compile(
    r'(?is)<input\b(?=[^>]*\bname=(["\'])(?P<name>[^"\']*)\1)'
    r'[^>]*?\bvalue=(["\'])(?P<value>[^"\']*)\3[^>]*>')


def _redact_applicant(body):
    """Strip the applicant's identity data out of a body before it is stored.

    `config.APPLICANT` holds 記号 / 番号 / カナ氏名 / 生年月日 / 電話 / 住所 / 事業所名 —
    identity credentials for somebody's insurance record. 申込内容確認画面 echoes
    every one of them back, and a dump of it goes into `DEBUG_DIR`, whose contents
    were tracked in a public remote until 2026-08-18.

    Two passes, because neither alone is enough:

    - every `value="…"` on an input whose *name* looks like an identity field,
      which survives the site reformatting the value; and
    - each configured value found literally in the prose, which catches
      「記号 1234」 rendered as text rather than as a form control.

    `sex` and `zokugara` are deliberately left alone: 男/女 and 本人 are drawn from a
    two- and five-value domain, so they identify nobody, and they appear in the
    form's own `<option>` labels whatever we submit — redacting them would shred
    the markup the dump exists to show. Values shorter than 3 characters are
    skipped for the same reason.

    Best-effort by construction, and load-bearing only as a second line of defence:
    the first is that these pages are dumped at all only when the flow has already
    failed.
    """
    def by_name(m):
        if not _PII_FIELD_NAME.search(m.group('name')):
            return m.group(0)
        value = m.group('value')
        if not value:
            return m.group(0)
        start = m.start('value') - m.start(0)
        end = m.end('value') - m.start(0)
        whole = m.group(0)
        return whole[:start] + _fingerprint(value) + whole[end:]

    body = _PII_INPUT.sub(by_name, body)

    values = {v for k, v in (APPLICANT or {}).items()
              if k not in ('sex', 'zokugara') and isinstance(v, str) and len(v) >= 3}
    values.add(EMAIL)
    values.update(_birth_renderings((APPLICANT or {}).get('birth')))
    # Longest first, so redacting 記号 does not leave a fragment of 番号 behind.
    for value in sorted((v for v in values if v), key=len, reverse=True):
        body = body.replace(value, _fingerprint(value))
    return body


def _birth_renderings(birth):
    """`2000-03-04` as the site writes it back on 申込内容確認画面.

    That page echoes 生年月日 as prose, not as the three selects it was submitted
    in, and in a different format from the one `.env` stores — so neither matching
    input values by field name nor matching the configured string literally finds
    it. Both zero-padded and unpadded forms, since the day/month selects use
    unpadded option values and the rendering follows no rule we control.
    """
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', birth or ''):
        return set()
    y, m, d = birth[:4], birth[5:7], birth[8:10]
    mi, di = str(int(m)), str(int(d))
    return {f'{y}年{m}月{d}日', f'{y}年{mi}月{di}日',
            f'{y}/{m}/{d}', f'{y}/{mi}/{di}', f'{y}.{m}.{d}'}


def _headers_section(resp):
    """The `# request` / `# body` preamble plus redacted headers for a response."""
    return (f'# request: {getattr(resp, "request", "") or "(unknown)"}\n'
            f'# body: {len(str(resp).encode("utf-8", "replace"))} bytes\n\n'
            f'{_redact_headers(getattr(resp, "headers", ""))}\n')


def _dump_debug(label, step, status, body, via=None, throttle=True):
    """Save an unexpected HTTP response for later debugging.

    Writes the redacted body to `<stem>.html` and the redacted response headers
    to `<stem>.headers.txt`. The headers are the diagnostic payload: whether a
    302 carries `x-runtime` (Rails generated it) or not (Apache/ALB/WAF did),
    whether `content-length` is 0 by intent or the body was truncated in
    transit, and whether `set-cookie` re-issued the session.

    `via` is the response *before* a redirect was followed. Without it, a dump
    taken after `if s == 302: c('GET', loc)` records the follow-up GET's headers
    and throws away the 302's — which is exactly the response worth reading.

    Throttled per (label, step) and pruned to DEBUG_DUMP_KEEP files: a failure
    that repeats every cycle would otherwise write one pair of files per cycle for
    as long as it lasted.
    """
    try:
        if throttle and not _dump_allowed(label, step):
            return
        os.makedirs(DEBUG_DIR, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_label = re.sub(r'[^\w.\-]+', '_', label)
        # The timestamp is only second-resolution, and several dates book in
        # parallel, so two dumps can land in the same second — without the
        # counter the second silently overwrites the first and a failure pair
        # goes missing exactly when two things went wrong at once.
        stem = os.path.join(
            DEBUG_DIR, f'{ts}_{_dump_seq()}_{safe_label}_{step}_status{status}')
        redacted = _redact_body(str(body))
        # A 0-byte body is the common case for these failures and its `.html` is
        # pure noise; the headers file records the size either way.
        if redacted:
            with open(stem + '.html', 'w', encoding='utf-8') as f:
                f.write(redacted)
        with open(stem + '.headers.txt', 'w', encoding='utf-8') as f:
            f.write(_headers_section(body))
            if via is not None:
                f.write('\n# ── preceding response (the redirect that led here) ──\n')
                f.write(_headers_section(via))
        loc = redact_url(getattr(via if via is not None else body, 'location', None) or '')
        log(f"  {Y}Debug response saved: {os.path.basename(stem)}"
            f" — location: {loc or '(none)'}{X}")
        _prune_debug_dir()
    except Exception as e:
        log(f"  {R}Failed to save debug response: {e}{X}")


_dump_lock = threading.Lock()
_dump_last = {}   # (label, step) -> monotonic time of last dump
_dump_counter = 0


def _dump_seq():
    """A short per-process counter, to keep same-second filenames distinct."""
    global _dump_counter
    with _dump_lock:
        _dump_counter += 1
        return f'{_dump_counter:04d}'


def _dump_allowed(label, step):
    """True at most once per DEBUG_DUMP_INTERVAL for a given label+step.

    Keyed on elapsed time rather than on the status, so an upstream flapping
    between two statuses cannot dump on every cycle by alternating.
    """
    now = time.monotonic()
    with _dump_lock:
        key = (label, step)
        if now - _dump_last.get(key, float('-inf')) < DEBUG_DUMP_INTERVAL:
            return False
        _dump_last[key] = now
        # Unbounded growth would otherwise be one entry per (date, hotel, step)
        # for the lifetime of the process.
        if len(_dump_last) > 2000:
            cutoff = now - DEBUG_DUMP_INTERVAL
            for k in [k for k, t in _dump_last.items() if t < cutoff and k != key]:
                del _dump_last[k]
        return True


def _prune_debug_dir():
    """Keep DEBUG_DIR to DEBUG_DUMP_KEEP files, oldest deleted first."""
    try:
        entries = []
        with os.scandir(DEBUG_DIR) as it:
            for e in it:
                if e.is_file():
                    entries.append((e.stat().st_mtime, e.path))
        if len(entries) <= DEBUG_DUMP_KEEP:
            return
        entries.sort()
        for _, path in entries[:len(entries) - DEBUG_DUMP_KEEP]:
            try:
                os.unlink(path)
            except OSError:
                pass
    except OSError:
        pass


# ── Hotel name matching ─────────────────────────────────────────────
# The site's names arrive HTML-escaped and mix full-width (U+3000) with ordinary
# spaces — SKIP_HOTELS already contains both. Exact string equality on the raw
# name means a skip entry can silently fail to match, and an unmatched skip name
# books a hotel that was meant to be skipped. Normalizing both sides removes
# that whole class of mistake.

def _norm_hotel(name):
    """Casefold, unescape entities, and collapse all whitespace."""
    return re.sub(r'\s+', '', _html.unescape(name or '')).casefold()


_SKIP_NORM = frozenset(_norm_hotel(n) for n in SKIP_HOTELS if _norm_hotel(n))
_PRIORITY_NORM = tuple(_norm_hotel(p) for p in PRIORITY_HOTELS if _norm_hotel(p))


def _priority_rank(name):
    """Index of the first PRIORITY_HOTELS substring this name matches.

    len(PRIORITY_HOTELS) — i.e. last — when nothing matches, so a plain sort on
    this rank puts priority hotels in front in the configured order and leaves
    everything else in the site's own order.
    """
    n = _norm_hotel(name)
    for i, p in enumerate(_PRIORITY_NORM):
        if p in n:
            return i
    return len(_PRIORITY_NORM)


def order_hotels(hotels):
    """Sort (id, name) pairs so PRIORITY_HOTELS come first, order preserved.

    Sorting is stable, so non-priority hotels keep the site's ordering. This is
    the difference between attempting NAGU with the first ~7 requests after a
    slot is spotted and attempting it a minute later, behind five other hotels.
    """
    return sorted(hotels, key=lambda h: _priority_rank(h[1]))


def is_skipped(name):
    return _norm_hotel(name) in _SKIP_NORM



def _date_css_class(body, date):
    """Extract the CSS class for a date cell from escaped JS response."""
    return ex(body, rf'class=\\"([^"\\]*)\\\"[^>]*data-join-time=\\"{date}\\"') or ''


# The classes the site puts on a calendar cell (docs/BOOKING_VIA_CURL.md):
#   empty     available (green, ○)           — clickable
#   a_little  limited availability (orange)  — clickable
#   full      no availability (red)          — JS blocks the click
#   over      a past date                    — JS blocks the click
# Both clickable classes can be applied for. Matching only `empty` skipped every
# limited-availability date in the scan *and* in the booking's own re-check, so
# those slots were never attempted at all — the ones most likely to still be open
# were the ones being ignored.
_AVAILABLE_CLASSES = ('empty', 'a_little')


def is_available(css_class):
    """True if a calendar cell's CSS class marks the date as bookable."""
    return any(c in (css_class or '') for c in _AVAILABLE_CLASSES)


# The site's "no vacant rooms in the specified facility" page. It has no booking
# form, so without this check it is indistinguishable from a broken extractor.
_NO_ROOMS_TEXT = '空き部屋がございません'

# Path every expired-session redirect lands on. Confirmed live: a stale `s=`
# token answers 302, 0 bytes, `Location: /service_category/index` — the exact
# signature of 302 of the 380 dumps on disk, including every
# `service_group_select` failure.
_SESSION_DEAD_PATH = '/service_category/index'


def _is_session_dead(status, location):
    return status == 302 and _SESSION_DEAD_PATH in (location or '')


def book_one_hotel(tag, c, target_date, s_param, auth, hotel_id, hotel_name):
    """Book a single hotel for a date. Steps 3-9. Returns True on success.

    Sets this (date, hotel)'s cooldown on the way through, so a hotel that keeps
    failing is not re-attempted on every scan cycle for as long as the date stays
    available. The short cooldown is claimed on entry and covers every failure
    below; reaching step 7 upgrades it to the long one, because from that request
    onwards we may be holding the room.
    """
    set_cooldown(target_date, hotel_name, HOTEL_RETRY_COOLDOWN)

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
        # The site's own "no vacant rooms at the specified facility" page has no
        # booking form, so it lands here. It is an ordinary outcome of racing for
        # a slot, not a fault: reporting it as a missing-parameter error buried
        # four red lines and a debug dump in the log every time someone else got
        # there first.
        if _NO_ROOMS_TEXT in body:
            log(f"{tag}   {Y}No rooms left at {hotel_name} (site reports facility full){X}")
            return False
        missing = [p for p, v in [('form_action', form_action), ('coma_s', coma_s),
                   ('csrf', csrf), ('auth', auth)] if not v]
        title = ex(body, r'<title>(.*?)</title>') or '(no title)'
        snippet = re.sub(r'<[^>]+>', '', body)[:200].strip()
        log(f"{tag}   {R}Missing form params on booking page (status {s}, missing: {', '.join(missing)}){X}")
        log(f"{tag}   {R}  url: {redact_url(loc)}{X}")
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

    # STEP 7: Submit room. This POST takes the site's hold on the room and is the
    # point of no return: nothing in this program can release one. The site then
    # refuses a second application at the same facility, answering the room search
    # 「空き部屋がございません」, so nothing here has to track the hold itself.
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
    # This POST only *dispatches the confirmation email* — the response page is
    # 「メール送信を完了しました」, not a reservation. It still must never be
    # repeated: it consumes `__token__` and sends mail, and `--max-time` can expire
    # after the server accepted it, so a retry would submit twice with no way to
    # tell. Steps 3-8 only navigate, so they stay retryable.
    s, body, loc = c('POST', email_url, post_data, retry=False)
    if s == 0:
        log(f"{tag}   {R}Email submit got no response: outcome unknown, not "
            f"retrying ({hotel_name} may already have been applied for){X}")
        return False
    via = None
    if s == 302 and loc:
        via, (s, body, _) = body, c('GET', loc)

    if 'send_complete' in body:
        # Not a reservation: this is 「メール送信を完了しました」. The room is held and
        # the site has emailed a link that still has to be followed.
        # bookings.json records the *hold*.
        log(f"{tag}   {B}{G}HELD + MAIL SENT: {hotel_name}{X}")
        save_booking(target_date, hotel_name)
        _finish_from_email(c, target_date, hotel_name, tag)
        return True

    log(f"{tag}   {R}Final page not send_complete{X}")
    _dump_debug(f"{target_date}_{hotel_name}", 'step9_final', s, body, via)
    return False


def _finish_from_email(c, target_date, hotel_name, tag):
    """Run steps 7-9 off the emailed link, if that module is usable.

    Imported here rather than at module scope: `confirm_booking` imports this
    module, and it must never be able to stop a booking thread. A hold plus a sent
    email is already worth keeping — the human fallback works from exactly that
    state — so any failure in here is logged and swallowed.
    """
    try:
        import confirm_booking
    except Exception as e:
        log(f"{tag}   {R}Cannot load confirm_booking ({e!r}); "
            f"{hotel_name} needs a human to finish from the mail{X}")
        return
    try:
        status, detail = confirm_booking.confirm_from_email(
            c, target_date, hotel_name, tag)
    except Exception as e:
        log(f"{tag}   {R}Confirmation step raised {e!r}; the room is held and the "
            f"mail is sent — finish it by hand{X}")
        return
    if status == 'confirmed':
        return
    # 'deferred' already explains itself and prints its own HUMAN NEEDED line.
    # 'failed' did not, and it is the case that most needs one: the room is held,
    # the mail is sent, and whatever went wrong leaves a person a short window to
    # finish from the link before the site releases the room to somebody else.
    log(f"{tag}   {Y}{hotel_name} on {target_date} not confirmed "
        f"({status}: {detail}){X}")
    if status == 'failed':
        log(f"{tag}   {B}{Y}HUMAN NEEDED: {hotel_name} on {target_date} is held and "
            f"the mail to {EMAIL} is sent. Open its link and finish now.{X}")


def _open_calendar_session(c, cookie_file, url, target_date, tag, label,
                           check_availability=True):
    """Start a clean session on the calendar, positioned on target_date's month.

    Shared by the first booking attempt and by every subsequent hotel in the
    loop, which previously carried its own copy of these twenty lines and had
    already drifted (the copy dropped the availability check and ignored the
    navigation's status).

    Returns `(outcome, csrf, auth, s_param)` where outcome is one of:
      'ok'           \u2014 session live, month in view
      'retry'        \u2014 transient: 5xx, transport failure, or a dead session
      'unavailable'  \u2014 the date is genuinely not open for application
      'failed'       \u2014 a response that repeating will not fix
    """
    open(cookie_file, 'w').close()  # fresh cookie jar

    s, body, loc = c('GET', url)
    if s != 200:
        if _is_retryable(s) or _is_session_dead(s, loc):
            log(f"{tag} {Y}calendar GET {s}, will retry{X}")
            return 'retry', None, None, None
        log(f"{tag} {Y}calendar GET returned {s}{X}")
        _dump_debug(label, 'calendar_get', s, body)
        return 'failed', None, None, None

    csrf = ex(body, r'csrf-token.*?content="(.*?)"')
    auth = ex(body, r'name="authenticity_token" value="(.*?)"')
    s_param = ex(body, r'name="s" id="s" value="(.*?)"')

    if f'data-join-time="{target_date}"' in body:
        return 'ok', csrf, auth, s_param

    target_ym = f"{target_date[:4]}-{target_date[5:7]}-01"
    s_nav, body_nav, loc_nav = c('POST', BASE + '/calendar_apply/calendar_select',
        {'join_date': target_ym, 's': s_param},
        {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
         'Accept': 'text/javascript, application/javascript, */*; q=0.01',
         'Referer': url})
    if s_nav != 200:
        if _is_retryable(s_nav) or _is_session_dead(s_nav, loc_nav):
            log(f"{tag} {Y}month nav {s_nav}, will retry{X}")
            return 'retry', None, None, None
        log(f"{tag} {Y}month nav returned {s_nav}{X}")
        _dump_debug(label, 'calendar_select', s_nav, body_nav)
        return 'failed', None, None, None

    if check_availability:
        cell = _date_css_class(body_nav, target_date)
        if not is_available(cell):
            log(f"{tag} {Y}date not available (class: {cell or 'no cell found'}){X}")
            return 'unavailable', None, None, None

    return 'ok', csrf, auth, s_param


def _select_date(c, target_date, auth, s_param, tag, label):
    """POST service_group_select. Returns `(outcome, hotels, auth)`.

    This is the request that produces the single largest failure class on disk:
    a 302 to /service_category/index with an empty body. That is a dead session,
    and it is worth another attempt on a fresh one rather than costing the slot.
    """
    s, body, loc = c('POST', BASE + '/calendar_apply/service_group_select',
        {'utf8': '\u2713', 'authenticity_token': auth,
         'join_time': target_date, 's': s_param})
    if s != 200:
        if _is_retryable(s) or _is_session_dead(s, loc):
            log(f"{tag} {Y}date select {s} "
                f"({'session expired' if _is_session_dead(s, loc) else 'transient'}),"
                f" will retry{X}")
            _dump_debug(label, 'service_group_select', s, body)
            return 'retry', [], auth
        log(f"{tag} {Y}date select returned {s}{X}")
        _dump_debug(label, 'service_group_select', s, body)
        return 'failed', [], auth

    all_hotels = [(gid, _html.unescape(name)) for gid, name in
                  re.findall(r'data-service-group-id="(\d+)".*?>(.*?)</a>', body)]
    if not all_hotels:
        log(f"{tag} {Y}no hotels listed (status 200){X}")
        _dump_debug(label, 'service_group_select', s, body)
        return 'failed', [], auth
    return 'ok', all_hotels, ex(body, r'name="authenticity_token" value="(.*?)"')


def book_all_hotels_for_date(target_date, label):
    """Book every eligible hotel for one date, retrying transient failures.

    Returns (date, list_of_booked_hotels).

    A date the scanner just saw as available is worth more than one request.
    Before, a single 503 on the first GET \u2014 49 of them in the last log, several
    landing in the same second the slot was spotted \u2014 abandoned the whole date
    until the next scan cycle RETRY_DELAY later. Retries only cover the setup
    requests; once a hotel booking is under way it either completes or is left
    alone, so nothing can be applied for twice.
    """
    tag = f"[{label}]"
    booked = []
    attempted = set()

    with _BookingInFlight():
        for attempt in range(1, max(1, BOOK_MAX_ATTEMPTS) + 1):
            if attempt > 1:
                log(f"{tag} {C}retry {attempt}/{BOOK_MAX_ATTEMPTS} on a fresh session{X}")
                time.sleep(BOOK_RETRY_DELAY)
            outcome = _book_date_once(target_date, label, tag, booked, attempted)
            if outcome != 'retry':
                break
        else:
            log(f"{tag} {R}gave up on {target_date} after {BOOK_MAX_ATTEMPTS} attempts{X}")

    return target_date, booked


def _cool_date_if_exhausted(target_date, names, tag):
    """Cool the whole date while every hotel offered for it is cooling off.

    Set as soon as a pass runs out of candidates, not on the pass after. A
    per-hotel cooldown alone still left the date paying `_open_calendar_session`
    plus `_select_date` every cycle purely to rediscover there is nothing on it
    worth trying, which is most of what the cooldown was meant to save.
    """
    waits = [cooldown_remaining(target_date, n) for n in names]
    if not waits or not all(w > 0 for w in waits):
        return
    wait = min(waits)
    set_cooldown(target_date, None, wait)
    log(f"{tag} {Y}every hotel for {target_date} is cooling off; leaving the "
        f"date alone for {wait / 60:.0f}m{X}")


def _book_date_once(target_date, label, tag, booked, attempted):
    """One full pass over a date's hotels. Returns an outcome string.

    `booked` is appended to in place and `attempted` records hotels already
    tried, so a retry resumes rather than re-applying for what it already sent.
    """
    # Checked before any request, since it needs none.
    if in_cooldown(target_date, None):
        log(f"{tag} {Y}{target_date} cooling off for another "
            f"{cooldown_remaining(target_date, None) / 60:.0f}m, skipping{X}")
        return 'done'

    url = _read_cached_url()
    if not url:
        return 'failed'

    cookie_fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix=f'cookies_{target_date}_')
    os.close(cookie_fd)

    def c(method, u, data=None, headers=None, retry=True):
        return curl(cookie_file, method, u, data, headers, retry)

    try:
        # csrf is re-extracted per page by book_one_hotel, so it is unused here.
        outcome, _csrf, auth, s_param = _open_calendar_session(
            c, cookie_file, url, target_date, tag, label)
        if outcome != 'ok':
            return outcome

        outcome, all_hotels, auth = _select_date(
            c, target_date, auth, s_param, tag, label)
        if outcome != 'ok':
            return outcome

        log(f"{tag} {C}Found {len(all_hotels)} hotels: "
            f"{', '.join(n for _, n in all_hotels)}{X}")

        already_booked = get_booked_hotels(target_date)
        booked_norm = {_norm_hotel(n) for n in already_booked}
        attempted_norm = {_norm_hotel(n) for n in attempted}
        skipped = [n for _, n in all_hotels if is_skipped(n)]
        already = [n for _, n in all_hotels
                   if _norm_hotel(n) in booked_norm | attempted_norm]
        cooling = [n for _, n in all_hotels if in_cooldown(target_date, n)]
        # Normalized on both sides. Comparing raw names let one full-width space
        # in bookings.json defeat the already-booked filter, which is a duplicate
        # application; is_skipped() has always normalized, so the two disagreed.
        hotels = order_hotels([(gid, name) for gid, name in all_hotels
                               if not is_skipped(name)
                               and _norm_hotel(name) not in booked_norm
                               and _norm_hotel(name) not in attempted_norm
                               and not in_cooldown(target_date, name)])

        if not hotels:
            reasons = []
            if skipped:
                reasons.append(f"skip list: {', '.join(skipped)}")
            if already:
                reasons.append(f"already booked/attempted: {', '.join(already)}")
            if cooling:
                reasons.append('cooling off: ' + ', '.join(
                    f'{n} ({cooldown_remaining(target_date, n) / 60:.0f}m)'
                    for n in cooling))
            log(f"{tag} {Y}All hotels filtered out ({'; '.join(reasons)}){X}")
            _cool_date_if_exhausted(target_date, cooling, tag)
            return 'done'

        log(f"{tag} {C}{len(hotels)} to book (priority first): "
            f"{', '.join(n for _, n in hotels)}{X}")

        for i, (hotel_id, hotel_name) in enumerate(hotels):
            if i > 0:
                # Fresh session per hotel. A failure here is worth reporting up
                # so the outer loop can retry the hotels not yet reached.
                outcome, _csrf, auth, s_param = _open_calendar_session(
                    c, cookie_file, url, target_date, tag, label,
                    check_availability=False)
                if outcome != 'ok':
                    return outcome
                outcome, _relisted, auth = _select_date(
                    c, target_date, auth, s_param, tag, label)
                if outcome != 'ok':
                    return outcome

            attempted.add(hotel_name)
            if book_one_hotel(tag, c, target_date, s_param, auth,
                              hotel_id, hotel_name):
                booked.append(hotel_name)
                log(f"{tag} {B}{G}=== Total booked for {target_date}: "
                    f"{len(booked)} ({', '.join(booked)}){X}")

        _cool_date_if_exhausted(target_date, [n for _, n in hotels], tag)
        return 'done'

    finally:
        try:
            os.unlink(cookie_file)
        except OSError:
            pass



def _future_dates(target_dates):
    """Drop dates that have already passed.

    Over a multi-week run every target date eventually goes by, and a past date
    can never be booked - the site marks those cells `over`. Polling them keeps
    a month's scanner asking forever about something that cannot happen.
    """
    if not SKIP_PAST_DATES:
        return list(target_dates)
    today = date.today().isoformat()
    return [d for d in target_dates if d >= today]


_ISO_DATE = re.compile(r'\A\d{4}-\d{2}-\d{2}\Z')


def days_until(target_date, today=None):
    """Whole days from `today` to `target_date`, or None if it will not parse.

    Strictly `YYYY-MM-DD`, which is the only form used anywhere in this program —
    `TARGET_DATES`, `data-join-time` and the `bookings.json` keys are all that
    shape. `date.fromisoformat` alone is far more liberal: since 3.11 it also
    accepts `20260905` and week dates like `2026-W36-6`, and a safety gate should
    not quietly widen because the standard library did. Anything else here means a
    caller is passing something unexpected, which is exactly when to stop.

    None rather than a raise or a guess: every caller treats an unknown distance as
    "do not commit", and a silent 0 would read as "today", the most dangerous
    possible interpretation.
    """
    if not isinstance(target_date, str) or not _ISO_DATE.match(target_date):
        return None
    try:
        target = date.fromisoformat(target_date)
    except ValueError:
        return None
    return (target - (today or date.today())).days


def confirm_allowed(target_date, today=None):
    """`(allowed, reason)` — may the application for this date be *completed*?

    Completing an application means 予約確定: a real reservation carrying a real
    cancellation liability. Free cancellation is web-only and ends at D−10; from
    D−9 it costs 50% and has to be arranged by telephone in office hours, and the
    full amount on the day of use. So anything inside that window must not be
    committed without a person deciding to.

    This gate governs the final POSTs only. It deliberately does not stop the room
    hold or the confirmation email: those are free and reversible (the hold simply
    lapses after 30 minutes), and having them in place is what lets a human finish
    a near-date booking themselves if they want it.

    Fails closed. An unparseable date, a missing config value or a clock we cannot
    reason about all return False, because the cost of wrongly committing is a
    non-refundable booking and the cost of wrongly refusing is one lost slot.
    """
    if not AUTO_CONFIRM:
        return False, 'AUTO_CONFIRM is off'

    left = days_until(target_date, today)
    if left is None:
        return False, f'cannot read a date out of {target_date!r}'

    try:
        minimum = int(AUTO_CONFIRM_MIN_DAYS)
    except (TypeError, ValueError):
        return False, f'AUTO_CONFIRM_MIN_DAYS is not a number: {AUTO_CONFIRM_MIN_DAYS!r}'
    if minimum < 0:
        return False, f'AUTO_CONFIRM_MIN_DAYS is negative: {minimum}'

    if left < minimum:
        return False, (f'{target_date} is {left} day(s) away, inside the '
                       f'{minimum}-day floor — free cancellation ends at D-10, '
                       f'so a person has to decide on this one')
    return True, ''


def _month_rendered(status, body):
    """True when a month-nav response actually carries a calendar.

    Status alone is not a sufficient test. If the site runs Rails'
    `protect_from_forgery with: :null_session`, a request whose CSRF token is
    rejected is not answered with 422 — it runs with an empty session and can
    return 200 and a page with no date cells in it. A scanner that trusted the
    status would then report "no dates available" for as long as it kept
    replaying a stale token, while looking perfectly healthy.
    """
    return status == 200 and 'data-join-time' in body


def scan_and_book_month(month_str, target_dates, label, stop_event=None):
    """Scan a month's calendar for availability, spawn booking threads per date.

    Runs indefinitely. Each cycle checks ALL target dates for the month with a
    single `calendar_select` POST; the calendar GET that mints the csrf/s pair for
    it is skipped while a cached pair still works (SCAN_REUSE_SESSION), so the
    steady-state cycle is one request rather than two.

    When availability is found, spawns parallel booking threads (one per date).
    If URL is missing or expired, logs and waits for next cycle.

    The whole loop body is guarded. Nothing in here may end the thread: it is the
    only thing scanning this month, it is a daemon, and main() never joins it — so
    an escaping exception would stop the month silently while the process carried
    on looking healthy.

    `stop_event` is an optional threading.Event for a cooperative shutdown.
    Production leaves it None and relies on daemon threads; the tests set it so a
    finished test's scanner cannot keep booking into the next one's fixtures.
    """
    tag = f"[{label}]"
    month_ym = f"{month_str}-01"

    def stopped():
        return stop_event is not None and stop_event.is_set()

    def nap(seconds):
        """Sleep, but wake immediately on a stop request."""
        if stop_event is not None:
            stop_event.wait(seconds)
        else:
            time.sleep(seconds)

    cookie_fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix=f'cookies_scan_{month_str}_')
    os.close(cookie_fd)

    def c(method, u, data=None, headers=None, retry=True):
        return curl(cookie_file, method, u, data, headers, retry)

    def mint_tokens(url, attempt):
        """GET the calendar for a fresh (csrf, s) pair. None if the GET failed.

        This is the request session reuse exists to avoid, and it is also the
        most failure-prone one in the program — most dumps on record are a 503
        here, and a 503 is how the 24-hour IP ban presents.
        """
        st, bd, lc = c('GET', url)
        if st != 200:
            # The cookie jar is only reset after a failure. Truncating it every
            # cycle asked the site for a brand-new session about 4,300 times a
            # day and threw a working one away each time.
            if _is_session_dead(st, lc) or _is_retryable(st):
                open(cookie_file, 'w').close()
            log(f"{tag} {Y}[{attempt}] URL returned {st}, waiting...{X}")
            return None
        return (ex(bd, r'csrf-token.*?content="(.*?)"'),
                ex(bd, r'name="s" id="s" value="(.*?)"'))

    def nav_month(tokens, url):
        """POST calendar_select. Its response carries every date in the month."""
        csrf, s_param = tokens
        return c('POST', BASE + '/calendar_apply/calendar_select',
            {'join_date': month_ym, 's': s_param},
            {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
             'Accept': 'text/javascript, application/javascript, */*; q=0.01',
             'Referer': url})

    try:
        attempt = 0
        failures = 0          # consecutive failed cycles, drives the backoff
        idle_since = None     # start of the current unchanged idle streak
        idle_count = 0
        tokens = None         # cached (csrf, s_param) for the live session
        tokens_url = None     # the URL they were minted from
        reuse = SCAN_REUSE_SESSION
        reuse_failures = 0    # consecutive rejections of a cached pair
        while not stopped():
            attempt += 1
            if attempt > 1:
                # Back off while the site is unhappy, and jitter always: the
                # per-month scanners otherwise settle into lockstep and arrive
                # as a burst.
                delay = min(RETRY_DELAY * (2 ** min(failures, 8)), SCAN_BACKOFF_MAX)
                nap(delay + random.uniform(0, SCAN_JITTER))
                if stopped():
                    break

            try:
                dates = _future_dates(target_dates)
                if not dates:
                    log(f"{tag} {Y}all target dates are in the past, idle{X}")
                    nap(SCAN_BACKOFF_MAX)
                    continue

                url = _read_cached_url()
                if not url:
                    log(f"{tag} {Y}[{attempt}] no URL available, waiting...{X}")
                    failures += 1
                    continue

                if url != tokens_url:
                    # A re-solved CAPTCHA means a new token and a new session.
                    tokens, tokens_url = None, url

                s_nav = 0
                body_nav = ''
                if tokens is not None:
                    s_nav, body_nav, _ = nav_month(tokens, url)
                    if _month_rendered(s_nav, body_nav):
                        reuse_failures = 0
                    else:
                        # Gate on the response *shape*, not the status — see
                        # _month_rendered. Re-mint in this same cycle rather than
                        # sleeping on it, and do not charge `failures` for it: a
                        # token reaching the end of its session is expected, not
                        # the site being unhappy, and letting it drive the
                        # backoff would walk the poll interval up to
                        # SCAN_BACKOFF_MAX and stop finding anything.
                        reuse_failures += 1
                        log(f"{tag} {Y}[{attempt}] cached session rejected "
                            f"({s_nav}), re-minting{X}")
                        open(cookie_file, 'w').close()
                        tokens = None
                        if reuse_failures >= max(1, SCAN_REUSE_MAX_FAILURES):
                            reuse = False
                            log(f"{tag} {Y}session reuse disabled after "
                                f"{reuse_failures} rejections; back to a "
                                f"calendar GET per cycle{X}")

                if tokens is None:
                    tokens = mint_tokens(url, attempt)
                    if tokens is None:
                        failures += 1
                        continue
                    s_nav, body_nav, _ = nav_month(tokens, url)

                if not _month_rendered(s_nav, body_nav):
                    # Freshly minted tokens and still no calendar. Without this
                    # the scan reports "no dates available" forever while the URL
                    # monitor's plain GET still returns 200, so no re-solve fires
                    # and nothing ever gets booked again.
                    log(f"{tag} {Y}[{attempt}] month nav returned {s_nav}, waiting...{X}")
                    open(cookie_file, 'w').close()
                    tokens = None
                    _dump_debug(label, 'calendar_select', s_nav, body_nav)
                    failures += 1
                    continue

                if not reuse:
                    tokens = None   # re-mint every cycle, as before

                failures = 0

                available = [td for td in dates
                             if is_available(_date_css_class(body_nav, td))]

                if not available:
                    # 116k of the last log's 150k lines were this one message.
                    # Collapse an unchanged idle streak into one line per
                    # IDLE_LOG_INTERVAL; everything else still logs in full.
                    idle_count += 1
                    now = time.monotonic()
                    if idle_since is None or now - idle_since >= IDLE_LOG_INTERVAL:
                        extra = f', {idle_count} scans' if idle_count > 1 else ''
                        log(f"{tag} {Y}[{attempt}] no dates available "
                            f"({len(dates)} checked{extra}), waiting...{X}")
                        idle_since, idle_count = now, 0
                    continue

                idle_since, idle_count = None, 0
                log(f"{tag} {C}[{attempt}] {len(available)}/{len(dates)} dates "
                    f"available: {', '.join(d[5:] for d in available)}{X}")

                # BOOK: Spawn parallel threads, one per available date
                with ThreadPoolExecutor(max_workers=len(available)) as pool:
                    futures = {pool.submit(book_all_hotels_for_date, td,
                                           f"{label} {td[5:]}"): td
                               for td in available}
                    for future in as_completed(futures):
                        try:
                            td, booked_list = future.result()
                        except Exception as e:
                            log(f"{tag} {R}Booking thread for "
                                f"{futures[future]} failed: {e!r}{X}")
                            continue
                        if booked_list:
                            log(f"{tag} {G}Booked for {td}: "
                                f"{', '.join(booked_list)}{X}")

            except Exception as e:
                failures += 1
                log(f"{tag} {R}[{attempt}] scan cycle error: {e!r}{X}")

    finally:
        try:
            os.unlink(cookie_file)
        except OSError:
            pass
