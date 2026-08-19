#!/usr/bin/env python3
"""Tests for the curl/debug-dump layer in book_hotels.

Covers the two things that are easy to get silently wrong: header merging
(curl emits every -H it is given, so a duplicate would let the server pick)
and redaction (debug dumps must never contain a cookie or token value).

Runs against a throwaway localhost HTTP server — never touches ITS.

    .venv/bin/python test_http_layer.py
"""
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import book_hotels as bh

# curl inherits this process's proxy settings; without this, a local HTTP
# proxy intercepts the loopback requests and rewrites the responses.
os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'

FAILURES = []
# Synthetic, and deliberately self-describing: base64 of
# "FAKE_TOKEN_FOR_TESTS_NOT_A_REAL_SESSION_TOKEN". Same 60-character length as a
# real ITS `s=` token, which is all these tests depend on. The value that used to
# sit here was a real (expired) token — reverse-base64 of a live session string —
# so it published both the token and the format to a public remote.
LONG_TOKEN = 'RkFLRV9UT0tFTl9GT1JfVEVTVFNfTk9UX0FfUkVBTF9TRVNTSU9OX1RPS0VO'


def check(name, cond, detail=''):
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f'  — {detail}' if detail and not cond else ''))
    if not cond:
        FAILURES.append(name)


# ── Local server ────────────────────────────────────────────────────

RECEIVED = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _record(self):
        RECEIVED.append(self.headers)

    def do_GET(self):
        self._record()
        body = b'<html>hello</html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self._record()
        length = int(self.headers.get('Content-Length') or 0)
        self.rfile.read(length)
        # Mimics the failure under investigation: bodyless 302 out of the flow.
        self.send_response(302)
        self.send_header('Location',
                         f'http://127.0.0.1:{self.server.server_port}'
                         f'/service_category/index?s={LONG_TOKEN}&n=1')
        self.send_header('Set-Cookie', '_src_session=deadbeefcafe1234; path=/; HttpOnly; Max-Age=0')
        self.send_header('X-Runtime', '0.0123')
        self.send_header('X-Secret-Thing', 'should-not-appear-in-dump')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *a):
        pass


def serve():
    srv = HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ── Pure-function tests ─────────────────────────────────────────────

def test_redact_url():
    out = bh.redact_url(f'https://as.its-kenpo.or.jp/apply/empty_new?s={LONG_TOKEN}')
    check('redact_url keeps host and path', out.startswith('https://as.its-kenpo.or.jp/apply/empty_new?s='), out)
    check('redact_url drops the token', LONG_TOKEN not in out, out)
    check('redact_url fingerprints length', f'len={len(LONG_TOKEN)}' in out, out)

    plain = 'https://as.its-kenpo.or.jp/service_category/index'
    check('redact_url passes through query-less URLs', bh.redact_url(plain) == plain)
    check('redact_url keeps short values', bh.redact_url('http://x/y?n=1&m=ab') == 'http://x/y?n=1&m=ab')
    check('redact_url tolerates empty', bh.redact_url('') == '' and bh.redact_url(None) == '')

    # Real ITS path segments must survive intact or the dumps lose their point.
    # The longest real ITS segment; shorter ones follow, redaction is length-scoped.
    seg = 'check_apply_service_coma'
    check('redact_url keeps real path segments',
          seg in bh.redact_url(f'https://h/calendar_apply/{seg}'))

    # Fail-closed shapes: a token can hide outside a query value.
    out = bh.redact_url(f'https://h/calendar_apply/index/{LONG_TOKEN}')
    check('token in path redacted', LONG_TOKEN not in out, out)
    out = bh.redact_url(f'https://h/x?{LONG_TOKEN}')
    check('bare query component redacted', LONG_TOKEN not in out, out)
    out = bh.redact_url(f'https://h/x#{LONG_TOKEN}')
    check('fragment redacted', LONG_TOKEN not in out, out)
    out = bh.redact_url('https://user:supersecretpassword@h/x')
    check('userinfo redacted', 'supersecretpassword' not in out, out)

    # redact_url runs on every request and on every dump, so a malformed URL
    # must not take out a booking attempt or lose the artifact being written.
    for bad in ('http://[garbage', 'http://[v1.x]y/z'):
        try:
            check(f'malformed URL does not raise: {bad}', isinstance(bh.redact_url(bad), str))
        except Exception as e:
            check(f'malformed URL does not raise: {bad}', False, repr(e))


def test_redact_set_cookie():
    out = bh._redact_set_cookie('_src_session=deadbeefcafe1234; path=/; HttpOnly; Max-Age=0')
    check('cookie name kept', out.startswith('_src_session='), out)
    check('cookie value gone', 'deadbeefcafe1234' not in out, out)
    check('cookie attributes kept', 'HttpOnly' in out and 'Max-Age=0' in out, out)

    a = bh._redact_set_cookie('_src_session=aaa')
    b = bh._redact_set_cookie('_src_session=aaa')
    c = bh._redact_set_cookie('_src_session=bbb')
    check('same value -> same fingerprint', a == b)
    check('different value -> different fingerprint', a != c)

    out = bh._redact_set_cookie('_src_session=aaa; Expires=Wed, 21 Oct 2015 07:28:00 GMT; path=/')
    check('comma inside Expires kept', '21 Oct 2015' in out, out)
    check('Expires cookie value still redacted', 'aaa' not in out.split('Expires')[0], out)

    # Fail-closed shapes.
    out = bh._redact_set_cookie('a=AAAAAAAAAAAA; path=/, b=SECRETBBBB; path=/')
    check('comma-joined second cookie redacted', 'SECRETBBBB' not in out, out)
    out = bh._redact_set_cookie('_src_session="aa;SECRETTAIL"; path=/')
    check('value broken across ; redacted', 'SECRETTAIL' not in out, out)
    check('valueless cookie line redacted', 'novalue' not in bh._redact_set_cookie('novalue'))


def test_redact_headers():
    raw = (
        'HTTP/1.1 302 Found\r\n'
        'server: Apache\r\n'
        'x-runtime: 0.0123\r\n'
        'content-length: 0\r\n'
        f'location: https://as.its-kenpo.or.jp/service_category/index?s={LONG_TOKEN}\r\n'
        'set-cookie: _src_session=secretvalue; path=/\r\n'
        'x-secret-thing: should-not-appear\r\n'
        'garbage line\r\n'
        '\r\n'
    )
    out = bh._redact_headers(raw)
    check('status line kept', 'HTTP/1.1 302 Found' in out, out)
    check('whitelisted header verbatim', 'x-runtime: 0.0123' in out and 'server: Apache' in out, out)
    check('content-length kept', 'content-length: 0' in out, out)
    check('unknown header value redacted', 'should-not-appear' not in out, out)
    check('unknown header name kept', 'x-secret-thing:' in out, out)
    check('location token redacted', LONG_TOKEN not in out, out)
    check('location path kept', '/service_category/index' in out, out)
    check('cookie value redacted', 'secretvalue' not in out, out)
    check('unparsed line redacted', 'garbage line' not in out and '# unparsed:' in out, out)
    check('empty input handled', bh._redact_headers('') == '(no headers captured)')
    check('None input handled', bh._redact_headers(None) == '(no headers captured)')

    folded = ('HTTP/1.1 302 Found\r\n'
              'set-cookie: _src_session=aaa; path=/;\r\n'
              '\tDomain=x; SECRETCONTINUATION\r\n')
    out = bh._redact_headers(folded)
    check('folded continuation redacted', 'SECRETCONTINUATION' not in out, out)
    check('folded line marked', '# folded:' in out, out)

    odd = 'HTTP/1.1 200 OK\r\nSeT-CookIE : _src_session=oddcasing; path=/\r\n'
    out = bh._redact_headers(odd)
    check('odd-cased cookie header redacted', 'oddcasing' not in out, out)
    check('odd-cased cookie name preserved', 'SeT-CookIE' in out, out)

    check('malformed location does not raise',
          'garbage' not in bh._redact_headers('HTTP/1.1 302 Found\r\nlocation: http://[garbage\r\n'))


def test_redact_body():
    body = (
        '<meta name="csrf-token" content="dHMHhXD3GIAxj4g2aXRsecret" />\n'
        '<input name="authenticity_token" value="O1T+wuzwvSMlbMsecret" />\n'
        '<form action="/apply/empty_create?s=PT1BYTB4V1lsa3NlY3JldA">\n'
        "<a onclick=\"coma_search('PT1BYTB4V1lsa3NlY3JldFg')\">x</a>\n"
        '<td class="empty td-n" data-join-time="2026-08-22">'
    )
    out = bh._redact_body(body)
    for secret in ('dHMHhXD3GIAxj4g2aXRsecret', 'O1T+wuzwvSMlbMsecret',
                   'PT1BYTB4V1lsa3NlY3JldA', 'PT1BYTB4V1lsa3NlY3JldFg'):
        check(f'body secret redacted: {secret[:12]}', secret not in out, out)
    check('body markup preserved', 'data-join-time="2026-08-22"' in out
          and 'class="empty td-n"' in out and '/apply/empty_create' in out, out)

    # AJAX responses are Rails-UJS JavaScript: the markup arrives escaped, and
    # the new step6/calendar_select dumps are exactly that shape.
    escaped = (
        r'$(".x").html(\'<input name=\"authenticity_token\" value=\"ESCAPEDSECRET123\" />'
        r'<form action=\"/apply/empty_create?s=ESCAPEDTOKEN456789\">\');'
    )
    out = bh._redact_body(escaped)
    check('escaped authenticity_token redacted', 'ESCAPEDSECRET123' not in out, out)
    check('escaped s= token redacted', 'ESCAPEDTOKEN456789' not in out, out)

    # A base64 token's +/= tail must not survive a partial match.
    b64 = '<a href="/x?s=AAAAAAAAAAAAA+BBBBBBBBBBBBB/CCCCCCCCCCCCC=">y</a>'
    out = bh._redact_body(b64)
    check('base64 token fully redacted',
          'BBBBBBBBBBBBB' not in out and 'CCCCCCCCCCCCC' not in out, out)

    # A short captured value that also occurs in the surrounding markup must
    # not be replaced everywhere — that would shred the tags.
    short = '<input name="authenticity_token" id="e" value="e" />'
    out = bh._redact_body(short)
    check('short value does not shred markup', out.startswith('<input name="authenticity_token"'), out)
    check('short value still redacted', 'value="e"' not in out, out)


def test_response_behaves_as_str():
    r = bh.Response('<a href="x">同意</a>', headers='h: v', location='/loc', request='POST /p')
    check('Response is a str the extractors can use',
          isinstance(r, str) and bh.ex(r, r'href="(.*?)"') == 'x')
    check('Response carries headers', r.headers == 'h: v' and r.location == '/loc')


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

def test_curl_against_local_server(srv):
    base = f'http://127.0.0.1:{srv.server_port}'
    fd, cookie_file = tempfile.mkstemp(prefix='test_cookies_')
    os.close(fd)
    try:
        RECEIVED.clear()
        status, body, loc = bh.curl(cookie_file, 'GET', base + '/ok')
        check('GET status', status == 200, str(status))
        check('GET body', body == '<html>hello</html>', repr(body))
        check('GET captured headers', 'content-type' in body.headers.lower(), body.headers)
        sent = RECEIVED[-1]
        check('exactly one User-Agent sent', len(sent.get_all('user-agent') or []) == 1)
        check('User-Agent is browser-like', 'Mozilla/5.0' in sent['user-agent'], sent['user-agent'])
        check('Accept-Language sent', sent['accept-language'] == bh.ACCEPT_LANGUAGE)
        check('navigation Accept is browser-like', sent['accept'] == bh.ACCEPT, sent['accept'])
        check('exactly one Accept sent', len(sent.get_all('accept') or []) == 1)

        RECEIVED.clear()
        status, body, loc = bh.curl(
            cookie_file, 'POST', base + '/redirect',
            {'utf8': '✓', 's': LONG_TOKEN},
            {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'text/javascript',
             'Accept-Language': 'en-US'})
        check('POST status is 302', status == 302, str(status))
        check('POST body is empty', body == '', repr(body))
        check('POST location captured', loc and '/service_category/index' in loc, str(loc))
        check('POST headers captured', 'x-runtime' in body.headers.lower(), body.headers)
        check('request recorded on Response', body.request.startswith('POST '), body.request)

        sent = RECEIVED[-1]
        check('AJAX header passed through', sent['x-requested-with'] == 'XMLHttpRequest')
        check('per-call Accept wins, once', (sent.get_all('accept') or []) == ['text/javascript'],
              str(sent.get_all('accept')))
        check('per-call Accept-Language wins, once',
              (sent.get_all('accept-language') or []) == ['en-US'],
              str(sent.get_all('accept-language')))
        check('still exactly one User-Agent', len(sent.get_all('user-agent') or []) == 1)

        # A None-valued header must not reach the wire as the string "None".
        RECEIVED.clear()
        bh.curl(cookie_file, 'GET', base + '/ok', None, {'X-CSRF-Token': None})
        sent = RECEIVED[-1]
        check('None header absent from the wire', sent.get('x-csrf-token') is None,
              str(sent.get('x-csrf-token')))

        # The request line on the Response must not carry the token.
        RECEIVED.clear()
        _, body, _ = bh.curl(cookie_file, 'GET', f'{base}/ok?s={LONG_TOKEN}')
        check('request line redacts the s= token', LONG_TOKEN not in body.request, body.request)
        check('request line keeps the path', '/ok?s=' in body.request, body.request)
    finally:
        os.unlink(cookie_file)


def test_dump_debug(srv, tmpdir):
    base = f'http://127.0.0.1:{srv.server_port}'
    fd, cookie_file = tempfile.mkstemp(prefix='test_cookies_')
    os.close(fd)
    orig = bh.DEBUG_DIR
    bh.DEBUG_DIR = tmpdir
    try:
        _, body, _ = bh.curl(cookie_file, 'POST', base + '/redirect', {'s': LONG_TOKEN})
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
        check('NO token in headers file', LONG_TOKEN not in text, text)
        check('NO cookie value in headers file', 'deadbeefcafe1234' not in text, text)
        check('NO unknown header value in headers file', 'should-not-appear' not in text, text)

        # `via` carries the headers of the 302 that a follow-up GET would erase.
        _, post_body, loc = bh.curl(cookie_file, 'POST', base + '/redirect', {'s': LONG_TOKEN})
        _, get_body, _ = bh.curl(cookie_file, 'GET', base + '/ok')
        bh._dump_debug('2026-08-22_NAGU 勝浦', 'step8_rules', 200, get_body, post_body)
        hdrs = sorted(f for f in os.listdir(tmpdir) if 'step8_rules' in f and f.endswith('.txt'))
        with open(os.path.join(tmpdir, hdrs[0])) as f:
            text = f.read()
        check('via section present', '# ── preceding response' in text, text)
        check('via keeps the 302 status line', 'HTTP/1.1 302' in text, text)
        check('via keeps the followed body status', 'HTTP/1.1 200' in text, text)
        check('via cookie still redacted', 'deadbeefcafe1234' not in text, text)

        # Byte count, not character count: content-length is bytes, and the
        # whole point of the line is comparing the two.
        jp = bh.Response('日本語テスト', headers='HTTP/1.1 200 OK\r\ncontent-length: 18\r\n')
        section = bh._headers_section(jp)
        check('body size is bytes not chars', '# body: 18 bytes' in section, section)

        # The .html sibling must be redacted too — it is the file people open.
        secret_body = bh.Response(
            '<input name="authenticity_token" value="O1T+wuzwvSMlbMsecret" />'
            f'<form action="/apply/empty_create?s={LONG_TOKEN}">',
            headers='HTTP/1.1 200 OK\r\n')
        bh._dump_debug('leaktest', 'step5_booking_form', 200, secret_body)
        leak = [f for f in os.listdir(tmpdir) if 'leaktest' in f and f.endswith('.html')]
        with open(os.path.join(tmpdir, leak[0])) as f:
            text = f.read()
        check('NO authenticity_token value in body file', 'O1T+wuzwvSMlbMsecret' not in text, text)
        check('NO s= token in body file', LONG_TOKEN not in text, text)
        check('body file keeps form action path', '/apply/empty_create' in text, text)
    finally:
        bh.DEBUG_DIR = orig
        os.unlink(cookie_file)


def test_dump_debug_with_plain_str():
    """_dump_debug must survive a body that is not a Response."""
    with tempfile.TemporaryDirectory() as d:
        orig = bh.DEBUG_DIR
        bh.DEBUG_DIR = d
        try:
            bh._dump_debug('plain', 'step0', 0, '')
            hdrs = [f for f in os.listdir(d) if f.endswith('.headers.txt')]
            check('plain str body still dumps', len(hdrs) == 1, str(os.listdir(d)))
            with open(os.path.join(d, hdrs[0])) as f:
                text = f.read()
            check('plain str body notes unknown request', '(unknown)' in text, text)
            check('plain str body notes no headers', '(no headers captured)' in text, text)
        finally:
            bh.DEBUG_DIR = orig


def test_curl_zero_attempts(srv):
    """CURL_MAX_ATTEMPTS=0 used to leave status/hdrs unbound -> NameError."""
    fd, cookie_file = tempfile.mkstemp(prefix='test_cookies_')
    os.close(fd)
    orig = bh.CURL_MAX_ATTEMPTS
    bh.CURL_MAX_ATTEMPTS = 0
    try:
        status, body, _ = bh.curl(cookie_file, 'GET', f'http://127.0.0.1:{srv.server_port}/ok')
        check('CURL_MAX_ATTEMPTS=0 still makes one request', status == 200, str(status))
    except Exception as e:
        check('CURL_MAX_ATTEMPTS=0 still makes one request', False, repr(e))
    finally:
        bh.CURL_MAX_ATTEMPTS = orig
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


def main():
    srv = serve()
    test_redact_url()
    test_redact_set_cookie()
    test_redact_headers()
    test_redact_body()
    test_response_behaves_as_str()
    test_user_agent()
    test_merge_headers()
    test_curl_against_local_server(srv)
    with tempfile.TemporaryDirectory() as d:
        test_dump_debug(srv, d)
    test_dump_debug_with_plain_str()
    test_curl_zero_attempts(srv)
    test_curl_unreachable()
    srv.shutdown()

    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED: ' + ', '.join(FAILURES))
        raise SystemExit(1)
    print('all checks passed')


if __name__ == '__main__':
    main()
