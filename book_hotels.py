#!/usr/bin/env python3
"""Booking engine — scans calendar months and takes a hold on every available date.

Reads the calendar URL from calendar_url_cache.txt each cycle and never solves a
CAPTCHA; main.py's URL monitor does that. Ends at the hold — confirm_booking's worker
turns it into a reservation.
"""
import subprocess, re, urllib.parse, os, json, tempfile, threading, time, hashlib
import contextlib
import html as _html
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date

from config import (
    CALENDAR_URL_CACHE, EMAIL, NUM_GUESTS,
    HOLDS_FILE, RETRY_DELAY, CURL_MAX_ATTEMPTS, SKIP_HOTELS,
    DEBUG_DIR, USER_AGENT_CACHE, BROWSER_HEADERS, ACCEPT, ACCEPT_LANGUAGE,
    FALLBACK_USER_AGENT, PRIORITY_HOTELS,
    CURL_RETRY_BACKOFF, CURL_RETRY_BACKOFF_MAX, CURL_TIMEOUT,
    BOOK_MAX_ATTEMPTS, BOOK_RETRY_DELAY,
    SCAN_BACKOFF_MAX, SCAN_JITTER,
    SCAN_REUSE_SESSION, SCAN_REUSE_MAX_FAILURES,
    AUTO_CONFIRM, AUTO_CONFIRM_MIN_DAYS,
    DEBUG_DUMP_INTERVAL, DEBUG_DUMP_KEEP, SKIP_PAST_DATES,
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


_log_handler = None  # main() routes this to stdout + LOG_FILE

def log(msg):
    """Reserve red for what is actually wrong: 「空き部屋がございません」 is an ordinary lost
    race, so it is yellow with no dump."""
    ts = datetime.now().strftime('%H:%M:%S')
    formatted = f'{ts} {msg}'
    if _log_handler:
        _log_handler(formatted)
    else:
        print(formatted, flush=True)


# Thread-safe holds access
_bookings_lock = threading.Lock()

# main.py's URL monitor reads this to defer a *proactive* CAPTCHA refresh: a booking
# carries one `s=` token across a ~10-request chain.
_active_lock = threading.Lock()
_active_bookings = 0


def active_bookings():
    with _active_lock:
        return _active_bookings


@contextlib.contextmanager
def _booking_in_flight():
    global _active_bookings
    with _active_lock:
        _active_bookings += 1
    try:
        yield
    finally:
        with _active_lock:
            _active_bookings -= 1


def _read_cached_url():
    """The current calendar URL, or None. Never raises: a scanner's loop body has no
    `except` around this, so an OSError would kill that month permanently."""
    try:
        with open(CALENDAR_URL_CACHE) as f:
            url = f.read().strip()
        return url or None
    except OSError:
        return None


def _load_bookings(path):
    """`(bookings, ok)`; `ok` is False when the contents are unknown.

    Never treat `not ok` as "nothing is booked" — a bad read returning {} plus a
    normal save rewrites the file with only the new entry. Losing one record risks a
    duplicate attempt; losing the file risks duplicating everything. Assumes
    `_bookings_lock` is held.
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
    """Record a hold or a reservation. Thread-safe, atomic, never destructive.

    `path` defaults to HOLDS_FILE; `confirm_booking` passes RESERVATIONS_FILE. A
    parameter, not a swapped global — both files are written concurrently.
    """
    path = path or HOLDS_FILE
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
            # It succeeded on the site, so this must not raise into the booking
            # thread and abort the remaining hotels.
            log(f"{R}BOOKED but failed to record {hotel_name} for {date_str}: {e}{X}")


def _write_bookings(bookings, path):
    """Write atomically — same directory, fsync, rename. `open(..., 'w')` truncates
    first, and this record is the only thing preventing duplicate applications."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.holds_', suffix='.tmp')
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
        return _load_bookings(HOLDS_FILE)[0].get(date_str, [])


class Response(str):
    """The response body with its own headers attached — a plain `str` to every call
    site, so `_dump_debug` gets *this* body's headers without widening the tuple that
    17 call sites unpack."""
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

# A UA must name a desktop platform: a mobile one can make the site serve a
# different template, breaking the markup-exact extractors and SKIP_HOTELS matching.
_UA_PLATFORMS = ('Macintosh', 'Windows NT', 'X11')


def _user_agent():
    """The UA of the Chrome that minted the current session token.

    Re-read when captcha_solver rewrites the file, so it cannot drift out of sync with
    its session. Never raises: callers sit outside curl()'s try, so an exception would
    kill a scanner or the URL monitor.
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

    In Python, not by appending `-H`: curl emits every header it is given, so a
    default plus a per-call `Accept` sends both and lets the server pick.
    Case-insensitive, so a case variant cannot duplicate a line either.
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
    """True for statuses worth sending again: 5xx and 0 (transport failure) are load,
    429 is explicit rate limiting, any other 4xx is a rejection. 302 is deliberately
    excluded — it is either flow progress or a dead session, and `_is_session_dead`
    tells those apart."""
    return status == 0 or status == 429 or status >= 500


def curl(cookie_file, method, url, data=None, headers=None, retry=True):
    """One HTTP request via curl. Returns `(status, Response, location)`.

    `retry=False` disables the retry loop for a request that must never be repeated.
    Retrying is safe for anything that only reads or navigates.
    """
    cmd = ['curl', '-s', '-c', cookie_file, '-b', cookie_file,
           '-D', '/dev/stderr', '--max-redirs', '0', '--max-time', str(CURL_TIMEOUT)]
    if method == 'POST':
        cmd.extend(['-X', 'POST'])
    cmd.extend(header_args(headers))
    if data:
        # `ex()` yields None when it found nothing; a browser would submit an empty
        # value, and a missing token here is the real cause of whatever follows.
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
            # Must not kill a scanner: the scan loop has no except around this.
            status, body, hdrs = 0, '', ''
            log(f"  {R}curl {method} raised: {e}{X}")
        else:
            body = r.stdout
            hdrs = r.stderr
            st = re.findall(r'HTTP/\S+ (\d+)', hdrs)
            status = int(st[-1]) if st else 0
        if not _is_retryable(status) or attempt + 1 == attempts:
            break
        # A zero-delay retry puts both attempts in the same millisecond, against a
        # server answering 503 because it is being asked too often. `Retry-After`
        # wins when the server states a figure.
        delay = min(CURL_RETRY_BACKOFF * (2 ** attempt), CURL_RETRY_BACKOFF_MAX)
        delay = max(delay, _retry_after(hdrs))
        log(f"  {Y}curl {method} failed ({status}), retrying in {delay:.1f}s...{X}")
        time.sleep(delay)
    loc = re.search(r'location: (.+)', hdrs, re.IGNORECASE)
    location = loc.group(1).strip() if loc else None
    return status, Response(body, hdrs, location, f'{method} {url}'), location


def _retry_after(hdrs):
    """Seconds from a `Retry-After` header, capped — the site may name a figure
    longer than the slot will survive. 0 if absent."""
    m = re.search(r'^retry-after:\s*(\d+)\s*$', hdrs or '', re.IGNORECASE | re.MULTILINE)
    if not m:
        return 0.0
    return min(float(m.group(1)), CURL_RETRY_BACKOFF_MAX)


def ex(html, pat):
    m = re.search(pat, html)
    return m.group(1) if m else None


# Named once because they are markup-exact: a template change is then a one-line
# fix rather than a hunt through six identical literals.
_AUTH_RE = r'name="authenticity_token" value="(.*?)"'
_CSRF_RE = r'csrf-token.*?content="(.*?)"'
_S_RE = r'name="s" id="s" value="(.*?)"'

# Every AJAX request the site expects as Rails-UJS. All four are required: without
# X-Requested-With the server answers HTML or a redirect instead of JavaScript.
def _ajax(csrf, referer):
    return {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrf,
            'Accept': 'text/javascript, application/javascript, */*; q=0.01',
            'Referer': referer}


def _follow(c, s, body, loc):
    """Follow a 302 by hand. Returns `(status, body, via)`, where `via` is the
    redirect itself — a dump taken after the follow-up GET would record the wrong
    response's headers."""
    if s == 302 and loc:
        return (*c('GET', loc)[:2], body)
    return s, body, None


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
    cf-cache-status cf-ray location
""".split())

# Set-Cookie attributes, kept verbatim. Anything else in the attribute list is
# a second comma-joined cookie (or a value that broke across a `;`), so it gets
# fingerprinted instead of passed through.
_COOKIE_ATTRS = frozenset(
    'expires max-age domain path samesite priority'.split())
_COOKIE_FLAGS = frozenset('secure httponly partitioned'.split())

def _fingerprint(value):
    """Stable, non-reversible stand-in for a secret. The digest is what lets two
    dumps be compared — same session or a fresh one — without either holding it."""
    digest = hashlib.sha256(value.encode('utf-8', 'replace')).hexdigest()[:8]
    return f'[len={len(value)} sha256={digest}]'


def _redact_set_cookie(value):
    """Fingerprint the value, keep the name and the real attributes — `Max-Age=0` or a
    past `Expires` is how a session *reset* is told from a *re-issue*."""
    out = []
    for i, part in enumerate(value.split(';')):
        name, eq, val = part.partition('=')
        attr = name.strip().lower()
        if i == 0:  # the cookie itself
            out.append(f'{name}={_fingerprint(val)}' if eq else _fingerprint(part))
        elif eq and attr in _COOKIE_ATTRS:
            # Only an RFC-1123 Expires may contain a comma; anywhere else it is
            # the join between two cookies, so the tail is a second name=value.
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
        # Splice by span, not str.replace: a short value can also occur in the
        # surrounding markup, and the dump exists to show those tags.
        start, end = m.start(1) - m.start(0), m.end(1) - m.start(0)
        whole = m.group(0)
        return whole[:start] + _fingerprint(m.group(1)) + whole[end:]
    for pat in _BODY_SECRETS:
        body = re.sub(pat, replace, body)
    return _redact_applicant(body)


# Inputs whose *name* marks them as carrying 資格認証のキー. Matched on the name, so a
# value the site reformats is still caught where exact-value matching would miss.
_PII_FIELD_NAME = re.compile(
    r'sign_no|kigou|insured_no|bangou|kana|birth|\[year\]|\[month\]|\[day\]'
    r'|\btel\b|phone|denwa|postal|\bzip\b|post_?code|address|juu?sho'
    r'|office_name|jigyou?sho|\bmail\b', re.I)

_PII_INPUT = re.compile(
    r'(?is)<input\b(?=[^>]*\bname=(["\'])(?P<name>[^"\']*)\1)'
    r'[^>]*?\bvalue=(["\'])(?P<value>[^"\']*)\3[^>]*>')


def _redact_applicant(body):
    """Strip the applicant's 資格認証のキー out of a body before it is stored —
    申込内容確認画面 echoes every one of them back.

    Two passes, because neither alone suffices: every `value="…"` on an input whose
    *name* looks like an identity field, plus each configured value found literally in
    the prose. `sex`/`zokugara` are left alone — 男/女 and 本人 identify nobody and
    appear in the form's own `<option>` labels regardless. Same for values under 3.
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
    """`2000-03-04` as the site writes it back: 申込内容確認画面 echoes 生年月日 as prose,
    in neither the three-select form it was submitted in nor the format `.env` stores,
    so neither other pass finds it. Padded and unpadded, since the rendering follows
    no rule we control."""
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
    """Save an unexpected HTTP response, redacted, for later debugging.

    The headers are the diagnostic payload: whether a 302 carries `x-runtime` (Rails
    made it) or not (Apache/ALB/WAF did), whether `content-length` is 0 by intent or
    truncated, whether `set-cookie` re-issued the session. `via` is the response
    *before* a redirect was followed. Throttled and pruned, so a failure repeating
    every cycle cannot fill the disk.
    """
    try:
        if throttle and not _dump_allowed(label, step):
            return
        os.makedirs(DEBUG_DIR, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_label = re.sub(r'[^\w.\-]+', '_', label)
        # Second-resolution timestamps plus parallel dates means two dumps can land
        # in the same second; without the counter one silently overwrites the other.
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
        loc = getattr(via if via is not None else body, 'location', None) or''
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
    """True at most once per DEBUG_DUMP_INTERVAL per label+step. Keyed on elapsed time,
    not status, so an upstream flapping between two statuses cannot dump every cycle."""
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
# Names arrive HTML-escaped and mix full-width (U+3000) with ordinary spaces, so raw
# equality lets a skip entry silently miss — and an unmatched skip name books a hotel
# meant to be skipped. Normalizing both sides removes the whole class.

def _norm_hotel(name):
    """Casefold, unescape entities, and collapse all whitespace."""
    return re.sub(r'\s+', '', _html.unescape(name or '')).casefold()


_SKIP_NORM = frozenset(_norm_hotel(n) for n in SKIP_HOTELS if _norm_hotel(n))
_PRIORITY_NORM = tuple(_norm_hotel(p) for p in PRIORITY_HOTELS if _norm_hotel(p))


def order_hotels(hotels):
    """Sort (id, name) pairs so PRIORITY_HOTELS come first, in configured order.

    Hotels book sequentially at ~10 requests each, so this is the difference between
    attempting NAGU at once and a minute later behind five others. Stable, so the rest
    keep the site's order; unmatched names rank last.
    """
    def rank(name):
        n = _norm_hotel(name)
        return next((i for i, p in enumerate(_PRIORITY_NORM) if p in n),
                    len(_PRIORITY_NORM))
    return sorted(hotels, key=lambda h: rank(h[1]))


def is_skipped(name):
    return _norm_hotel(name) in _SKIP_NORM



def _date_css_class(body, date):
    """Extract the CSS class for a date cell from escaped JS response."""
    return ex(body, rf'class=\\"([^"\\]*)\\\"[^>]*data-join-time=\\"{date}\\"') or ''


# Calendar cell classes: `empty` (○) and `a_little` (few left) are both clickable
# and both applicable for; `full` and `over` are not. Matching `empty` alone
# silently skipped every limited-availability date — the slots most likely to still
# be open were the ones being ignored. See docs/SITE.md §4.
_AVAILABLE_CLASSES = ('empty', 'a_little')


def is_available(css_class):
    """True if a calendar cell's CSS class marks the date as bookable."""
    return any(c in (css_class or '') for c in _AVAILABLE_CLASSES)


# The site's "no vacant rooms in the specified facility" page. It has no booking
# form, so without this check it is indistinguishable from a broken extractor.
_NO_ROOMS_TEXT = '空き部屋がございません'

# Path every expired-session redirect lands on: a stale `s=` token answers 302,
# 0 bytes, `Location: /service_category/index`. The largest failure class on disk.
_SESSION_DEAD_PATH = '/service_category/index'


def _is_session_dead(status, location):
    return status == 302 and _SESSION_DEAD_PATH in (location or '')


def book_one_hotel(tag, c, target_date, s_param, auth, hotel_id, hotel_name):
    """Book a single hotel for a date. Steps 3-9. Returns True on success."""
    label = f"{target_date}_{hotel_name}"

    # STEP 3: Select hotel
    log(f"{tag} {C}Booking: {hotel_name}{X}")
    s, body, _ = c('POST', BASE + '/calendar_apply/apply_service_select',
        {'utf8': '\u2713', 'authenticity_token': auth, 'empty': '',
         'join_time': target_date, 's': s_param, 'service_group_id': hotel_id})
    services = re.findall(r'data-apply-service-id="(\d+)".*?>(.*?)</a>', body)
    if not services:
        log(f"{tag}   {R}No services for {hotel_name}{X}")
        _dump_debug(label, 'step3_service_select', s, body)
        return False
    auth = ex(body, _AUTH_RE)

    # STEP 4: Select service (302)
    service_id = services[0][0]
    s, body, loc = c('POST', BASE + '/calendar_apply/check_apply_service_coma',
        {'utf8': '\u2713', 'authenticity_token': auth,
         'join_time': target_date, 's': s_param, 'apply_service_id': service_id})
    if not loc or 'empty_new' not in loc:
        log(f"{tag}   {R}Step 4 redirect failed{X}")
        _dump_debug(label, 'step4_check_coma', s, body)
        return False

    # STEP 5: Load booking form
    referer_url = loc
    s, body, _ = c('GET', loc)
    csrf = ex(body, _CSRF_RE)
    auth = ex(body, _AUTH_RE)
    form_action = ex(body, r'action="(/apply/empty_create\?s=[^"]+)"')
    coma_s = ex(body, r"coma_search\('([^']+)'\)")
    if not form_action or not coma_s:
        # The no-vacant-rooms page has no booking form, so it lands here. An ordinary
        # lost race, not a fault, so no red lines and no dump.
        if _NO_ROOMS_TEXT in body:
            log(f"{tag}   {Y}No rooms left at {hotel_name} (site reports facility full){X}")
            return False
        missing = [p for p, v in [('form_action', form_action), ('coma_s', coma_s),
                   ('csrf', csrf), ('auth', auth)] if not v]
        title = ex(body, r'<title>(.*?)</title>') or '(no title)'
        snippet = re.sub(r'<[^>]+>', '', body)[:200].strip()
        log(f"{tag}   {R}Missing form params on booking page (status {s}, missing: {', '.join(missing)}){X}")
        log(f"{tag}   {R}  url: {loc}{X}")
        log(f"{tag}   {R}  title: {title}{X}")
        log(f"{tag}   {R}  snippet: {snippet}{X}")
        _dump_debug(label, 'step5_booking_form', s, body)
        return False

    # STEP 6: Search rooms
    s, body, _ = c('POST',
        BASE + '/apply/empty_new?s=' + urllib.parse.quote(coma_s, safe=''),
        {'utf8': '\u2713', 'authenticity_token': auth,
         'apply[join_time]': target_date, 'apply[night_count]': '1',
         'apply[stay_persons]': NUM_GUESTS, 'apply[hope_rooms]': '1'},
        _ajax(csrf, referer_url))
    if 'service_category' in body:
        log(f"{tag}   {R}Session expired at room search{X}")
        _dump_debug(label, 'step6_room_search', s, body)
        return False
    rooms = re.findall(r'name=\\"apply\[coma\[(\d+)\]\]\\".*?value=\\"(\d+)\\"', body)
    guid = ex(body, r'apply_session_guid.*?value=\\"([^"\\]+)\\"')
    if not rooms:
        log(f"{tag}   {R}No rooms available{X}")
        _dump_debug(label, 'step6_no_rooms', s, body)
        return False
    log(f"{tag}   {C}{len(rooms)} rooms -> selecting room{X}")

    # STEP 7: the hold, and the point of no return — nothing here can release one.
    # The site then refuses a second application at the same facility, so no code
    # has to track the hold itself.
    room_id = rooms[0][0]
    s, body, loc = c('POST', BASE + form_action,
        {'utf8': '\u2713', 'authenticity_token': auth,
         'apply[join_time]': target_date, 'apply[night_count]': '1',
         'apply[stay_persons]': NUM_GUESTS, 'apply[hope_rooms]': '1',
         'apply_session_guid': guid, f'apply[coma[{room_id}]]': room_id},
        {'Referer': referer_url})
    s, body, via = _follow(c, s, body, loc)

    # STEP 8: Agree to rules
    if '\u540c\u610f' not in body:
        log(f"{tag}   {R}Not on rules page{X}")
        _dump_debug(label, 'step8_rules', s, body, via)
        return False
    auth = ex(body, _AUTH_RE)
    form_act = ex(body, r'<form[^>]*action="([^"]*)"[^>]*method="post"')
    # The hidden `s` is mandatory even though the action carries no `s` query param;
    # omit it and the server drops the session. Send no `commit`: 同意する is a button.
    s_rule = ex(body, r'name="s"[^>]*value="([^"]*)"')
    if not form_act:
        log(f"{tag}   {R}Missing rules form action{X}")
        _dump_debug(label, 'step8_rules_form', s, body)
        return False
    post_data = {'utf8': '\u2713', 'authenticity_token': auth}
    if s_rule:
        post_data['s'] = s_rule
    s, body, loc = c('POST', BASE + form_act, post_data)
    s, body, via = _follow(c, s, body, loc)

    # STEP 9: Submit email
    if 'email' not in body.lower():
        log(f"{tag}   {R}Not on email page{X}")
        _dump_debug(label, 'step9_email_page', s, body, via)
        return False
    auth = ex(body, _AUTH_RE)
    form_act = ex(body, r'<form[^>]*action="([^"]*)"[^>]*method="post"')
    token_field = ex(body, r'name="__token__"[^>]*value="([^"]*)"')
    if not form_act:
        log(f"{tag}   {R}Missing email form action{X}")
        _dump_debug(label, 'step9_email_form', s, body)
        return False
    post_data = {
        'utf8': '\u2713', 'authenticity_token': auth,
        'email': EMAIL, 'commit': '\u9001\u4fe1',
    }
    if token_field:
        post_data['__token__'] = token_field
    # Dispatches the confirmation email — 「メール送信を完了しました」, not a reservation.
    # Never repeat it: it consumes `__token__` and sends mail, and `--max-time` can
    # expire after the server accepted it. Steps 3-8 only navigate, so they retry.
    s, body, loc = c('POST', BASE + form_act, post_data, retry=False)
    if s == 0:
        log(f"{tag}   {R}Email submit got no response: outcome unknown, not "
            f"retrying ({hotel_name} may already have been applied for){X}")
        return False
    s, body, via = _follow(c, s, body, loc)

    if 'send_complete' in body:
        # Not a reservation: this is 「メール送信を完了しました」. The room is held and
        # the site has emailed a link that still has to be followed. holds.json
        # records the *hold*.
        log(f"{tag}   {B}{G}HELD + MAIL SENT: {hotel_name}{X}")
        save_booking(target_date, hotel_name)
        _queue_confirmation(target_date, hotel_name, tag)
        return True

    log(f"{tag}   {R}Final page not send_complete{X}")
    _dump_debug(label, 'step9_final', s, body, via)
    return False


def _queue_confirmation(target_date, hotel_name, tag):
    """Hand the emailed leg to the confirm worker and return immediately.

    Deliberately not run here: two holds taken in the same second used to race for
    the same confirmation mail and both took the same `c=` link, filing one
    application against an unknown date and wasting the other hold.

    Imported lazily because `confirm_booking` imports this module, and it must never
    be able to stop a booking thread — a hold plus a sent mail is worth keeping, so
    every failure here is logged and swallowed.
    """
    allowed, why = confirm_allowed(target_date)
    if not allowed:
        log(f"{tag}   {Y}Not completing {hotel_name} for {target_date}: {why}{X}")
        log(f"{tag}   {B}{Y}HUMAN NEEDED: the room is held and the mail to {EMAIL} "
            f"is sent. Open its link and finish it now.{X}")
        return
    try:
        import confirm_booking
        depth = confirm_booking.enqueue(target_date, hotel_name)
    except Exception as e:
        log(f"{tag}   {R}Cannot queue the confirmation ({e!r}); {hotel_name} needs "
            f"a human to finish from the mail{X}")
        return
    log(f"{tag}   {C}Queued for confirmation ({depth} pending){X}")


def _calendar_select(c, csrf, s_param, month_first_day, referer):
    """POST the month-nav AJAX. One response carries every date in that month."""
    return c('POST', BASE + '/calendar_apply/calendar_select',
             {'join_date': month_first_day, 's': s_param},
             _ajax(csrf, referer))


def _month_rendered(status, body):
    """True when a month-nav response actually carries a calendar.

    Status alone is insufficient: under `protect_from_forgery with: :null_session` a
    rejected CSRF token is not a 422 \u2014 it runs with an empty session and can return
    200 with no date cells. Trusting the status reports "no dates available" forever
    while looking healthy.
    """
    return status == 200 and 'data-join-time' in body


def _open_calendar_session(c, cookie_file, url, target_date, tag, label,
                           check_availability=True):
    """Start a clean session on the calendar, positioned on target_date's month.

    Returns `(outcome, auth, s_param)` where outcome is one of:
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
            return 'retry', None, None
        log(f"{tag} {Y}calendar GET returned {s}{X}")
        _dump_debug(label, 'calendar_get', s, body)
        return 'failed', None, None

    csrf = ex(body, _CSRF_RE)
    auth = ex(body, _AUTH_RE)
    s_param = ex(body, _S_RE)

    if f'data-join-time="{target_date}"' in body:
        return 'ok', auth, s_param

    target_ym = f"{target_date[:4]}-{target_date[5:7]}-01"
    s_nav, body_nav, loc_nav = _calendar_select(c, csrf, s_param, target_ym, url)
    # Gated on the response *shape*, not the status \u2014 see _month_rendered. Gating on
    # the status alone reported a null-session 200 as 'unavailable', i.e. as the date
    # being full, which is the one answer that suppresses the retry this needs.
    if not _month_rendered(s_nav, body_nav):
        if s_nav == 200 or _is_retryable(s_nav) or _is_session_dead(s_nav, loc_nav):
            log(f"{tag} {Y}month nav {s_nav} with no calendar, will retry{X}")
            return 'retry', None, None
        log(f"{tag} {Y}month nav returned {s_nav}{X}")
        _dump_debug(label, 'calendar_select', s_nav, body_nav)
        return 'failed', None, None

    if check_availability:
        cell = _date_css_class(body_nav, target_date)
        if not is_available(cell):
            log(f"{tag} {Y}date not available (class: {cell or 'no cell found'}){X}")
            return 'unavailable', None, None

    return 'ok', auth, s_param


def _select_date(c, target_date, auth, s_param, tag, label):
    """POST service_group_select. Returns `(outcome, hotels, auth)`.

    This produces the single largest failure class on disk: a 302 to
    /service_category/index with an empty body. That is a dead session, and worth
    another attempt on a fresh one rather than costing the slot.
    """
    s, body, loc = c('POST', BASE + '/calendar_apply/service_group_select',
        {'utf8': '\u2713', 'authenticity_token': auth,
         'join_time': target_date, 's': s_param})
    if s != 200:
        _dump_debug(label, 'service_group_select', s, body)
        if _is_retryable(s) or _is_session_dead(s, loc):
            log(f"{tag} {Y}date select {s} "
                f"({'session expired' if _is_session_dead(s, loc) else 'transient'}),"
                f" will retry{X}")
            return 'retry', [], None
        log(f"{tag} {Y}date select returned {s}{X}")
        return 'failed', [], None

    all_hotels = [(gid, _html.unescape(name)) for gid, name in
                  re.findall(r'data-service-group-id="(\d+)".*?>(.*?)</a>', body)]
    if not all_hotels:
        log(f"{tag} {Y}no hotels listed (status 200){X}")
        _dump_debug(label, 'service_group_select', s, body)
        return 'failed', [], None
    return 'ok', all_hotels, ex(body, _AUTH_RE)


def book_all_hotels_for_date(target_date, label):
    """Hold every eligible hotel for one date, retrying transient failures.

    Returns (date, list_of_held_hotels). Retries cover only the setup requests: once a
    hotel is under way it either completes or is left alone, so nothing is applied for
    twice.
    """
    tag = f"[{label}]"
    booked = []
    attempted = set()

    with _booking_in_flight():
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

    def c(method, u, data=None, headers=None, retry=True):
        return curl(cookie_file, method, u, data, headers, retry)

    try:
        outcome, auth, s_param = _open_calendar_session(
            c, cookie_file, url, target_date, tag, label)
        if outcome != 'ok':
            return outcome

        outcome, all_hotels, auth = _select_date(
            c, target_date, auth, s_param, tag, label)
        if outcome != 'ok':
            return outcome

        log(f"{tag} {C}Found {len(all_hotels)} hotels: "
            f"{', '.join(n for _, n in all_hotels)}{X}")

        # Normalized on both sides: comparing raw names let one full-width space in
        # holds.json defeat the already-booked filter, i.e. a duplicate application.
        seen = {_norm_hotel(n) for n in get_booked_hotels(target_date)}
        seen |= {_norm_hotel(n) for n in attempted}
        hotels, filtered = [], []
        for gid, name in all_hotels:
            if is_skipped(name):
                filtered.append(f'{name} (skip list)')
            elif _norm_hotel(name) in seen:
                filtered.append(f'{name} (already booked/attempted)')
            else:
                hotels.append((gid, name))
        hotels = order_hotels(hotels)

        if not hotels:
            log(f"{tag} {Y}All hotels filtered out ({'; '.join(filtered)}){X}")
            return 'done'

        log(f"{tag} {C}{len(hotels)} to book (priority first): "
            f"{', '.join(n for _, n in hotels)}{X}")

        for i, (hotel_id, hotel_name) in enumerate(hotels):
            if i > 0:
                # Fresh session per hotel; report a failure up so the outer loop can
                # retry the hotels not yet reached.
                outcome, auth, s_param = _open_calendar_session(
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
                log(f"{tag} {B}{G}=== Total HELD for {target_date}: "
                    f"{len(booked)} ({', '.join(booked)}){X}")

        return 'done'

    finally:
        try:
            os.unlink(cookie_file)
        except OSError:
            pass



def _future_dates(target_dates):
    """Drop dates that have already passed — the site marks those cells `over`,
    so polling them asks forever about something that cannot happen."""
    if not SKIP_PAST_DATES:
        return list(target_dates)
    today = date.today().isoformat()
    return [d for d in target_dates if d >= today]


_ISO_DATE = re.compile(r'\A\d{4}-\d{2}-\d{2}\Z')


def days_until(target_date, today=None):
    """Whole days from `today` to `target_date`, or None if it will not parse.

    Strictly `YYYY-MM-DD`: `date.fromisoformat` also takes `20260905` and week dates
    since 3.11, and a safety gate should not widen because the stdlib did. None rather
    than a raise or a guess — every caller reads it as "do not commit", and a silent 0
    would read as "today".
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

    Completing means 予約確定, with a real cancellation liability. Free web cancellation
    ends at D−10, so anything nearer is left for a person. Gates the final POSTs only:
    the hold and the mail still happen, which is what lets a human finish by hand.

    Fails closed — wrongly committing costs a non-refundable booking, wrongly refusing
    costs one slot.
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


def scan_and_book_month(month_str, target_dates, label, stop_event=None):
    """Scan a month's calendar forever, spawning a booking thread per available date.

    One `calendar_select` POST covers every target date in the month; the calendar GET
    that mints the csrf/s pair is skipped while a cached pair works, so the
    steady-state cycle is one request.

    The whole loop body is guarded — this is the only thing scanning this month, it is
    a daemon, and main() never joins it, so an escaping exception would stop the month
    silently. `stop_event` is for the tests, so a finished test's scanner cannot book
    into the next one's fixtures.
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

        The request session reuse exists to avoid, and the most failure-prone in the
        program: most dumps are a 503 here, and a 503 is how the 24-hour ban presents.
        """
        st, bd, lc = c('GET', url)
        if st != 200:
            # Only after a failure: truncating every cycle asked for a brand-new
            # session ~4,300 times a day, discarding a working one each time.
            if _is_session_dead(st, lc) or _is_retryable(st):
                open(cookie_file, 'w').close()
            log(f"{tag} {Y}[{attempt}] URL returned {st}, waiting...{X}")
            return None
        return ex(bd, _CSRF_RE), ex(bd, _S_RE)

    def nav_month(tokens, url):
        csrf, s_param = tokens
        return _calendar_select(c, csrf, s_param, month_ym, url)

    try:
        attempt = 0
        failures = 0          # consecutive failed cycles, drives the backoff
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
                        # Re-mint in this same cycle, and do not charge `failures`:
                        # a token reaching the end of its session is expected, and
                        # letting it drive the backoff would walk the poll interval
                        # up to SCAN_BACKOFF_MAX and stop finding anything.
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
                    # Fresh tokens and still no calendar. Without this the scan says
                    # "no dates available" forever while the URL monitor's plain GET
                    # still returns 200, so no re-solve ever fires.
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
                    log(f"{tag} {Y}[{attempt}] no dates available "
                        f"({len(dates)} checked), waiting...{X}")
                    continue

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
                            log(f"{tag} {G}Held for {td}: "
                                f"{', '.join(booked_list)}{X}")

            except Exception as e:
                failures += 1
                log(f"{tag} {R}[{attempt}] scan cycle error: {e!r}{X}")

    finally:
        try:
            os.unlink(cookie_file)
        except OSError:
            pass
