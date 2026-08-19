#!/usr/bin/env python3
"""End-to-end tests for the booking flow, against a fake ITS server.

test_http_layer.py covers curl and redaction. This covers the part that actually
wins or loses a slot: the nine-step booking chain, hotel ordering and filtering,
and the retry behaviour on the transient failures the production log is full of
(1072 x 503 on the calendar GET, 302 dumps out of service_group_select).

The fake server replays the real markup shapes from docs/BOOKING_VIA_CURL.md and
from the dumps in debug_responses/, including the escaped-quote form that AJAX
responses arrive in — the extractors are markup-exact, so a fake that pretties
the markup up would test nothing.

Never touches ITS.

    .venv/bin/python test_booking_flow.py
"""
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import book_hotels as bh

# curl inherits this process's proxy settings; without this a local HTTP proxy
# intercepts the loopback requests and rewrites the responses.
os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'

FAILURES = []
# Synthetic; see the note in test_http_layer.py. Never put a real `s=` token in a
# fixture — even an expired one publishes the token format.
S_TOKEN = 'RkFLRV9UT0tFTl9GT1JfVEVTVFNfTk9UX0FfUkVBTF9TRVNTSU9OX1RPS0VO'
TARGET = '2026-09-05'
TARGET_LIMITED = '2026-09-06'    # 'a_little': few rooms left, but still bookable
OTHER_MONTH_DAY = '2026-08-05'   # what the initial calendar page shows

NAGU = 'NAGU 勝浦'
RESOL = 'リソルの森'
NIKKO = '日光千姫物語'
SKIPME = '和倉温泉 あえの風'          # half-width space, as in SKIP_HOTELS
SKIPME_FULLWIDTH = '和倉温泉　あえの風'  # what the site may actually send

# A fail_once entry meaning "close the connection without answering", as opposed
# to an HTTP status. Distinct from 0, which the queue uses for "serve normally".
HANGUP = 'hangup'


def check(name, cond, detail=''):
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f'  — {detail}' if detail and not cond else ''))
    if not cond:
        FAILURES.append(name)


# ── Fake ITS ────────────────────────────────────────────────────────

class FakeITS:
    """Server state and failure injection, shared by every handler thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.hits = []                # every path requested, in order
        self.fail_once = {}           # path fragment -> [statuses to serve]
        self.hotels = [(7, NAGU), (8, RESOL), (9, NIKKO)]
        self.no_rooms = set()         # hotel names whose form page reports full
        # Number of upcoming month-nav POSTs to answer 200 with a page that has
        # no date cells in it. That is what Rails' `protect_from_forgery with:
        # :null_session` does to a request whose CSRF token it rejects: not a
        # 422, but a session-less 200. A scanner that trusted the status would
        # read it as "no dates available" forever.
        self.blank_nav = 0
        # When set, a month-nav POST is blanked unless a calendar GET immediately
        # preceded it — i.e. the cached token is rejected but a freshly minted one
        # works. That is the shape of the failure the give-up logic exists for.
        self.blank_cached_nav = False
        self.completed = []           # (email, hotel) pairs that reached send_complete
        self.selected_hotel = {}      # cookie session -> hotel name, for step 5

    def nav_was_cached(self):
        """True when the nav POST just recorded had no calendar GET before it."""
        with self.lock:
            prior = self.hits[-2] if len(self.hits) >= 2 else ''
        return 'GET /calendar_apply/calendar_select' not in prior

    def record(self, path):
        with self.lock:
            self.hits.append(path)

    def injected(self, path):
        """Pop a queued failure status for this path, if one is pending."""
        with self.lock:
            for frag, statuses in self.fail_once.items():
                if frag in path and statuses:
                    return statuses.pop(0)
        return None

    def count(self, frag):
        with self.lock:
            return sum(1 for h in self.hits if frag in h)


STATE = FakeITS()


def _cell(date_str, cls):
    """A calendar cell in the plain-HTML form used by the initial page."""
    return f'<td class="{cls}" data-join-time="{date_str}" onclick="selectJoinTime(this);">5</td>'


def _escaped_cell(date_str, cls):
    r"""A calendar cell as it arrives inside an AJAX response.

    Rails-UJS sends markup embedded in JavaScript, so every quote is
    backslash-escaped. `_date_css_class` matches that exact shape, which is why
    this must be a raw string producing literal `class=\"empty td-n\"`.
    """
    return (r'<td class=\"' + cls + r'\" data-join-time=\"' + date_str +
            r'\" onclick=\"selectJoinTime(this);\">5</td>')


CALENDAR_PAGE = f"""<html><head>
<meta name="csrf-token" content="CSRF-CAL-TOKEN" />
<title>施設予約システム</title></head><body>
<table class="tcas_1"><tr>{_cell(OTHER_MONTH_DAY, 'empty td-n')}</tr></table>
<form action="/calendar_apply/service_group_select" method="post">
<input type="hidden" name="utf8" value="&#x2713;" />
<input type="hidden" name="authenticity_token" value="AUTH-CAL-TOKEN" />
<input type="hidden" name="join_time" id="join_time" />
<input type="hidden" name="s" id="s" value="{S_TOKEN}" />
</form></body></html>"""


def _nav_class(date_str):
    """The CSS class the fake puts on a calendar cell.

    The site marks a date `empty` (rooms free), `a_little` (few left), `full` or
    `over`, and the first two are both clickable and both bookable — see
    docs/BOOKING_VIA_CURL.md. The fake serves one of each, because a filter that
    only looks for `empty` passes every other test in this file while silently
    never attempting half the dates that are actually open.
    """
    if date_str == TARGET:
        return 'empty td-n'
    if date_str == TARGET_LIMITED:
        return 'a_little td-n'
    return 'full td-n'


def nav_response(month_first_day):
    """The AJAX month-navigation response: JS that swaps the calendar in."""
    days = (f'{month_first_day[:7]}-{d:02d}' for d in range(1, 29))
    cells = ''.join(_escaped_cell(day, _nav_class(day)) for day in days)
    return f'$(".tcas_1").html(\'<div class=\\"month-navi\\">{cells}</div>\');\nloading(false);\n'


# A 200 that carries no date cells — see FakeITS.blank_nav. Deliberately shaped
# like a plausible response rather than empty, so only a check for the cells
# themselves can tell it apart from a real one.
BLANK_NAV_RESPONSE = (
    '$(".tcas_1").html(\'<div class=\\"month-navi\\">'
    '<p>\\u30bb\\u30c3\\u30b7\\u30e7\\u30f3\\u304c\\u5207\\u308c\\u307e\\u3057\\u305f</p>'
    '</div>\');\nloading(false);\n')


def hotel_list_page(hotels):
    items = '\n'.join(
        f'<li><a data-service-group-id="{hid}" class="select_service_group" '
        f'href="javascript:;">{name}</a></li>' for hid, name in hotels)
    return f"""<html><head><meta name="csrf-token" content="CSRF-HOTELS" /></head><body>
<h2>施設選択</h2><ul class="items mb20">
{items}
</ul>
<form action="/calendar_apply/apply_service_select" method="post">
<input type="hidden" name="authenticity_token" value="AUTH-HOTELS" />
</form></body></html>"""


SERVICE_LIST_PAGE = """<html><body>
<ul><li><a data-apply-service-id="41" class="select_apply_service" href="javascript:;">宿泊</a></li></ul>
<form action="/calendar_apply/check_apply_service_coma" method="post">
<input type="hidden" name="authenticity_token" value="AUTH-SERVICES" />
</form></body></html>"""

BOOKING_FORM_PAGE = f"""<html><head>
<meta name="csrf-token" content="CSRF-BOOKING" /></head><body>
<form action="/apply/empty_create?s={S_TOKEN}" method="post" id="empty_form">
<input type="hidden" name="authenticity_token" value="AUTH-BOOKING" />
</form>
<a href="javascript:;" onclick="coma_search('COMA{S_TOKEN}')">検索</a>
</body></html>"""

# The real page from debug_responses/*step5_booking_form_status200.html: a valid
# 200 with no booking form, because somebody else took the last room.
NO_ROOMS_PAGE = """<html><head><title>施設予約システム</title></head><body>
<p>大変申し訳ございませんが、ご指定の施設において空き部屋がございません。</p>
</body></html>"""

ROOM_SEARCH_RESPONSE = (
    r'$("#rooms").html(\'<input type=\"hidden\" name=\"apply_session_guid\" '
    r'value=\"GUID-1234-ABCD\" />'
    r'<input type=\"checkbox\" name=\"apply[coma[551]]\" value=\"551\" />'
    r'<input type=\"checkbox\" name=\"apply[coma[552]]\" value=\"552\" />\');'
)

RULES_PAGE = """<html><body><h2>利用規約</h2>
<p>下記に同意のうえお進みください。</p>
<form action="/apply/rule_agree" method="post">
<input type="hidden" name="authenticity_token" value="AUTH-RULES" />
<input type="hidden" name="s" value="RULE-S-VALUE" />
</form></body></html>"""

EMAIL_PAGE = """<html><body><h2>確認</h2>
<form action="/apply/send_confirm" method="post">
<input type="hidden" name="authenticity_token" value="AUTH-EMAIL" />
<input type="hidden" name="__token__" value="TOKEN-FIELD" />
<input type="text" name="email" value="" />
</form></body></html>"""

COMPLETE_PAGE = '<html><body><h2>send_complete</h2><p>受付完了</p></body></html>'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    # ── plumbing ──
    def _send(self, status, body='', location=None, ctype='text/html; charset=utf-8'):
        raw = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        if location:
            self.send_header('Location', location)
        self.send_header('X-Runtime', '0.0421')
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _session_dead(self):
        """The confirmed signature of an expired ITS session."""
        self._send(302, '', f'{self._base()}/service_category/index')

    def _base(self):
        return f'http://127.0.0.1:{self.server.server_port}'

    def _form(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length).decode('utf-8') if length else ''
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def _dispatch(self, method):
        path = self.path
        STATE.record(f'{method} {path}')
        injected = STATE.injected(path)
        if injected == 302:
            return self._session_dead()
        if injected == HANGUP:
            # Answer nothing and close. curl reports an empty reply, i.e. the
            # status-0 transport failure — the case where the server may well
            # have processed the request we are refusing to repeat.
            if method == 'POST':
                self._form()   # drain the body first, so curl sees EOF not a reset
            self.close_connection = True
            return
        if injected:
            return self._send(injected, f'<html>error {injected}</html>')
        return self.route(method, path, self._form() if method == 'POST' else {})

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    # ── the ITS flow ──
    def route(self, method, path, form):
        base = self._base()

        if '/calendar_apply/calendar_select' in path and method == 'GET':
            return self._send(200, CALENDAR_PAGE)

        if '/calendar_apply/calendar_select' in path and method == 'POST':
            with STATE.lock:
                blank = STATE.blank_nav > 0
                if blank:
                    STATE.blank_nav -= 1
            if blank or (STATE.blank_cached_nav and STATE.nav_was_cached()):
                return self._send(200, BLANK_NAV_RESPONSE,
                                  ctype='text/javascript; charset=utf-8')
            return self._send(200, nav_response(form.get('join_date', '')),
                              ctype='text/javascript; charset=utf-8')

        if '/calendar_apply/service_group_select' in path:
            return self._send(200, hotel_list_page(STATE.hotels))

        if '/calendar_apply/apply_service_select' in path:
            with STATE.lock:
                gid = form.get('service_group_id')
                STATE.selected_hotel[self.headers.get('Cookie', '')] = next(
                    (n for i, n in STATE.hotels if str(i) == gid), '?')
            return self._send(200, SERVICE_LIST_PAGE)

        if '/calendar_apply/check_apply_service_coma' in path:
            return self._send(302, '', f'{base}/apply/empty_new?s={S_TOKEN}')

        if path.startswith('/apply/empty_new') and method == 'GET':
            with STATE.lock:
                hotel = STATE.selected_hotel.get(self.headers.get('Cookie', ''), '?')
            if hotel in STATE.no_rooms:
                return self._send(200, NO_ROOMS_PAGE)
            return self._send(200, BOOKING_FORM_PAGE)

        if path.startswith('/apply/empty_new') and method == 'POST':
            return self._send(200, ROOM_SEARCH_RESPONSE,
                              ctype='text/javascript; charset=utf-8')

        if path.startswith('/apply/empty_create'):
            return self._send(302, '', f'{base}/apply/rule')

        if path == '/apply/rule':
            return self._send(200, RULES_PAGE)

        if path == '/apply/rule_agree':
            return self._send(302, '', f'{base}/apply/confirm')

        if path == '/apply/confirm':
            return self._send(200, EMAIL_PAGE)

        if path == '/apply/send_confirm':
            with STATE.lock:
                hotel = STATE.selected_hotel.get(self.headers.get('Cookie', ''), '?')
                STATE.completed.append((form.get('email'), hotel))
            return self._send(302, '', f'{base}/apply/send_complete')

        if path == '/apply/send_complete':
            return self._send(200, COMPLETE_PAGE)

        return self._send(404, '<html>not found</html>')


# ── Harness ─────────────────────────────────────────────────────────

class Env:
    """Point book_hotels at the fake server and temp files, then restore."""

    ATTRS = ('BASE', 'CALENDAR_URL_CACHE', 'BOOKINGS_FILE', 'DEBUG_DIR',
             'SKIP_HOTELS', '_SKIP_NORM', 'PRIORITY_HOTELS', '_PRIORITY_NORM',
             'EMAIL', 'NUM_GUESTS', 'BOOK_MAX_ATTEMPTS', 'BOOK_RETRY_DELAY',
             'CURL_RETRY_BACKOFF', 'CURL_MAX_ATTEMPTS', 'RETRY_DELAY',
             'SCAN_JITTER', 'SCAN_BACKOFF_MAX', 'IDLE_LOG_INTERVAL',
             'DEBUG_DUMP_INTERVAL', 'SKIP_PAST_DATES',
             'HOTEL_RETRY_COOLDOWN', 'HOTEL_HOLD_COOLDOWN',
             'MAX_BOOKINGS_PER_DATE', 'SCAN_REUSE_SESSION',
             'SCAN_REUSE_MAX_FAILURES')

    def __init__(self, port, skip=(), priority=('NAGU',)):
        self.port, self.skip, self.priority = port, list(skip), list(priority)

    def __enter__(self):
        self.saved = {a: getattr(bh, a) for a in self.ATTRS}
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        bh.BASE = f'http://127.0.0.1:{self.port}'
        bh.CALENDAR_URL_CACHE = os.path.join(d, 'url.txt')
        bh.BOOKINGS_FILE = os.path.join(d, 'bookings.json')
        bh.DEBUG_DIR = os.path.join(d, 'debug')
        with open(bh.CALENDAR_URL_CACHE, 'w') as f:
            f.write(f'{bh.BASE}/calendar_apply/calendar_select?s={S_TOKEN}\n')
        bh.SKIP_HOTELS = self.skip
        bh._SKIP_NORM = frozenset(bh._norm_hotel(n) for n in self.skip if n)
        bh.PRIORITY_HOTELS = self.priority
        bh._PRIORITY_NORM = tuple(bh._norm_hotel(p) for p in self.priority if p)
        bh.EMAIL = 'test@example.com'
        bh.NUM_GUESTS = '2'
        bh.BOOK_MAX_ATTEMPTS = 3
        bh.BOOK_RETRY_DELAY = 0.01
        bh.CURL_RETRY_BACKOFF = 0.01
        bh.CURL_MAX_ATTEMPTS = 1      # isolate book-level retries from curl-level
        bh.RETRY_DELAY = 0.05
        bh.SCAN_JITTER = 0
        bh.SCAN_BACKOFF_MAX = 0.2
        bh.IDLE_LOG_INTERVAL = 0
        bh.DEBUG_DUMP_INTERVAL = 0    # no throttling, so dumps are countable
        bh.SKIP_PAST_DATES = True
        # Cooldowns and the per-date cap are off unless a test opts in, so the
        # suites that assert the retry and ordering behaviour keep testing it.
        # The registry is module-global and several tests share TARGET, so
        # clearing it is what stops one test's holds suppressing the next's.
        bh.HOTEL_RETRY_COOLDOWN = 0
        bh.HOTEL_HOLD_COOLDOWN = 0
        bh.MAX_BOOKINGS_PER_DATE = 0
        bh.SCAN_REUSE_SESSION = True
        bh.SCAN_REUSE_MAX_FAILURES = 3
        STATE.hits.clear()
        STATE.fail_once.clear()
        STATE.completed.clear()
        STATE.no_rooms.clear()
        STATE.blank_nav = 0
        STATE.blank_cached_nav = False
        STATE.selected_hotel.clear()
        STATE.hotels = [(7, NAGU), (8, RESOL), (9, NIKKO)]
        bh._dump_last.clear()
        bh._cooldowns.clear()
        return self

    def __exit__(self, *exc):
        for a, v in self.saved.items():
            setattr(bh, a, v)
        bh._cooldowns.clear()
        self.tmp.cleanup()
        return False

    def bookings(self):
        if not os.path.exists(bh.BOOKINGS_FILE):
            return {}
        with open(bh.BOOKINGS_FILE, encoding='utf-8') as f:
            return json.load(f)

    def dumps(self):
        if not os.path.isdir(bh.DEBUG_DIR):
            return []
        return sorted(os.listdir(bh.DEBUG_DIR))


class CapturedLog:
    """Collect book_hotels' log lines while still passing them on.

    Tees rather than replaces, so `-v` still shows the flow's own logging for a
    test that asserts on what the operator is told.
    """

    def __enter__(self):
        self.lines = []
        self.outer = bh._log_handler
        bh._log_handler = self._tee
        return self

    def _tee(self, msg):
        self.lines.append(msg)
        if self.outer:
            self.outer(msg)
        else:
            print(msg, flush=True)   # what log() itself does with no handler set

    def __exit__(self, *exc):
        bh._log_handler = self.outer
        return False

    def saw(self, fragment):
        return any(fragment in m for m in self.lines)


# ── Tests ───────────────────────────────────────────────────────────

def test_happy_path(port):
    """The whole nine-step chain, for every eligible hotel, in priority order."""
    with Env(port) as env:
        date_str, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('happy: returns the date', date_str == TARGET, date_str)
        check('happy: books all three hotels', booked == [NAGU, RESOL, NIKKO], str(booked))
        check('happy: NAGU booked first', booked[:1] == [NAGU], str(booked))
        check('happy: bookings.json written', env.bookings() == {TARGET: [NAGU, RESOL, NIKKO]},
              str(env.bookings()))
        check('happy: server saw three completions', len(STATE.completed) == 3,
              str(STATE.completed))
        check('happy: email submitted', all(e == 'test@example.com' for e, _ in STATE.completed),
              str(STATE.completed))
        check('happy: no debug dumps', env.dumps() == [], str(env.dumps()))
        # Month navigation is required because the initial page shows August.
        check('happy: navigated to the target month', STATE.count('POST /calendar_apply/calendar_select') >= 1)


def test_priority_ordering(port):
    """NAGU goes first even when the site lists it last."""
    with Env(port) as env:
        STATE.hotels = [(8, RESOL), (9, NIKKO), (7, NAGU)]
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('priority: NAGU attempted first despite being listed last',
              booked[0] == NAGU, str(booked))
        first_completed = STATE.completed[0][1] if STATE.completed else None
        check('priority: NAGU reached the server first', first_completed == NAGU,
              str(STATE.completed))

    with Env(port, priority=()) as env:
        STATE.hotels = [(8, RESOL), (9, NIKKO), (7, NAGU)]
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('priority: empty PRIORITY_HOTELS keeps site order',
              booked == [RESOL, NIKKO, NAGU], str(booked))


def test_skip_list(port):
    """Skipping matches through full-width spaces and HTML entities."""
    with Env(port, skip=[SKIPME]) as env:
        STATE.hotels = [(7, NAGU), (8, SKIPME_FULLWIDTH)]
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('skip: full-width space still matches', booked == [NAGU], str(booked))

    with Env(port, skip=[RESOL]) as env:
        STATE.hotels = [(7, NAGU), (8, 'リソル&#12398;森')]
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('skip: HTML entity still matches', booked == [NAGU], str(booked))

    with Env(port, skip=[NAGU, RESOL, NIKKO]) as env:
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('skip: everything skipped books nothing', booked == [], str(booked))
        check('skip: nothing reached the server', STATE.completed == [], str(STATE.completed))


def test_already_booked_not_repeated(port):
    with Env(port) as env:
        bh.save_booking(TARGET, NAGU)
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('already-booked: NAGU not re-applied for', NAGU not in booked, str(booked))
        check('already-booked: the others still booked', booked == [RESOL, NIKKO], str(booked))
        check('already-booked: record preserved and extended',
              env.bookings() == {TARGET: [NAGU, RESOL, NIKKO]}, str(env.bookings()))


def test_503_on_calendar_get_is_retried(port):
    """The single biggest source of lost slots in the production log.

    49 times, the scanner spotted an open date and the booking thread's first GET
    came back 503 — several in the very same second — and the whole date was
    abandoned until the next scan cycle. It must survive that.
    """
    with Env(port) as env:
        STATE.fail_once['/calendar_apply/calendar_select'] = [503]
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('503: recovered and booked anyway', booked == [NAGU, RESOL, NIKKO], str(booked))

    with Env(port) as env:
        STATE.fail_once['/calendar_apply/calendar_select'] = [503, 503]
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('503: recovers from two in a row', booked == [NAGU, RESOL, NIKKO], str(booked))

    with Env(port) as env:
        # More failures than attempts: give up, but cleanly and without raising.
        STATE.fail_once['/calendar_apply/calendar_select'] = [503] * 10
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('503: gives up cleanly past BOOK_MAX_ATTEMPTS', booked == [], str(booked))
        check('503: attempted exactly BOOK_MAX_ATTEMPTS times',
              STATE.count('GET /calendar_apply/calendar_select') == 3,
              str(STATE.count('GET /calendar_apply/calendar_select')))


def test_session_death_on_date_select_is_retried(port):
    """302 -> /service_category/index out of service_group_select.

    The largest failure class on disk: 302 of 380 dumps, every one a 0-byte body.
    A live probe confirmed this is what an expired `s=` token answers, so it is
    worth one more attempt on a fresh session rather than costing the slot.
    """
    with Env(port) as env:
        STATE.fail_once['/calendar_apply/service_group_select'] = [302]
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('session-death: retried and booked', booked == [NAGU, RESOL, NIKKO], str(booked))
        check('session-death: dumped for diagnosis',
              any('service_group_select' in f for f in env.dumps()), str(env.dumps()))
        check('session-death: dump records the redirect target',
              any(f.endswith('.headers.txt') for f in env.dumps()), str(env.dumps()))

    with Env(port) as env:
        STATE.fail_once['/calendar_apply/service_group_select'] = [302] * 10
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('session-death: gives up after BOOK_MAX_ATTEMPTS', booked == [], str(booked))


def test_session_death_mid_hotel_loop(port):
    """A session that dies between hotels resumes without re-applying."""
    with Env(port) as env:
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('mid-loop baseline books three', len(booked) == 3, str(booked))

    with Env(port) as env:
        # Let the first hotel's date-select through, then kill the session on the
        # re-select that sets up hotel #2. A falsy entry means "serve normally",
        # so this queue is: pass, then 302.
        STATE.fail_once['/calendar_apply/service_group_select'] = [0, 302]
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('mid-loop: all three still booked across the break',
              booked == [NAGU, RESOL, NIKKO], str(booked))
        applied = [h for _, h in STATE.completed]
        check('mid-loop: no hotel applied for twice',
              len(applied) == len(set(applied)), str(applied))
        check('mid-loop: NAGU still went first', applied[0] == NAGU, str(applied))


def test_no_rooms_is_not_an_error(port):
    """The site's own "facility full" page is an outcome, not a fault.

    It used to log four red lines and write a debug dump every time somebody else
    got to the room first — 4 of them in the last log, plus the dumps.
    """
    with Env(port) as env:
        STATE.no_rooms.add(NAGU)
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('no-rooms: NAGU not counted as booked', NAGU not in booked, str(booked))
        check('no-rooms: the other hotels still booked', booked == [RESOL, NIKKO], str(booked))
        check('no-rooms: no debug dump written', env.dumps() == [], str(env.dumps()))

    with Env(port) as env:
        STATE.no_rooms.update({NAGU, RESOL, NIKKO})
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('no-rooms: all full books nothing, still no dumps',
              booked == [] and env.dumps() == [], f'{booked} {env.dumps()}')


def test_unexpected_status_is_dumped_not_retried(port):
    """A 404 is not worth repeating; it should be recorded and abandoned."""
    with Env(port) as env:
        STATE.fail_once['/calendar_apply/service_group_select'] = [404]
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('404: books nothing', booked == [], str(booked))
        check('404: attempted once, not retried',
              STATE.count('POST /calendar_apply/service_group_select') == 1,
              str(STATE.count('POST /calendar_apply/service_group_select')))
        check('404: dumped', any('service_group_select' in f for f in env.dumps()),
              str(env.dumps()))


def test_date_unavailable(port):
    with Env(port) as env:
        _, booked = bh.book_all_hotels_for_date('2026-09-07', 'TEST')  # 'full' cell
        check('unavailable: books nothing', booked == [], str(booked))
        check('unavailable: never selected a date',
              STATE.count('POST /calendar_apply/service_group_select') == 0)


def test_limited_availability_is_booked(port):
    """`a_little` is "few rooms left", not "unavailable".

    Both classes are clickable on the real site, so both can be applied for. The
    filter used to match `empty` only, which meant the dates closest to selling
    out — the ones a booker exists for — were the ones never attempted.
    """
    with Env(port) as env:
        date_str, booked = bh.book_all_hotels_for_date(TARGET_LIMITED, 'TEST')
        check('a_little: returns the date', date_str == TARGET_LIMITED, date_str)
        check('a_little: books every eligible hotel',
              booked == [NAGU, RESOL, NIKKO], str(booked))
        check('a_little: recorded', env.bookings() == {TARGET_LIMITED: booked},
              str(env.bookings()))
        check('a_little: not treated as a fault', env.dumps() == [], str(env.dumps()))


def test_final_submit_is_never_retried(port):
    """The request that files an application must go out at most once.

    curl's retry covers 5xx and transport failures, which is right for every
    navigation step in the chain and wrong for this one: `--max-time` can expire
    after the server accepted the submission, and repeating it then applies twice
    for the same room. Nothing downstream could catch that, because the booking is
    not recorded until a response confirms it.
    """
    for label, injected in (('503', 503), ('transport failure', HANGUP)):
        with Env(port) as env, CapturedLog() as log:
            bh.CURL_MAX_ATTEMPTS = 3        # what production runs with
            STATE.hotels = [(7, NAGU)]      # one hotel, so the count is unambiguous
            STATE.fail_once['/apply/send_confirm'] = [injected]
            _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
            check(f'final-submit: {label} sent exactly once',
                  STATE.count('POST /apply/send_confirm') == 1,
                  str(STATE.count('POST /apply/send_confirm')))
            check(f'final-submit: {label} not counted as booked', booked == [], str(booked))
            check(f'final-submit: {label} not recorded', env.bookings() == {},
                  str(env.bookings()))
            if injected == HANGUP:
                # The operator has to learn that an application may exist for a
                # hotel that is not in bookings.json.
                check('final-submit: unknown outcome reported to the operator',
                      log.saw('outcome unknown'), str(log.lines[-2:]))

    # The same CURL_MAX_ATTEMPTS must still retry a step that only navigates,
    # or this fix would have quietly disabled retrying everywhere.
    with Env(port) as env:
        bh.CURL_MAX_ATTEMPTS = 3
        STATE.hotels = [(7, NAGU)]
        STATE.fail_once['/calendar_apply/apply_service_select'] = [503]
        _, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('final-submit: navigation steps still retried', booked == [NAGU], str(booked))
        check('final-submit: retry really happened',
              STATE.count('POST /calendar_apply/apply_service_select') == 2,
              str(STATE.count('POST /calendar_apply/apply_service_select')))
        check('final-submit: booked once, not twice',
              [h for _, h in STATE.completed] == [NAGU], str(STATE.completed))


def test_missing_url(port):
    with Env(port) as env:
        os.unlink(bh.CALENDAR_URL_CACHE)
        date_str, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('no-url: returns cleanly', (date_str, booked) == (TARGET, []), str(booked))
        check('no-url: made no requests', STATE.hits == [], str(STATE.hits))


def test_token_summary():
    """The `s=` token decoder, and the guarantee that it leaks nothing.

    The token is base64(reverse(base64("k=v&k=v"))) with no MAC, so the payload
    is only `service_category_id` plus a timestamp. Printing that timestamp in
    full would reconstruct the token, which is why it is rendered as a delta.
    """
    import base64 as b64

    def mint(payload):
        inner = b64.b64encode(payload.encode()).decode()
        return b64.b64encode(inner[::-1].encode()).decode()

    now = 1786183972
    tok = mint(f'service_category_id=1&verify_expires={now}')

    fields = bh.decode_s_token(tok)
    check('token: fields decoded in order',
          fields == [('service_category_id', '1'), ('verify_expires', str(now))],
          str(fields))

    # Round-trips the real shape observed live.
    real = 'service_category_id=1&verify_expires=1786183972'
    check('token: real payload shape round-trips',
          bh.decode_s_token(mint(real)) == bh.decode_s_token(tok), str(fields))

    url = f'https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s={tok}'
    s = bh.token_summary(url, now=now - 3600)
    check('token: field names shown', 'service_category_id=1' in s, s)
    check('token: expiry shown as a delta', 'verify_expires=+1h00m' in s, s)
    check('token: absolute timestamp NEVER logged', str(now) not in s, s)
    check('token: raw token NEVER logged', tok not in s, s)

    s = bh.token_summary(url, now=now + 90)
    check('token: expired shows negative delta', 'verify_expires=-1m30s' in s, s)

    # A field the site adds later must show its name but mask its value.
    tok2 = mint(f'service_category_id=1&member_no=A1234567890&verify_expires={now}')
    s = bh.token_summary(f'https://h/x?s={tok2}', now=now)
    check('token: unknown field name shown', 'member_no=' in s, s)
    check('token: unknown field value masked', 'A1234567890' not in s, s)
    check('token: unknown field length reported', '<11 chars>' in s, s)

    # Strictness: a truncated token must report failure, not garbage. Every
    # token in the previous production log was cut at 80 characters.
    check('token: truncated reports failure',
          bh.token_summary(f'https://h/x?s={tok[:60]}').startswith('token does not decode'),
          bh.token_summary(f'https://h/x?s={tok[:60]}'))
    check('token: non-base64 reports failure',
          'does not decode' in bh.token_summary('https://h/x?s=!!!!not-base64!!!!'))
    check('token: single-layer base64 rejected',
          bh.decode_s_token(b64.b64encode(b'service_category_id=1').decode()) is None)

    # Never raises: this is in the URL monitor's logging path.
    for bad in (None, '', 'not a url', 'https://h/x', 'https://h/x?s=',
                'http://[garbage?s=abc', 'https://h/x?s=' + 'A' * 4000):
        try:
            check(f'token: no raise on {str(bad)[:24]!r}',
                  isinstance(bh.token_summary(bad), str))
        except Exception as e:
            check(f'token: no raise on {str(bad)[:24]!r}', False, repr(e))


def test_relative_duration():
    check('relative: hours', bh._relative(9061) == '+2h31m', bh._relative(9061))
    check('relative: minutes', bh._relative(125) == '+2m05s', bh._relative(125))
    check('relative: seconds', bh._relative(9) == '+9s', bh._relative(9))
    check('relative: zero', bh._relative(0) == '+0s', bh._relative(0))
    check('relative: negative', bh._relative(-9061) == '-2h31m', bh._relative(-9061))
    check('relative: negative seconds', bh._relative(-5) == '-5s', bh._relative(-5))


def _run_scanner(month, dates, label):
    """Start a scanner in a thread and hand back (thread, stop_event).

    The stop event matters for isolation: without it a finished test's scanner
    keeps polling the fake server and booking into the *next* test's fixtures,
    which is exactly what made the active-bookings assertions fail spuriously.
    """
    stop = threading.Event()
    t = threading.Thread(target=bh.scan_and_book_month,
                         args=(month, dates, label), kwargs={'stop_event': stop},
                         daemon=True)
    t.start()
    return t, stop


def _wait_for(predicate, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_scan_reuses_the_session(port):
    """Steady state must be one request per cycle, not two.

    The calendar GET exists only to mint the csrf/s pair the month-nav POST
    needs, and that pair stays valid for the life of the session, so a healthy
    scanner should GET once and then POST for as long as the session lasts.
    """
    with Env(port) as env:
        # A date the fake's calendar does not carry, so cycles stay idle and the
        # request counts are only the scan's own.
        t, stop = _run_scanner('2026-09', ['2026-09-15'], 'REUSE')
        try:
            grew = _wait_for(lambda: STATE.count('POST /calendar_apply/calendar_select') >= 5)
            gets = STATE.count('GET /calendar_apply/calendar_select')
            posts = STATE.count('POST /calendar_apply/calendar_select')
            check('reuse: month-nav POSTs kept coming', grew, str(posts))
            check('reuse: the calendar was fetched exactly once',
                  gets == 1, f'{gets} GETs for {posts} POSTs')
        finally:
            stop.set()
            t.join(timeout=10)


def test_scan_remints_when_the_cached_session_is_rejected(port):
    """A session-less 200 must be detected by shape and re-minted in the cycle.

    This is the failure the status check could not see: had the scanner trusted
    `status == 200`, it would have reported "no dates available" for as long as it
    kept replaying the stale token, while looking perfectly healthy.
    """
    with Env(port) as env:
        with CapturedLog() as logs:
            t, stop = _run_scanner('2026-09', ['2026-09-15'], 'REMINT')
            try:
                # Let the first cycle mint and cache, then poison the next nav.
                _wait_for(lambda: STATE.count('POST /calendar_apply/calendar_select') >= 1)
                with STATE.lock:
                    STATE.blank_nav = 1
                remade = _wait_for(
                    lambda: STATE.count('GET /calendar_apply/calendar_select') >= 2)
                check('remint: re-fetched the calendar after the rejection', remade,
                      str(STATE.count('GET /calendar_apply/calendar_select')))
                check('remint: said so in the log', logs.saw('cached session rejected'))
                # Recovered, and back to reusing: no further GETs.
                before = STATE.count('GET /calendar_apply/calendar_select')
                posts = STATE.count('POST /calendar_apply/calendar_select')
                _wait_for(lambda: STATE.count(
                    'POST /calendar_apply/calendar_select') >= posts + 3)
                check('remint: went back to reusing the new session',
                      STATE.count('GET /calendar_apply/calendar_select') == before,
                      str(STATE.count('GET /calendar_apply/calendar_select')))
                check('remint: thread still alive', t.is_alive())
            finally:
                stop.set()
                t.join(timeout=10)


def test_scan_gives_up_on_reuse_after_repeated_rejection(port):
    """If token reuse simply does not work here, degrade to the old behaviour.

    Not to a 300-second poll interval: a rejection must not drive the failure
    backoff, and a standing stream of rejected POSTs is exactly the sort of
    traffic the site says it blocks. After a few rejections, stop trying.
    """
    with Env(port) as env:
        bh.SCAN_REUSE_MAX_FAILURES = 2
        with CapturedLog() as logs:
            # Only the *cached* navs are rejected; a freshly minted token works.
            # So each cycle rejects once, re-mints, succeeds, caches, and is
            # rejected again next cycle — consecutive rejections, which is what
            # the give-up counter needs to see.
            with STATE.lock:
                STATE.blank_cached_nav = True
            t, stop = _run_scanner('2026-09', ['2026-09-15'], 'GIVEUP')
            try:
                off = _wait_for(lambda: logs.saw('session reuse disabled'))
                check('giveup: reuse switched off', off)
                check('giveup: thread still alive', t.is_alive())
                # With reuse off it re-mints every cycle, as it always did.
                gets = STATE.count('GET /calendar_apply/calendar_select')
                more = _wait_for(
                    lambda: STATE.count('GET /calendar_apply/calendar_select') >= gets + 2)
                check('giveup: back to a calendar GET per cycle', more,
                      str(STATE.count('GET /calendar_apply/calendar_select')))
                check('giveup: stopped being rejected once it re-mints every time',
                      not logs.saw('month nav returned'))
            finally:
                stop.set()
                t.join(timeout=10)


def test_failing_hotel_is_not_retried_until_its_cooldown_expires(port):
    """The real request tail is repetition, not breadth.

    `attempted` only lives for one book_all_hotels_for_date call, so without a
    cooldown a date that stays available re-attempts the same failing hotels on
    every scan cycle for as long as it is open.
    """
    with Env(port) as env:
        bh.HOTEL_RETRY_COOLDOWN = 60
        STATE.no_rooms.update({NAGU, RESOL, NIKKO})   # every hotel fails at step 5

        _, booked = bh.book_all_hotels_for_date(TARGET, 'COOL')
        check('cooldown: first pass booked nothing', booked == [], str(booked))
        first = len(STATE.hits)
        check('cooldown: first pass did try the hotels', first > 0, str(first))
        check('cooldown: all three are now cooling off',
              all(bh.in_cooldown(TARGET, h) for h in (NAGU, RESOL, NIKKO)))

        _, booked2 = bh.book_all_hotels_for_date(TARGET, 'COOL')
        check('cooldown: second pass booked nothing', booked2 == [], str(booked2))
        check('cooldown: second pass made no requests at all',
              len(STATE.hits) == first, f'{len(STATE.hits) - first} extra requests')

        # A different date is unaffected — the cooldown is per (date, hotel).
        check('cooldown: another date is not suppressed',
              not bh.in_cooldown(TARGET_LIMITED, NAGU))

        # And it lapses.
        bh.set_cooldown(TARGET, NAGU, 0)
        check('cooldown: expires', not bh.in_cooldown(TARGET, NAGU))


def test_reaching_the_room_hold_earns_the_long_cooldown(port):
    """Step 7 takes a 30-minute hold and nothing here can release one.

    So the cooldown claimed on entry has to be upgraded before that request, or a
    re-attempt inside the hold window stacks a second hold on the same facility
    and we end up reading our own holds back as 空き部屋がございません.
    """
    with Env(port) as env:
        bh.HOTEL_RETRY_COOLDOWN = 60
        bh.HOTEL_HOLD_COOLDOWN = 1800
        _, booked = bh.book_all_hotels_for_date(TARGET, 'HOLD')
        check('hold: booked', NAGU in booked, str(booked))
        left = bh.cooldown_remaining(TARGET, NAGU)
        check('hold: cooldown upgraded past the short one', left > 60, f'{left:.0f}s')
        check('hold: cooldown covers the site\'s 30-minute hold',
              left > 1700, f'{left:.0f}s')


def test_max_bookings_per_date_stops_at_the_cap(port):
    """The cap counts successes, and the priority hotel is the one that gets in."""
    with Env(port) as env:
        bh.MAX_BOOKINGS_PER_DATE = 1
        _, booked = bh.book_all_hotels_for_date(TARGET, 'CAP')
        check('cap: exactly one booking', booked == [NAGU], str(booked))
        check('cap: only the one recorded', env.bookings() == {TARGET: [NAGU]},
              str(env.bookings()))
        completions = [c for c in STATE.completed if c[1] == NAGU]
        check('cap: the site saw one completion', len(STATE.completed) == 1
              and len(completions) == 1, str(STATE.completed))

        # Already at the cap, so a later pass must not spend a single request.
        hits = len(STATE.hits)
        _, again = bh.book_all_hotels_for_date(TARGET, 'CAP')
        check('cap: a capped date books nothing more', again == [], str(again))
        check('cap: and makes no requests', len(STATE.hits) == hits,
              f'{len(STATE.hits) - hits} extra requests')


def test_cap_fails_closed_when_bookings_are_unreadable(port):
    """A cap that silently became unlimited on a read error would file duplicates.

    `get_booked_hotels` cannot tell "nothing booked" from "could not read", so the
    cap has to consult a flag that can.
    """
    with Env(port) as env:
        bh.MAX_BOOKINGS_PER_DATE = 1
        os.mkdir(bh.BOOKINGS_FILE)        # a read of this raises OSError
        hits = len(STATE.hits)
        _, booked = bh.book_all_hotels_for_date(TARGET, 'CLOSED')
        check('fail-closed: booked nothing', booked == [], str(booked))
        check('fail-closed: made no requests', len(STATE.hits) == hits,
              f'{len(STATE.hits) - hits} requests')


def test_scanner_books_and_survives_errors(port):
    """The scan loop must book, and must never let an exception end the thread."""
    with Env(port) as env:
        real = bh._date_css_class
        boom = {'n': 0}

        def flaky(body, date_str):
            boom['n'] += 1
            if boom['n'] == 1:
                raise RuntimeError('injected parser failure')
            return real(body, date_str)

        bh._date_css_class = flaky
        t, stop = _run_scanner('2026-09', [TARGET], 'TEST')
        try:
            booked_all = _wait_for(lambda: len(env.bookings().get(TARGET, [])) >= 3)
            check('scanner: survived the injected exception and booked all three',
                  booked_all and env.bookings()[TARGET] == [NAGU, RESOL, NIKKO],
                  str(env.bookings()))
            check('scanner: thread still alive', t.is_alive())
            check('scanner: the injected failure really fired', boom['n'] >= 2, str(boom))
        finally:
            bh._date_css_class = real
            stop.set()
            t.join(timeout=10)
        check('scanner: stops when asked', not t.is_alive())


def test_scanner_survives_dead_url(port):
    """A cache file that never becomes valid must not end the thread either."""
    with Env(port) as env:
        with open(bh.CALENDAR_URL_CACHE, 'w') as f:
            f.write('http://127.0.0.1:1/nope\n')
        t, stop = _run_scanner('2026-09', [TARGET], 'TEST')
        try:
            time.sleep(2)
            check('dead-url: scanner thread still alive', t.is_alive())
            check('dead-url: booked nothing', env.bookings() == {}, str(env.bookings()))
        finally:
            stop.set()
            t.join(timeout=10)


def test_scanner_skips_past_dates(port):
    """A month whose dates have all gone by must not keep hammering the site."""
    with Env(port) as env:
        t, stop = _run_scanner('1999-01', ['1999-01-01'], 'OLD')
        try:
            time.sleep(1)
            check('past-dates: no requests made for a past month',
                  STATE.count('calendar_select') == 0, str(STATE.hits[:5]))
            check('past-dates: thread still alive', t.is_alive())
        finally:
            stop.set()
            t.join(timeout=10)


def test_scanner_spots_a_limited_availability_date(port):
    """The scan filter decides whether a date is looked at at all.

    `_open_calendar_session` re-checks availability, but a date the scanner never
    reports is never handed to a booking thread in the first place, so this path
    is the one that decided `a_little` slots went unbooked.
    """
    with Env(port) as env:
        t, stop = _run_scanner('2026-09', [TARGET_LIMITED], 'LTD')
        try:
            booked = _wait_for(
                lambda: len(env.bookings().get(TARGET_LIMITED, [])) >= 3)
            check('scanner: a_little date spotted and booked',
                  booked and env.bookings()[TARGET_LIMITED] == [NAGU, RESOL, NIKKO],
                  str(env.bookings()))
        finally:
            stop.set()
            t.join(timeout=10)


# ── Persistence ─────────────────────────────────────────────────────

def test_bookings_atomic_and_non_destructive():
    with tempfile.TemporaryDirectory() as d:
        saved = bh.BOOKINGS_FILE
        bh.BOOKINGS_FILE = os.path.join(d, 'bookings.json')
        try:
            bh.save_booking('2026-09-05', NAGU)
            bh.save_booking('2026-09-05', RESOL)
            bh.save_booking('2026-09-19', NAGU)
            with open(bh.BOOKINGS_FILE, encoding='utf-8') as f:
                data = json.load(f)
            check('persist: both dates recorded',
                  data == {'2026-09-05': [NAGU, RESOL], '2026-09-19': [NAGU]}, str(data))
            check('persist: names not mangled to \\u escapes',
                  NAGU in open(bh.BOOKINGS_FILE, encoding='utf-8').read())

            bh.save_booking('2026-09-05', NAGU)
            with open(bh.BOOKINGS_FILE, encoding='utf-8') as f:
                check('persist: duplicate save is a no-op',
                      json.load(f)['2026-09-05'] == [NAGU, RESOL])

            # No temp files left behind — one per booking over weeks would add up.
            leftovers = [f for f in os.listdir(d) if f != 'bookings.json']
            check('persist: no temp files left behind', leftovers == [], str(leftovers))

            # A corrupt file must be preserved, not silently overwritten. The
            # production log has 5 bookings for 2026-08-22 that no longer appear
            # in bookings.json, which is this failure having already happened.
            with open(bh.BOOKINGS_FILE, 'w') as f:
                f.write('{"2026-08-22": ["truncated wri')
            bh.save_booking('2026-09-26', NIKKO)
            salvaged = [f for f in os.listdir(d) if '.corrupt.' in f]
            check('persist: corrupt file preserved', len(salvaged) == 1, str(os.listdir(d)))
            check('persist: corrupt bytes still readable',
                  'truncated wri' in open(os.path.join(d, salvaged[0])).read())
            with open(bh.BOOKINGS_FILE, encoding='utf-8') as f:
                check('persist: new booking still recorded after corruption',
                      json.load(f) == {'2026-09-26': [NIKKO]})

            # A non-object payload is corruption too, not an empty history.
            with open(bh.BOOKINGS_FILE, 'w') as f:
                f.write('[1, 2, 3]')
            bh.save_booking('2026-09-27', NAGU)
            check('persist: list payload treated as corrupt',
                  len([f for f in os.listdir(d) if '.corrupt.' in f]) == 2,
                  str(os.listdir(d)))
        finally:
            bh.BOOKINGS_FILE = saved


def test_unreadable_bookings_file_is_never_clobbered():
    """A read error is not corruption, and must not be treated as it.

    An OSError used to share a branch with a parse failure, so one transient read
    error was enough to rename a perfectly good file to `bookings.json.corrupt.*`
    and replace it with a single entry — destroying the record for every other
    date, which is the only thing preventing duplicate applications.
    """
    with tempfile.TemporaryDirectory() as d:
        saved = bh.BOOKINGS_FILE
        bh.BOOKINGS_FILE = os.path.join(d, 'bookings.json')
        try:
            # A directory: present, so not the missing-file path, but any read of
            # it raises OSError rather than producing unparseable bytes.
            os.mkdir(bh.BOOKINGS_FILE)
            with CapturedLog() as logs:
                bh.save_booking('2026-09-05', NAGU)
            check('unreadable: nothing salvaged aside',
                  [f for f in os.listdir(d) if '.corrupt.' in f] == [],
                  str(os.listdir(d)))
            check('unreadable: the file itself is untouched',
                  os.path.isdir(bh.BOOKINGS_FILE))
            check('unreadable: refusal is reported', logs.saw('could not be read'))
            check('unreadable: flagged unreadable but not corrupt',
                  bh._bookings_unreadable and not bh._bookings_corrupt,
                  f'{bh._bookings_unreadable=} {bh._bookings_corrupt=}')

            # Unparseable bytes, by contrast, ARE corruption and get salvaged.
            os.rmdir(bh.BOOKINGS_FILE)
            with open(bh.BOOKINGS_FILE, 'w') as f:
                f.write('{ not json')
            bh.save_booking('2026-09-05', NAGU)
            check('unreadable: a parse failure is still salvaged',
                  len([f for f in os.listdir(d) if '.corrupt.' in f]) == 1,
                  str(os.listdir(d)))
            check('unreadable: and the booking is recorded',
                  bh.get_booked_hotels('2026-09-05') == [NAGU],
                  str(bh.get_booked_hotels('2026-09-05')))
        finally:
            bh.BOOKINGS_FILE = saved


def test_bookings_concurrent_writes():
    """Twenty threads, no lost updates — the lock has to actually hold."""
    with tempfile.TemporaryDirectory() as d:
        saved = bh.BOOKINGS_FILE
        bh.BOOKINGS_FILE = os.path.join(d, 'bookings.json')
        try:
            def worker(i):
                bh.save_booking('2026-09-05', f'hotel-{i}')
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with open(bh.BOOKINGS_FILE, encoding='utf-8') as f:
                names = json.load(f)['2026-09-05']
            check('concurrent: all 20 writes survived', len(set(names)) == 20, str(len(names)))
        finally:
            bh.BOOKINGS_FILE = saved


# ── Unit-level pieces ───────────────────────────────────────────────

def test_availability_classes():
    """Which calendar cell classes count as bookable (docs/BOOKING_VIA_CURL.md)."""
    for cls in ('empty', 'empty td-n', 'a_little', 'a_little td-n', 'td-n a_little'):
        check(f'available: {cls!r}', bh.is_available(cls))
    for cls in ('full', 'full td-n', 'over', 'over td-n', 'td-n', '', None):
        check(f'not available: {cls!r}', not bh.is_available(cls))

    # Through the extractor, on the escaped markup an AJAX response really sends.
    body = nav_response('2026-09-01')
    check('available: empty cell read off the AJAX body',
          bh.is_available(bh._date_css_class(body, TARGET)),
          bh._date_css_class(body, TARGET))
    check('available: a_little cell read off the AJAX body',
          bh.is_available(bh._date_css_class(body, TARGET_LIMITED)),
          bh._date_css_class(body, TARGET_LIMITED))
    check('available: full cell read as unavailable',
          not bh.is_available(bh._date_css_class(body, '2026-09-07')),
          bh._date_css_class(body, '2026-09-07'))
    check('available: a date absent from the body is not bookable',
          not bh.is_available(bh._date_css_class(body, '2026-12-25')))


def test_retry_classification():
    for s in (0, 429, 500, 502, 503, 504):
        check(f'retryable: {s}', bh._is_retryable(s))
    for s in (200, 302, 400, 401, 403, 404, 422):
        check(f'not retryable: {s}', not bh._is_retryable(s))

    check('session dead: 302 to service_category',
          bh._is_session_dead(302, 'https://as.its-kenpo.or.jp/service_category/index'))
    check('session dead: not a 302',
          not bh._is_session_dead(200, 'https://x/service_category/index'))
    check('session dead: 302 elsewhere is progress',
          not bh._is_session_dead(302, 'https://x/apply/empty_new?s=abc'))
    check('session dead: no location', not bh._is_session_dead(302, None))


def test_retry_after():
    check('retry-after parsed', bh._retry_after('HTTP/1.1 503\r\nRetry-After: 3\r\n') == 3.0)
    check('retry-after case-insensitive', bh._retry_after('retry-after: 2\n') == 2.0)
    check('retry-after capped at backoff max',
          bh._retry_after('Retry-After: 99999\n') == bh.CURL_RETRY_BACKOFF_MAX)
    check('retry-after absent -> 0', bh._retry_after('HTTP/1.1 503\r\n') == 0.0)
    check('retry-after http-date ignored (not seconds)',
          bh._retry_after('Retry-After: Wed, 21 Oct 2015 07:28:00 GMT\n') == 0.0)
    check('retry-after None-safe', bh._retry_after(None) == 0.0)


def test_hotel_name_matching():
    check('norm collapses full-width space',
          bh._norm_hotel('和倉温泉　あえの風') == bh._norm_hotel('和倉温泉 あえの風'))
    check('norm unescapes entities', bh._norm_hotel('a&amp;b') == bh._norm_hotel('a&b'))
    check('norm is case-insensitive', bh._norm_hotel('nagu') == bh._norm_hotel('NAGU'))
    check('norm tolerates None', bh._norm_hotel(None) == '')

    saved = bh._PRIORITY_NORM
    try:
        bh._PRIORITY_NORM = ('nagu', 'トスラブ')
        ordered = bh.order_hotels([('1', RESOL), ('2', 'トスラブ箱根ビオーレ'),
                                   ('3', NIKKO), ('4', NAGU)])
        check('order: priority list order respected',
              [n for _, n in ordered] == [NAGU, 'トスラブ箱根ビオーレ', RESOL, NIKKO],
              str([n for _, n in ordered]))
        stable = bh.order_hotels([('1', RESOL), ('2', NIKKO)])
        check('order: stable for non-priority', [n for _, n in stable] == [RESOL, NIKKO])
        check('order: empty list', bh.order_hotels([]) == [])
    finally:
        bh._PRIORITY_NORM = saved


def test_dotenv_loader():
    """`.env` carries the Gmail app password and the applicant's identity.

    Precedence is the whole point here. An exported-but-empty variable must count
    as unset: the shipped template is all empty assignments, so `setdefault`
    semantics let one stale `export ITS_KIGOU=` in a shell profile shadow the file
    permanently, and the only symptom is a booking that stalls at the email step
    for a reason nothing in the log explains.
    """
    import config as cfg
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, '.env')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('# a comment\n'
                    '\n'
                    'export DOTENV_T_EXPORTED=exported\n'
                    'DOTENV_T_PLAIN=plain\n'
                    'DOTENV_T_QUOTED="has space"\n'
                    "DOTENV_T_SINGLE='single'\n"
                    'DOTENV_T_EMPTY=\n'
                    'DOTENV_T_REALWINS=fromfile\n'
                    'DOTENV_T_EMPTYWINS=fromfile\n'
                    'DOTENV_T_HASH=val#notacomment\n'
                    'DOTENV_T_SPACED =  spaced  \n'
                    'DOTENV_T_NOEQUALS\n')

        keys = [k for k in os.environ if k.startswith('DOTENV_T_')]
        for k in keys:
            del os.environ[k]
        os.environ['DOTENV_T_REALWINS'] = 'fromenv'
        os.environ['DOTENV_T_EMPTYWINS'] = ''        # a placeholder, not a decision
        try:
            cfg._load_dotenv(path)
            e = os.environ
            check('dotenv: export prefix stripped',
                  e.get('DOTENV_T_EXPORTED') == 'exported', repr(e.get('DOTENV_T_EXPORTED')))
            check('dotenv: plain assignment', e.get('DOTENV_T_PLAIN') == 'plain')
            check('dotenv: double quotes stripped',
                  e.get('DOTENV_T_QUOTED') == 'has space', repr(e.get('DOTENV_T_QUOTED')))
            check('dotenv: single quotes stripped',
                  e.get('DOTENV_T_SINGLE') == 'single', repr(e.get('DOTENV_T_SINGLE')))
            check('dotenv: empty value stays empty', e.get('DOTENV_T_EMPTY') == '')
            check('dotenv: a real env value wins over the file',
                  e.get('DOTENV_T_REALWINS') == 'fromenv', repr(e.get('DOTENV_T_REALWINS')))
            check('dotenv: an EMPTY env value does NOT shadow the file',
                  e.get('DOTENV_T_EMPTYWINS') == 'fromfile',
                  repr(e.get('DOTENV_T_EMPTYWINS')))
            check('dotenv: # inside a value is not a comment',
                  e.get('DOTENV_T_HASH') == 'val#notacomment', repr(e.get('DOTENV_T_HASH')))
            check('dotenv: key and value trimmed',
                  e.get('DOTENV_T_SPACED') == 'spaced', repr(e.get('DOTENV_T_SPACED')))
            check('dotenv: comments and blank lines ignored',
                  not any(k.startswith('#') for k in e))
            check('dotenv: a line with no = is ignored',
                  'DOTENV_T_NOEQUALS' not in e)
        finally:
            for k in [k for k in os.environ if k.startswith('DOTENV_T_')]:
                del os.environ[k]

    # A missing file must be silent, not fatal: not every install has one.
    cfg._load_dotenv(os.path.join(d, 'gone.env'))
    check('dotenv: missing file is not an error', True)


def test_confirm_gate():
    """The 10-day rule. This gate is the only thing standing between the bot and a
    booking that cannot be cancelled, so it is tested for failing closed, not just
    for the happy arithmetic."""
    saved = (bh.AUTO_CONFIRM, bh.AUTO_CONFIRM_MIN_DAYS)
    try:
        bh.AUTO_CONFIRM = True
        bh.AUTO_CONFIRM_MIN_DAYS = 11
        today = date(2026, 9, 1)

        # ── the boundary ──
        cases = [
            ('2026-09-30', 29, True),
            ('2026-09-13', 12, True),
            ('2026-09-12', 11, True),    # exactly the floor: allowed
            ('2026-09-11', 10, False),   # D-10 is the last cancellable day: no
            ('2026-09-10', 9, False),
            ('2026-09-02', 1, False),
            ('2026-09-01', 0, False),    # today
            ('2026-08-31', -1, False),   # already past
        ]
        for target, expect_days, expect_ok in cases:
            got_days = bh.days_until(target, today)
            ok, why = bh.confirm_allowed(target, today)
            check(f'gate: {target} is {expect_days} days out',
                  got_days == expect_days, str(got_days))
            check(f'gate: {target} ({expect_days}d) '
                  f'{"allowed" if expect_ok else "blocked"}',
                  ok is expect_ok, f'{ok} — {why}')

        # A blocked date must say why, so the operator knows to step in.
        ok, why = bh.confirm_allowed('2026-09-05', today)
        check('gate: refusal explains itself', not ok and '4 day(s) away' in why, why)

        # Fails closed. Note `20260905` and `2026-W36-6` are accepted by
        # date.fromisoformat on 3.11+, so the gate pins the format itself.
        for bad in (None, '', 'tomorrow', '2026-13-45', '20260905', '2026-W36-6',
                    '2026-09-05 00:00', ' 2026-09-05', 12345, [], {}):
            ok, why = bh.confirm_allowed(bad, today)
            check(f'gate: unparseable date {bad!r} is refused', not ok, f'{ok} — {why}')
            check(f'gate: days_until({bad!r}) is None',
                  bh.days_until(bad, today) is None)

        for bad in (None, '', 'eleven', float('nan')):
            bh.AUTO_CONFIRM_MIN_DAYS = bad
            ok, why = bh.confirm_allowed('2027-01-01', today)
            check(f'gate: unusable floor {bad!r} is refused', not ok, f'{ok} — {why}')
        bh.AUTO_CONFIRM_MIN_DAYS = -5
        ok, why = bh.confirm_allowed('2027-01-01', today)
        check('gate: negative floor is refused', not ok, f'{ok} — {why}')

        # ── the master switch wins over everything ──
        bh.AUTO_CONFIRM = False
        bh.AUTO_CONFIRM_MIN_DAYS = 11
        ok, why = bh.confirm_allowed('2027-01-01', today)
        check('gate: AUTO_CONFIRM=False blocks even a distant date',
              not ok and 'AUTO_CONFIRM is off' in why, f'{ok} — {why}')

        # ── a date crossing the boundary while the process runs ──
        bh.AUTO_CONFIRM = True
        target = '2026-09-20'
        allowed_on = [d for d in range(1, 21)
                      if bh.confirm_allowed(target, date(2026, 9, d))[0]]
        check('gate: re-evaluated per call, so a date closes as it approaches',
              allowed_on == list(range(1, 10)), str(allowed_on))
    finally:
        bh.AUTO_CONFIRM, bh.AUTO_CONFIRM_MIN_DAYS = saved


def test_future_dates():
    saved = bh.SKIP_PAST_DATES
    try:
        bh.SKIP_PAST_DATES = True
        out = bh._future_dates(['1999-01-01', '2999-12-31'])
        check('future: past date dropped', out == ['2999-12-31'], str(out))
        today = __import__('datetime').date.today().isoformat()
        check('future: today is kept', today in bh._future_dates([today]))
        check('future: all past -> empty', bh._future_dates(['1999-01-01']) == [])
        bh.SKIP_PAST_DATES = False
        check('future: flag off keeps everything',
              bh._future_dates(['1999-01-01']) == ['1999-01-01'])
    finally:
        bh.SKIP_PAST_DATES = saved


def test_dump_throttle_and_prune():
    with tempfile.TemporaryDirectory() as d:
        saved = (bh.DEBUG_DIR, bh.DEBUG_DUMP_INTERVAL, bh.DEBUG_DUMP_KEEP)
        bh.DEBUG_DIR, bh.DEBUG_DUMP_INTERVAL, bh.DEBUG_DUMP_KEEP = d, 300, 5
        bh._dump_last.clear()
        try:
            for _ in range(10):
                bh._dump_debug('L', 'step', 302, bh.Response('x', headers='HTTP/1.1 302\r\n'))
            files = os.listdir(d)
            check('throttle: repeated identical failure dumped once',
                  len([f for f in files if f.endswith('.headers.txt')]) == 1, str(files))

            bh._dump_last.clear()
            for i in range(10):
                bh._dump_debug('L', f'step{i}', 302, bh.Response('x', headers='HTTP/1.1 302\r\n'))
            check('prune: directory capped at DEBUG_DUMP_KEEP',
                  len(os.listdir(d)) <= 5, str(len(os.listdir(d))))

            bh._dump_last.clear()
            bh._dump_debug('L', 'unthrottled', 302, bh.Response('x', headers='h'), throttle=False)
            bh._dump_debug('L', 'unthrottled', 302, bh.Response('x', headers='h'), throttle=False)
            check('throttle: can be bypassed explicitly',
                  len([f for f in os.listdir(d) if 'unthrottled' in f and f.endswith('.txt')]) == 2,
                  str(os.listdir(d)))
        finally:
            bh.DEBUG_DIR, bh.DEBUG_DUMP_INTERVAL, bh.DEBUG_DUMP_KEEP = saved
            bh._dump_last.clear()


def test_active_bookings_counter(port):
    """main.py defers a proactive CAPTCHA refresh on this; it must reach 0 again."""
    check('active: starts at zero', bh.active_bookings() == 0, str(bh.active_bookings()))
    with Env(port) as env:
        seen = []
        real = bh._book_date_once

        def spy(*a, **k):
            seen.append(bh.active_bookings())
            return real(*a, **k)

        bh._book_date_once = spy
        try:
            bh.book_all_hotels_for_date(TARGET, 'TEST')
        finally:
            bh._book_date_once = real
        check('active: counted during a booking', seen and seen[0] == 1, str(seen))
    check('active: back to zero afterwards', bh.active_bookings() == 0, str(bh.active_bookings()))

    with Env(port) as env:
        STATE.fail_once['/calendar_apply/calendar_select'] = [404]
        bh.book_all_hotels_for_date(TARGET, 'TEST')
    check('active: zero even after a failure', bh.active_bookings() == 0)


def test_read_cached_url_never_raises():
    saved = bh.CALENDAR_URL_CACHE
    try:
        bh.CALENDAR_URL_CACHE = '/definitely/not/here/url.txt'
        check('cached-url: missing file -> None', bh._read_cached_url() is None)
        # A directory raises IsADirectoryError, not FileNotFoundError. That used
        # to escape into a scanner loop that has no except, killing the month.
        bh.CALENDAR_URL_CACHE = tempfile.mkdtemp()
        try:
            check('cached-url: a directory -> None, no raise', bh._read_cached_url() is None)
        except Exception as e:
            check('cached-url: a directory -> None, no raise', False, repr(e))
        finally:
            os.rmdir(bh.CALENDAR_URL_CACHE)
    finally:
        bh.CALENDAR_URL_CACHE = saved


# ── main.py / captcha_solver.py ─────────────────────────────────────

def test_watchdog_restarts_a_dead_worker():
    import main
    started = []

    def dies():
        started.append(1)
        raise RuntimeError('worker exploded')

    w = main._Worker('doomed', dies)
    w.start()
    for _ in range(50):
        if not w.thread.is_alive():
            break
        time.sleep(0.02)
    check('watchdog: worker really died', not w.thread.is_alive())
    check('watchdog: restart reported', w.ensure_alive() is True)
    for _ in range(50):
        if len(started) >= 2:
            break
        time.sleep(0.02)
    check('watchdog: worker was restarted', len(started) >= 2, str(started))
    check('watchdog: restart counted', w.restarts == 1, str(w.restarts))

    alive = main._Worker('fine', lambda: time.sleep(5))
    alive.start()
    check('watchdog: living worker left alone', alive.ensure_alive() is False)


def test_group_dates_by_month():
    import main
    out = main.group_dates_by_month(['2026-08-22', '2026-08-29', '2026-09-05'])
    check('group: months split', out == {'2026-08': ['2026-08-22', '2026-08-29'],
                                        '2026-09': ['2026-09-05']}, str(out))
    check('group: empty input', main.group_dates_by_month([]) == {})


def test_captcha_timeout_wrapper():
    """A hung solve must give up, not wedge the only thread that re-mints a session."""
    import asyncio
    import captcha_solver as cs

    saved = (cs._solve_and_cache, cs.CAPTCHA_TIMEOUT, cs._kill_stray_chrome)
    killed = []
    try:
        cs.CAPTCHA_TIMEOUT = 0.2
        cs._kill_stray_chrome = lambda: killed.append(1)

        async def hangs():
            await asyncio.sleep(30)

        cs._solve_and_cache = hangs
        t0 = time.time()
        out = asyncio.run(cs.get_calendar_url())
        elapsed = time.time() - t0
        check('captcha: timeout returns None', out is None, repr(out))
        check('captcha: timeout is enforced', elapsed < 5, f'{elapsed:.1f}s')
        check('captcha: stray Chrome reaped on timeout', killed == [1], str(killed))

        async def explodes():
            raise RuntimeError('pydoll blew up')

        cs._solve_and_cache = explodes
        check('captcha: exception returns None instead of propagating',
              asyncio.run(cs.get_calendar_url()) is None)

        async def works():
            return 'https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s=abc'

        cs._solve_and_cache = works
        check('captcha: success passes the URL through',
              asyncio.run(cs.get_calendar_url()).endswith('s=abc'))
    finally:
        cs._solve_and_cache, cs.CAPTCHA_TIMEOUT, cs._kill_stray_chrome = saved


def test_log_rotation():
    import main
    with tempfile.TemporaryDirectory() as d:
        saved = (main.LOG_FILE, main.LOG_MAX_BYTES, main.LOG_BACKUPS)
        main.LOG_FILE = os.path.join(d, 'its.log')
        main.LOG_MAX_BYTES, main.LOG_BACKUPS = 100, 3
        try:
            main._rotate_log()
            check('rotate: missing file is fine', not os.path.exists(main.LOG_FILE))

            with open(main.LOG_FILE, 'w') as f:
                f.write('x' * 50)
            main._rotate_log()
            check('rotate: small file left alone',
                  os.path.exists(main.LOG_FILE) and not os.path.exists(main.LOG_FILE + '.1'))

            with open(main.LOG_FILE, 'w') as f:
                f.write('first' + 'x' * 200)
            main._rotate_log()
            check('rotate: oversized file moved to .1', os.path.exists(main.LOG_FILE + '.1'))
            check('rotate: contents preserved',
                  open(main.LOG_FILE + '.1').read().startswith('first'))

            for gen in ('second', 'third', 'fourth'):
                with open(main.LOG_FILE, 'w') as f:
                    f.write(gen + 'x' * 200)
                main._rotate_log()
            backups = sorted(f for f in os.listdir(d) if '.log.' in f)
            check('rotate: kept to LOG_BACKUPS', len(backups) <= 3, str(backups))
            check('rotate: newest backup is the most recent generation',
                  open(main.LOG_FILE + '.1').read().startswith('fourth'),
                  open(main.LOG_FILE + '.1').read()[:10])
        finally:
            main.LOG_FILE, main.LOG_MAX_BYTES, main.LOG_BACKUPS = saved


# ── Runner ──────────────────────────────────────────────────────────

SERVER_TESTS = (
    'test_happy_path', 'test_priority_ordering', 'test_skip_list',
    'test_already_booked_not_repeated', 'test_503_on_calendar_get_is_retried',
    'test_session_death_on_date_select_is_retried',
    'test_session_death_mid_hotel_loop', 'test_no_rooms_is_not_an_error',
    'test_unexpected_status_is_dumped_not_retried', 'test_date_unavailable',
    'test_limited_availability_is_booked', 'test_final_submit_is_never_retried',
    'test_missing_url', 'test_scanner_books_and_survives_errors',
    'test_scanner_survives_dead_url', 'test_scanner_skips_past_dates',
    'test_scanner_spots_a_limited_availability_date',
    'test_active_bookings_counter',
    'test_scan_reuses_the_session',
    'test_scan_remints_when_the_cached_session_is_rejected',
    'test_scan_gives_up_on_reuse_after_repeated_rejection',
    'test_failing_hotel_is_not_retried_until_its_cooldown_expires',
    'test_reaching_the_room_hold_earns_the_long_cooldown',
    'test_max_bookings_per_date_stops_at_the_cap',
    'test_cap_fails_closed_when_bookings_are_unreadable',
)

STANDALONE_TESTS = (
    'test_bookings_atomic_and_non_destructive', 'test_bookings_concurrent_writes',
    'test_unreadable_bookings_file_is_never_clobbered',
    'test_availability_classes', 'test_retry_classification', 'test_retry_after',
    'test_hotel_name_matching',
    'test_future_dates', 'test_dump_throttle_and_prune',
    'test_confirm_gate', 'test_dotenv_loader',
    'test_read_cached_url_never_raises', 'test_watchdog_restarts_a_dead_worker',
    'test_group_dates_by_month', 'test_captcha_timeout_wrapper',
    'test_log_rotation', 'test_token_summary', 'test_relative_duration',
)


def main_(argv=()):
    """Run the suite. Args select tests by substring; -v shows the flow's logs."""
    verbose = '-v' in argv
    names = [a for a in argv if not a.startswith('-')]

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_port

    # Keep the flow's own logging out of the way unless it is being read.
    bh._log_handler = None if verbose else (lambda _m: None)

    def selected(pool):
        return [n for n in pool if not names or any(k in n for k in names)]

    try:
        for name in selected(SERVER_TESTS):
            globals()[name](port)
        for name in selected(STANDALONE_TESTS):
            globals()[name]()
    finally:
        bh._log_handler = None
        srv.shutdown()

    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED:')
        for f in FAILURES:
            print(f'  - {f}')
        raise SystemExit(1)
    print('all checks passed')


if __name__ == '__main__':
    import sys
    main_(sys.argv[1:])