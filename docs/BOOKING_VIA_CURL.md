# ITS Booking Flow via curl (HTTP-Only)

**Status: VERIFIED** — steps 1–9 below ran live on 2026-03-26, taking a room
hold for ホテルハーヴェスト斑尾 on 2026-04-13 and dispatching the confirmation
email. `book_hotels.py` is the current implementation. The original one-shot
script is out of the tree: `git show 9e644df:run_live.py`.

## What this flow does and does not do

The official 空き照会申込手順
(https://www.its-kenpo.or.jp/shisetsu/moushikomi.html) has **nine steps**; this
curl flow implements **six**. It ends at a 30-minute room hold plus an email —
it does not file an application.

> 「予約手続きに進む」ボタンを押して以降、**30分以内**に申込手続きを完了して
> ください。完了しない場合、選択した部屋は無効となります。

> 申込完了画面に遷移し申込受付番号が表示され、申込完了メールが自動送信されます。
> **この時点で申込手続き完了及び予約確定となります。**

- **Step 7** (`/apply/empty_create`) is 「予約手続きに進む」: it takes the
  30-minute hold and is the point of no return.
- **Step 9** (`send_complete`) only dispatches an email.
- The three remaining steps are manual and automated nowhere in this repo: open
  the URL in the emailed message, fill the applicant form, press 申込する, then
  確認. Only that yields a 申込受付番号 and 予約確定.
- So `bookings.json` records **holds**, not reservations — each entry expired 30
  minutes later unless a human clicked the emailed link.
- Nothing here ever **releases** a hold. Any failure after step 7 abandons a
  held room, and since the hotel is retried next scan cycle, holds stack: the
  bot then reads its own holds back as the 「空き部屋がございません」 page at
  step 5.

---

## High-Level Architecture

| Aspect | Detail |
|--------|--------|
| Framework | Rails (Phusion Passenger 6.0.13 / Apache) |
| CSRF | `authenticity_token` in hidden fields AND `<meta name="csrf-token">`; lifetimes in Finding 1 |
| Session | `_src_session` cookie (HttpOnly, Secure) |
| Load balancer | AWS ALB, `AWSALB` / `AWSALBTG` sticky-session cookies |
| AJAX pattern | Rails UJS (`dataType: 'script'`) — responses are executable JavaScript that mutates the DOM |
| Auth gate | **Cloudflare Turnstile** (`.cf-turnstile`, `input[name="cf-turnstile-response"]`), solved by `captcha_solver.py`. Was reCAPTCHA v2 (sitekey `6LftanIUAAAAAHclwcrVt3KUiq-W2pqRxF6RGycz`) when this document was written; `docs/CAPTCHA_SOLVER.md` predates the switch. |

### The `s` parameter

Every URL carries an `s` query parameter — a server-side session token (Base64,
URL-encoded). Two distinct values appear:

| Token | Minted at | Used for |
|-------|-----------|----------|
| Calendar `s` | the CAPTCHA (step 0) | steps 1–4, `/calendar_apply/*` |
| Apply `s` | the step 4 redirect | steps 5–9, `/apply/*` |

The calendar `s` is cached in `calendar_url_cache.txt` and serves **many
complete booking flows**; each flow mints its own apply-side session at step 4.
It expires on inactivity, not on use. The token is not opaque — it decodes to
`service_category_id=1&verify_expires=<epoch>` with no signature, so read the
`s=` token section of `CLAUDE.md` before logging any part of it.

---

## Prerequisites

`curl` with cookie-jar support, plus a valid calendar `s` token (browser-only;
Step 0). Steps 1–9 need curl alone. Every request `book_hotels.curl()` makes
carries the same flags, abbreviated below as `$C`:

```bash
C='curl -s -c cookies.txt -b cookies.txt --max-redirs 0 -D hdrs.txt --max-time 30'
```

`--max-redirs 0` is **global**, not per-step: no redirect anywhere is
auto-followed, each is re-issued by hand from the captured `Location`, and `-D`
is the only way the status line and `Location` are read at all.

`BROWSER_HEADERS` adds three more headers to every request, merged in Python so
a per-call header replaces the default rather than duplicating it (curl emits
every `-H` it is given). Send these too:

```
User-Agent: <UA of the Chrome that solved the CAPTCHA, from chrome_user_agent.txt>
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
Accept-Language: ja-JP,ja;q=0.9
```

---

## Step 0: Acquire Calendar URL (Browser Required)

The only step needing a browser. `GET /` returns the link to the CAPTCHA page:

```html
<a href="/calendar_apply?s=PUVUUGtsMlg1SjNi...%3D">
  直営・通年・夏季・冬季保養施設(空き照会)
  カレンダーから探す
</a>
```

`GET /calendar_apply?s={MAIN_S}` contains a form that POSTs back to
`/calendar_apply`:

```html
<form action="/calendar_apply" method="post">
  <input type="hidden" name="authenticity_token" value="pzwI/Gnd..." />
  <input type="hidden" name="s" value="PUVUUGtsMlg1SjNi..." />
  <div class="cf-turnstile" data-sitekey="..."></div>
  <input value="次へ" type="button" onclick="...this.form.submit();" />
</form>
```

`captcha_solver.py` clicks the Turnstile checkbox with CDP mouse events, polls
`input[name="cf-turnstile-response"]` for the token, then re-enables and submits
次へ. The server answers:

```
302 -> https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s={CALENDAR_S}
```

`CALENDAR_S` drives every later step. It is written to
`calendar_url_cache.txt` only if the resulting URL contains `calendar_select`.

---

## Step 1: Load the Calendar Page

```bash
$C -o step1.html \
  'https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s={CALENDAR_S}'
```

**Response (200 OK):** full HTML page for the current month. Extract, exactly as
`_open_calendar_session` does:

| Field | Regex |
|-------|-------|
| CSRF token (meta) | `csrf-token.*?content="(.*?)"` |
| Auth token (form) | `name="authenticity_token" value="(.*?)"` |
| `s` param | `name="s" id="s" value="(.*?)"` |

### Calendar HTML structure

Each date is a `<td>` with a `data-join-time` attribute; the class carries the
availability and `full`/`over` cells differ only in class and icon:

```html
<!-- Available date (class="empty", icon ○; a full date is class="full", icon ☓) -->
<td class="empty td-n" onclick="selectJoinTime(this);"
    data-night-count="1"
    data-join-time="2026-03-30" data-person="" data-service-catrgroy="1">
  <p>30</p>
  <span class="icon">○</span>
</td>
```

- `empty` — available (green ○), clickable
- `a_little` — limited availability (orange), clickable
- `full` — no availability (red ☓), JS blocks the click
- `over` — past date, JS blocks the click

**Both `empty` and `a_little` can be applied for** (`is_available()`,
`_AVAILABLE_CLASSES`). Matching only `empty` silently skips the dates closest to
selling out.

### Hidden form for date selection

Populated and submitted by `selectJoinTime`:

```html
<div style="display: none">
  <form action="/calendar_apply/service_group_select" method="post">
    <input type="hidden" name="utf8" value="✓" />
    <input type="hidden" name="authenticity_token" value="{AUTH_TOKEN}" />
    <input type="hidden" name="join_time" id="join_time" />
    <input type="hidden" name="s" id="s" value="{CALENDAR_S}" />
    <input type="submit" id="service_group_select"/>
  </form>
</div>
```

```javascript
function selectJoinTime(self){
    if($(self).hasClass('full')) return;   // blocks full dates
    if($(self).hasClass('over')) return;   // blocks past dates
    $("#join_time").val($(self).data('join-time'));
    $("#service_group_select").click();
}
```

---

## Step 1b: Navigate to a Different Month

An **AJAX POST** returning executable JavaScript. Not optional in practice: the
calendar opens on the current month, and this one POST returns availability for
every date in the target month.

```bash
$C -X POST \
  -H 'X-Requested-With: XMLHttpRequest' \
  -H 'X-CSRF-Token: {CSRF_TOKEN}' \
  -H 'Accept: text/javascript, application/javascript, */*; q=0.01' \
  -H 'Referer: https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s={CALENDAR_S}' \
  --data-urlencode 'join_date=2026-04-01' \
  --data-urlencode 's={CALENDAR_S_UNENCODED}' \
  'https://as.its-kenpo.or.jp/calendar_apply/calendar_select'
```

Two parameters only — `join_date` (first day of the target month) and `s`. No
`utf8`, no `authenticity_token`; the CSRF value travels in `X-CSRF-Token`.

**Response:** JavaScript replacing the calendar markup, with quotes
backslash-escaped:

```javascript
$(".tcas_1").html('<div class=\"month-navi\">...');
loading(false);
```

The date-cell extractor must match that escaped form (`_date_css_class`):

```python
rf'class=\\"([^"\\]*)\\\"[^>]*data-join-time=\\"{date}\\"'
```

Next/prev `join_date` values live in the button `onclick` handlers:

```html
<input id="nextMonth" type="button" value="翌月＞"
  onclick="toNextMonth('2026-04-01','{CALENDAR_S_UNENCODED}');" />
```

---

## Step 2: Select a Date

```bash
$C -X POST \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN}' \
  --data-urlencode 'join_time=2026-04-13' \
  --data-urlencode 's={CALENDAR_S_UNENCODED}' \
  -o step2.html \
  'https://as.its-kenpo.or.jp/calendar_apply/service_group_select'
```

**Response (200 OK):** hotel selection page. This request is the single largest
failure class on disk — Finding 5.

```html
<h2>施設選択</h2>
<p>2026年04月13日に空きがある施設です。</p>
<ul class="items mb20">
  <li><a data-service-group-id="7"  class="select_service_group" href="javascript:;">ホテルハーヴェスト斑尾</a></li>
  <li><a data-service-group-id="8"  class="select_service_group" href="javascript:;">ブルーベリーヒル勝浦</a></li>
  <li><a data-service-group-id="13" class="select_service_group" href="javascript:;">ホテルハーヴェスト南紀田辺</a></li>
</ul>
```

Extract with `data-service-group-id="(\d+)".*?>(.*?)</a>`, then
`html.unescape()` each name: names arrive HTML-escaped and mix full-width
(U+3000) with ordinary spaces, so raw equality against a skip list can miss.

Per the header 「{date}に空きがある施設です」, **only facilities with vacancy
are listed** — a date typically shows ~3, not the full 24-facility roster.

### Hidden form

The click handler copies `data-service-group-id` into `#service_group_id` and
submits:

```html
<form action="/calendar_apply/apply_service_select" method="post">
  <input type="hidden" name="authenticity_token" value="{AUTH_TOKEN_2}" />
  <input type="hidden" name="empty" id="empty" />
  <input type="hidden" name="join_time" id="join_time" value="2026-04-13" />
  <input type="hidden" name="s" id="s" value="{CALENDAR_S_UNENCODED}" />
  <input type="hidden" name="service_group_id" id="service_group_id" value="" />
</form>
```

---

## Step 3: Select a Hotel

```bash
$C -X POST \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN_2}' \
  --data-urlencode 'empty=' \
  --data-urlencode 'join_time=2026-04-13' \
  --data-urlencode 's={CALENDAR_S_UNENCODED}' \
  --data-urlencode 'service_group_id=7' \
  -o step3.html \
  'https://as.its-kenpo.or.jp/calendar_apply/apply_service_select'
```

**Response (200 OK):** service selection page.

```html
<h1>申込対象サービス</h1>
<ul class="items mb20">
  <li><a data-apply-service-id="764" class="select_apply_service"
         href="javascript:;">ホテルハーヴェスト斑尾申込</a></li>
</ul>
```

Extract with `data-apply-service-id="(\d+)".*?>(.*?)</a>`; the code takes the
first service. Re-extract `authenticity_token` from this page as `AUTH_TOKEN_3`.
The hidden form POSTs to `/calendar_apply/check_apply_service_coma`.

---

## Step 4: Select a Service (Critical Redirect)

A domain transition from `/calendar_apply/` to `/apply/`.

```bash
$C -X POST \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN_3}' \
  --data-urlencode 'join_time=2026-04-13' \
  --data-urlencode 's={CALENDAR_S_UNENCODED}' \
  --data-urlencode 'apply_service_id=764' \
  'https://as.its-kenpo.or.jp/calendar_apply/check_apply_service_coma'
```

**Response (302 Found):**

```
Location: https://as.its-kenpo.or.jp/apply/empty_new?s={APPLY_S}
```

The redirect mints `APPLY_S` and sets new ALB cookies. Capture `Location`, let
curl save the cookies, then GET it yourself (step 5); the code treats a
`Location` without `empty_new` as a failed step 4. Auto-following with `-L` is
unsafe: the server sometimes issues a second 302 to `/service_category/index`,
and curl lands on the dead end with the informative headers already discarded.

---

## Step 5: Load the Booking Form

```bash
$C -o step5.html 'https://as.its-kenpo.or.jp/apply/empty_new?s={APPLY_S}'
```

**Response (200 OK):** booking form page.

| Field | Regex |
|-------|-------|
| CSRF (meta) | `csrf-token.*?content="(.*?)"` |
| Auth token | `name="authenticity_token" value="(.*?)"` |
| Form action | `action="(/apply/empty_create\?s=[^"]+)"` |
| COMA search `s` | `coma_search\('([^']+)'\)` |

### Two non-fatal pages that land here

```html
<p align='center'>セッションがタイムアウトしました。最初からやり直してください。</p>
```

The form is still rendered under this warning and can still work if you proceed
immediately.

```
空き部屋がございません
```

The site's "no vacant rooms at this facility" page. It has **no booking form**,
so a missing `form_action` plus this string is an ordinary lost race, not a
broken extractor — and it is often this bot reading back its own abandoned
holds.

### Form fields

```html
<form id="new_apply" action="/apply/empty_create?s={FORM_S}" method="post">
  <input name="authenticity_token" value="{AUTH_TOKEN_4}" />
  <select name="apply[join_time]">          <!-- pre-selected to target date -->
    <option selected="selected" value="2026-04-13">2026年04月13日(月)</option>
  </select>
  <select name="apply[night_count]">
    <option value="1">一泊</option>
    <option value="2">二泊</option>
  </select>
  <input name="apply[stay_persons]" />      <!-- guest count, text input -->
  <select name="apply[hope_rooms]">         <!-- room count -->
    <option value="1">1</option>
  </select>
</form>

<input value="空き検索" onclick="coma_search('{COMA_S}');" type="button" />
```

```javascript
function coma_search(params) {
    $.ajax({
        url: 'empty_new?s=' + params,   // relative to /apply/
        type: 'POST',
        data: $('#new_apply').serialize()
    });
}
```

`COMA_S` must be URL-encoded when spliced into the step 6 URL.

---

## Step 6: Search for Available Rooms (AJAX)

```bash
$C -X POST \
  -H 'X-Requested-With: XMLHttpRequest' \
  -H 'X-CSRF-Token: {CSRF_TOKEN}' \
  -H 'Accept: text/javascript, application/javascript, */*; q=0.01' \
  -H 'Referer: https://as.its-kenpo.or.jp/apply/empty_new?s={APPLY_S}' \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN_4}' \
  --data-urlencode 'apply[join_time]=2026-04-13' \
  --data-urlencode 'apply[night_count]=1' \
  --data-urlencode 'apply[stay_persons]=2' \
  --data-urlencode 'apply[hope_rooms]=1' \
  'https://as.its-kenpo.or.jp/apply/empty_new?s={COMA_S}'
```

All four headers are required; without them the server returns a session
redirect. `X-CSRF-Token` is the `<meta>` value, not the form field, and
`Referer` is the step 5 URL (`APPLY_S`, not `COMA_S`).

**Response:** JavaScript injecting room data into `#coma`:

```javascript
$('#coma').html("...");
$('.coma_title').show();
$('.main_submit').show();
```

The injected HTML (quotes escaped in transit):

```html
<input type="hidden" name="apply_session_guid"
       value="5c748e8b-2e54-414c-bf1d-df6ff97c494b" />

<table id="coma_search_result">
  <tr>
    <td><input type="checkbox" name="apply[coma[5176632]]"
               value="5176632" /></td>
    <td>1</td>                        <!-- room number -->
    <td>2026年04月13日</td>           <!-- date -->
    <td>和洋室</td>                    <!-- room type -->
    <td>2 ～ 5</td>                    <!-- guest capacity -->
    <td>空き</td>                      <!-- status -->
  </tr>
  <!-- one <tr> per room -->
</table>
```

Extract against the escaped form, as `book_one_hotel` does:

```python
rooms = re.findall(r'name=\\"apply\[coma\[(\d+)\]\]\\".*?value=\\"(\d+)\\"', body)
guid  = ex(body, r'apply_session_guid.*?value=\\"([^"\\]+)\\"')
```

An expired session answers with:

```javascript
parent.location.href='/service_category/index'
```

---

## Step 7: Submit Room Selection — Takes the 30-Minute Hold

**The point of no return.** This is 「予約手続きに進む」: the room is held for 30
minutes, and from here either the flow finishes or a room sits held and wasted.

POST the main form (`#new_apply`) with the room checkbox included:

```bash
$C -X POST \
  -H 'Referer: https://as.its-kenpo.or.jp/apply/empty_new?s={APPLY_S}' \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN_4}' \
  --data-urlencode 'apply[join_time]=2026-04-13' \
  --data-urlencode 'apply[night_count]=1' \
  --data-urlencode 'apply[stay_persons]=2' \
  --data-urlencode 'apply[hope_rooms]=1' \
  --data-urlencode 'apply_session_guid=5c748e8b-2e54-414c-bf1d-df6ff97c494b' \
  --data-urlencode 'apply[coma[5176632]]=5176632' \
  'https://as.its-kenpo.or.jp/apply/empty_create?s={FORM_S}'
```

`AUTH_TOKEN_4` is the step 5 form token, reused unchanged from step 6 — see
Finding 1.

**Expected response (302):** `Location: /apply/rule?s={RULE_S}`, followed by
hand.

---

## Step 8: Agree to Rules

`/apply/rule` is titled
`保養施設等の利用およびイベントにおける個人情報の取り扱いについて`; the code
recognises it by the string 同意 in the body.

```html
<form action="/apply/email_input" accept-charset="UTF-8" method="post">
  <input name="utf8" type="hidden" value="✓" />
  <input type="hidden" name="authenticity_token" value="{AUTH_TOKEN_5}" />
  <input type="hidden" name="s" id="s" value="{RULE_S}" />

  <input value="同意する" type="button"
    onclick="$('.button-select').attr('disabled',true);this.form.submit();" />
  <input value="同意しない" type="button"
    onclick="...document.getElementById('disagree').click();" />
</form>
```

Extract the action with `<form[^>]*action="([^"]*)"[^>]*method="post"` and the
token with `name="s"[^>]*value="([^"]*)"`.

**The hidden `s` field is mandatory** — omit it and the server loses the session
and redirects to `/service_category/index`. It is easy to miss because the form
action carries no `s` query parameter; this was the hardest bug in the flow.
**Send no `commit` param:** 同意する is `type="button"`, so its value is never
submitted.

```bash
$C -X POST \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN_5}' \
  --data-urlencode 's={RULE_S}' \
  -o step8.html \
  'https://as.its-kenpo.or.jp/apply/email_input'
```

**Expected response (200 OK):** the email input page rendered directly. The code
also follows a 302 here if one appears.

---

## Step 9: Submit Email — Dispatches an Email, Nothing More

```html
<form action="/apply/send_complete?s={SEND_S}" accept-charset="UTF-8" method="post">
  <input name="utf8" type="hidden" value="✓" />
  <input type="hidden" name="authenticity_token" value="{AUTH_TOKEN_6}" />
  <input type="hidden" name="__token__" id="__token__"
         value="c556074cb7a266b451928c025c5affeba0a70354" />

  <label>メールアドレス</label>
  <input type="text" name="email" id="email_inp" value="" />

  <input type="submit" name="commit" value="送信"
         onclick="return confirm_submit();" />
</form>
```

`confirm_submit()` raises a JS `confirm()` dialog
(`メールを送信します。よろしいですか？`); with curl there is no dialog. Extract
the nonce with `name="__token__"[^>]*value="([^"]*)"`.

```bash
$C -X POST \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN_6}' \
  --data-urlencode '__token__={DOUBLE_SUBMIT_TOKEN}' \
  --data-urlencode 'email=user@example.com' \
  --data-urlencode 'commit=送信' \
  -o step9.html \
  'https://as.its-kenpo.or.jp/apply/send_complete?s={SEND_S}'
```

**`__token__` and `commit=送信` must both be present.** Without them the server
returns an identical `send_complete` page and **sends no email** — nothing in
the HTTP response distinguishes the two. This is the most dangerous silent
failure in the flow.

**Never retry this request.** It is the one request `book_hotels.py` issues with
`retry=False`: `--max-time` can expire after the server accepted the
submission, and a repeat sends a second application for the same room. Status 0
is logged as `outcome unknown` and the hotel abandoned.

**Expected response (200 OK):**

```html
<h1>送信結果</h1>
<p>メール送信を完了しました。</p>
<strong>送信されたメールに記載のURLより手続きを進めてください。</strong>
```

`book_hotels.py` treats `send_complete` in the body as `BOOKED` and calls
`save_booking()`. Read that as *hold taken, email sent*.

---

## After Step 9: What Still Has To Happen

Open the emailed URL, fill the applicant form, press 申込する, then 確認. That
produces the 申込受付番号, and only that is 申込手続き完了及び予約確定. Nothing
in this repo does any of it, and the clock started at step 7: **≤30 minutes**
from the hold to 申込完了, and no request in this flow can cancel a hold early.

### Steps 7–9, captured live on 2026-08-19

Run end to end against the real site: hold taken on ブルーベリーヒル勝浦 for
2026-09-01, mail received 10 s later, application filed as 申込受付番号
**10287126**. `confirm_booking.py` is these three steps.

**The mail.** From `関東ITソフトウェア健保 <noreply@mail.its-kenpo.or.jp>`, subject
「{施設名}申込手続きのご案内」, containing exactly one URL:
`https://as.its-kenpo.or.jp/apply/new?c=<uuid4>`. It names only the date it was
*sent*, never the stay date — a 2026-08-19 mail for a 2026-09-16 stay contains
「2026年08月19日」 and 09-16 nowhere — so a stay-date filter can never match. Arrival
time after the hold is the only honest discriminator.

**Step 7 — `GET /apply/new?c=<uuid>`.** 200, one form:

```html
<form class="edit_apply" id="edit_apply_10287126"
      action="/apply/confirm?c=<uuid>" accept-charset="UTF-8" method="post">
  <input type="hidden" name="_method" value="true" autocomplete="off" />
  <input type="hidden" name="authenticity_token" value="…" autocomplete="off" />
  …13 applicant controls…
  <input value="申込する" onclick="this.form.submit();" type="button" />
</form>
```

Fifteen submittable controls, and every one of these details bites:

| Trap | What is actually there |
|---|---|
| `_method` | `value="true"`, **not** a verb. Echo it verbatim. Sending `_method=patch` instead makes Rack::MethodOverride rewrite the verb and the route 404s. |
| `utf8` | **absent** — unlike every form in steps 1–9. |
| required-ness | no `required` attribute, and 「必須」 appears nowhere. It is an `<img src=".../must-*.png" name="sign_no_img">` inside `<dd class="must">`. A guard keyed on `required` reports "0 unmapped" for a blank form. |
| those `<img>`s | they carry `name=`, so a parser that collects every *named* element invents 22 fields no browser submits (`sign_no_img`, `house1`…`house10`, …). Collect `input`/`select`/`textarea` only. |
| labels | adjacent, never `for=`-linked. `apply[month]`'s preceding text is the tail of the year dropdown's options; `apply[state]`'s is `postal" /> （半角）`. **Match on field name first, label only as a fallback.** |
| option values | `man`/`woman`, `myself`/`family`, prefectures `1`…`47`. Matching 男/女/本人 needs the `<option>` *label*, not its value. |
| birth date | three selects, `apply[year|month|day]`, values **unpadded** (`3`, not `03`); the year labels are 和暦, `平成12年(2000年)`. |

Field names: `apply[sign_no]` 記号, `apply[insured_no]` 番号,
`apply[office_name]` 事業所名, `apply[kana_name]` 申込代表者名（カナ氏名） — one
combined box, not 姓/名 — `apply[year|month|day]` 生年月日, `apply[gender]` 性別,
`apply[relationship]` 続柄, `apply[contact_phone]` 連絡先電話番号,
`apply[postal]` 〒, `apply[state]` 都道府県, `apply[address]` 住所.

**Step 8 — `POST /apply/confirm?c=<uuid>`** → 200 申込内容確認画面, echoing every
submitted value back as prose plus a two-field form to `/apply/complete?c=<uuid>`.
That echo is why `_redact_body` has to strip 記号/番号/カナ氏名/生年月日/電話/住所: a
dump of this page is a dump of somebody's insurance record.

**Step 9 — `POST /apply/complete?c=<uuid>`** → 200 `/apply/complete`:

```html
<p class="complete"><strong>申込受付番号：  10287126</strong></p>
```

Label and number inside one tag, so a raw-markup regex reads it correctly by luck;
one tag between them and `([0-9A-Za-z-]{4,})` captures `strong`. Search the
tag-stripped text (`parse_receipt`). **Never retried** — it files the application
and sends 申込完了メール.

**When the hold lapses** the emailed link answers **200 with no form at all**:
「30分が経過しましたので、ご利用のURLは無効となりました。」 Only that text tells it
apart from the form's markup having changed.

### Finding 6: `POST /apply/confirm` refuses curl and accepts Chrome

Measured against a live hold on 2026-08-19, and unresolved. The POST is answered
`302 → /service_category/index` with an empty body and `x-runtime: ~0.02` — Rails'
own bounce, from a `before_action`, in 20 ms. It is served identically for:

- a valid `authenticity_token`, a corrupted one, and none at all;
- an empty body;
- `+ utf8=✓`, `+ commit=申込する`, `+ c=` in the body, `_method` omitted;
- `+ Origin`, `+ Sec-Fetch-Site/Mode/Dest/User`, `+ sec-ch-ua*`,
  `+ Upgrade-Insecure-Requests`, `+ Cache-Control` — singly and all together;
- the form GET and the POST issued on **one** curl connection (`--next`).

So the guard runs before the request body is read. The same POST — same URL, same
15 fields, same cookies — **succeeds from real Chrome**, both as a natural
`form.submit()` and as an in-page `fetch()` with the identical serialised body.
Since `fetch()` sends none of the navigation headers and still works, the
difference is not the request: it is the **client**. What is left is curl's TLS
fingerprint and the egress path.

That run was inside a sandbox whose HTTPS proxy intercepts, and which may present a
different source address per request; a session pinned to `request.remote_ip` would
behave exactly like this, including the GET always succeeding, because the GET is
what establishes the session. **Before changing anything about the request, check
whether it reproduces off a proxied network.** The 抽選処理 banner visible on these
pages is site-wide layout — it appears on the 404 page too — and is not evidence of
a functional block.

---

## Key Findings

### 1. CSRF tokens are per-render, not single-use

Every response carries a fresh `authenticity_token` and a fresh
`<meta name="csrf-token">`. The freshness is **BREACH mitigation**, not a
one-shot: `form_authenticity_token` XORs a random one-time pad over the
per-session secret, and `valid_authenticity_token?` is a stateless
unmask-and-compare against `session[:_csrf_token]`. There is no nonce store,
ledger, counter or expiry, so a Rails CSRF token **cannot** be single-use. The
`<meta>` value is the global, action-agnostic token (`csrf_meta_tags` passes no
`form_options`), so `per_form_csrf_tokens` does not apply to it either.

**A CSRF token's validity window is exactly the life of the session.** The
verified 2026-03-26 booking extracted `auth` once and reused it unchanged on
both the step 6 and step 7 POSTs; the same pattern still ships in
`book_one_hotel` (`book_hotels.py:834/861/882` — extract at step 5, use at step
6, use again at step 7, no reassignment); and 629 recovered debug dumps contain
**zero** 422s and zero `InvalidAuthenticityToken`.

An earlier version of this finding claimed a stale token caused "a 422 or a
redirect to the error page". The redirect is real, but its cause is the session
dying (Finding 5). The two findings described one redirect and gave it two
causes; only Finding 5 has evidence.

**Do not over-read this.** `__token__` on the email form is a *different*
mechanism and is plausibly single-use — three fields, three lifetimes:

| Field | Where | Mechanism | Lifetime |
|-------|-------|-----------|----------|
| `authenticity_token` | every form | Rails CSRF, masked per render | the session |
| `<meta name="csrf-token">` | every page, for `X-CSRF-Token` | Rails CSRF, global | the session |
| `__token__` (40 hex) | email form only | application-level double-submit nonce | plausibly one use |

Omitting `__token__` returns an identical success page but sends no email — the
signature of a consumable server-side nonce. The step 9 extractor
(`book_hotels.py:919`) re-reads it from the email-page response every time,
which is correct; conflating it with CSRF is the likely origin of the old
single-use claim.

### 2. Two response families, two extractor dialects

Navigation responses are HTML. AJAX responses (1b, 6) are Rails-UJS JavaScript
in which the markup arrives with backslash-escaped quotes, so their extractors
must match `\"`, not `"` — and the required AJAX headers are not optional:
without `X-Requested-With` the server treats the request as a navigation and
returns HTML or a redirect instead of JavaScript.

Steps 8 and 9 answer 200, not 302, rendering the next page directly; their JS
fakes navigation with
`history.pushState(null, '施設予約システム', 'email_input' | 'send_complete')`.

### 3. Following a redirect by hand is what makes failures readable

`--max-redirs 0` everywhere. It matters most at step 4 (which mints `APPLY_S`
and new ALB cookies) and step 7: follow automatically and you end up holding the
dead end's headers instead of the informative ones.

```bash
REDIRECT=$(grep -i 'location:' hdrs.txt | head -1 | tr -d '\r' | sed 's/location: //i')
curl -c cookies.txt -b cookies.txt "$REDIRECT"
```

### 4. Cookie management is essential

| Cookie | Purpose | Flags |
|--------|---------|-------|
| `_src_session` | Rails session ID | HttpOnly, Secure |
| `AWSALB` | ALB sticky session | Path=/ |
| `AWSALBTG` | ALB target group sticky | Path=/ |
| `AWSALBCORS` | ALB CORS sticky | SameSite=None, Secure |
| `AWSALBTGCORS` | ALB target group CORS sticky | SameSite=None, Secure |

`-c cookies.txt -b cookies.txt` on every call, so both the Rails session and ALB
affinity survive. Each thread needs its own jar; the booking flow starts each
hotel on a fresh one.

### 5. Sessions expire in ~30s, and that is where the flow breaks

A dead session appears as a 302 to `/service_category/index` (which 404s),
`セッションがタイムアウトしました` in a rendered page, or
`parent.location.href='/service_category/index'` in an AJAX response. Roughly 30
seconds of inactivity between steps is enough, so run steps 1–9 back-to-back
with no pauses.

From 629 debug dumps (2026-04-03 → 2026-08-18):

| Failure | Count |
|---------|-------|
| `service_group_select` 302 → `/service_category/index` (step 2) | 261 |
| `calendar_get` 503 (step 1) | 172 |
| `step3_service_select` 302 (step 3) | 111 |
| `step5_booking_form` 200, no-rooms page (step 5) | 42 |
| `calendar_get` 302 (step 1) | 35 |
| other | 8 |

Post-detection conversion is **~8.5%** — of the dates the scanner sees as
available, that fraction reach `send_complete`. **62% of failures are one code
path:** the step 2 POST on a session that has already died.

**Every `calendar_get` 503 is an IP ban, not site load.** All 467 non-empty dumps
in the corpus are one page, `<title>セキュリティアラート</title>`, whose body reads
「ご利用のIPアドレス（…）から、アクセス過多を検知しました…**約24時間**、一時的に
システムへのアクセスを遮断します」 — a roughly 24-hour, IP-scoped block, which is
notice 144 being enforced. Three episodes on record (2026-04-07, 2026-04-12,
2026-08-17) across three egress IPs, every one served on step 1 simply because it
is the most frequent request. `DEBUG_DUMP_KEEP` prunes oldest-first, so the
corpus is survivorship-biased; do not infer the cause from dump timestamps, read
the bodies. See `docs/ITS_RULES.md`.

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

manual (not automated):
      emailed URL -> applicant form -> 申込する -> 確認 -> 申込受付番号 -> 予約確定
```

`confirm_booking.py` now implements that last line — see "Steps 7–9, captured live"
above, and Finding 6 for the one request in it that curl cannot get past.

---

## Reference Implementation

`book_hotels.py`:

| Steps | Function |
|-------|----------|
| 1, 1b | `_open_calendar_session()` |
| 2 | `_select_date()` |
| 3–9 | `book_one_hotel()` |
| 7–9 of the *official* flow (the emailed leg) | `confirm_booking.confirm_from_email()` |

`test_booking_flow.py` replays the markup above — including the escaped-quote
AJAX responses — against a localhost `FakeITS` server and injects the failures
in Finding 5. It is the fastest way to check a change to any extractor here.

The original single-shot script is out of the tree: `git show
9e644df:run_live.py`. (`curl_booking.py`, which earlier revisions of this
document cited, never existed in this repository.)
