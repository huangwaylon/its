#!/usr/bin/env python3
"""Booking engine — scans calendar months and books all available hotels per date.

Pure booking logic only. Reads the calendar URL from calendar_url_cache.txt each
cycle. If the URL is missing or expired, it simply waits for the next cycle
(the URL monitor in main.py handles CAPTCHA solving separately).
"""
import subprocess, re, urllib.parse, os, json, tempfile, threading, time, hashlib
import base64
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
    DEBUG_DUMP_INTERVAL, DEBUG_DUMP_KEEP, IDLE_LOG_INTERVAL, SKIP_PAST_DATES,
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


# Set when the bookings file could not be parsed. While this is set, writes
# refuse to replace the file wholesale: a bad read returning {} followed by a
# normal save would rewrite the file with only the one new entry and destroy
# every prior booking. The last log has 5 bookings for 2026-08-22 that no longer
# appear in bookings.json, which is exactly that failure having already happened.
_bookings_unreadable = False


def _load_bookings():
    global _bookings_unreadable
    if not os.path.exists(BOOKINGS_FILE):
        _bookings_unreadable = False
        return {}
    try:
        with open(BOOKINGS_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        data = json.loads(content) if content else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _bookings_unreadable = True
        log(f"{R}Warning: failed to load {BOOKINGS_FILE}: {e}{X}")
        return {}
    if not isinstance(data, dict):
        _bookings_unreadable = True
        log(f"{R}Warning: {BOOKINGS_FILE} is not an object, ignoring{X}")
        return {}
    _bookings_unreadable = False
    return data


def save_booking(date_str, hotel_name):
    """Record a successful booking. Thread-safe, atomic, never destructive."""
    with _bookings_lock:
        bookings = _load_bookings()
        if _bookings_unreadable:
            # Move the unreadable file aside instead of overwriting it, so the
            # bytes survive for inspection and this booking is still recorded.
            # The sequence number matters: two corruptions in the same second
            # would otherwise have the second rename clobber the first salvage.
            salvage = (f'{BOOKINGS_FILE}.corrupt.'
                       f'{datetime.now():%Y%m%d_%H%M%S}.{_dump_seq()}')
            try:
                os.replace(BOOKINGS_FILE, salvage)
                log(f"{Y}Unreadable bookings file preserved as "
                    f"{os.path.basename(salvage)}{X}")
            except OSError as e:
                log(f"{R}Could not preserve unreadable bookings file: {e}{X}")
        if hotel_name in bookings.get(date_str, []):
            return
        bookings.setdefault(date_str, []).append(hotel_name)
        try:
            _write_bookings(bookings)
        except OSError as e:
            # The booking itself succeeded on the site; losing the record only
            # risks a duplicate attempt later, so this must not raise into the
            # booking thread and abort the remaining hotels.
            log(f"{R}BOOKED but failed to record {hotel_name} for {date_str}: {e}{X}")


def _write_bookings(bookings):
    """Write the bookings file atomically — same directory, then rename.

    A plain `open(..., 'w')` truncates first, so a crash or a full disk midway
    through leaves a half-written file that parses as nothing. This process is
    meant to run unattended for weeks; the record of what is already booked is
    the only thing preventing duplicate applications.
    """
    d = os.path.dirname(os.path.abspath(BOOKINGS_FILE))
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.bookings_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(bookings, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, BOOKINGS_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_booked_hotels(date_str):
    with _bookings_lock:
        return _load_bookings().get(date_str, [])



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


def curl(cookie_file, method, url, data=None, headers=None):
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
    attempts = max(1, CURL_MAX_ATTEMPTS)
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


# ── The `s=` token ──────────────────────────────────────────────────
# The token is not opaque. Decoded live, an 88-character token is:
#
#   base64 -> reverse -> base64 -> "service_category_id=1&verify_expires=<10 digits>"
#
# 47 bytes of printable ASCII with nothing left over: no signature, no MAC.
# Nothing cryptographically binds the token to the Turnstile solve that
# produced it.
#
# That has a direct consequence for logging. Because the payload is only those
# two fields, and one of them is constant, printing `verify_expires` in full is
# equivalent to printing the token — anyone holding the log could rebuild it. So
# timestamp fields are logged as a *relative* delta, never as an absolute value,
# and every other field's value is masked. Field *names* are always shown, so a
# field the site adds later becomes visible without its value leaking.
#
# `verify_expires` is worth watching because it could tell the URL monitor when
# to refresh instead of guessing at URL_REFRESH_INTERVAL. It is not usable for
# that yet: one live sample contradicted the obvious reading (a token minted
# 2026-08-18 13:33 carried 2026-08-08 19:12:52, ten days in its own past), so
# the field needs to be observed across several solves before anything relies on
# it. Every token in the previous log was truncated at 80 characters and cannot
# be decoded, which is exactly why this exists.

_PRINTABLE_ASCII = re.compile(r'\A[\x20-\x7e]+\Z')
_TOKEN_PAYLOAD = re.compile(r'\A[^=&]+=[^=&]*(?:&[^=&]+=[^=&]*)*\Z')
# A 10-digit value in roughly 2020..2100, i.e. plausibly unix epoch seconds.
_EPOCH_RANGE = (1577836800, 4102444800)


def _b64_to_ascii(s):
    """base64-decode to a printable-ASCII str, or None. Padding is inferred."""
    for pad in ('', '=', '=='):
        try:
            raw = base64.b64decode(s + pad, validate=True)
        except Exception:
            continue
        try:
            out = raw.decode('ascii')
        except UnicodeDecodeError:
            return None
        return out if _PRINTABLE_ASCII.match(out) else None
    return None


def decode_s_token(token):
    """The token's plaintext payload as an ordered list of (key, value).

    Returns None if it does not decode cleanly. Deliberately strict: a truncated
    token still base64-decodes into plausible-looking bytes, and reporting
    garbage as a payload is worse than reporting nothing at all.
    """
    if not token:
        return None
    middle = _b64_to_ascii(token)
    if middle is None:
        return None
    payload = _b64_to_ascii(middle[::-1])
    if payload is None or not _TOKEN_PAYLOAD.match(payload):
        return None
    return [(k, v) for k, _, v in (p.partition('=') for p in payload.split('&'))]


def _relative(seconds):
    """A signed, human duration: `+2h34m`, `-11m`, `+0s`."""
    sign = '-' if seconds < 0 else '+'
    s = int(abs(seconds))
    if s >= 3600:
        return f'{sign}{s // 3600}h{(s % 3600) // 60:02d}m'
    if s >= 60:
        return f'{sign}{s // 60}m{s % 60:02d}s'
    return f'{sign}{s}s'


def token_summary(url, now=None):
    """Loggable summary of a calendar URL's `s=` token. Never raises.

    Runs in the URL monitor's logging path — the one thread that re-mints a
    session — so a malformed URL must not cost a solve.
    """
    try:
        token = urllib.parse.parse_qs(
            urllib.parse.urlsplit(url or '').query).get('s', [''])[0]
        if not token:
            return 'no s= token in URL'
        fields = decode_s_token(token)
        if fields is None:
            return f'token does not decode ({len(token)} chars)'
        now = time.time() if now is None else now
        out = []
        for k, v in fields:
            if v.isdigit() and _EPOCH_RANGE[0] <= int(v) <= _EPOCH_RANGE[1]:
                # Relative only. The absolute value would reconstruct the token.
                out.append(f'{k}={_relative(int(v) - now)}')
            elif len(v) <= 4:
                out.append(f'{k}={v}')
            else:
                out.append(f'{k}=<{len(v)} chars>')
        return ' '.join(out)
    except Exception as e:
        return f'token unreadable ({e.__class__.__name__})'


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

    Throttled per (label, step) and pruned to DEBUG_DUMP_KEEP files. A failure
    that repeats every cycle used to write one pair of files per cycle for as
    long as it lasted: 83 of the 380 existing dumps are one date's
    service_group_select, and 302 of them are 0-byte bodies.
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

    if check_availability and 'empty' not in _date_css_class(body_nav, target_date):
        log(f"{tag} {Y}date not available{X}")
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


def _book_date_once(target_date, label, tag, booked, attempted):
    """One full pass over a date's hotels. Returns an outcome string.

    `booked` is appended to in place and `attempted` records hotels already
    tried, so a retry resumes rather than re-applying for what it already sent.
    """
    url = _read_cached_url()
    if not url:
        return 'failed'

    cookie_fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix=f'cookies_{target_date}_')
    os.close(cookie_fd)

    def c(method, u, data=None, headers=None):
        return curl(cookie_file, method, u, data, headers)

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
        skipped = [n for _, n in all_hotels if is_skipped(n)]
        already = [n for _, n in all_hotels
                   if n in already_booked or n in attempted]
        hotels = order_hotels([(gid, name) for gid, name in all_hotels
                               if not is_skipped(name)
                               and name not in already_booked
                               and name not in attempted])

        if not hotels:
            reasons = []
            if skipped:
                reasons.append(f"skip list: {', '.join(skipped)}")
            if already:
                reasons.append(f"already booked/attempted: {', '.join(already)}")
            log(f"{tag} {Y}All hotels filtered out ({'; '.join(reasons)}){X}")
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


def scan_and_book_month(month_str, target_dates, label, stop_event=None):
    """Scan a month's calendar for availability, spawn booking threads per date.

    Runs indefinitely. Each cycle: 1 GET + 1 POST checks ALL target dates.
    When availability is found, spawns parallel booking threads (one per date).
    If URL is missing or expired, logs and waits for next cycle.

    The whole loop body is guarded. Nothing in here may end the thread: it is the
    only thing scanning this month, it is a daemon, and main() blocks on the
    display rather than joining it - so an escaping exception would stop the
    month silently while the process carried on looking healthy.

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

    def c(method, u, data=None, headers=None):
        return curl(cookie_file, method, u, data, headers)

    try:
        attempt = 0
        failures = 0          # consecutive failed cycles, drives the backoff
        idle_since = None     # start of the current unchanged idle streak
        idle_count = 0
        while not stopped():
            attempt += 1
            if attempt > 1:
                # Back off while the site is unhappy, and jitter always: the
                # per-month scanners otherwise settle into lockstep and arrive
                # as a burst, the likeliest reason 1072 of the last log's
                # calendar GETs came back 503.
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

                # SCAN: Load calendar (1 GET). The cookie jar is only reset
                # after a failure - truncating it every cycle asked the site for
                # a brand-new session about 4,300 times a day and threw away a
                # working one each time.
                s, body, loc = c('GET', url)
                if s != 200:
                    if _is_session_dead(s, loc) or _is_retryable(s):
                        open(cookie_file, 'w').close()
                    log(f"{tag} {Y}[{attempt}] URL returned {s}, waiting...{X}")
                    failures += 1
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
                    open(cookie_file, 'w').close()
                    _dump_debug(label, 'calendar_select', s_nav, body_nav)
                    failures += 1
                    continue

                failures = 0

                available = [td for td in dates
                             if 'empty' in _date_css_class(body_nav, td)]

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
