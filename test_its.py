#!/usr/bin/env python3
"""The whole suite, against a fake ITS server. Never touches ITS.

Two halves. The booking flow — the part that actually wins or loses a slot: the
nine-step chain, hotel ordering and filtering, and the retry behaviour on the
transient failures the production log is full of (1072 x 503 on the calendar GET,
302 dumps out of service_group_select). And the curl layer underneath it: header
merging, the UA cache, and the debug dumps.

The fake server replays the real markup shapes from docs/SITE.md and from the dumps
in debug_responses/, including the escaped-quote form that AJAX responses arrive in
— the extractors are markup-exact, so a fake that pretties the markup up would test
nothing.

Pass a substring to select tests, `-v` to see the flow's own logging:

    .venv/bin/python test_its.py
    .venv/bin/python test_its.py -v test_503
"""
import json
import os
import tempfile
import threading
import time
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import book_hotels as bh
import confirm_booking as cb

# curl inherits this process's proxy settings; without this a local HTTP proxy
# intercepts the loopback requests and rewrites the responses.
os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'

FAILURES = []
# Synthetic, and deliberately self-describing: base64 of
# "FAKE_TOKEN_FOR_TESTS_NOT_A_REAL_SESSION_TOKEN", the same 60 characters a real ITS
# `s=` token runs to. Never put a real one here — even expired, it publishes the format.
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
        self.applicant_mode = 'ok'   # 'ok' | 'expired' | 'reject_apply'
        self.filed = []               # bodies that reached 申込する
        self.confirmed = []           # bodies that reached 確認
        self.browser_calls = []       # calls into the Chrome fallback
        self.browser_result = ('confirmed', '10287126')
        self.received = []            # raw request headers, for the curl tests

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
    docs/SITE.md §4. The fake serves one of each, because a filter that
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

# ── The emailed leg (steps 7-9), captured live on 2026-08-19 ─────────
#
# Shapes that matter, all of them load-bearing and none of them guessable:
#   - `_method` is `value="true"`, not a verb, and must be echoed verbatim;
#   - nothing is marked `required` and 「必須」 never appears — required-ness is an
#     `<img name="*_img">`, so a guard keyed on the attribute passes a blank form;
#   - those `<img>`s carry `name=`, so a parser that collects every named element
#     invents a dozen fields that no browser submits;
#   - the labels are only *adjacent* to their control, never `for=`-linked;
#   - `apply[year]`'s preceding text is the tail of another dropdown's options, so
#     the birth selects can only be matched on field name;
#   - the option values are `man`/`woman`, `myself`/`family` and prefecture codes,
#     so matching 男/女/本人 needs the label, not the value.
FAKE_APPLY_C = 'FAKE-C-0000-1111-2222'

APPLICANT_FORM_PAGE = f"""<html><head><title>施設予約システム</title></head><body>
<div class="attention">現在、保養施設の抽選処理を実施しております。</div>
<dl class="input_item clearfix"><dt><label>申込対象サービス</label></dt>
<dd class="elements">ブルーベリーヒル勝浦申込</dd></dl>
<form class="edit_apply" id="edit_apply_9999" \
action="/apply/confirm?c={FAKE_APPLY_C}" accept-charset="UTF-8" method="post">\
<input type="hidden" name="_method" value="true" autocomplete="off" />\
<input type="hidden" name="authenticity_token" value="AUTH-APPLICANT" \
autocomplete="off" />
<dl class="input_item clearfix"><dt><label>記号</label></dt>
<dd class="must"><img src="/assets/users/must-x.png" alt="" name="sign_no_img"/></dd>
<dd class="elements"><input value="" maxlength="5" type="text" name="apply[sign_no]" /></dd></dl>
<dl class="input_item clearfix"><dt><label>番号</label></dt>
<dd class="must"><img src="/assets/users/must-x.png" alt="" name="insured_no_img"/></dd>
<dd class="elements"><input value="" type="text" name="apply[insured_no]" /></dd></dl>
<dl class="input_item clearfix"><dt><label>事業所名</label></dt>
<dd class="must"><img src="/assets/users/must-x.png" alt="" name="office_name_img"/></dd>
<dd class="elements"><input value="" type="text" name="apply[office_name]" /></dd></dl>
<dl class="input_item clearfix"><dt><label>申込代表者名（カナ氏名）</label></dt>
<dd class="must"><img src="/assets/users/must-x.png" alt="" name="kana_name_img"/></dd>
<dd class="elements"><input value="" type="text" name="apply[kana_name]" /></dd></dl>
<dl class="input_item clearfix"><dt><label>生年月日</label></dt>
<dd class="must"><img src="/assets/users/must-x.png" alt="" name="birth_img"/></dd>
<dd class="elements">
<select name="apply[year]"><option value="" label=" "></option>\
<option value="1999">平成11年(1999年)</option><option value="2000">平成12年(2000年)</option>\
</select>年
<select name="apply[month]"><option value="" label=" "></option>\
<option value="3">3</option><option value="4">4</option></select>月
<select name="apply[day]"><option value="" label=" "></option>\
<option value="4">4</option><option value="5">5</option></select>日
</dd></dl>
<dl class="input_item clearfix"><dt><label>性別</label></dt>
<dd class="must"><img src="/assets/users/must-x.png" alt="" name="gender_img"/></dd>
<dd class="elements"><select name="apply[gender]">\
<option value="man">男性</option><option value="woman">女性</option></select></dd></dl>
<dl class="input_item clearfix"><dt><label>続柄</label></dt>
<dd class="must"><img src="/assets/users/must-x.png" alt="" name="relationship_img"/></dd>
<dd class="elements"><select name="apply[relationship]">\
<option value="myself">本人（被保険者）</option>\
<option value="family">家族（被扶養者）</option></select></dd></dl>
<dl class="input_item clearfix"><dt><label>連絡先電話番号</label></dt>
<dd class="must"><img src="/assets/users/must-x.png" alt="" name="contact_phone_img"/></dd>
<dd class="elements"><input value="" type="text" name="apply[contact_phone]" /></dd></dl>
<dl class="input_item clearfix"><dt><label>住所</label></dt>
<dd class="elements">〒<input value="" type="text" name="apply[postal]" />
<span class="ticket red">（半角）</span>
<select name="apply[state]"><option value="" label=" "></option>\
<option value="12">千葉県</option><option value="13">東京都</option></select>
<input value="" type="text" name="apply[address]" /></dd></dl>
<input value="申込する" onclick="this.form.submit();" type="button" />
</form></body></html>"""

# 「30分が経過しましたので、ご利用のURLは無効となりました。」 — 200, and with no form
# at all, so only the text tells it apart from the markup having changed.
HOLD_EXPIRED_PAGE = """<html><head><title>施設予約システム</title></head><body>
<div class="attention">現在、保養施設の抽選処理を実施しております。</div>
<p>30分が経過しましたので、ご利用のURLは無効となりました。</p>
<p>ＩＴＳホームページより最初から空き照会申込手続きをおこなってください。</p>
</body></html>"""

APPLY_CONFIRM_PAGE = f"""<html><head><title>施設予約システム</title></head><body>
<h2>申込内容確認画面</h2>
<p>この内容で申し込みをする場合は「確認」ボタンを押してください。</p>
<form action="/apply/complete?c={FAKE_APPLY_C}" accept-charset="UTF-8" method="post">
<input type="hidden" name="authenticity_token" value="AUTH-CONFIRM" autocomplete="off" />
<input value="確認" onclick="this.form.submit();" type="button" />
</form></body></html>"""

APPLY_COMPLETE_PAGE = """<html><head><title>施設予約システム</title></head><body>
<h2>申込完了</h2><p>申込を完了しました。<br>申込完了メールを送信しましたのでご確認ください。</p>
<p class="complete"><strong>申込受付番号：  10287126</strong></p>
</body></html>"""


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
        with STATE.lock:
            STATE.received.append(self.headers)
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

        # ── the emailed leg (steps 7-9). Checked first: the fake's email page
        # also lives at /apply/confirm, and the real site tells the two apart by
        # the `c=` the emailed link carries.
        if path.startswith('/apply/new'):
            if STATE.applicant_mode == 'expired':
                return self._send(200, HOLD_EXPIRED_PAGE)
            return self._send(200, APPLICANT_FORM_PAGE)

        # These two are **tripwires**, not the path under test. Chrome commits now, so
        # nothing should ever reach them; if curl regresses into committing, `filed` /
        # `confirmed` fill and test_curl_is_never_asked_to_commit fails loudly.
        if path.startswith('/apply/confirm') and 'c=' in path:
            if STATE.applicant_mode == 'reject_apply':
                return self._session_dead()
            with STATE.lock:
                STATE.filed.append(dict(form))
            return self._send(200, APPLY_CONFIRM_PAGE)

        if path.startswith('/apply/complete'):
            with STATE.lock:
                STATE.confirmed.append(dict(form))
            return self._send(200, APPLY_COMPLETE_PAGE)

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

        # ── raw shapes for the curl / dump-layer tests ──
        if path.startswith('/ok'):
            return self._send(200, '<html>hello</html>', ctype='text/html')

        if path.startswith('/raw302'):
            # A bodyless 302 out of the flow, carrying the header shapes a dump is
            # kept for: x-runtime says Rails answered, set-cookie says whether the
            # session was re-issued.
            self.send_response(302)
            self.send_header('Location',
                             f'{base}/service_category/index?s={S_TOKEN}&n=1')
            self.send_header('Set-Cookie',
                             '_src_session=deadbeefcafe1234; path=/; HttpOnly; Max-Age=0')
            self.send_header('X-Runtime', '0.0123')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return

        return self._send(404, '<html>not found</html>')


# ── Harness ─────────────────────────────────────────────────────────

def _settings(mod):
    """Every module-level setting, snapshotted for restore.

    Taken by reflection rather than a hand-listed tuple: a knob added to config.py
    would otherwise leak across tests until someone remembered to name it here.
    """
    return {k: v for k, v in vars(mod).items()
            if not k.startswith('__') and not callable(v)
            and not isinstance(v, type(os))}


class Env:
    """Point book_hotels at the fake server and temp files, then restore."""

    def __init__(self, port, skip=(), priority=('NAGU',), confirm=False,
                 applicant=None, mail=True):
        self.port, self.skip, self.priority = port, list(skip), list(priority)
        self.confirm, self.applicant, self.mail = confirm, applicant, mail

    def __enter__(self):
        self.saved = _settings(bh)
        self.saved_cb = _settings(cb)
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        bh.BASE = f'http://127.0.0.1:{self.port}'
        bh.CALENDAR_URL_CACHE = os.path.join(d, 'url.txt')
        bh.HOLDS_FILE = os.path.join(d, 'holds.json')
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
        bh.DEBUG_DUMP_INTERVAL = 0    # no throttling, so dumps are countable
        bh.SKIP_PAST_DATES = True
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
        STATE.applicant_mode = 'ok'
        STATE.filed.clear()
        STATE.confirmed.clear()
        # Both are module-global and several tests share a link fixture, so a
        # leftover claim would make the next test's hold find no mail.
        cb._pending.clear()
        cb._claimed.clear()

        # The emailed leg reaches the network for real: the confirm worker polls
        # IMAP. Left alone, every booking in this file would reach the operator's
        # actual Gmail. So AUTO_CONFIRM is off by default and the tests that want
        # the leg inject a mail source instead of a mailbox.
        cb.BASE = bh.BASE
        cb.RESERVATIONS_FILE = os.path.join(d, 'reservations.json')
        cb.CONFIRM_MAIL_TIMEOUT = 1.0
        bh.AUTO_CONFIRM = self.confirm
        bh.AUTO_CONFIRM_MIN_DAYS = 11
        if self.applicant is not None:
            cb.APPLICANT = self.applicant
        link = f'{bh.BASE}/apply/new?c={FAKE_APPLY_C}'
        # A str message is treated as pre-decoded text with no Date header, which
        # is exactly the injection point the module documents.
        cb._mail_source = (lambda _since: [f'手続きのご案内\n{link}\n']) if self.mail \
            else (lambda _since: [])

        # The commit must never launch a real Chrome from a test. Chrome is now the
        # only thing that can file an application, so every confirm test goes through
        # this stub.
        STATE.browser_calls.clear()
        STATE.browser_result = ('confirmed', '10287126')

        def _browser_stub(link_, values, log_, tag_, allow_commit):
            STATE.browser_calls.append({
                'link': link_, 'values': dict(values),
                'allow_commit': allow_commit,
            })
            result = STATE.browser_result
            return result() if callable(result) else result

        cb._browser_submit = _browser_stub

        bh._dump_last.clear()
        return self

    def __exit__(self, *exc):
        for a, v in self.saved.items():
            setattr(bh, a, v)
        for a, v in self.saved_cb.items():
            setattr(cb, a, v)
        self.tmp.cleanup()
        return False

    def reservations(self):
        if not os.path.exists(cb.RESERVATIONS_FILE):
            return {}
        with open(cb.RESERVATIONS_FILE, encoding='utf-8') as f:
            return json.load(f)

    def bookings(self):
        if not os.path.exists(bh.HOLDS_FILE):
            return {}
        with open(bh.HOLDS_FILE, encoding='utf-8') as f:
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
        check('happy: holds.json written', env.bookings() == {TARGET: [NAGU, RESOL, NIKKO]},
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
                # hotel that is not in holds.json.
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
    """Writes are atomic, and an unreadable file is never replaced.

    A bad read returning {} followed by a normal save would rewrite the file with
    only the one new entry. The production log has 5 bookings for 2026-08-22 that
    no longer appear in holds.json, which is that failure having happened.
    """
    with tempfile.TemporaryDirectory() as d:
        saved = bh.HOLDS_FILE
        bh.HOLDS_FILE = os.path.join(d, 'holds.json')
        try:
            bh.save_booking('2026-09-05', NAGU)
            bh.save_booking('2026-09-05', RESOL)
            bh.save_booking('2026-09-19', NAGU)
            with open(bh.HOLDS_FILE, encoding='utf-8') as f:
                data = json.load(f)
            check('persist: both dates recorded',
                  data == {'2026-09-05': [NAGU, RESOL], '2026-09-19': [NAGU]}, str(data))
            check('persist: names not mangled to \\u escapes',
                  NAGU in open(bh.HOLDS_FILE, encoding='utf-8').read())

            bh.save_booking('2026-09-05', NAGU)
            with open(bh.HOLDS_FILE, encoding='utf-8') as f:
                check('persist: duplicate save is a no-op',
                      json.load(f)['2026-09-05'] == [NAGU, RESOL])

            # No temp files left behind — one per booking over weeks would add up.
            leftovers = [f for f in os.listdir(d) if f != 'holds.json']
            check('persist: no temp files left behind', leftovers == [], str(leftovers))

            # Every unreadable shape must leave the bytes exactly as they were.
            for label, payload in (('truncated json', '{"2026-08-22": ["truncated wri'),
                                   ('non-object payload', '[1, 2, 3]')):
                with open(bh.HOLDS_FILE, 'w') as f:
                    f.write(payload)
                with CapturedLog() as logs:
                    bh.save_booking('2026-09-26', NIKKO)
                check(f'persist: {label} is not overwritten',
                      open(bh.HOLDS_FILE).read() == payload,
                      open(bh.HOLDS_FILE).read())
                check(f'persist: {label} refusal is reported',
                      logs.saw('could not be read'))

            # A directory: present, so not the missing-file path, but any read of
            # it raises OSError rather than producing unparseable bytes.
            os.unlink(bh.HOLDS_FILE)
            os.mkdir(bh.HOLDS_FILE)
            with CapturedLog() as logs:
                bh.save_booking('2026-09-05', NAGU)
            check('persist: an OSError leaves the path untouched',
                  os.path.isdir(bh.HOLDS_FILE))
            check('persist: OSError refusal is reported', logs.saw('could not be read'))
        finally:
            bh.HOLDS_FILE = saved


def test_bookings_concurrent_writes():
    """Twenty threads, no lost updates — the lock has to actually hold."""
    with tempfile.TemporaryDirectory() as d:
        saved = bh.HOLDS_FILE
        bh.HOLDS_FILE = os.path.join(d, 'holds.json')
        try:
            def worker(i):
                bh.save_booking('2026-09-05', f'hotel-{i}')
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with open(bh.HOLDS_FILE, encoding='utf-8') as f:
                names = json.load(f)['2026-09-05']
            check('concurrent: all 20 writes survived', len(set(names)) == 20, str(len(names)))
        finally:
            bh.HOLDS_FILE = saved


# ── Unit-level pieces ───────────────────────────────────────────────

def test_availability_classes():
    """Which calendar cell classes count as bookable (docs/SITE.md §4)."""
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

    # The site serves 祥 as U+FA1A, a compatibility ideograph; the roster-typed skip
    # entry uses U+7965. Visually identical, unequal without NFKC — and the miss booked
    # and confirmed a skipped hotel.
    check('norm folds compatibility ideographs (祥 U+FA1A)',
          bh._norm_hotel('鳴子温泉　湯元　吉\uFA1A')
          == bh._norm_hotel('鳴子温泉　湯元　吉祥'))
    check('norm folds 館 U+FA2C', bh._norm_hotel('ゆふいん山水\uFA2C')
          == bh._norm_hotel('ゆふいん山水館'))
    check('norm folds full-width ASCII',
          bh._norm_hotel('ラビスタ函館ベイＡＮＮＥＸ') == bh._norm_hotel('ラビスタ函館ベイANNEX'))

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


# ── main.py / chrome.py ─────────────────────────────────────────────

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


def test_captcha_timeout_wrapper():
    """A hung solve must give up, not wedge the only thread that re-mints a session."""
    import asyncio
    import chrome as cs

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


# ── The emailed leg (steps 7-9) ──────────────────────────────────────
#
# Everything past `send_complete` had no coverage at all, which is how a booking
# that reaches 「メール送信を完了しました」 and then silently fails to file went
# unnoticed. These run the real `confirm_from_email` against the fake, with a mail
# source injected in place of a mailbox.

APPLICANT_FIXTURE = {
    'kigou': '9999', 'bangou': '123', 'office': 'Test Company',
    'kana_sei': 'ヤマダ', 'kana_mei': 'タロウ', 'kana_name': '',
    'name_sei': '', 'name_mei': '',
    'birth': '2000-03-04', 'sex': '女', 'zokugara': '本人',
    'tel': '09012345678', 'zip': '1420051', 'state': '東京都',
    'addr': 'テスト区1-2-3',
}


def _confirm(port, mode=None, setup=None, **kw):
    """Run confirm_from_email against the fake.

    Returns `(status, detail, captured, logged)`. `captured` is read *inside* the
    Env block: Env.__exit__ restores cb.RESERVATIONS_FILE, so reading it afterwards
    reads the operator's real reservations.json.

    `mode` is set inside the block — Env.__enter__ resets applicant_mode to 'ok', so
    setting it beforehand is a no-op. `setup` runs there too, for the tests that need
    to arrange STATE before the leg starts.
    """
    with Env(port, confirm=True, applicant=dict(APPLICANT_FIXTURE), **kw) as env:
        if mode:
            STATE.applicant_mode = mode
        if setup:
            setup()
        with CapturedLog() as logged:
            links = cb._fetch_links(0)
            link = cb._take_link(0, links) if links else None
            if link is None:
                status, detail = 'failed', 'mail not received'
            else:
                status, detail = cb.confirm_from_email(
                    link, TARGET, NAGU, '[TEST]')
        captured = {'reservations': env.reservations(), 'dumps': env.dumps()}
        return status, detail, captured, logged


def test_confirm_files_the_application(port):
    """The whole emailed leg: link -> applicant form -> 申込する -> 確認 -> 受付番号."""
    status, detail, got, _log = _confirm(port)
    check('confirm: reports confirmed', status == 'confirmed', f'{status}: {detail}')
    check('confirm: parses the 申込受付番号', detail == '10287126', repr(detail))
    check('confirm: the commit went to Chrome exactly once',
          len(STATE.browser_calls) == 1, str(len(STATE.browser_calls)))
    check('confirm: reservation recorded with its receipt',
          got['reservations'] == {TARGET: [f'{NAGU}\t10287126']},
          str(got['reservations']))
    # curl reads the form and stops. These only fill if it tried to commit.
    check('confirm: curl never committed',
          STATE.filed == [] and STATE.confirmed == [],
          f'{STATE.filed} {STATE.confirmed}')

    filed = STATE.browser_calls[0]['values'] if STATE.browser_calls else {}
    # Every identity field, and the option *values* rather than the labels — the
    # form offers man/woman and myself/family, so matching 女 or 本人 literally
    # would have submitted an empty select.
    check('confirm: 記号 submitted', filed.get('apply[sign_no]') == '9999', repr(filed))
    check('confirm: 番号 submitted', filed.get('apply[insured_no]') == '123')
    check('confirm: 事業所名 submitted',
          filed.get('apply[office_name]') == 'Test Company')
    check('confirm: カナ氏名 derived with a full-width space',
          filed.get('apply[kana_name]') == 'ヤマダ　タロウ',
          repr(filed.get('apply[kana_name]')))
    check('confirm: birth split across three unpadded selects',
          (filed.get('apply[year]'), filed.get('apply[month]'),
           filed.get('apply[day]')) == ('2000', '3', '4'), repr(filed))
    check('confirm: 性別 sent as the option value, not the label',
          filed.get('apply[gender]') == 'woman', repr(filed.get('apply[gender]')))
    check('confirm: 続柄 sent as the option value',
          filed.get('apply[relationship]') == 'myself',
          repr(filed.get('apply[relationship]')))
    check('confirm: 都道府県 sent as its code',
          filed.get('apply[state]') == '13', repr(filed.get('apply[state]')))
    # The DOM already holds the live `_method` and `authenticity_token`; the scraped
    # copies belong to a different page load, so handing them over would break the
    # submit for a reason that looks like the bug this path works around.
    check('confirm: hidden fields are not handed to Chrome',
          '_method' not in filed and 'authenticity_token' not in filed,
          str(sorted(filed)))
    # The 必須 markers are <img name="..._img"> elements. A parser that took every
    # named element for a control would add a dozen fields no browser submits.
    check('confirm: the 必須 marker images are not submitted',
          not any(k.endswith('_img') for k in filed), str(sorted(filed)))
    check('confirm: every applicant field is handed over', len(filed) == 13,
          str(sorted(filed)))
    check('confirm: Chrome got the emailed link, not the POST url',
          '/apply/new' in STATE.browser_calls[0].get('link', ''),
          STATE.browser_calls[0].get('link'))
    check('confirm: reports RESERVED', _log.saw('RESERVED'), str(_log.lines))
    check('confirm: no debug dumps on the happy path', got['dumps'] == [],
          str(got['dumps']))


def test_confirm_expired_hold_is_not_a_parse_failure(port):
    """「30分が経過しましたので…」 is the hold lapsing, not a markup change."""
    status, detail, got, logged = _confirm(port, mode='expired')
    check('expired: reported as an expired hold', detail == 'hold expired',
          f'{status}: {detail}')
    check('expired: says so in the log', logged.saw('30-minute hold expired'),
          str(logged.lines))
    # Reporting it as 'no form' and dumping made a normal lost race look like the
    # applicant form having changed underneath the parser.
    check('expired: not dumped as a parse failure', got['dumps'] == [],
          str(got['dumps']))
    check('expired: nothing was filed', STATE.filed == [], str(STATE.filed))


def test_curl_is_never_asked_to_commit(port):
    """curl reads the form; Chrome files it. curl must not POST /apply/confirm at all.

    It never once worked — 0 for 3 in the logs, refused across every variation in the
    docs/SITE.md §5 matrix — so the attempt was deleted rather than left as a dead first
    try. `applicant_mode='reject_apply'` makes the fake refuse a curl commit, and the
    booking must succeed anyway because curl never tries.
    """
    status, detail, got, logged = _confirm(port, mode='reject_apply')
    check('read-only: confirmed anyway, because curl never tried',
          (status, detail) == ('confirmed', '10287126'), f'{status}: {detail}')
    check('read-only: curl never POSTed the apply form',
          STATE.count('POST /apply/confirm') == 0,
          str(STATE.count('POST /apply/confirm')))
    check('read-only: curl never POSTed the completion form',
          STATE.count('POST /apply/complete') == 0,
          str(STATE.count('POST /apply/complete')))
    check('read-only: curl did still read the applicant form',
          STATE.count('GET /apply/new') >= 1, str(STATE.count('GET /apply/new')))
    check('read-only: no rejection dump, because nothing was rejected',
          not any('apply_rejected' in f for f in got['dumps']), str(got['dumps']))
    check('read-only: says it is filing in Chrome',
          logged.saw('Filing 申込する'), str(logged.lines))


def test_confirm_never_submits_a_form_it_cannot_fill(port):
    """A missing identity value defers to a human, and files nothing.

    These are 資格認証のキー, checked against the insurance record: a half-filled
    form is a rejected application and a wasted hold, not a near miss.
    """
    with Env(port, confirm=True,
             applicant=dict(APPLICANT_FIXTURE, tel='', addr='')) as env:
        with CapturedLog() as logged:
            link = cb._take_link(0, cb._fetch_links(0))
            status, detail = cb.confirm_from_email(link, TARGET, NAGU, '[TEST]')
        check('unmapped: deferred, not submitted', status == 'deferred',
              f'{status}: {detail}')
        check('unmapped: names the fields it could not fill',
              'apply[contact_phone]' in detail and 'apply[address]' in detail,
              repr(detail))
        check('unmapped: nothing reached 申込する', STATE.filed == [], str(STATE.filed))
        check('unmapped: the form is dumped for a human',
              any('step10_unmapped_form' in f for f in env.dumps()), str(env.dumps()))
        check('unmapped: HUMAN NEEDED logged', logged.saw('HUMAN NEEDED'),
              str(logged.lines))


def test_worker_gives_up_when_no_mail_arrives(port):
    """A hold whose mail never lands is dropped with HUMAN NEEDED, not retried forever."""
    with Env(port, confirm=True, applicant=dict(APPLICANT_FIXTURE), mail=False):
        # Held longer ago than the mail budget, so this cycle is its last.
        cb.enqueue(TARGET, NAGU, held_at=time.time() - 10)
        check('no-mail: queued', cb.pending_count() == 1, str(cb.pending_count()))
        with CapturedLog() as logged:
            done = cb.drain_once()
        check('no-mail: dropped from the queue', done == 1 and cb.pending_count() == 0,
              f'done={done} pending={cb.pending_count()}')
        check('no-mail: nothing filed', STATE.filed == [], str(STATE.filed))
        check('no-mail: HUMAN NEEDED logged', logged.saw('HUMAN NEEDED'),
              str(logged.lines))


def test_worker_keeps_a_hold_queued_until_its_mail_arrives(port):
    """A recent hold with no mail yet stays queued rather than being abandoned."""
    with Env(port, confirm=True, applicant=dict(APPLICANT_FIXTURE), mail=False):
        cb.CONFIRM_MAIL_TIMEOUT = 60
        cb.enqueue(TARGET, NAGU)          # just now, so the budget has not expired
        done = cb.drain_once()
        check('patience: not dropped', done == 0 and cb.pending_count() == 1,
              f'done={done} pending={cb.pending_count()}')
        check('patience: nothing filed', STATE.filed == [], str(STATE.filed))


def test_two_holds_never_share_one_application_link(port):
    """The bug this restructure exists for.

    On 2026-08-19 two dates were held on the same facility in the same second. Each
    booking thread ran its own emailed leg and the newest-mail-wins matcher handed
    both the *same* `c=` UUID: one application was filed against an unknown date and
    the other hold was wasted (its link had already been consumed, which surfaced as
    "Browser filled 0/13 fields ... :missing").

    The worker drains holds oldest-first and claims each link, so two holds can no
    longer collide however close together they were taken.
    """
    with Env(port, confirm=True, applicant=dict(APPLICANT_FIXTURE)) as env:
        first = f'{bh.BASE}/apply/new?c=' + 'a' * 36
        second = f'{bh.BASE}/apply/new?c=' + 'b' * 36
        # Two mails, one per hold, both "arriving" with no Date header — the
        # worst case for telling them apart.
        cb._mail_source = lambda _since: [f'ご案内 {first} ', f'ご案内 {second} ']

        now = time.time()
        cb.enqueue('2026-09-01', NAGU, held_at=now)
        cb.enqueue('2026-09-16', NAGU, held_at=now)     # same second, as observed
        check('share: both queued', cb.pending_count() == 2, str(cb.pending_count()))

        cb.drain_once()

        check('share: both holds handled', cb.pending_count() == 0,
              str(cb.pending_count()))
        check('share: two distinct links were claimed', len(cb._claimed) == 2,
              str(sorted(cb._claimed)))
        # The whole point: two applications reached the server, not one twice.
        check('share: two applications were filed, not one twice',
              len(STATE.browser_calls) == 2, str(len(STATE.browser_calls)))
        check('share: each got its own link',
              len({c['link'] for c in STATE.browser_calls}) == 2,
              str([c['link'] for c in STATE.browser_calls]))
        reservations = env.reservations()
        check('share: a reservation recorded for each date',
              sorted(reservations) == ['2026-09-01', '2026-09-16'],
              str(reservations))


def test_a_claimed_link_is_never_handed_out_twice(port):
    """`_take_link` is the guard; assert it directly, without the HTTP round trip."""
    with Env(port, confirm=True, applicant=dict(APPLICANT_FIXTURE)):
        links = [(f'{bh.BASE}/apply/new?c=' + 'a' * 36, None)]
        one = cb._take_link(0, links)
        two = cb._take_link(0, links)
        check('claim: first caller gets the link', one is not None, repr(one))
        check('claim: second caller gets nothing', two is None, repr(two))


def test_worker_survives_a_leg_that_raises(port):
    """The worker is the only thing that completes an application; it may not die."""
    with Env(port, confirm=True, applicant=dict(APPLICANT_FIXTURE)):
        saved = cb.confirm_from_email
        try:
            def boom(*_a, **_k):
                raise RuntimeError('leg exploded')
            cb.confirm_from_email = boom
            cb.enqueue(TARGET, NAGU)
            with CapturedLog() as logged:
                done = cb.drain_once()
            check('worker-raise: hold still cleared', done == 1, str(done))
            check('worker-raise: reported for a human',
                  logged.saw('finish it by hand'), str(logged.lines))
        finally:
            cb.confirm_from_email = saved


def test_browser_fallback_rechecks_the_gate_live(port):
    """`allow_commit` must be re-evaluated on 申込内容確認画面, not captured earlier.

    Filling the form takes tens of seconds in a browser, and the free-cancellation
    gate can close inside that window — so what the fallback receives has to be a
    callable, and calling it has to reach bh.confirm_allowed as it stands *then*.
    """
    seen = {}

    def late_gate():
        call = STATE.browser_calls[-1]
        # What a gate closing mid-submit looks like.
        bh.AUTO_CONFIRM = False
        seen['allowed'] = call['allow_commit']()
        return 'deferred', seen['allowed'][1]

    status, detail, _got, _log = _confirm(
        port, setup=lambda: setattr(STATE, 'browser_result', late_gate))
    check('gate-live: allow_commit is callable and live',
          seen.get('allowed', (None,))[0] is False, str(seen))
    check('gate-live: a closed gate defers rather than filing',
          status == 'deferred', f'{status}: {detail}')
    check('gate-live: nothing was filed', STATE.confirmed == [],
          str(STATE.confirmed))


def test_browser_fallback_failure_still_asks_for_a_human(port):
    """If Chrome cannot finish it either, the room is still held — say so.

    Booking only queues now, so this drives the worker: the hold must survive the
    booking pass, and the worker must report it for a person. That HUMAN NEEDED line
    is the one the old code omitted for a browser 'deferred'/'failed'.
    """
    with Env(port, confirm=True, applicant=dict(APPLICANT_FIXTURE)):
        STATE.browser_result = ('failed', 'browser submit outcome unknown')
        with CapturedLog() as booking_log:
            _d, booked = bh.book_all_hotels_for_date(TARGET, 'TEST')
        check('fallback-fail: the hold is still recorded', NAGU in booked, str(booked))
        check('fallback-fail: booking only queued, it did not run the leg',
              STATE.browser_calls == [], str(STATE.browser_calls))
        check('fallback-fail: queued for the worker', cb.pending_count() == 3,
              str(cb.pending_count()))
        check('fallback-fail: booking said so', booking_log.saw('Queued for confirmation'),
              str(booking_log.lines))

        with CapturedLog() as logged:
            cb.drain_once()
        check('fallback-fail: the browser was tried', len(STATE.browser_calls) >= 1,
              str(len(STATE.browser_calls)))
        check('fallback-fail: HUMAN NEEDED reaches the operator',
              logged.saw('HUMAN NEEDED'), str(logged.lines))
        check('fallback-fail: it says where to finish and that mail was sent',
              all('the mail to' in l for l in logged.lines if 'HUMAN NEEDED' in l),
              str([l for l in logged.lines if 'HUMAN NEEDED' in l]))


def test_chrome_is_never_driven_twice_at_once():
    """The Turnstile solve and chrome.submit must not both hold a Chrome.

    `chrome._kill_stray_chrome()` reaps by `pgrep -f remote-debugging-port`,
    which matches the browser filing an application as readily as the one solving a
    Turnstile. Serialising them is what stops a timed-out solve SIGKILLing a browser
    somewhere between 申込する and 確認, with no way to learn which side of the commit
    it died on.
    """
    import chrome

    with chrome.hold(timeout=1) as owned:
        check('guard: first caller gets it', owned)

        got = []

        def second():
            with chrome.hold(timeout=0.2) as also:
                got.append(also)

        t = threading.Thread(target=second)
        t.start()
        t.join(5)
        check('guard: a second caller is refused, not blocked forever',
              got == [False], str(got))
        check('guard: the refused caller finished', not t.is_alive())

    # A solve that cannot get the browser gives up for this cycle rather than
    # racing: the URL monitor comes back in URL_CHECK_INTERVAL seconds. Run in this
    # thread with a short wait — a background thread that outlives the assertion
    # goes on to launch a real Chrome once the lock frees.
    import asyncio
    import chrome as cs
    saved_wait = chrome.DEFAULT_WAIT
    started = []
    saved_solve = cs._solve_and_cache
    try:
        chrome.DEFAULT_WAIT = 0.2

        async def _must_not_run():
            started.append(True)
            return 'http://example.invalid/calendar_select?s=x'

        cs._solve_and_cache = _must_not_run
        with chrome.hold(timeout=1) as owned:
            check('guard: held for the solve test', owned)
            out = asyncio.run(cs.get_calendar_url())
        check('guard: a busy Chrome defers the solve', out is None, repr(out))
        check('guard: and never started one', started == [], str(started))

        # Free again, the same call goes through — the guard defers, it does not
        # permanently disable solving.
        out = asyncio.run(cs.get_calendar_url())
        check('guard: solving resumes once Chrome is free', started == [True],
              str(started))
    finally:
        chrome.DEFAULT_WAIT = saved_wait
        cs._solve_and_cache = saved_solve


def test_receipt_parsing():
    """The 申込受付番号, whatever markup the label and the number sit in.


    The live page is `<strong>申込受付番号：  10287126</strong>` — one tag around both,
    which a raw-markup search reads correctly by luck. Put a tag between them and
    `[0-9A-Za-z-]{4,}` matches the tag name instead, so reservations.json records
    `strong` as the only proof a real reservation exists.
    """
    live = '<p class="complete"><strong>申込受付番号：  10287126</strong></p>'
    check('receipt: live markup', cb.parse_receipt(live) == '10287126',
          repr(cb.parse_receipt(live)))
    nested = '<p>申込受付番号：<strong>10287126</strong></p>'
    check('receipt: a tag between label and number', cb.parse_receipt(nested) == '10287126',
          repr(cb.parse_receipt(nested)))
    check('receipt: absent is empty, not a tag name',
          cb.parse_receipt('<p>申込を完了しました。</p>') == '',
          repr(cb.parse_receipt('<p>申込を完了しました。</p>')))
    check('receipt: no page at all', cb.parse_receipt(None) == '')


def test_name_match_beats_label_match():
    """A field's name owns it; an adjacent label must never override it.

    Both halves were live failures waiting to happen. `apply[state]`'s label on the
    captured page is 「postal" /> （半角）」 — the *previous* field's markup — and
    `apply[month]`'s is the tail of the year dropdown's options.
    """
    values = {'zip': '1420051', 'addr': '', 'state': '東京都',
              'birth_year': '2000', 'kana_name': 'ヤマダ　タロウ'}
    check('match: 〒 next to the prefecture select does not claim it',
          cb._match_rule('apply[state]', '住所 〒 （半角）', values) == 'state')
    check('match: カナ氏名 bleeding into the year select does not claim it',
          cb._match_rule('apply[year]', 'カナ氏名 生年月日', values) == 'birth_year')
    # With ITS_ADDR unset the address box used to match the 〒 a few characters
    # before it and submit the postcode as the street address.
    check('match: the address field stays the address field when we have none',
          cb._match_rule('apply[address]', '住所 〒 （半角）', values) == 'addr')
    check('match: an unrecognised control matches nothing',
          cb._match_rule('apply[mystery]', 'なにか', values) is None)


# ── The curl / dump layer ─────────────────────────────────────────

def test_user_agent():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'ua.txt')
        orig, bh.USER_AGENT_CACHE, bh._ua_cache = bh.USER_AGENT_CACHE, path, (None, None)
        desktop = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/140'
        try:
            check('missing file -> fallback', bh._user_agent() == bh.FALLBACK_USER_AGENT)
            with open(path, 'w') as f:
                f.write(desktop + '\n')
            check('reads recorded UA', bh._user_agent() == desktop)
            os.utime(path, (1, 1))
            with open(path, 'w') as f:
                f.write(desktop + '-v2\n')
            check('picks up a rewrite', bh._user_agent() == desktop + '-v2')
            os.utime(path, (2, 2))
            with open(path, 'w') as f:
                f.write('   \n')
            check('empty file -> fallback', bh._user_agent() == bh.FALLBACK_USER_AGENT)

            # A raise here would kill a scanner or the URL monitor thread.
            os.utime(path, (3, 3))
            with open(path, 'wb') as f:
                f.write(b'Mozilla/5.0 (Macintosh) \xff\xfe bad bytes\n')
            try:
                ua = bh._user_agent()
                check('undecodable UA file does not raise', isinstance(ua, str), repr(ua))
            except Exception as e:
                check('undecodable UA file does not raise', False, repr(e))

            # The UA is spliced into a curl -H flag.
            os.utime(path, (4, 4))
            with open(path, 'w') as f:
                f.write('Mozilla/5.0 (Macintosh) evil\nX-Injected: 1\n')
            check('newline injection stripped', '\n' not in bh._user_agent(), repr(bh._user_agent()))
            check('injected header not present',
                  'X-Injected' not in ''.join(bh.header_args()), str(bh.header_args()))

            # A mobile template would break SKIP_HOTELS name matching.
            os.utime(path, (5, 5))
            with open(path, 'w') as f:
                f.write('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari\n')
            check('mobile UA rejected -> fallback', bh._user_agent() == bh.FALLBACK_USER_AGENT)

            os.utime(path, (6, 6))
            with open(path, 'w') as f:
                f.write('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140\n')
            check('windows UA accepted', 'Windows NT' in bh._user_agent())
        finally:
            bh.USER_AGENT_CACHE, bh._ua_cache = orig, (None, None)


def test_merge_headers():
    orig = bh.BROWSER_HEADERS
    try:
        bh.BROWSER_HEADERS = True
        m = bh._merge_headers(None)
        check('defaults include UA', 'User-Agent' in m)
        check('defaults include Accept', m.get('Accept') == bh.ACCEPT)
        check('defaults include Accept-Language', m.get('Accept-Language') == bh.ACCEPT_LANGUAGE)

        m = bh._merge_headers({'Accept-Language': 'en-US'})
        check('per-call overrides default', m['Accept-Language'] == 'en-US')
        check('override does not duplicate', len([k for k in m if k.lower() == 'accept-language']) == 1, str(m))

        m = bh._merge_headers({'accept-language': 'en-US', 'user-agent': 'custom'})
        check('override is case-insensitive',
              len([k for k in m if k.lower() == 'accept-language']) == 1
              and len([k for k in m if k.lower() == 'user-agent']) == 1, str(m))

        m = bh._merge_headers({'X-CSRF-Token': 'tok', 'Referer': 'r'})
        check('per-call extras preserved', m['X-CSRF-Token'] == 'tok' and m['Referer'] == 'r')

        # ex() returns None on no match; `X-CSRF-Token: None` would 422.
        m = bh._merge_headers({'X-CSRF-Token': None})
        check('None-valued header dropped', 'X-CSRF-Token' not in m, str(m))
        check('None does not clobber defaults', m.get('Accept') == bh.ACCEPT, str(m))

        caller = {'Accept': 'text/javascript'}
        bh._merge_headers(caller)
        check('caller dict not mutated', caller == {'Accept': 'text/javascript'}, str(caller))

        bh.BROWSER_HEADERS = False
        m = bh._merge_headers({'Accept': 'text/javascript'})
        check('flag off -> only per-call headers', m == {'Accept': 'text/javascript'}, str(m))
    finally:
        bh.BROWSER_HEADERS = orig


# ── Live curl tests against localhost ───────────────────────────────

def test_curl_against_local_server(port):
    base = f'http://127.0.0.1:{port}'
    fd, cookie_file = tempfile.mkstemp(prefix='test_cookies_')
    os.close(fd)
    try:
        STATE.received.clear()
        status, body, loc = bh.curl(cookie_file, 'GET', base + '/ok')
        check('GET status', status == 200, str(status))
        check('GET body', body == '<html>hello</html>', repr(body))
        check('GET captured headers', 'content-type' in body.headers.lower(), body.headers)
        sent = STATE.received[-1]
        check('exactly one User-Agent sent', len(sent.get_all('user-agent') or []) == 1)
        check('User-Agent is browser-like', 'Mozilla/5.0' in sent['user-agent'], sent['user-agent'])
        check('Accept-Language sent', sent['accept-language'] == bh.ACCEPT_LANGUAGE)
        check('navigation Accept is browser-like', sent['accept'] == bh.ACCEPT, sent['accept'])
        check('exactly one Accept sent', len(sent.get_all('accept') or []) == 1)

        STATE.received.clear()
        status, body, loc = bh.curl(
            cookie_file, 'POST', base + '/raw302',
            {'utf8': '✓', 's': S_TOKEN},
            {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'text/javascript',
             'Accept-Language': 'en-US'})
        check('POST status is 302', status == 302, str(status))
        check('POST body is empty', body == '', repr(body))
        check('POST location captured', loc and '/service_category/index' in loc, str(loc))
        check('POST headers captured', 'x-runtime' in body.headers.lower(), body.headers)
        check('request recorded on Response', body.request.startswith('POST '), body.request)

        sent = STATE.received[-1]
        check('AJAX header passed through', sent['x-requested-with'] == 'XMLHttpRequest')
        check('per-call Accept wins, once', (sent.get_all('accept') or []) == ['text/javascript'],
              str(sent.get_all('accept')))
        check('per-call Accept-Language wins, once',
              (sent.get_all('accept-language') or []) == ['en-US'],
              str(sent.get_all('accept-language')))
        check('still exactly one User-Agent', len(sent.get_all('user-agent') or []) == 1)

        # A None-valued header must not reach the wire as the string "None".
        STATE.received.clear()
        bh.curl(cookie_file, 'GET', base + '/ok', None, {'X-CSRF-Token': None})
        sent = STATE.received[-1]
        check('None header absent from the wire', sent.get('x-csrf-token') is None,
              str(sent.get('x-csrf-token')))

        STATE.received.clear()
        _, body, _ = bh.curl(cookie_file, 'GET', f'{base}/ok?s={S_TOKEN}')
        check('request line records the URL verbatim', f'/ok?s={S_TOKEN}' in body.request,
              body.request)
    finally:
        os.unlink(cookie_file)


def test_dump_debug(port, tmpdir):
    base = f'http://127.0.0.1:{port}'
    fd, cookie_file = tempfile.mkstemp(prefix='test_cookies_')
    os.close(fd)
    orig = bh.DEBUG_DIR
    bh.DEBUG_DIR = tmpdir
    try:
        _, body, _ = bh.curl(cookie_file, 'POST', base + '/raw302', {'s': S_TOKEN})
        bh._dump_debug('2026-08-22_NAGU 勝浦', 'step3_service_select', 302, body)
        files = sorted(os.listdir(tmpdir))
        html = [f for f in files if f.endswith('.html')]
        hdrs = [f for f in files if f.endswith('.headers.txt')]
        # The 302 body is 0 bytes — 302 of the 380 real dumps are — so no .html
        # is written for it. The headers file still records the size.
        check('no .html for an empty body', html == [], str(files))
        check('dump wrote a headers file', len(hdrs) == 1, str(files))
        check('label spaces sanitized', 'NAGU_勝浦' in hdrs[0], hdrs[0])

        with open(os.path.join(tmpdir, hdrs[0])) as f:
            text = f.read()
        check('headers file records request', '# request: POST' in text, text)
        check('headers file records body size', '# body: 0 bytes' in text, text)
        check('headers file keeps status line', 'HTTP/1.1 302' in text, text)
        check('headers file keeps x-runtime', 'x-runtime: 0.0123' in text.lower(), text)
        check('headers file keeps location path', '/service_category/index' in text, text)

        # `via` carries the headers of the 302 that a follow-up GET would erase.
        _, post_body, loc = bh.curl(cookie_file, 'POST', base + '/raw302', {'s': S_TOKEN})
        _, get_body, _ = bh.curl(cookie_file, 'GET', base + '/ok')
        bh._dump_debug('2026-08-22_NAGU 勝浦', 'step8_rules', 200, get_body, post_body)
        hdrs = sorted(f for f in os.listdir(tmpdir) if 'step8_rules' in f and f.endswith('.txt'))
        with open(os.path.join(tmpdir, hdrs[0])) as f:
            text = f.read()
        check('via section present', '# ── preceding response' in text, text)
        check('via keeps the 302 status line', 'HTTP/1.1 302' in text, text)
        check('via keeps the followed body status', 'HTTP/1.1 200' in text, text)

        # Byte count, not character count: content-length is bytes, and the
        # whole point of the line is comparing the two.
        jp = bh.Response('日本語テスト', headers='HTTP/1.1 200 OK\r\ncontent-length: 18\r\n')
        section = bh._headers_section(jp)
        check('body size is bytes not chars', '# body: 18 bytes' in section, section)
    finally:
        bh.DEBUG_DIR = orig
        os.unlink(cookie_file)


def test_curl_unreachable():
    """A transport failure must return, not raise — the scan loop has no except."""
    fd, cookie_file = tempfile.mkstemp(prefix='test_cookies_')
    os.close(fd)
    try:
        status, body, loc = bh.curl(cookie_file, 'GET', 'http://127.0.0.1:1/nope')
        check('unreachable host returns status 0', status == 0, str(status))
        check('unreachable host returns empty body', body == '', repr(body))
        check('unreachable body still carries attrs', hasattr(body, 'headers'))
    except Exception as e:
        check('unreachable host does not raise', False, repr(e))
    finally:
        os.unlink(cookie_file)


# ── Runner ──────────────────────────────────────────────────────────

def main_(argv=()):
    """Run the suite. Args select tests by substring; -v shows the flow's logs.

    Tests are **discovered**, not hand-listed, so a new `def test_*` cannot be
    silently never run and a deleted one cannot abort the suite with a KeyError.
    A test that needs the fake server declares a `port` parameter; the rest take none.
    """
    verbose = '-v' in argv
    names = [a for a in argv if not a.startswith('-')]

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_port

    # Keep the flow's own logging out of the way unless it is being read.
    bh._log_handler = None if verbose else (lambda _m: None)

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)
             and (not names or any(k in n for k in names))]
    if not tests:
        raise SystemExit(f'no tests matched {names}')
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Injected by parameter name, so a test declares what it needs.
            available = {'port': port, 'tmpdir': tmpdir}
            for name, fn in tests:
                wanted = fn.__code__.co_varnames[:fn.__code__.co_argcount]
                fn(**{k: v for k, v in available.items() if k in wanted})
    finally:
        bh._log_handler = None
        srv.shutdown()

    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED:')
        for f in FAILURES:
            print(f'  - {f}')
        raise SystemExit(1)
    print(f'all checks passed ({len(tests)} tests)')


if __name__ == '__main__':
    import sys
    main_(sys.argv[1:])