# ITS Booking Flow via curl (HTTP-Only)

**Status: VERIFIED** -- All 9 steps completed successfully on 2026-03-26,
booking for ホテルハーヴェスト斑尾 on 2026-04-13.
See `run_live.py` for the working reference implementation.

This document describes the complete booking process for ITS Health Insurance
facilities (`as.its-kenpo.or.jp`) using raw HTTP requests. Every interaction
the browser automation performs was reverse-engineered and reproduced with
`curl`.

---

## High-Level Architecture

The site is a **Ruby on Rails** application behind an **AWS ALB** (Application
Load Balancer). Key traits:

| Aspect | Detail |
|--------|--------|
| Framework | Rails (Phusion Passenger 6.0.13 / Apache) |
| CSRF | Per-page `authenticity_token` in hidden form fields AND `<meta name="csrf-token">` tag |
| Session | `_src_session` cookie (HttpOnly, Secure) |
| Load Balancer | AWS ALB with `AWSALB` / `AWSALBTG` sticky-session cookies |
| AJAX pattern | Rails UJS (`dataType: 'script'`) -- AJAX responses are executable JavaScript that mutates the DOM |
| Auth gate | Google reCAPTCHA v2 (sitekey `6LftanIUAAAAAHclwcrVt3KUiq-W2pqRxF6RGycz`) |

### The `s` Parameter

Every URL carries an `s` query parameter that is a **server-side session
token** (Base64-encoded, URL-encoded). Two different `s` values appear during
the flow:

1. **Calendar `s`** -- issued after passing reCAPTCHA, used for all
   `/calendar_apply/*` endpoints.
2. **Apply `s`** -- issued when transitioning from `/calendar_apply/` to
   `/apply/`, embedded in the 302 redirect from `check_apply_service_coma`.

These tokens expire after a period of inactivity (observed ~30 seconds between
steps). The calendar `s` is cached in `calendar_url_cache.txt`.

---

## Prerequisites

- `curl` with cookie-jar support (`-c` / `-b` flags)
- A valid calendar `s` token (requires browser + reCAPTCHA; see "Step 0")
- All subsequent steps (1--9) can be done with curl alone

---

## Step 0: Acquire Calendar URL (Browser Required)

This is the **only step that requires a browser**. reCAPTCHA cannot be solved
with curl.

### 0a. Load the main page

```
GET https://as.its-kenpo.or.jp/
```

The response contains a link to the CAPTCHA page:

```html
<a href="/calendar_apply?s=PUVUUGtsMlg1SjNi...%3D">
  直営・通年・夏季・冬季保養施設(空き照会)
  カレンダーから探す
</a>
```

### 0b. Load the CAPTCHA page

```
GET https://as.its-kenpo.or.jp/calendar_apply?s={MAIN_S}
```

The page contains a form that POSTs back to `/calendar_apply`:

```html
<form action="/calendar_apply" method="post">
  <input type="hidden" name="authenticity_token" value="pzwI/Gnd..." />
  <input type="hidden" name="s" value="PUVUUGtsMlg1SjNi..." />
  <div class="g-recaptcha" data-sitekey="6LftanIUAAAAAHclwcrVt3KUiq-W2pqRxF6RGycz"></div>
  <input value="次へ" type="button" onclick="...this.form.submit();" />
</form>
```

### 0c. Solve reCAPTCHA and submit

After solving the CAPTCHA, the form submits and the server responds with a
**302 redirect** to the calendar page:

```
302 -> https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s={CALENDAR_S}
```

This `CALENDAR_S` is the token used for all subsequent steps. The existing
browser automation (`main.py`) handles this and saves the URL to
`calendar_url_cache.txt`.

---

## Step 1: Load the Calendar Page

```bash
curl -s \
  -c cookies.txt -b /dev/null \
  -o step1.html \
  'https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s={CALENDAR_S}'
```

**Response (200 OK):** Full HTML page showing the current month's calendar.

### What to extract

| Field | Regex | Example |
|-------|-------|---------|
| CSRF token (meta) | `csrf-token.*?content="(.*?)"` | `DA/5o4bPvvLeQ48K...` |
| Auth token (form) | `name="authenticity_token" value="(.*?)"` | `O1T+wuzwvSMlbM...` |
| `s` param | `name="s" id="s" value="(.*?)"` | `PWdETXhrRE94UXpO...` |
| Current month | `<span class="month">(.*?)</span>` | `2026年03月` |

### Calendar HTML structure

Each date is a `<td>` cell with a `data-join-time` attribute:

```html
<!-- Available date (class="empty", icon ○) -->
<td class="empty td-n" onclick="selectJoinTime(this);"
    data-night-count="1"
    data-join-time="2026-03-30" data-person="" data-service-catrgroy="1">
  <p>30</p>
  <span class="icon">○</span>
</td>

<!-- Full date (class="full", icon ☓) -->
<td class="full td-n" onclick="selectJoinTime(this);"
    data-night-count="1"
    data-join-time="2026-03-01" data-person="" data-service-catrgroy="1">
  <p>1</p>
  <span class="icon">☓</span>
</td>
```

CSS class determines clickability:
- `empty` = available (green background), clickable
- `a_little` = limited availability (orange), clickable
- `full` = no availability (red), JS blocks the click
- `over` = past date, JS blocks the click

### Hidden form for date selection

At the bottom of the page there is a **hidden form** that the `selectJoinTime`
JavaScript function populates and submits:

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

The JS sets `#join_time` to the clicked cell's `data-join-time`, then triggers
the hidden submit button:

```javascript
function selectJoinTime(self){
    if($(self).hasClass('full')) return;   // blocks full dates
    if($(self).hasClass('over')) return;   // blocks past dates
    $("#join_time").val($(self).data('join-time'));
    $("#service_group_select").click();
}
```

---

## Step 1b (Optional): Navigate to a Different Month

Month navigation is an **AJAX POST** that returns executable JavaScript.

```bash
curl -s \
  -c cookies.txt -b cookies.txt \
  -X POST \
  -H 'X-Requested-With: XMLHttpRequest' \
  -H 'X-CSRF-Token: {CSRF_TOKEN}' \
  -H 'Accept: text/javascript, application/javascript, */*; q=0.01' \
  -H 'Referer: https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s={CALENDAR_S}' \
  --data-urlencode 'join_date=2026-04-01' \
  --data-urlencode 's={CALENDAR_S_UNENCODED}' \
  'https://as.its-kenpo.or.jp/calendar_apply/calendar_select'
```

**Parameters:**
- `join_date` -- first day of the target month (e.g. `2026-04-01`)
- `s` -- the calendar session token (unencoded)

**Response:** JavaScript that replaces the calendar HTML:

```javascript
$(".tcas_1").html('<div class=\"month-navi\">...');
loading(false);
```

The `join_date` values for next/prev month are embedded in the button
`onclick` handlers on the page:

```html
<input id="nextMonth" type="button" value="翌月＞"
  onclick="toNextMonth('2026-04-01','{CALENDAR_S_UNENCODED}');" />
```

Parse the AJAX response the same way as the initial calendar page -- look for
`data-join-time` attributes and `class="empty"` to find available dates.

---

## Step 2: Select a Date

POST the hidden form to select an available date:

```bash
curl -s \
  -c cookies.txt -b cookies.txt \
  -X POST \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN}' \
  --data-urlencode 'join_time=2026-04-13' \
  --data-urlencode 's={CALENDAR_S_UNENCODED}' \
  -o step2.html \
  'https://as.its-kenpo.or.jp/calendar_apply/service_group_select'
```

**Response (200 OK):** Hotel selection page.

### Hotel list HTML

```html
<h2>施設選択</h2>
<p>2026年04月13日に空きがある施設です。</p>
<ul class="items mb20">
  <li><a data-service-group-id="7"  class="select_service_group" href="javascript:;">ホテルハーヴェスト斑尾</a></li>
  <li><a data-service-group-id="8"  class="select_service_group" href="javascript:;">ブルーベリーヒル勝浦</a></li>
  <li><a data-service-group-id="13" class="select_service_group" href="javascript:;">ホテルハーヴェスト南紀田辺</a></li>
</ul>
```

Extract hotel IDs with: `data-service-group-id="(\d+)".*?>(.*?)</a>`

### Hidden form

Same pattern -- a hidden form POSTs to the next endpoint:

```html
<form action="/calendar_apply/apply_service_select" method="post">
  <input type="hidden" name="authenticity_token" value="{AUTH_TOKEN_2}" />
  <input type="hidden" name="empty" id="empty" />
  <input type="hidden" name="join_time" id="join_time" value="2026-04-13" />
  <input type="hidden" name="s" id="s" value="{CALENDAR_S_UNENCODED}" />
  <input type="hidden" name="service_group_id" id="service_group_id" value="" />
</form>
```

The click handler sets `service_group_id` from `data-service-group-id`:

```javascript
$(".select_service_group").click(function () {
    $("#service_group_id").val($(this).data('service-group-id'));
    $("#service_group_select").click();
})
```

---

## Step 3: Select a Hotel

```bash
curl -s \
  -c cookies.txt -b cookies.txt \
  -X POST \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN_2}' \
  --data-urlencode 'empty=' \
  --data-urlencode 'join_time=2026-04-13' \
  --data-urlencode 's={CALENDAR_S_UNENCODED}' \
  --data-urlencode 'service_group_id=7' \
  -o step3.html \
  'https://as.its-kenpo.or.jp/calendar_apply/apply_service_select'
```

**Response (200 OK):** Service selection page.

```html
<h1>申込対象サービス</h1>
<ul class="items mb20">
  <li><a data-apply-service-id="764" class="select_apply_service"
         href="javascript:;">ホテルハーヴェスト斑尾申込</a></li>
</ul>
```

Extract with: `data-apply-service-id="(\d+)".*?>(.*?)</a>`

Hidden form POSTs to `/calendar_apply/check_apply_service_coma`.

---

## Step 4: Select a Service (Critical Redirect)

This step is a **domain transition** from `/calendar_apply/` to `/apply/`.

```bash
curl -s \
  -c cookies.txt -b cookies.txt \
  -X POST \
  --max-redirs 0 \
  -D headers.txt \
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

**CRITICAL:** Do **not** use `-L` (auto-follow redirects) here. The redirect
generates a new `s` token (`APPLY_S`) and sets new ALB sticky-session cookies.
You must:

1. Capture the `Location` header
2. Let curl save the new cookies to the cookie jar
3. Make a separate GET request to the redirect URL (Step 5)

If you auto-follow, the request chain sometimes breaks because the server
issues a second 302 to `/service_category/index` (a dead-end error page).

---

## Step 5: Load the Booking Form

```bash
curl -s \
  -c cookies.txt -b cookies.txt \
  -o step5.html \
  'https://as.its-kenpo.or.jp/apply/empty_new?s={APPLY_S}'
```

**Response (200 OK):** Booking form page.

### What to extract

| Field | Location | Example |
|-------|----------|---------|
| CSRF (meta) | `<meta name="csrf-token" content="...">` | `fbuOhjide...` |
| Auth token | `<input ... name="authenticity_token" value="...">` | `oaertq3ol...` |
| Form action | `<form ... action="/apply/empty_create?s={FORM_S}">` | `/apply/empty_create?s=PT1BY...` |
| COMA search `s` | `coma_search('...')` in the onclick attribute | `PT1BYTB4V1lsa...` |

### Session timeout warning

The page may contain:
```html
<p align='center'>セッションがタイムアウトしました。最初からやり直してください。</p>
```

This appears if there was too much delay between steps. Despite the warning,
the form is still rendered and **can still work** if you proceed immediately.

### Form fields

```html
<form id="new_apply" action="/apply/empty_create?s={FORM_S}" method="post">
  <input name="authenticity_token" value="{AUTH_TOKEN_4}" />

  <!-- Date selector (pre-selected to target date) -->
  <select name="apply[join_time]">
    <option selected="selected" value="2026-04-13">2026年04月13日(月)</option>
    ...
  </select>

  <!-- Night count -->
  <select name="apply[night_count]">
    <option value="1">一泊</option>
    <option value="2">二泊</option>
  </select>

  <!-- Guest count (text input) -->
  <input name="apply[stay_persons]" />

  <!-- Room count (select) -->
  <select name="apply[hope_rooms]">
    <option value="1">1</option>
    ...
  </select>
</form>

<!-- Search button triggers AJAX -->
<input value="空き検索"
  onclick="coma_search('{COMA_S}');" type="button" />
```

The `coma_search` function serializes the form and sends it via AJAX:

```javascript
function coma_search(params) {
    $.ajax({
        url: 'empty_new?s=' + params,   // relative to /apply/
        type: 'POST',
        data: $('#new_apply').serialize()
    });
}
```

---

## Step 6: Search for Available Rooms (AJAX)

```bash
curl -s \
  -c cookies.txt -b cookies.txt \
  -X POST \
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

**Required headers** (without these the server returns a session redirect):
- `X-Requested-With: XMLHttpRequest`
- `X-CSRF-Token: {CSRF_TOKEN}` (from the `<meta>` tag, NOT the form field)
- `Accept: text/javascript, ...`

**Response:** JavaScript that injects room data into `#coma`:

```javascript
$('#coma').html("...");
$('.coma_title').show();
$('.main_submit').show();
```

### Room data structure

Inside the injected HTML:

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
  <tr>
    <td><input type="checkbox" name="apply[coma[5176641]]"
               value="5176641" /></td>
    <td>10</td>
    <td>2026年04月13日</td>
    <td>洋室</td>
    <td>2 ～ 4</td>
    <td>空き</td>
  </tr>
  <!-- more rooms... -->
</table>
```

Extract with:
- Session GUID: `apply_session_guid.*?value="([^"]+)"`
- Room IDs: `name="apply\[coma\[(\d+)\]\]".*?value="(\d+)"`

**If the session has expired**, the response will be:
```javascript
parent.location.href='/service_category/index'
```

---

## Step 7: Submit Room Selection

POST the main booking form (`#new_apply`) with the selected room checkbox
included:

```bash
curl -s \
  -c cookies.txt -b cookies.txt \
  -X POST \
  --max-redirs 0 \
  -D headers.txt \
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

**Expected response (302):** Redirect to `/apply/rule?s={RULE_S}`

Follow the redirect manually:

```bash
curl -s -c cookies.txt -b cookies.txt -o step7.html '{REDIRECT_URL}'
```

---

## Step 8: Agree to Rules

The rules page (`/apply/rule`) displays terms and conditions with an "agree"
button. The page title is
`保養施設等の利用およびイベントにおける個人情報の取り扱いについて`.

### Rules form HTML (captured live)

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

**CRITICAL:** The form has a hidden `s` field that **must** be included in
the POST. Without it the server redirects to `/service_category/index`
(session lost). The "同意する" button is `type="button"` (not `type="submit"`),
so its `value` is **not** sent as form data -- do not send a `commit` param.

```bash
curl -s \
  -c cookies.txt -b cookies.txt \
  -X POST \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN_5}' \
  --data-urlencode 's={RULE_S}' \
  -o step8.html \
  'https://as.its-kenpo.or.jp/apply/email_input'
```

**Expected response (200 OK):** The email input page directly (no redirect).
The form action is `/apply/email_input` and the response URL is also
`/apply/email_input`. This is a direct POST-to-page, not a POST-redirect-GET.

---

## Step 9: Submit Email

The email page (`Web申し込み`) has a form with one text field and a hidden
double-submit-prevention token.

### Email form HTML (captured live)

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

The `confirm_submit()` function shows a JavaScript `confirm()` dialog
(`メールを送信します。よろしいですか？`). With curl, there is no dialog
to handle -- the form just submits.

```bash
curl -s \
  -c cookies.txt -b cookies.txt \
  -X POST \
  --data-urlencode 'utf8=✓' \
  --data-urlencode 'authenticity_token={AUTH_TOKEN_6}' \
  --data-urlencode '__token__={DOUBLE_SUBMIT_TOKEN}' \
  --data-urlencode 'email=user@example.com' \
  --data-urlencode 'commit=送信' \
  -o step9.html \
  'https://as.its-kenpo.or.jp/apply/send_complete?s={SEND_S}'
```

**CRITICAL:** The `__token__` hidden field and `commit=送信` submit button
value **must** be included. Without them the server returns the `send_complete`
page but **does not actually send the email**. This was confirmed by testing:
omitting these fields produced a success page with no email delivery;
including them triggered immediate email delivery.

**Expected response (200 OK):** The completion page directly (no redirect).

---

## Step 10: Booking Complete

The response body from step 9 IS the completion page. Look for:

```html
<h1>送信結果</h1>
<p>メール送信を完了しました。</p>
<strong>送信されたメールに記載のURLより手続きを進めてください。</strong>
```

The JS on the page also pushes the URL state to `send_complete`:
```javascript
history.pushState(null, '施設予約システム', 'send_complete');
```

**What happens next:** A confirmation email is sent to the provided address
with a URL to finalize the booking. The room is held for 30 minutes from
step 7. If the email URL is not visited in time, the room is released.

---

## Key Findings

### 1. Every page issues a fresh CSRF token

Each response contains a new `authenticity_token` in a hidden `<input>` field
and a CSRF token in a `<meta>` tag. You must extract and use the token from the
**most recent** response. Using a stale token causes a 422 or a redirect to the
error page.

### 2. The hidden-form pattern

The site uses a recurring pattern: clickable links (`href="javascript:;"`) with
`data-*` attributes, and a **hidden `<div style="display: none">`** containing
a `<form>`. The click handler copies the `data-*` value into a hidden input and
triggers the form submit. In curl, you skip the JS and POST the form directly.

For example, on the hotel selection page:

```
Visible:  <a data-service-group-id="7" href="javascript:;">Hotel Name</a>
Hidden:   <form action="/calendar_apply/apply_service_select" method="post">
            <input name="service_group_id" id="service_group_id" value="" />
          </form>
JS:       $("#service_group_id").val($(this).data('service-group-id'));
          $("#service_group_select").click();
curl:     POST with service_group_id=7
```

### 3. AJAX calls require specific headers

For any AJAX endpoint (month navigation, room search), you must send:

```
X-Requested-With: XMLHttpRequest
X-CSRF-Token: {value from <meta name="csrf-token">}
Accept: text/javascript, application/javascript, */*; q=0.01
```

Without `X-Requested-With`, the server treats it as a non-AJAX request and
may return HTML or redirect instead of JavaScript.

### 4. The Step 4 redirect must not be auto-followed

`check_apply_service_coma` transitions from the `/calendar_apply/` domain to
`/apply/`. It returns a 302 with:
- A new `s` parameter in the Location URL
- New ALB cookies

If you auto-follow (`-L`), curl sometimes encounters a second 302 to
`/service_category/index` (a 404 dead-end). The safe approach is:

```bash
# Don't follow
curl --max-redirs 0 -D headers.txt ...

# Extract Location header
REDIRECT=$(grep -i 'location:' headers.txt | head -1 | tr -d '\r' | sed 's/location: //i')

# Follow manually
curl -c cookies.txt -b cookies.txt "$REDIRECT"
```

### 5. Sessions expire quickly

The server-side session expires after approximately **30 seconds** of
inactivity. If you take too long between steps, the response will be one of:

- A 302 redirect to `/service_category/index` (which returns 404)
- The page loads but contains: `セッションがタイムアウトしました`
- The AJAX response is: `parent.location.href='/service_category/index'`

To avoid this, **execute all steps in rapid succession** without pauses. In
testing, a Python script executing steps 1--6 back-to-back succeeded
consistently.

### 6. The `s` parameter has two distinct scopes

| Token | Created at | Used for | Scope |
|-------|-----------|----------|-------|
| Calendar `s` | After reCAPTCHA (step 0) | Steps 1--4 (`/calendar_apply/*`) | All calendar + hotel + service selection |
| Apply `s` | Step 4 redirect | Steps 5--10 (`/apply/*`) | Booking form, rules, email, completion |

The Calendar `s` is reused across steps 1--4. The Apply `s` appears in the
step 4 redirect URL and is used for all remaining steps.

### 7. Room search returns JavaScript, not HTML

The room availability search (step 6) returns raw JavaScript that jQuery
executes to inject HTML into the page:

```javascript
$('#coma').html("...<table>...</table>...");
$('.coma_title').show();
$('.main_submit').show();
```

Parse the JavaScript string (with escaped quotes `\"`) to extract room
checkbox `name`/`value` pairs and the `apply_session_guid`.

### 8. Cookie management is essential

You must persist cookies across all requests. The critical cookies are:

| Cookie | Purpose | Flags |
|--------|---------|-------|
| `_src_session` | Rails session ID | HttpOnly, Secure |
| `AWSALB` | ALB sticky session | Path=/ |
| `AWSALBTG` | ALB target group sticky | Path=/ |
| `AWSALBCORS` | ALB CORS sticky | SameSite=None, Secure |
| `AWSALBTGCORS` | ALB target group CORS sticky | SameSite=None, Secure |

Use `-c cookies.txt -b cookies.txt` on every curl call so that session and
load-balancer affinity are maintained.

### 9. Steps 8 and 9 return 200, not 302

Unlike earlier steps, the rules agreement (step 8) and email submission
(step 9) return **200 OK** with the next page rendered directly. They do
NOT return 302 redirects. The JavaScript on these pages uses
`history.pushState()` to update the browser URL bar, simulating navigation:

```javascript
history.pushState(null, '施設予約システム', 'email_input');  // step 8
history.pushState(null, '施設予約システム', 'send_complete'); // step 9
```

### 10. The rules form `s` field is mandatory

The rules page form has a hidden `s` field. If you omit it, the server
loses track of the session and redirects to `/service_category/index`.
This was the single hardest bug to find during testing -- the `s` field
is easy to miss because the form action (`/apply/email_input`) does not
contain an `s` query parameter.

### 11. The calendar `s` token is reusable

The calendar `s` token (from CAPTCHA) can be used for **multiple complete
booking flows** in succession. Each booking creates a new apply-side
session via the step 4 redirect. The calendar `s` only expires after
extended inactivity, not after a single use.

### 12. The email form silently fails without `__token__` and `commit`

The `send_complete` endpoint returns an identical success page regardless
of whether the email actually sends. The `__token__` (double-submit
prevention) and `commit=送信` (submit button value) fields are both
required for the server to actually dispatch the email. Without them the
page says "メール送信を完了しました" but no email arrives. This is the
most dangerous silent failure in the flow -- there is no way to
distinguish success from failure by inspecting the HTTP response alone.

---

## Complete Flow Diagram

```
Browser Required
=================
  Main Page (GET /)
       |
       v
  CAPTCHA Page (GET /calendar_apply?s=MAIN_S)
       |  [solve reCAPTCHA]
       v
  POST /calendar_apply  -->  302  -->  /calendar_apply/calendar_select?s=CALENDAR_S
                                       (saved to calendar_url_cache.txt)

curl Only
==========
  Step 1:  GET  /calendar_apply/calendar_select?s=CALENDAR_S
                Extract: auth_token, s, month data
       |
  Step 1b: POST /calendar_apply/calendar_select  (AJAX, optional month nav)
                Data: join_date, s
       |
       v
  Step 2:  POST /calendar_apply/service_group_select
                Data: join_time, s, auth_token
                Response: hotel list with data-service-group-id
       |
       v
  Step 3:  POST /calendar_apply/apply_service_select
                Data: service_group_id, join_time, s, auth_token
                Response: service list with data-apply-service-id
       |
       v
  Step 4:  POST /calendar_apply/check_apply_service_coma
                Data: apply_service_id, join_time, s, auth_token
                Response: 302 -> /apply/empty_new?s=APPLY_S
       |
       v  (follow redirect manually)
  Step 5:  GET  /apply/empty_new?s=APPLY_S
                Extract: csrf, auth_token, form_action, coma_s
       |
       v
  Step 6:  POST /apply/empty_new?s=COMA_S  (AJAX room search)
                Data: join_time, night_count, stay_persons, hope_rooms
                Headers: X-Requested-With, X-CSRF-Token
                Response: JS with room checkboxes + apply_session_guid
       |
       v
  Step 7:  POST /apply/empty_create?s=FORM_S
                Data: join_time, night_count, stay_persons, hope_rooms,
                      apply_session_guid, apply[coma[ROOM_ID]]
                Response: 302 -> /apply/rule?s=...
       |
       v
  Step 8:  POST /apply/email_input (agree to terms)
                Data: s (hidden field), auth_token (NO commit param)
                Response: 200 -> email input page rendered directly
       |
       v
  Step 9:  POST /apply/send_complete?s=SEND_S (submit email)
                Data: email, auth_token, __token__, commit=送信
                Response: 200 -> completion page rendered directly (DONE)
```

---

## Reference Implementation

`run_live.py` is the verified working script that completed a real booking.
It executes steps 1--9 using `subprocess` calls to `curl`:

```bash
# 1. Get a fresh calendar URL (solve CAPTCHA in browser, save URL to cache)
# 2. Run the curl-based booking
python3 run_live.py
```

`curl_booking.py` is an expanded version with better error handling,
configurable target date/guest count, and month navigation logic.
