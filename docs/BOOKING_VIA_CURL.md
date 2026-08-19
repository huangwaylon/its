# ITS Booking Flow via curl (HTTP-Only)

**Status: VERIFIED.** The curl chain (steps 1–9) ran live on 2026-03-26, taking a hold
on ホテルハーヴェスト斑尾 for 2026-04-13 and dispatching the confirmation mail. The
emailed leg ran live on 2026-08-19: ブルーベリーヒル勝浦 for 2026-09-01, 申込受付番号
**10287126**, 予約確定. `book_hotels.py` and `confirm_booking.py` are the
implementation; the original one-shot script is out of the tree
(`git show 9e644df:run_live.py`).

## What this flow does

The official 空き照会申込手順
(https://www.its-kenpo.or.jp/shisetsu/moushikomi.html) has **nine steps**. The curl
chain in `book_hotels.book_one_hotel` implements the first six and ends at
`send_complete` — a 30-minute hold plus a dispatched email, not a reservation.

> 「予約手続きに進む」ボタンを押して以降、**30分以内**に申込手続きを完了してください。完了しない場合、選択した部屋は無効となります。
>
> 申込完了画面に遷移し申込受付番号が表示され、申込完了メールが自動送信されます。**この時点で申込手続き完了及び予約確定となります。**

- **Step 7** (`/apply/empty_create`) is 「予約手続きに進む」: it takes the 30-minute
  hold and is the point of no return. **Step 9** (`send_complete`) only sends mail.
- The remaining three official steps — open the emailed URL, fill the applicant form,
  申込する, 確認 — are `confirm_booking.py`, with `browser_apply.py` doing 申込する in
  real Chrome when curl is refused there (Finding 6). Only that leg yields a
  申込受付番号 and 予約確定. So `bookings.json` holds *holds*, which lapse after 30
  minutes unless that leg completes; `reservations.json` holds the confirmed ones.
- Nothing here ever **releases** a hold. Any failure after step 7 abandons a held
  room. That is bounded rather than unbounded because the site refuses a second
  application at a facility we already hold: step 5 answers 「空き部屋がございません」,
  so the bot reads its own holds back and the facility drops out of what it is
  offered. This is why nothing in the code tracks the 30-minute clock.

## High-Level Architecture

| Aspect | Detail |
|--------|--------|
| Framework | Rails (Phusion Passenger 6.0.13 / Apache) |
| CSRF | `authenticity_token` in hidden fields AND `<meta name="csrf-token">`; lifetimes in Finding 1 |
| Session | `_src_session` cookie (HttpOnly, Secure) |
| Load balancer | AWS ALB, sticky-session cookies — Finding 4 |
| AJAX | Rails UJS (`dataType: 'script'`) — responses are executable JavaScript that mutates the DOM |
| Auth gate | **Cloudflare Turnstile** (`.cf-turnstile`, `input[name="cf-turnstile-response"]`), solved by `captcha_solver.py` |

### The `s` parameter

Every URL carries an `s` query parameter, a server-side session token (Base64,
URL-encoded). Two distinct values appear: the **calendar `s`**, minted by the CAPTCHA
at step 0 and used for steps 1–4 (`/calendar_apply/*`), and the **apply `s`**, minted
by the step 4 redirect and used for steps 5–9 (`/apply/*`). The calendar `s` is cached
in `calendar_url_cache.txt` and serves **many complete booking flows** — it expires on
inactivity, not on use — while each flow mints its own apply-side session at step 4.
The token is not opaque: it decodes to `service_category_id=1&verify_expires=<epoch>`
with no signature, so read the `s=` token section of `CLAUDE.md` before logging any
part of it.

## Prerequisites

`curl` with cookie-jar support, plus a valid calendar `s` token (browser-only; step 0).
Steps 1–9 need curl alone. Every request `book_hotels.curl()` makes carries the same
flags, abbreviated below as `$C`, against `$U='https://as.its-kenpo.or.jp'`:

```bash
C='curl -s -c cookies.txt -b cookies.txt --max-redirs 0 -D hdrs.txt --max-time 30'
```

`--max-redirs 0` is **global**, not per-step: no redirect anywhere is auto-followed,
each is re-issued by hand from the captured `Location`, and `-D` is the only way the
status line and `Location` are read at all.

`BROWSER_HEADERS` adds three headers to every request, merged in Python so a per-call
header replaces the default rather than duplicating it (curl emits every `-H` it is
given): `User-Agent` (the UA of the Chrome that solved the CAPTCHA, from
`chrome_user_agent.txt`), `Accept:
text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8` and `Accept-Language:
ja-JP,ja;q=0.9`.

Captured markup for every page below lives in `test_booking_flow.py`'s `FakeITS`,
which replays it — escaped-quote AJAX bodies included — on every test run. Read the
markup there; only the samples quoted here are ones the fake does not carry.

## Step 0: Acquire Calendar URL (Browser Required)

The only step needing a browser. `GET /` links to `/calendar_apply?s={MAIN_S}`
(「カレンダーから探す」), whose form POSTs back to `/calendar_apply` with
`authenticity_token`, `s` and the Turnstile response. `captcha_solver.py` clicks the
Turnstile checkbox with CDP mouse events, polls `input[name="cf-turnstile-response"]`
for the token, then submits 次へ; the server answers
`302 -> /calendar_apply/calendar_select?s={CALENDAR_S}`.

`CALENDAR_S` drives every later step. It is cached **only if** the resulting URL
contains `calendar_select` — a non-calendar URL still answers 200, so caching one
poisons the cache with a session that looks healthy and never triggers a re-solve.

## Step 1: Load the Calendar Page

```bash
$C "$U/calendar_apply/calendar_select?s={CALENDAR_S}"
```

**200 OK:** the current month's page. `_open_calendar_session` extracts
`csrf-token.*?content="(.*?)"` (the meta CSRF),
`name="authenticity_token" value="(.*?)"` (the form token) and
`name="s" id="s" value="(.*?)"`.

Each date is a `<td>` with `data-join-time="YYYY-MM-DD"` whose class carries the
availability: `empty` (green ○), `a_little` (orange, few left), `full` (red ☓), `over`
(past). `selectJoinTime` blocks the click on the last two. **Both `empty` and
`a_little` can be applied for** (`is_available()`, `_AVAILABLE_CLASSES`) — matching
only `empty` silently skips the dates closest to selling out. The hidden form
`selectJoinTime` submits POSTs `utf8`, `authenticity_token`, `join_time` and `s` to
`/calendar_apply/service_group_select`.

## Step 1b: Navigate to a Different Month

An **AJAX POST** returning executable JavaScript. Not optional in practice: the
calendar opens on the current month, and this one POST returns availability for every
date in the target month.

```bash
$C -X POST "$U/calendar_apply/calendar_select" \
  -H 'X-Requested-With: XMLHttpRequest' -H 'X-CSRF-Token: {CSRF_TOKEN}' \
  -H 'Accept: text/javascript, application/javascript, */*; q=0.01' \
  -H "Referer: $U/calendar_apply/calendar_select?s={CALENDAR_S}" \
  --data-urlencode 'join_date=2026-04-01' --data-urlencode 's={CALENDAR_S_RAW}'
```

Two parameters only — `join_date` (first day of the target month) and `s`. No `utf8`,
no `authenticity_token`; the CSRF value travels in `X-CSRF-Token`.

**Response:** `$(".tcas_1").html('…');` with the markup's quotes backslash-escaped, so
the date-cell extractor must match that escaped form (`_date_css_class`):

```python
rf'class=\\"([^"\\]*)\\\"[^>]*data-join-time=\\"{date}\\"'
```

Next/prev `join_date` values appear nowhere but the button `onclick` handlers:
`onclick="toNextMonth('2026-04-01','{CALENDAR_S_RAW}');"`.

## Step 2: Select a Date

```bash
$C -X POST "$U/calendar_apply/service_group_select" \
  --data-urlencode 'utf8=✓' --data-urlencode 'authenticity_token={AUTH_TOKEN}' \
  --data-urlencode 'join_time=2026-04-13' --data-urlencode 's={CALENDAR_S_RAW}'
```

**200 OK:** the hotel selection page, and this request is the single largest failure
class on disk (Finding 5). Extract with
`re.findall(r'data-service-group-id="(\d+)".*?>(.*?)</a>', body)`, then
`html.unescape()` each name: names arrive HTML-escaped and mix full-width (U+3000)
with ordinary spaces, so raw equality against a skip list can miss. Per the page
header 「{date}に空きがある施設です」, **only facilities with vacancy are listed** —
typically ~3, not the full 24-facility roster. The hidden form POSTs `utf8`,
`authenticity_token` (re-extracted from *this* page), `empty`, `join_time`, `s` and
`service_group_id` to `/calendar_apply/apply_service_select`.

## Step 3: Select a Hotel

```bash
$C -X POST "$U/calendar_apply/apply_service_select" \
  --data-urlencode 'utf8=✓' --data-urlencode 'authenticity_token={AUTH_TOKEN_2}' \
  --data-urlencode 'empty=' --data-urlencode 'join_time=2026-04-13' \
  --data-urlencode 's={CALENDAR_S_RAW}' --data-urlencode 'service_group_id=7'
```

**200 OK:** the 申込対象サービス page. Extract with
`data-apply-service-id="(\d+)".*?>(.*?)</a>`; the code takes the first service and
re-extracts `authenticity_token` from this page as `AUTH_TOKEN_3`.

## Step 4: Select a Service (Critical Redirect)

A transition from `/calendar_apply/` to `/apply/`.

```bash
$C -X POST "$U/calendar_apply/check_apply_service_coma" \
  --data-urlencode 'utf8=✓' --data-urlencode 'authenticity_token={AUTH_TOKEN_3}' \
  --data-urlencode 'join_time=2026-04-13' --data-urlencode 's={CALENDAR_S_RAW}' \
  --data-urlencode 'apply_service_id=764'
```

**302 Found:** `Location: $U/apply/empty_new?s={APPLY_S}`. The redirect mints
`APPLY_S` and sets new ALB cookies. Capture `Location`, let curl save the cookies,
then GET it yourself (step 5); the code treats a `Location` without `empty_new` as a
failed step 4. Auto-following with `-L` is unsafe: the server sometimes issues a second
302 to `/service_category/index`, and curl lands on the dead end with the informative
headers already discarded.

## Step 5: Load the Booking Form

```bash
$C "$U/apply/empty_new?s={APPLY_S}"
```

**200 OK:** the booking form page. Extract `csrf-token.*?content="(.*?)"`,
`name="authenticity_token" value="(.*?)"`,
`action="(/apply/empty_create\?s=[^"]+)"` (→ `FORM_S`) and `coma_search\('([^']+)'\)`
(→ `COMA_S`, which must be URL-encoded when spliced into the step 6 URL). The form's
own fields are `apply[join_time]` (pre-selected to the target date),
`apply[night_count]` (`1`/`2`), `apply[stay_persons]` (text) and `apply[hope_rooms]`.

Two non-fatal pages land here.
「セッションがタイムアウトしました。最初からやり直してください。」 — the form is still
rendered under this warning and can still work if you proceed immediately.
「…ご指定の施設において空き部屋がございません。」 — the site's no-vacant-rooms page,
which has **no booking form**, so a missing `form_action` plus this string is an
ordinary lost race, not a broken extractor, and it is often this bot reading back its
own abandoned holds.

## Step 6: Search for Available Rooms (AJAX)

```bash
$C -X POST "$U/apply/empty_new?s={COMA_S}" \
  -H 'X-Requested-With: XMLHttpRequest' -H 'X-CSRF-Token: {CSRF_TOKEN}' \
  -H 'Accept: text/javascript, application/javascript, */*; q=0.01' \
  -H "Referer: $U/apply/empty_new?s={APPLY_S}" \
  --data-urlencode 'utf8=✓' --data-urlencode 'authenticity_token={AUTH_TOKEN_4}' \
  --data-urlencode 'apply[join_time]=2026-04-13' \
  --data-urlencode 'apply[night_count]=1' --data-urlencode 'apply[stay_persons]=2' \
  --data-urlencode 'apply[hope_rooms]=1'
```

All four headers are required; without them the server returns a session redirect.
`X-CSRF-Token` is the `<meta>` value, not the form field, and `Referer` is the step 5
URL (`APPLY_S`, not `COMA_S`).

**Response:** JavaScript injecting the room table into `#coma`, quotes escaped in
transit. Extract against that escaped form, as `book_one_hotel` does:

```python
rooms = re.findall(r'name=\\"apply\[coma\[(\d+)\]\]\\".*?value=\\"(\d+)\\"', body)
guid  = ex(body, r'apply_session_guid.*?value=\\"([^"\\]+)\\"')
```

An expired session answers with JavaScript instead:
`parent.location.href='/service_category/index'`.

## Step 7: Submit Room Selection — Takes the 30-Minute Hold

**The point of no return.** This is 「予約手続きに進む」: the room is held for 30
minutes, and from here either the flow finishes or a room sits held and wasted.

```bash
$C -X POST "$U/apply/empty_create?s={FORM_S}" \
  -H "Referer: $U/apply/empty_new?s={APPLY_S}" \
  --data-urlencode 'utf8=✓' --data-urlencode 'authenticity_token={AUTH_TOKEN_4}' \
  --data-urlencode 'apply[join_time]=2026-04-13' \
  --data-urlencode 'apply[night_count]=1' --data-urlencode 'apply[stay_persons]=2' \
  --data-urlencode 'apply[hope_rooms]=1' --data-urlencode 'apply_session_guid={GUID}' \
  --data-urlencode 'apply[coma[5176632]]=5176632'
```

`AUTH_TOKEN_4` is the step 5 form token, reused unchanged from step 6 — Finding 1.
**302:** `Location: /apply/rule?s={RULE_S}`, followed by hand.

## Step 8: Agree to Rules

`/apply/rule` is titled
`保養施設等の利用およびイベントにおける個人情報の取り扱いについて`; the code
recognises it by the string 同意 in the body. Extract the action with
`<form[^>]*action="([^"]*)"[^>]*method="post"` and the token with
`name="s"[^>]*value="([^"]*)"`.

```bash
$C -X POST "$U/apply/email_input" \
  --data-urlencode 'utf8=✓' --data-urlencode 'authenticity_token={AUTH_TOKEN_5}' \
  --data-urlencode 's={RULE_S}'
```

**The hidden `s` field is mandatory** — omit it and the server loses the session and
redirects to `/service_category/index`. It is easy to miss because the form action
carries no `s` query parameter; this was the hardest bug in the flow. **Send no
`commit` param:** 同意する is `type="button"`, so its value is never submitted.

**200 OK:** the email input page rendered directly. The code also follows a 302 here if
one appears.

## Step 9: Submit Email — Dispatches an Email, Nothing More

The email form carries `authenticity_token`, a 40-hex `name="__token__"` nonce
(`name="__token__"[^>]*value="([^"]*)"`), a text `email` field and a `commit=送信`
submit button whose `onclick` raises a JS `confirm()` that curl never sees.

```bash
$C -X POST "$U/apply/send_complete?s={SEND_S}" \
  --data-urlencode 'utf8=✓' --data-urlencode 'authenticity_token={AUTH_TOKEN_6}' \
  --data-urlencode '__token__={DOUBLE_SUBMIT_TOKEN}' \
  --data-urlencode 'email=user@example.com' --data-urlencode 'commit=送信'
```

**`__token__` and `commit=送信` must both be present.** Without them the server returns
an identical `send_complete` page and **sends no email** — nothing in the HTTP response
distinguishes the two. This is the most dangerous silent failure in the flow.

**Never retry this request.** It is the one request `book_one_hotel` issues with
`retry=False`: `--max-time` can expire after the server accepted the submission, and a
repeat sends a second application for the same room. Status 0 is logged as `outcome
unknown` and the hotel abandoned.

**200 OK:** 「送信結果 / メール送信を完了しました。」 plus
「送信されたメールに記載のURLより手続きを進めてください。」 `book_hotels.py` treats
`send_complete` in the body as HELD and calls `save_booking()`. Read that as *hold
taken, email sent*: the clock started at step 7 and nothing in this flow can cancel a
hold early.

---

## The Emailed Leg — Official Steps 7–9 (`confirm_booking.py`)

**The mail.** From `関東ITソフトウェア健保 <noreply@mail.its-kenpo.or.jp>`, subject
「{施設名}申込手続きのご案内」, containing exactly one URL:
`$U/apply/new?c=<uuid4>`. It names only the date it was *sent*, never the stay date —
a 2026-08-19 mail for a 2026-09-16 stay contains 「2026年08月19日」 and 09-16 nowhere —
so a stay-date filter can never match. Arrival time after the hold is the only honest
discriminator.

**`GET /apply/new?c=<uuid>`** → 200, one form posting to `/apply/confirm?c=<uuid>` with
15 submittable controls. Every one of these bites:

| Trap | What is actually there |
|---|---|
| `_method` | `value="true"`, **not** a verb. Echo it verbatim; `_method=patch` makes Rack::MethodOverride rewrite the verb and the route 404s. |
| `utf8` | **absent** — unlike every form in steps 1–9. |
| required-ness | no `required` attribute and 「必須」 nowhere: it is an `<img src=".../must-*.png" name="sign_no_img">` in `<dd class="must">`, so a guard keyed on `required` reports "0 unmapped" for a blank form. Those `<img>`s carry `name=`, so a parser collecting every *named* element invents fields no browser submits — take `input`/`select`/`textarea` only. |
| labels | adjacent, never `for=`-linked. `apply[month]`'s preceding text is the tail of the year dropdown's options; `apply[state]`'s is `postal" /> （半角）`. **Match on field name first, label only as a fallback.** |
| option values | `man`/`woman`, `myself`/`family`, prefectures `1`…`47`, and birth date is three **unpadded** selects `apply[year|month|day]` with 和暦 labels (`平成12年(2000年)`). Matching 男/女/本人 needs the `<option>` label, not its value. |

The 13 applicant fields (`apply[sign_no]` 記号 … `apply[address]` 住所) are enumerated
in `test_booking_flow.APPLICANT_FORM_PAGE`, which is the live markup; `map_fields`
reads them off the page rather than hard-coding them.

**`POST /apply/confirm?c=<uuid>`** → 200 申込内容確認画面, echoing every submitted value
back as prose plus a two-field form to `/apply/complete?c=<uuid>`. That echo is why
`_redact_body` strips 記号/番号/カナ氏名/生年月日/電話/住所: a dump of this page is a
dump of somebody's insurance record.

**`POST /apply/complete?c=<uuid>`** → 200 with
`<p class="complete"><strong>申込受付番号：  10287126</strong></p>`. Label and number
sit inside one tag, so a raw-markup regex reads it correctly by luck; one tag between
them and `([0-9A-Za-z-]{4,})` captures `strong`. Search the tag-stripped text
(`parse_receipt`). **Never retried** — it files the application and sends 申込完了メール.

**When the hold lapses** the emailed link answers **200 with no form at all**:
「30分が経過しましたので、ご利用のURLは無効となりました。」 Only that text tells it apart
from the form's markup having changed.

### Finding 6: `POST /apply/confirm` refuses curl and accepts Chrome

Measured against a live hold on 2026-08-19, and unresolved. The POST is answered
`302 → /service_category/index` with an empty body and `x-runtime: ~0.02` — Rails' own
bounce, from a `before_action`, in 20 ms. It is served identically for:

- a valid `authenticity_token`, a corrupted one, and none at all;
- an empty body;
- `+ utf8=✓`, `+ commit=申込する`, `+ c=` in the body, `_method` omitted;
- `+ Origin`, `+ Sec-Fetch-Site/Mode/Dest/User`, `+ sec-ch-ua*`,
  `+ Upgrade-Insecure-Requests`, `+ Cache-Control` — singly and all together;
- the form GET and the POST issued on **one** curl connection (`--next`).

So the guard runs before the request body is read. The same POST — same URL, same 15
fields, same cookies — **succeeds from real Chrome**, both as a natural `form.submit()`
and as an in-page `fetch()` with the identical serialised body. Since `fetch()` sends
none of the navigation headers and still works, the difference is not the request: it
is the **client**. What is left is curl's TLS fingerprint and the egress path.

That run was inside a sandbox whose HTTPS proxy intercepts, and which may present a
different source address per request; a session pinned to `request.remote_ip` would
behave exactly like this, including the GET always succeeding, because the GET is what
establishes the session. **Before changing anything about the request, check whether it
reproduces off a proxied network.** The 抽選処理 banner visible on these pages is
site-wide layout — it appears on the 404 page too — and is not evidence of a functional
block.

This is why `browser_apply.py` exists. curl stays the primary path and the browser
fires only after curl was refused, so if the cause turns out to be environmental the
fallback simply stops firing, with nothing to undo.

---

## Key Findings

### 1. CSRF tokens are per-render, not single-use

Every response carries a fresh `authenticity_token` and a fresh
`<meta name="csrf-token">`, but that freshness is BREACH mitigation over a stateless
check — there is no nonce store — so **a token's validity window is exactly the life of
the session.** The verified booking extracted `auth` once at step 5 and reused it on
both the step 6 and step 7 POSTs, which is still what `book_one_hotel` does, and
`SCAN_REUSE_SESSION` depends on the same property.

**Do not over-read this.** `__token__` on the email form is a *different* mechanism —
three fields, three lifetimes:

| Field | Where | Mechanism | Lifetime |
|-------|-------|-----------|----------|
| `authenticity_token` | every form | Rails CSRF, masked per render | the session |
| `<meta name="csrf-token">` | every page, for `X-CSRF-Token` | Rails CSRF, global | the session |
| `__token__` (40 hex) | email form only | application-level double-submit nonce | plausibly one use |

Omitting `__token__` returns an identical success page but sends no email — the
signature of a consumable server-side nonce. The step 9 extractor re-reads it from the
email page every time, which is correct; conflating it with CSRF is not.

### 2. Two response families, two extractor dialects

Navigation responses are HTML. AJAX responses (1b, 6) are Rails-UJS JavaScript in which
the markup arrives with backslash-escaped quotes, so their extractors must match `\"`,
not `"` — and the AJAX headers are not optional: without `X-Requested-With` the server
treats the request as a navigation and returns HTML or a redirect instead of
JavaScript. Steps 8 and 9 answer 200, not 302, rendering the next page directly; their
JS fakes navigation with
`history.pushState(null, '施設予約システム', 'email_input' | 'send_complete')`.

### 3. Follow every redirect by hand

`--max-redirs 0` everywhere, then re-issue from the captured `Location`. It matters most
at steps 4 and 7: follow automatically and you hold the dead end's headers instead of
the informative ones.

### 4. Cookie management is essential

| Cookie | Purpose | Flags |
|--------|---------|-------|
| `_src_session` | Rails session ID | HttpOnly, Secure |
| `AWSALB` | ALB sticky session | Path=/ |
| `AWSALBTG` | ALB target group sticky | Path=/ |
| `AWSALBCORS` | ALB CORS sticky | SameSite=None, Secure |
| `AWSALBTGCORS` | ALB target group CORS sticky | SameSite=None, Secure |

`-c cookies.txt -b cookies.txt` on every call, so both the Rails session and ALB
affinity survive. Each thread needs its own jar; the booking flow starts each hotel on
a fresh one.

### 5. Sessions expire in ~30s, and that is where the flow breaks

A dead session appears as a 302 to `/service_category/index` (which 404s),
`セッションがタイムアウトしました` in a rendered page, or
`parent.location.href='/service_category/index'` in an AJAX response. Roughly 30
seconds of inactivity between steps is enough, so run steps 1–9 back-to-back with no
pauses. The dominant failure on disk is the step 2 POST on a session already dead.

Not to be confused with it: a `503` carrying `<title>セキュリティアラート</title>` is a
~24-hour, IP-scoped ban, not site load. See `docs/ITS_RULES.md` §9.

---

## Complete Flow Diagram

```
Browser:  GET /  ->  GET /calendar_apply?s=MAIN_S  ->  [solve Turnstile]
          ->  POST /calendar_apply  ->  302 /calendar_apply/calendar_select?s=CALENDAR_S
                                        (saved to calendar_url_cache.txt)
curl:
  1   GET  /calendar_apply/calendar_select?s=CALENDAR_S   -> csrf, auth, s
  1b  POST /calendar_apply/calendar_select   (AJAX)       -> month availability
  2   POST /calendar_apply/service_group_select           -> hotel list
  3   POST /calendar_apply/apply_service_select           -> service list
  4   POST /calendar_apply/check_apply_service_coma       -> 302 /apply/empty_new?s=APPLY_S
  5   GET  /apply/empty_new?s=APPLY_S                     -> auth, FORM_S, COMA_S
  6   POST /apply/empty_new?s=COMA_S        (AJAX)        -> room ids, apply_session_guid
  7   POST /apply/empty_create?s=FORM_S     *** 30-MIN HOLD ***  -> 302 /apply/rule?s=RULE_S
  8   POST /apply/email_input                             -> 200 email page
  9   POST /apply/send_complete?s=SEND_S    never retried -> 200 "メール送信を完了しました"

emailed leg (confirm_booking.py; 申込する via browser_apply.py when curl is refused):
      GET  /apply/new?c=<uuid>       -> applicant form
      POST /apply/confirm?c=<uuid>   -> 申込内容確認画面      [Finding 6]
      POST /apply/complete?c=<uuid>  never retried -> 申込受付番号 -> 予約確定
```

## Reference Implementation

Steps 1/1b are `book_hotels._open_calendar_session()`, step 2 `_select_date()`, steps
3–9 `book_one_hotel()`; the emailed leg is `confirm_booking.confirm_from_email()` and
申込する/確認 in Chrome is `browser_apply.submit()`.

`test_booking_flow.py` replays every page above against a localhost `FakeITS` and
injects the production failures. It is the fastest way to check a change to any
extractor here, and it is the authority on the markup: the fake is verified on every
run, a doc copy is not.
