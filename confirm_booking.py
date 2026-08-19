#!/usr/bin/env python3
"""Complete the application that the site's confirmation email points at.

`book_hotels.book_one_hotel` stops at `send_complete`, which is only
「メール送信を完了しました」 — an email was dispatched. The official 空き照会申込手順
continues:

    7. 届いたメールをご確認いただき、メール記載のURLから申込画面を開き、
       必要事項を入力し「申込する」ボタンを押してください。
    8. 申込内容確認画面にて申し込みいただいた内容を確認のうえ、「確認」ボタンを
       押してください。
    9. 申込完了画面に遷移し申込受付番号が表示され … この時点で申込手続き完了及び
       予約確定となります。

This module is steps 7-9, and step 9 is the first irreversible, money-bearing
action in the program: past it a real reservation exists, carrying a real
cancellation liability. Two things guard it.

`book_hotels.confirm_allowed()` is consulted immediately before each committing
POST, not once at the top. Free cancellation ends at D-10, so anything nearer than
`AUTO_CONFIRM_MIN_DAYS` is left for a person — the room is still held and the email
still sent, so they can finish it themselves inside the site's 30 minutes.

And the form is filled from what the form itself declares, never from hard-coded
field names. Nobody has captured this page: 2,460 blobs in this repository's
history contain no 記号/生年月日/続柄 markup, because every dump predates
`send_complete`. So `map_fields` matches the live form's own inputs against the
values in `config.APPLICANT`, and `unmapped` lists anything required it could not
place. A form we do not fully understand is abandoned and dumped for a human
rather than submitted half-filled against somebody's insurance number.
"""
import email
import email.header
import email.utils
import imaplib
import os
import re
import socket
import time
import urllib.parse
from datetime import datetime, timezone

from config import (
    IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_APP_PASSWORD, MAIL_FROM, APPLICANT,
    CONFIRM_MAIL_TIMEOUT, CONFIRM_HOLD_SECONDS, CONFIRM_HOLD_MARGIN,
    RESERVATIONS_FILE,
)
import book_hotels as bh
from book_hotels import log, R, G, Y, C, B, X, redact_url

BASE = 'https://as.its-kenpo.or.jp'

# Replaceable so the tests can inject messages without an IMAP server, exactly as
# `book_hotels._log_handler` is replaceable. Signature: (since_epoch) -> [str]
_mail_source = None


# ── the emailed link ─────────────────────────────────────────────────

def _decode_header(raw):
    if not raw:
        return ''
    out = []
    for text, enc in email.header.decode_header(raw):
        if isinstance(text, bytes):
            out.append(text.decode(enc or 'utf-8', 'replace'))
        else:
            out.append(text)
    return ''.join(out)


def message_text(raw_bytes):
    """All text of a message, charset-decoded.

    ITS mail is Japanese and may arrive as ISO-2022-JP or UTF-8, base64 or
    quoted-printable, and as either plain text or multipart. Every part is
    concatenated because the application URL only has to appear in one of them.
    """
    try:
        msg = email.message_from_bytes(raw_bytes)
    except Exception:
        return raw_bytes.decode('utf-8', 'replace')
    chunks = []
    for part in msg.walk():
        if part.get_content_maintype() != 'text':
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or 'utf-8'
        try:
            chunks.append(payload.decode(charset, 'replace'))
        except LookupError:
            chunks.append(payload.decode('utf-8', 'replace'))
    return '\n'.join(chunks)


_URL_RE = re.compile(r'https?://as\.its-kenpo\.or\.jp/[^\s<>"\']+')


def extract_apply_link(text):
    """The application URL in a confirmation mail, or None.

    Prefers a link carrying a session-ish parameter: the live mail's link is
    `/apply/new?c=<36 chars>`, so `c=` matters as much as `s=`. Among candidates the
    longest wins, because the mail also contains short generic links — the site's
    homepage, a do-not-reply notice — that would otherwise be picked.
    """
    urls = [u.rstrip('.,)>]') for u in _URL_RE.findall(text or '')]
    if not urls:
        return None
    tokened = [u for u in urls if re.search(r'[?&][cs]=', u)]
    return max(tokened or urls, key=len)


def _imap_messages(since_epoch):
    """Raw bytes of candidate messages from the site, newest last."""
    if not (IMAP_USER and IMAP_APP_PASSWORD):
        log(f"  {R}No IMAP credentials — cannot read the confirmation mail. "
            f"See check_env.py{X}")
        return []
    since = datetime.fromtimestamp(since_epoch, timezone.utc).strftime('%d-%b-%Y')
    out = []
    try:
        socket.setdefaulttimeout(30)
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as m:
            m.login(IMAP_USER, IMAP_APP_PASSWORD)
            # All Mail, not INBOX: Gmail's own classifier or a stray filter can
            # file the message elsewhere, and losing a held room to a
            # mailbox-selection detail would be an absurd way to lose it.
            for box in ('"[Gmail]/All Mail"', 'INBOX'):
                if m.select(box, readonly=True)[0] == 'OK':
                    break
            typ, data = m.search(None, 'FROM', f'"{MAIL_FROM}"', 'SINCE', since)
            if typ != 'OK' or not data or not data[0]:
                return []
            for num in data[0].split()[-20:]:
                typ, parts = m.fetch(num, '(BODY.PEEK[])')
                if typ != 'OK':
                    continue
                for part in parts:
                    if isinstance(part, tuple) and part[1]:
                        out.append(part[1])
    except (imaplib.IMAP4.error, OSError) as e:
        log(f"  {R}IMAP failed: {type(e).__name__}: {e}{X}")
    return out


def _message_epoch(raw_bytes):
    """The message's Date header as an epoch, or None."""
    try:
        msg = email.message_from_bytes(raw_bytes)
    except Exception:
        return None
    dt = email.utils.parsedate_to_datetime(_decode_header(msg.get('Date')) or '')
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def wait_for_apply_link(target_date, since_epoch, timeout=None, poll=5.0,
                        seen=None):
    """Poll the mailbox for this booking's application link.

    Matched on **when the message arrived**, not on the stay date. The mail names
    only the date it was sent — a live 2026-08-19 message for a 2026-09-16 stay
    contains 「2026年08月19日」 twice and 09-16 nowhere — so a stay-date filter can
    never match and always falls through to a guess. Arrival time after the hold
    was taken is the honest discriminator; `seen` stops two bookings in flight from
    consuming each other's link.
    """
    deadline = time.monotonic() + (timeout or CONFIRM_MAIL_TIMEOUT)
    seen = seen if seen is not None else set()
    while True:
        source = _mail_source or _imap_messages
        best = None
        for raw in source(since_epoch):
            text = raw if isinstance(raw, str) else message_text(raw)
            link = extract_apply_link(text)
            if not link or link in seen:
                continue
            when = None if isinstance(raw, str) else _message_epoch(raw)
            if when is not None and when < since_epoch:
                continue
            if best is None or (when or 0) >= (best[1] or 0):
                best = (link, when)
        if best:
            seen.add(best[0])
            return best[0]
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


# ── the applicant form ───────────────────────────────────────────────

_TIMEOUT_TEXT = 'セッションがタイムアウトしました'


def _rejected(status, body, location):
    """True when a response is the site refusing the session rather than answering.

    Two shapes, and following the redirect hides both: a 302 to
    `/service_category/index`, and that page's own text once followed. The first
    live run reported "No confirmation form on 申込内容確認画面" for what was really
    a rejected POST, because the 302 had already been followed into the top page.
    """
    if bh._is_session_dead(status, location):
        return True
    return bool(body) and (_TIMEOUT_TEXT in body
                           or bh._SESSION_DEAD_PATH in (location or ''))


_TAG_RE = re.compile(r'<(input|select|textarea)\b([^>]*)>', re.I)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"|([\w:-]+)\s*=\s*\'([^\']*)\'')
_OPTION_RE = re.compile(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', re.I | re.S)


def _attrs(blob):
    out = {}
    for m in _ATTR_RE.finditer(blob or ''):
        key = (m.group(1) or m.group(3) or '').lower()
        out[key] = m.group(2) if m.group(2) is not None else (m.group(4) or '')
    for flag in ('required', 'checked', 'disabled'):
        if re.search(rf'\b{flag}\b', blob or '', re.I) and flag not in out:
            out[flag] = flag
    return out


def _strip_tags(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s or '')).strip()


def parse_form(html, prefer_action=None):
    """`(action, fields)` for the form most likely to be the applicant form.

    `fields` are dicts of name/type/value/required/label/options. `label` is the
    visible text immediately before the control, which is how the Japanese field
    names are recovered — this site labels by proximity, not by `for=`.
    """
    forms = re.findall(r'(?is)<form\b([^>]*)>(.*?)</form>', html or '')
    if not forms:
        return None, []

    def score(attrs_blob, body):
        a = _attrs(attrs_blob)
        act = a.get('action', '')
        s = len(re.findall(r'<(input|select|textarea)\b', body, re.I))
        if prefer_action and prefer_action in act:
            s += 100
        if re.search(r'apply|regist|entry|confirm', act, re.I):
            s += 10
        return s

    attrs_blob, body = max(forms, key=lambda f: score(f[0], f[1]))
    action = _attrs(attrs_blob).get('action', '')

    fields = []
    for m in _TAG_RE.finditer(body):
        tag, blob = m.group(1).lower(), m.group(2)
        a = _attrs(blob)
        name = a.get('name')
        if not name:
            continue
        ftype = (a.get('type') or ('select' if tag == 'select' else 'text')).lower()
        if ftype in ('submit', 'button', 'image', 'reset'):
            continue
        options = []
        if tag == 'select':
            tail = body[m.end():]
            close = re.search(r'</select>', tail, re.I)
            if close:
                options = [(v, _strip_tags(t))
                           for v, t in _OPTION_RE.findall(tail[:close.start()])]
        preceding = _strip_tags(body[max(0, m.start() - 400):m.start()])
        fields.append({
            'name': name,
            'type': ftype,
            'value': a.get('value', ''),
            'required': 'required' in a,
            'checked': 'checked' in a,
            'label': preceding[-40:],
            'options': options,
        })
    return action, fields


# Field names, and the Japanese labels beside them, as captured live from
# /apply/new on 2026-08-19. Ordered most specific first: `kana_name` must win
# before the split `kana_sei`, and 番号 must not be claimed by 記号.
#
# The birth date arrives as three separate selects whose *labels* are useless —
# `apply[month]`'s preceding text is the tail of the year dropdown's options — so
# those three are matched on field name only.
_MAP_RULES = (
    ('kigou', (r'sign_no|kigou|kigo|\bsymbol\b', r'記号')),
    ('bangou', (r'insured_no|bangou|bango|member_?no', r'(?<!電話)番号')),
    ('office', (r'office_name|jigyou?sho', r'事業所')),
    ('kana_name', (r'kana_?name', r'カナ氏名')),
    ('kana_sei', (r'(sei|last|family|surname).*kana|kana.*(sei|last|family)',
                  r'(セイ|カナ).*(姓|氏)|姓.*カナ')),
    ('kana_mei', (r'(mei|first|given).*kana|kana.*(mei|first|given)',
                  r'(メイ|カナ).*名|名.*カナ')),
    ('name_sei', (r'\b(sei|last_?name|family_?name|surname)\b', r'^(?!.*カナ).*姓')),
    ('name_mei', (r'\b(mei|first_?name|given_?name)\b', r'^(?!.*カナ).*名前?$')),
    ('birth_year', (r'\[year\]|birth_?year|seinen_?y', None)),
    ('birth_month', (r'\[month\]|birth_?month', None)),
    ('birth_day', (r'\[day\]|birth_?day', None)),
    ('sex', (r'gender|\bsex\b|seibetsu', r'性別')),
    ('zokugara', (r'relationship|zokugara|relation', r'続柄')),
    ('tel', (r'contact_phone|\btel\b|phone|denwa', r'電話')),
    ('zip', (r'postal|\bzip\b|post_?code|yuubin', r'郵便|〒')),
    ('state', (r'\[state\]|prefecture|todou?fuken', r'都道府県')),
    ('addr', (r'address|juu?sho', r'住所')),
    ('email', (r'\bmail\b', r'メール')),
)


def applicant_values(applicant=None, email_address=None):
    """`config.APPLICANT` plus the forms the live page actually asks for.

    The captured form wants one combined カナ氏名 box rather than separate 姓/名,
    and the birth date as three dropdowns. Deriving those here keeps `.env` as the
    plain facts off the insurance card.
    """
    a = dict(APPLICANT if applicant is None else applicant)
    if email_address:
        a.setdefault('email', email_address)

    if not a.get('kana_name') and (a.get('kana_sei') or a.get('kana_mei')):
        # Full-width space: what a Japanese 「カナ氏名」 box conventionally holds.
        # Override with ITS_KANA_NAME if the card is written differently.
        a['kana_name'] = f"{a.get('kana_sei', '')}　{a.get('kana_mei', '')}".strip()

    birth = a.get('birth') or ''
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', birth):
        # Unpadded: the day/month options are value="1".."12", not "01".
        a['birth_year'] = birth[:4]
        a['birth_month'] = str(int(birth[5:7]))
        a['birth_day'] = str(int(birth[8:10]))
    return a


def _match_option(options, wanted):
    """The option value whose label or value matches `wanted`.

    Exact first, then substring, because the live選択肢 are `man`/`男性` and
    `myself`/`本人（被保険者）` — a plain equality test matches neither 男 nor 本人.
    """
    if not options or not wanted:
        return None
    for value, label in options:
        if wanted in (value, label):
            return value
    for value, label in options:
        if value and wanted in label:
            return value
    return None


def map_fields(fields, applicant=None, email_address=None, optional=()):
    """`(post_data, unmapped)` — fill what we recognise, report what we do not.

    **`required` attributes cannot be trusted here.** The live applicant form marks
    nothing required: no `required` attribute anywhere and not one occurrence of
    「必須」. A guard keyed on that attribute reported "15 fields, 0 unmapped" for a
    form in which 事業所名, the birth month and day, and the whole address were
    blank, and the submission was rejected.

    So the rule is the other way round: any *visible* control we could neither fill
    from `applicant` nor leave at a server-provided value is unmapped, and a caller
    finding `unmapped` non-empty must not submit. These values are 資格認証のキー,
    checked against the insurance record, so a partly-filled form is not a near
    miss — it is a rejected application and a wasted room hold.

    `optional` names fields that are genuinely allowed to stay empty.
    """
    a = applicant_values(applicant, email_address)
    post, unmapped = {}, []

    for f in fields:
        name, ftype = f['name'], f['type']

        if ftype == 'hidden':
            # Includes Rails' `_method` and `authenticity_token`; echo verbatim.
            post[name] = f['value']
            continue
        if ftype in ('checkbox', 'radio'):
            # Only the site knows the value it wants for a consent box, so these
            # are surfaced to the caller rather than guessed at.
            if f['checked']:
                post[name] = f['value']
            elif name not in optional:
                unmapped.append(name)
            continue

        key = None
        for candidate, (name_pat, label_pat) in _MAP_RULES:
            if not a.get(candidate):
                continue
            if re.search(name_pat, name, re.I) or (
                    label_pat and re.search(label_pat, f['label'])):
                key = candidate
                break

        if key is None:
            post[name] = f['value']
            if not f['value'] and name not in optional:
                unmapped.append(name)
            continue

        wanted = a[key]
        if f['options']:
            chosen = _match_option(f['options'], wanted)
            if chosen is None:
                post[name] = f['value']
                if name not in optional:
                    unmapped.append(name)
                continue
            wanted = chosen
        post[name] = wanted

    return post, unmapped


# ── committing the application ───────────────────────────────────────

def _save_reservation(target_date, hotel_name, receipt):
    """Record a confirmed reservation. Separate from bookings.json, which holds
    *holds*; an entry here means a real cancellation liability exists."""
    saved = bh.BOOKINGS_FILE
    try:
        bh.BOOKINGS_FILE = RESERVATIONS_FILE
        bh.save_booking(target_date, f'{hotel_name}\t{receipt}'.strip())
    finally:
        bh.BOOKINGS_FILE = saved


_RECEIPT_RE = re.compile(r'申込受付番号[^0-9A-Za-z]{0,12}([0-9A-Za-z-]{4,})')


def confirm_from_email(c, target_date, hotel_name, tag, held_at=None,
                       label=None):
    """Steps 7-9. Returns `(status, detail)`.

    status is one of:
      'confirmed'  — 予約確定, `detail` is the 申込受付番号 (or '' if unparsed)
      'deferred'   — deliberately left for a human; `detail` says why
      'failed'     — could not get there; `detail` says how far it got

    `c` is a `book_hotels.curl`-style callable bound to a cookie jar.
    """
    label = label or f'{target_date}_{hotel_name}'
    held_at = held_at or time.time()

    def hold_left():
        return CONFIRM_HOLD_SECONDS - (time.time() - held_at)

    allowed, why = bh.confirm_allowed(target_date)
    if not allowed:
        log(f"{tag}   {Y}Not completing {hotel_name} for {target_date}: {why}{X}")
        log(f"{tag}   {B}{Y}HUMAN NEEDED: the room is held and the email is sent. "
            f"Open the link in the mail to {bh.EMAIL} and finish within "
            f"{hold_left() / 60:.0f} minutes.{X}")
        return 'deferred', why

    log(f"{tag}   {C}Waiting for the confirmation mail ({hold_left() / 60:.0f}m "
        f"of hold left){X}")
    budget = max(0.0, min(CONFIRM_MAIL_TIMEOUT, hold_left() - CONFIRM_HOLD_MARGIN))
    if budget <= 0:
        log(f"{tag}   {R}Too little hold left to finish safely{X}")
        return 'failed', 'hold nearly expired'

    link = wait_for_apply_link(target_date, held_at - 300, timeout=budget)
    if not link:
        log(f"{tag}   {R}No confirmation mail within {budget:.0f}s{X}")
        return 'failed', 'mail not received'
    log(f"{tag}   {C}Got the application link: {redact_url(link)}{X}")

    s, body, loc = c('GET', link)
    if s == 302 and loc and not _rejected(s, body, loc):
        s, body, loc = c('GET', loc)
    if _rejected(s, body, loc):
        log(f"{tag}   {R}The site refused the emailed link's session{X}")
        bh._dump_debug(label, 'step10_apply_page_rejected', s, body)
        return 'failed', 'apply page session rejected'
    if s != 200 or not body:
        log(f"{tag}   {R}Application page returned {s}{X}")
        bh._dump_debug(label, 'step10_apply_page', s, body)
        return 'failed', f'apply page {s}'

    action, fields = parse_form(body)
    if not action or not fields:
        log(f"{tag}   {R}No applicant form found on the page{X}")
        bh._dump_debug(label, 'step10_no_form', s, body)
        return 'failed', 'no form'

    post, unmapped = map_fields(fields, email_address=bh.EMAIL)
    log(f"{tag}   {C}Applicant form: {len(fields)} fields, "
        f"{len(unmapped)} unmapped{X}")
    if unmapped:
        # Never submit a form we do not fully understand: these values are
        # 資格認証のキー, validated against the insurance record.
        log(f"{tag}   {R}Unmapped required field(s): {', '.join(unmapped)}{X}")
        log(f"{tag}   {B}{Y}HUMAN NEEDED: form dumped to {bh.DEBUG_DIR}. The room "
            f"is held — finish from the mail within {hold_left() / 60:.0f} "
            f"minutes.{X}")
        bh._dump_debug(label, 'step10_unmapped_form', s, body, throttle=False)
        return 'deferred', f'unmapped: {", ".join(unmapped)}'

    apply_url = urllib.parse.urljoin(link, action)

    allowed, why = bh.confirm_allowed(target_date)
    if not allowed:
        log(f"{tag}   {Y}Gate closed while reading the mail: {why}{X}")
        return 'deferred', why

    # 申込する. Navigational in effect — it renders 申込内容確認画面 — so it stays
    # retryable, but nothing past here may be repeated blindly.
    s, body, loc = c('POST', apply_url, post, {'Referer': link})
    if s == 302 and loc and not _rejected(s, body, loc):
        s, body, loc = c('GET', loc)
    if _rejected(s, body, loc):
        # Distinguished from "no form on the page", which is what this looked like
        # on the first live run once the 302 had been followed.
        log(f"{tag}   {R}申込する was rejected: the site dropped the session{X}")
        bh._dump_debug(label, 'step11_apply_rejected', s, body)
        return 'failed', 'apply post session rejected'
    if s != 200 or not body:
        log(f"{tag}   {R}申込する returned {s}{X}")
        bh._dump_debug(label, 'step11_apply_post', s, body)
        return 'failed', f'apply post {s}'
    if bh._NO_ROOMS_TEXT in body:
        log(f"{tag}   {Y}Room gone before the application landed{X}")
        return 'failed', 'room taken'

    action2, fields2 = parse_form(body, prefer_action='confirm')
    if not action2:
        log(f"{tag}   {R}No confirmation form on 申込内容確認画面{X}")
        bh._dump_debug(label, 'step11_no_confirm_form', s, body)
        return 'failed', 'no confirm form'
    post2 = {f['name']: f['value'] for f in fields2
             if f['type'] in ('hidden', 'text', 'select') and f['name']}

    allowed, why = bh.confirm_allowed(target_date)
    if not allowed:
        log(f"{tag}   {Y}Gate closed before 確認: {why}{X}")
        return 'deferred', why

    # 確認 — the point of no return. Not idempotent (it files the application and
    # dispatches 申込完了メール) and `--max-time` can expire after the server has
    # accepted it, so a repeat could file twice with no way to tell them apart.
    log(f"{tag}   {B}確認: filing the application for {hotel_name} "
        f"on {target_date}{X}")
    s, body, loc = c('POST', urllib.parse.urljoin(apply_url, action2), post2,
                     {'Referer': apply_url}, retry=False)
    if s == 0:
        log(f"{tag}   {R}確認 got no response: outcome unknown, NOT retrying. "
            f"Check the mailbox for a 申込完了メール before trying again.{X}")
        return 'failed', 'confirm outcome unknown'
    if s == 302 and loc:
        s, body, loc = c('GET', loc)

    receipt = ''
    m = _RECEIPT_RE.search(body or '')
    if m:
        receipt = m.group(1)
    if receipt or '申込完了' in (body or ''):
        log(f"{tag}   {B}{G}RESERVED: {hotel_name} on {target_date}"
            + (f' — 申込受付番号 {receipt}' if receipt else '') + X)
        _save_reservation(target_date, hotel_name, receipt)
        return 'confirmed', receipt

    log(f"{tag}   {R}確認 did not produce a completion page{X}")
    bh._dump_debug(label, 'step12_confirm_result', s, body)
    return 'failed', 'no completion page'
