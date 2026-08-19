#!/usr/bin/env python3
"""Turn holds into reservations, off the link in the site's confirmation mail.

`book_hotels` stops at `send_complete` (a mail dispatched) and enqueues the hold here.
`worker()` drains that queue serially: read the applicant form, then 申込する → 確認 →
申込受付番号. That commit is the first irreversible, money-bearing action in the program.

**curl reads; only Chrome commits.** `POST /apply/confirm` answers 302 →
/service_category/index for curl whatever it sends (0 for 3 in the logs, and refused
across every variation in docs/SITE.md §5) while Chrome is 2 for 2, so the curl attempt
was deleted rather than kept as a dead first try. The GET and the form parse work fine
over curl and stay there.

Three things guard the commit:

- `bh.confirm_allowed()` runs before the commit and again on the 申込内容確認画面.
- The form is filled from what the form declares, never hard-coded names; anything
  `map_fields` cannot place is `unmapped`, and an unmapped form is dumped for a human
  rather than submitted half-filled against somebody's insurance number.
- Each emailed link is claimed exactly once, so two holds cannot collide.
"""
import email
import email.utils
import imaplib
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone

from config import (
    IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_APP_PASSWORD, MAIL_FROM, APPLICANT,
    CONFIRM_MAIL_TIMEOUT, CONFIRM_POLL_INTERVAL, RESERVATIONS_FILE,
)
import book_hotels as bh
from book_hotels import log, R, G, Y, C, B, X, BASE

# Replaceable so the tests can inject messages without an IMAP server, exactly as
# `book_hotels._log_handler` is replaceable. Signature: (since_epoch) -> [str]
_mail_source = None


# ── the emailed link ─────────────────────────────────────────────────

def parse_message(raw_bytes):
    """`(text, arrival_epoch)` for one message. `arrival_epoch` is None if unreadable.

    All text parts are concatenated, charset-decoded: ITS mail may be ISO-2022-JP or
    UTF-8, base64 or quoted-printable, plain or multipart, and the URL only has to
    appear in one of them. The Date is read in the same pass — `parsedate_to_datetime`
    *raises* on a malformed header, and one bad Date on an unrelated message in the
    mailbox used to silently cost a booking.
    """
    try:
        msg = email.message_from_bytes(raw_bytes)
    except Exception:
        return raw_bytes.decode('utf-8', 'replace'), None

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

    try:
        dt = email.utils.parsedate_to_datetime(msg.get('Date') or '')
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        epoch = dt.timestamp()
    except Exception:
        epoch = None
    return '\n'.join(chunks), epoch


def _url_re():
    """URLs on the configured site only — a safety property, since the mail body is
    the one input here nobody in this repo authors and an arbitrary link would receive
    somebody's 記号/番号. Built from `BASE` so the tests can use a loopback fake."""
    return re.compile(re.escape(BASE.rstrip('/')) + r'/[^\s<>"\']+')


def extract_apply_link(text):
    """The application URL in a confirmation mail, or None.

    Prefers a link carrying `c=` or `s=`; among those the longest wins, because the
    mail also carries short generic links that would otherwise be picked.
    """
    urls = [u.rstrip('.,)>]') for u in _url_re().findall(text or '')]
    if not urls:
        return None
    tokened = [u for u in urls if re.search(r'[?&][cs]=', u)]
    return max(tokened or urls, key=len)


def _imap_messages(since_epoch):
    """Raw bytes of candidate messages from the site, newest last."""
    if not (IMAP_USER and IMAP_APP_PASSWORD):
        log(f"  {R}No IMAP credentials — cannot read the confirmation mail. "
            f"See `uv run main.py --check`{X}")
        return []
    since = datetime.fromtimestamp(since_epoch, timezone.utc).strftime('%d-%b-%Y')
    out = []
    try:
        # Scoped to this connection: setdefaulttimeout() is process-wide and would
        # retime pydoll's CDP websocket too.
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30) as m:
            m.login(IMAP_USER, IMAP_APP_PASSWORD)
            # All Mail, not INBOX: Gmail's classifier or a stray filter can file it
            # elsewhere, and a mailbox detail is an absurd way to lose a room.
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


def _fetch_links(since_epoch):
    """`[(link, arrival_epoch)]` for every confirmation mail since `since_epoch`.

    One IMAP round trip serves the whole queue. `arrival_epoch` is None when the source
    cannot supply one (the tests inject strings); callers treat that as eligible.
    """
    out = []
    for raw in (_mail_source or _imap_messages)(since_epoch):
        # A str is an injected test message: pre-decoded, and with no Date header.
        text, epoch = (raw, None) if isinstance(raw, str) else parse_message(raw)
        link = extract_apply_link(text)
        if link:
            out.append((link, epoch))
    return out


# ── the pending-hold queue ───────────────────────────────────────────
# One worker drains this serially, which buys two things:
#
# - A booking thread no longer blocks for CONFIRM_MAIL_TIMEOUT plus a browser submit,
#   so several dates opening at once are all held at full speed.
# - **Two holds can no longer consume the same emailed link.** They did: two dates
#   held in the same second on 2026-08-19 both got the same `c=` UUID from the old
#   newest-mail-wins matcher, filing one application against an unknown date and
#   wasting the other hold.
#
# Safe to run off-thread because `/apply/new?c=<uuid>` establishes its own session —
# which is also why the browser fallback works from a fresh Chrome.
_pending_lock = threading.Lock()
_pending = []      # [(held_at, target_date, hotel_name)], oldest first
_claimed = set()   # `c=` values already handed to a leg; never reused

# Slack on the IMAP SINCE floor: the mail is dispatched seconds before the hold is
# enqueued, and neither clock is ours.
_CLOCK_SLACK = 300


def enqueue(target_date, hotel_name, held_at=None):
    """Register a hold whose emailed leg still has to run."""
    with _pending_lock:
        _pending.append((held_at or time.time(), target_date, hotel_name))
        return len(_pending)


def pending_count():
    with _pending_lock:
        return len(_pending)


def _link_id(link):
    """The `c=` (or `s=`) value that identifies one application, for claiming."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    return (q.get('c') or q.get('s') or [link])[0]


def _take_link(hold_at, links):
    """Claim the **oldest** unclaimed link that could belong to this hold.

    Oldest, not newest: the queue drains oldest hold first and the site mails one per
    hold in that order, so oldest-to-oldest lines up. Newest-wins is what handed two
    holds the same link.
    """
    for link, when in sorted(links, key=lambda p: (p[1] is not None, p[1])):
        if when is not None and when < hold_at - _CLOCK_SLACK:
            continue
        with _pending_lock:
            key = _link_id(link)
            if key in _claimed:
                continue
            _claimed.add(key)
        return link
    return None



# ── the applicant form ───────────────────────────────────────────────

_TIMEOUT_TEXT = 'セッションがタイムアウトしました'

# The emailed link's own expiry page — the 30-minute hold lapsing — served 200 with
# no form at all. The only failure on this leg that is certainly not a bug, but it
# renders as a formless page, so without this text it is indistinguishable from the
# applicant form's markup having changed underneath the parser.
_EXPIRED_TEXT = 'ご利用のURLは無効となりました'


def _rejected(status, body, location):
    """True when a response is the site refusing the session rather than answering.

    Must be checked *before* the redirect is followed: once followed, a rejected POST
    is indistinguishable from "no confirmation form on 申込内容確認画面".
    """
    return bh._is_session_dead(status, location) or bool(
        body and _TIMEOUT_TEXT in body)


_TAG_RE = re.compile(r'<(input|select|textarea)\b([^>]*)>', re.I)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"|([\w:-]+)\s*=\s*\'([^\']*)\'')
_OPTION_RE = re.compile(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', re.I | re.S)


def _attrs(blob):
    out = {}
    for m in _ATTR_RE.finditer(blob or ''):
        key = (m.group(1) or m.group(3) or '').lower()
        out[key] = m.group(2) if m.group(2) is not None else (m.group(4) or '')
    return out


def _strip_tags(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s or '')).strip()


def parse_form(html):
    """`(action, fields)` for the form most likely to be the applicant form.

    `fields` are dicts of name/type/value/label/options; `label` is the visible text
    immediately before the control, which is how the Japanese names are recovered —
    this site labels by proximity, not `for=`. No `required` key, deliberately: the
    live form marks nothing required. See `map_fields`.
    """
    forms = re.findall(r'(?is)<form\b([^>]*)>(.*?)</form>', html or '')
    if not forms:
        return None, []

    def score(attrs_blob, body):
        act = _attrs(attrs_blob).get('action', '')
        s = len(re.findall(r'<(input|select|textarea)\b', body, re.I))
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
            'label': preceding[-40:],
            'options': options,
        })
    return action, fields


# Field names and the labels beside them, captured live from /apply/new on
# 2026-08-19. Most specific first: `kana_name` must win over the split `kana_sei`, and
# 番号 must not be claimed by 記号. The three birth selects are matched on name only —
# `apply[month]`'s label is the tail of the year dropdown's options.
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


def applicant_values(email_address=None):
    """`config.APPLICANT` plus the shapes the live page asks for: one combined カナ氏名
    box and the birth date as three dropdowns. Deriving them here keeps `.env` as the
    plain facts off the insurance card."""
    a = dict(APPLICANT)
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
    """The option value whose label or value matches `wanted`. Exact first, then
    substring: the live options are `man`/`男性` and `myself`/`本人（被保険者）`, so plain
    equality matches neither 男 nor 本人."""
    if not options or not wanted:
        return None
    for value, label in options:
        if wanted in (value, label):
            return value
    for value, label in options:
        if value and wanted in label:
            return value
    return None


def _match_rule(name, label, values):
    """Which `_MAP_RULES` key fills the control called `name`, or None.

    **Field name first, label only as a fallback**, because `label` is just the 400
    characters before the control and can contain another field's Japanese label. A
    name is structural, so a name match wins even with no value configured — otherwise
    `apply[address]` with `ITS_ADDR` unset matches the 〒 before it and submits the
    postcode as the street address.
    """
    for candidate, (name_pat, _label_pat) in _MAP_RULES:
        if re.search(name_pat, name, re.I):
            return candidate
    for candidate, (_name_pat, label_pat) in _MAP_RULES:
        if values.get(candidate) and label_pat and re.search(label_pat, label):
            return candidate
    return None


def map_fields(fields, email_address=None):
    """`(post_data, unmapped)` — fill what we recognise, report what we do not.

    **`required` cannot be trusted**: the live form marks nothing required and never
    says 「必須」, so a guard keyed on it reported "0 unmapped" for a form with 事業所名,
    the birth month and day and the whole address blank. The rule is the other way
    round — any visible control we could neither fill nor leave at a server value is
    unmapped, and **a caller finding `unmapped` non-empty must not submit.** These are
    資格認証のキー: a half-filled form is a rejected application and a wasted hold.
    """
    a = applicant_values(email_address)
    post, unmapped = {}, []

    for f in fields:
        name, ftype = f['name'], f['type']

        if ftype == 'hidden':
            # Includes `_method` and `authenticity_token`; echo verbatim.
            post[name] = f['value']
            continue
        key = _match_rule(name, f['label'], a)

        if key is None:
            post[name] = f['value']
            if not f['value']:
                unmapped.append(name)
            continue

        wanted = a.get(key) or ''
        if not wanted:
            # Recognised but unset (a missing ITS_* variable): unmapped, so the
            # caller defers rather than submitting a blank 資格認証のキー.
            post[name] = f['value']
            unmapped.append(name)
            continue
        if f['options']:
            chosen = _match_option(f['options'], wanted)
            if chosen is None:
                post[name] = f['value']
                unmapped.append(name)
                continue
            wanted = chosen
        post[name] = wanted

    return post, unmapped


# ── committing the application ───────────────────────────────────────

_RECEIPT_RE = re.compile(r'申込受付番号[^0-9A-Za-z]{0,12}([0-9A-Za-z-]{4,})')

# Replaceable so the tests can exercise the commit without launching Chrome, the same
# way `_mail_source` stands in for a mailbox. Signature matches `chrome.submit`.
_browser_submit = None


def _commit(link, post, target_date, hotel_name, tag):
    """File the application in real Chrome. The only way that works.

    curl is **not** tried. `POST /apply/confirm` answers 302 → /service_category/index
    for curl whatever it sends — 0 for 3 in the logs, and refused across every variation
    in the docs/SITE.md §5 bisect matrix — while Chrome is 2 for 2. Reading the form over
    curl still works fine, so only the two committing POSTs moved.

    Hidden fields are dropped: the DOM already has `_method` and `authenticity_token`,
    and the scraped copies belong to a *different* page load.
    """
    submit = _browser_submit
    if submit is None:
        try:
            import chrome
        except Exception as e:
            # pydoll missing or broken. The hold and the mail still stand, so this is a
            # degraded outcome, not a crash.
            log(f"{tag}   {R}Cannot load browser ({e!r}); nothing can file this "
                f"application{X}")
            return 'failed', 'no browser available'
        submit = chrome.submit

    values = {k: v for k, v in post.items()
              if k not in ('_method', 'authenticity_token')}
    log(f"{tag}   {C}Filing 申込する → 確認 in real Chrome{X}")

    status, detail = submit(
        link, values, log, tag,
        # Re-consulted on the 申込内容確認画面, immediately before the commit.
        allow_commit=lambda: bh.confirm_allowed(target_date))

    if status == 'confirmed':
        log(f"{tag}   {B}{G}RESERVED: {hotel_name} on {target_date}"
            + (f' — 申込受付番号 {detail}' if detail else '') + X)
        # reservations.json, not holds.json: an entry here means a real
        # cancellation liability exists.
        bh.save_booking(target_date, f'{hotel_name}\t{detail}'.strip(),
                        path=RESERVATIONS_FILE)
    return status, detail


def parse_receipt(body):
    """The 申込受付番号 on 申込完了画面, or ''.

    Against the **tag-stripped** text. The live page is one tag around both label and
    number, so a raw-markup regex works by luck; one tag between them and it captures
    `strong`, writing a made-up number into the only record a reservation exists.
    """
    m = _RECEIPT_RE.search(_strip_tags(body))
    return m.group(1) if m else ''


def confirm_from_email(link, target_date, hotel_name, tag):
    """The emailed leg, given an already-resolved application link.

    curl reads the applicant form and maps its fields; Chrome files it. Returns
    `(status, detail)`: 'confirmed' (detail is the 申込受付番号), 'deferred' (left for a
    human) or 'failed'. The read runs on its own cookie jar — the emailed link
    establishes its own session, which is what lets this run on the worker.
    """
    label = f'{target_date}_{hotel_name}'
    with bh.session_jar('cookies_confirm_') as (_jar, c):
        return _run_leg(c, link, target_date, hotel_name, tag, label)


def _run_leg(c, link, target_date, hotel_name, tag, label):
    log(f"{tag}   {C}Application link: {link}{X}")

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
    if _EXPIRED_TEXT in body:
        # Not a parser problem and not worth a dump: the 30 minutes ran out.
        log(f"{tag}   {Y}The 30-minute hold expired before the mail was "
            f"followed — the site has released {hotel_name}{X}")
        return 'failed', 'hold expired'

    action, fields = parse_form(body)
    if not action or not fields:
        log(f"{tag}   {R}No applicant form found on the page{X}")
        bh._dump_debug(label, 'step10_no_form', s, body)
        return 'failed', 'no form'

    post, unmapped = map_fields(fields, email_address=bh.EMAIL)
    log(f"{tag}   {C}Applicant form: {len(fields)} fields, "
        f"{len(unmapped)} unmapped{X}")
    if unmapped:
        # Never submit a form we do not fully understand.
        log(f"{tag}   {R}Unmapped required field(s): {', '.join(unmapped)}{X}")
        log(f"{tag}   {B}{Y}HUMAN NEEDED: form dumped to {bh.DEBUG_DIR}. The room "
            f"is held — finish from the mail now.{X}")
        bh._dump_debug(label, 'step10_unmapped_form', s, body, throttle=False)
        return 'deferred', f'unmapped: {", ".join(unmapped)}'

    allowed, why = bh.confirm_allowed(target_date)
    if not allowed:
        log(f"{tag}   {Y}Gate closed while reading the mail: {why}{X}")
        return 'deferred', why

    return _commit(link, post, target_date, hotel_name, tag)


# ── the worker ───────────────────────────────────────────────────────

def _process(hold, links):
    """Run one hold's leg if a link is available. True when done with the hold, False
    to leave it queued for a later poll."""
    held_at, target_date, hotel_name = hold
    tag = f'[MAIL {target_date[5:]}]'

    link = _take_link(held_at, links)
    if link is None:
        if time.time() - held_at >= CONFIRM_MAIL_TIMEOUT:
            log(f"{tag}   {R}No confirmation mail for {hotel_name} on "
                f"{target_date} within {CONFIRM_MAIL_TIMEOUT:.0f}s{X}")
            log(f"{tag}   {B}{Y}HUMAN NEEDED: the room is held and the mail was "
                f"sent to {bh.EMAIL}. Open its link and finish now.{X}")
            return True
        return False

    try:
        status, detail = confirm_from_email(link, target_date, hotel_name, tag)
    except Exception as e:
        # A hold plus a sent mail is worth keeping, so nothing here may kill the
        # worker.
        log(f"{tag}   {R}Confirmation raised {e!r}; the room is held and the mail "
            f"is sent — finish it by hand{X}")
        return True

    if status == 'confirmed':
        return True
    log(f"{tag}   {Y}{hotel_name} on {target_date} not confirmed "
        f"({status}: {detail}){X}")
    # Every non-confirmed outcome leaves a held room and a sent mail a person can
    # still finish. 'deferred' was assumed to announce itself, which was untrue of
    # the browser path.
    log(f"{tag}   {B}{Y}HUMAN NEEDED: {hotel_name} on {target_date} is held and the "
        f"mail to {bh.EMAIL} is sent. Open its link and finish now.{X}")
    return True


def drain_once():
    """One worker cycle: fetch mail once, then run each ready hold's leg serially.

    Returns how many holds left the queue. Separate from `worker()` so the tests can
    drive one cycle instead of racing a loop.
    """
    with _pending_lock:
        queue = sorted(_pending)
    if not queue:
        return 0

    links = _fetch_links(min(h[0] for h in queue) - _CLOCK_SLACK)
    done = 0
    for hold in queue:
        if _process(hold, links):
            done += 1
            with _pending_lock:
                if hold in _pending:
                    _pending.remove(hold)
    return done


def worker(stop_event=None):
    """Drain the pending-hold queue, serially, forever.

    Legs run one at a time, which is what makes `_take_link`'s claiming sufficient: no
    two are ever mid-flight competing for the same mail. The loop body is guarded —
    this is the only thing that completes an application and main() never joins it, so
    an escaping exception would strand every future hold silently.
    """
    # An Event nobody sets makes `.wait(n)` behave exactly like `time.sleep(n)`.
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        try:
            drain_once()
        except Exception as e:
            log(f"{R}Confirm worker error: {e!r}{X}")
        stop_event.wait(CONFIRM_POLL_INTERVAL)
