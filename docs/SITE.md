# The ITS site — everything the code cannot tell you

Published rules read 2026-08-19 from [申し込みから利用まで](https://www.its-kenpo.or.jp/shisetsu/moushikomi.html)
(procedures, 申込受付期間一覧表, cancellation terms), the
[直営](https://www.its-kenpo.or.jp/shisetsu/chokuei/) /
[通年](https://www.its-kenpo.or.jp/shisetsu/keiyaku/tsunen/) /
[夏季](https://www.its-kenpo.or.jp/shisetsu/keiyaku/kaki/) rosters, and
[notice id=144](https://as.its-kenpo.or.jp/column_comments/notice_info?id=144).
**measured** marks facts derived from this repository's own history; **unverified** says so.

Verified live end to end on 2026-08-19: ブルーベリーヒル勝浦, 2026-09-01, 申込受付番号 **10287126**, 予約確定.

`test_booking_flow.py`'s `FakeITS` replays every page below and is checked on every run.
**Where this doc and the fake disagree, the fake is right.**

## 1. Published rules

- **Two channels.** 抽選申込 (lottery) and 空き照会申込 (vacancy). Only the second is
  automated — the lottery allocates 抽選により, by lot, so a fast client wins nothing. It caps
  at five applications a month per insurance 記号番号, and a win is already 予約確定.
- **Release instant.** Whatever the draw did not fill drops at **00:00 JST on the 照会開始日**.
  In FY2026 that is the 27th of M−2 in all twelve rows; FY2025 used the 25th, 26th or 27th
  depending on the month. **Per-year editorial — never hard-code it.**
- **Two cutoffs, and which applies is a property of the facility.** **D−4** for 直営 (the three
  トスラブ), ブルーベリーヒル勝浦, 日光千姫物語, 熱海後楽園ホテル and all 夏季/冬季. **D−10** for
  every other 通年.
- **Rosters** — the only place a `SKIP_HOTELS` / `PRIORITY_HOTELS` entry can be classified.
  - *直営 (3), D−4:* トスラブ箱根 ビオーレ・和奏林, トスラブ館山 ルアーナ
  - *夏季 (13), D−4:* グランドメルキュール伊勢志摩, スパリゾートハワイアンズ モノリスタワー,
    フルーツパーク富士屋, 定山渓 ゆらく草庵, 鎌倉パークホテル, **NAGU勝浦**, 伊豆赤沢温泉,
    軽井沢マリオット, 蓼科東急, 高山グリーン, NASPAニューオータニ, アオアヲナルトリゾート, ホテル日航アリビラ
  - *通年 (24), D−10 except the three marked:* ラビスタ熱海テラス, ハーヴェスト鬼怒川, 鳴子温泉 湯元 吉祥,
    ハーヴェスト那須, **日光千姫物語 (D−4)**, 草津温泉 ホテルヴィレッジ, 伊香保温泉 ホテル天坊,
    ラビスタ横須賀観音崎テラス, ホテルオークラ東京ベイ, リソルの森, **ブルーベリーヒル勝浦 (D−4)**,
    和倉温泉 あえの風, ラビスタ富士河口湖, ハーヴェスト斑尾・旧軽井沢, **熱海後楽園ホテル (D−4)**,
    ハーヴェスト伊東・浜名湖, ホテル琵琶レイクオーツカ, ホテル日航プリンセス京都,
    ハーヴェスト京都鷹峯・有馬六彩・南紀田辺, ゆふいん山水館
  - *冬季 (5), D−4:* ラビスタ函館ベイANNEX, ホテル日航アリビラ, NAGU勝浦, NASPAニューオータニ,
    蓼科東急ホテル — so all but 函館ベイANNEX also sit on the 夏季 roster. Read from
    `/shisetsu/keiyaku/touki/index.html` 2026-08-19.
  - `ホテルハーヴェスト スキージャム勝山` is on **none** of the four rosters, nor 提携施設 —
    presumably delisted. `SKIP_HOTELS` keeps it anyway: skipping a facility that never
    appears is free, dropping it books the place if ITS re-contracts it.
- **The booking system and the roster pages do not always agree on a name.**
  `service_group_select` returns 「グランドメルキュール伊勢志摩」 where the 夏季 roster says
  「グランドメルキュール 伊勢志摩リゾート＆スパ」. `is_skipped()` compares normalised names for
  *exact* equality, so the roster spelling would never match — always prefer a string
  observed in a 「Found N hotels: …」 log line over one transcribed from the website.
- **`PRIORITY_HOTELS = ['NAGU']` is NAGU勝浦, 夏季**: D−4, and never listed for stays past 9/30
  (「夏季保養施設の9/30～2泊…は選択いただけません」 — the season boundary itself is inferred).
- **Month-end two-night stays** open only from the 照会開始日 of the month the *second* night
  falls in. Applications are per the month the stay date falls in — which is why there is one
  scanner thread per month.
- **Cancellation is the only supply after the release instant, and it is structured.** Web
  self-service until **D−10** only; from D−9 it is 50% of the fee and telephone-only, Mon–Fri
  09:00–17:00 JST excluding holidays; full price on the day. Nothing lapses on its own, and
  reducing guests or nights counts as a cancellation. **No waitlist exists** — polling is the
  only mechanism. So late releases can physically only appear on weekday afternoons.
- **Stay limits.** Max 2 nights per stay per facility; no overlapping applications at one
  facility; max 10 rooms per application per date.
- **Only dates with vacancy are displayed**, and a displayed date can still yield no room
  matching the guest/room count. The hotel list for a date is headed 「{date}に空きがある施設です」
  and typically holds about three facilities, not the 24-facility roster.
- **Vacancy search is suspended while a draw is processed** (a banner on the front page). Start
  and end are unpublished and **unverified**; the bot polls straight through, so a suspension
  looks like a quiet month.

## 2. Measured

- **A 503 carrying `<title>セキュリティアラート</title>` is a ~24-hour, IP-scoped ban, not site
  load.** The body says so: アクセス過多 from this IP, access blocked 約**24時間**, use another
  network. All 467 non-empty recovered dumps are that page, across three episodes (2026-04-07,
  2026-04-12, 2026-08-17) over three egress IPs, every one served on the scanner's
  `calendar_get` — simply the most frequent request. **Request volume is therefore the dominant
  operational constraint**: one ban costs a full day of every target date at once, so a faster
  poll that earns a ban is strictly worse than a slower poll that does not. Budget freed by
  `SCAN_REUSE_SESSION` should be banked, not respent.
- **The dump corpus is not a census.** `DEBUG_DUMP_KEEP` prunes oldest-first, so what survives
  is survivorship-biased. An earlier reading of the timestamps alone concluded these 503s were
  ordinary office-hours load; the bodies flatly contradict it. **Read the bodies.**
- **22% of availability episodes begin at exactly D−10, 57% within D−9…D−14** — the published
  free-cancellation boundary predicting the observed data.
- **Episodes are not seconds long.** Of 104 intra-episode gaps, 38 are 20–21 s (still there a
  full scan cycle later) and 66 are ≥1800 s. Design for a half-hour window, not 200 ms.
- **Sessions die after roughly 30 s of inactivity between steps**, so run a chain back to back.

## 3. The official procedure, and the 30-minute hold

The 【空き照会申込手順】 numbers nine steps to 予約確定 (then 10–12, guest registration and the
利用案内). Steps 1–4 choose facility, date and room behind the 画像認証; **step 5,
「予約手続きに進む」, starts the 30-minute clock**; step 6 sends the confirmation mail; steps 7–9
are the emailed leg — open the URL, 申込する, 確認 — and **step 9 alone is 予約確定**:

> 「予約手続きに進む」ボタンを押して以降、30分以内に申込手続きを完了してください。完了しない場合、選択した部屋は無効となります。
>
> 申込完了画面に遷移し申込受付番号が表示され、申込完了メールが自動送信されます。**この時点で申込手続き完了及び予約確定となります。**

So nothing before step 9 is a booking. `holds.json` records step-5 holds; `reservations.json`
records step-9 申込受付番号, the only entries carrying a real cancellation liability. **The site
refuses a second application at a facility it already holds a room for**, answering the room
search 「空き部屋がございません」 — which is the only bound on a leaked hold, and the reason
nothing in the code tracks the 30-minute clock.

## 4. The request chain

Rails (Passenger/Apache) behind an AWS ALB. `_src_session` is the Rails session;
`AWSALB`/`AWSALBTG`/`AWSALBCORS`/`AWSALBTGCORS` carry ALB affinity — **all five must survive**,
so every call shares one cookie jar and each thread has its own. Cloudflare Turnstile guards the
entry point. No redirect is ever auto-followed (`--max-redirs 0`): the server sometimes answers a
second 302 to `/service_category/index`, and `-L` would land on the dead end with the informative
headers already discarded.

| # | Request | Mints / needs |
|---|---|---|
| 0 | browser: `GET /` → `/calendar_apply` → solve Turnstile → `POST /calendar_apply` | 302 → `calendar_select?s=CALENDAR_S`. Cached **only if** the URL contains `calendar_select` — a non-calendar URL still answers 200, so caching one poisons the cache with a session that looks healthy forever |
| 1 | `GET /calendar_apply/calendar_select?s=CALENDAR_S` | meta `csrf-token`, form `authenticity_token`, `name="s" id="s"` |
| 1b | `POST /calendar_apply/calendar_select` **(AJAX)** | `join_date` (1st of month) + `s` only — no `utf8`, no `authenticity_token`; CSRF travels in `X-CSRF-Token`. One response carries every date in the month |
| 2 | `POST /calendar_apply/service_group_select` | `utf8`, `authenticity_token`, `join_time`, `s` → hotel list. **The largest failure class on disk** |
| 3 | `POST /calendar_apply/apply_service_select` | `+ empty=`, `service_group_id` → service list |
| 4 | `POST /calendar_apply/check_apply_service_coma` | `apply_service_id` → **302** `/apply/empty_new?s=APPLY_S` + new ALB cookies. A `Location` without `empty_new` is a failed step |
| 5 | `GET /apply/empty_new?s=APPLY_S` | `authenticity_token`, `action="/apply/empty_create?s=…"` (FORM_S), `coma_search('…')` (COMA_S, URL-encode it) |
| 6 | `POST /apply/empty_new?s=COMA_S` **(AJAX)** | room ids + `apply_session_guid`. `X-CSRF-Token` is the `<meta>` value; `Referer` is the **APPLY_S** URL, not COMA_S |
| 7 | `POST /apply/empty_create?s=FORM_S` | **★ takes the 30-minute hold — the point of no return.** → 302 `/apply/rule?s=RULE_S` |
| 8 | `POST` the `/apply/rule` form action | **the hidden `s` field is mandatory** even though the action carries no `s` query param; omit it → 302 `/service_category/index`. Send **no** `commit`: 同意する is `type="button"` |
| 9 | `POST /apply/send_complete?s=SEND_S` | **never retried.** `__token__` **and** `commit=送信` must both be present — without them the server returns an *identical* `send_complete` page and **sends no mail**. Nothing in the response distinguishes the two |

Markup-exact gotchas:

- **Date cells** are `<td data-join-time="YYYY-MM-DD">` classed `empty` (○), `a_little` (few
  left), `full` (☓), `over` (past). **Both `empty` and `a_little` are clickable and applicable
  for** — matching `empty` alone silently skips the dates closest to selling out.
- **AJAX responses (1b, 6) are Rails-UJS JavaScript with backslash-escaped quotes**, so their
  extractors must match `\"`, not `"`. All four AJAX headers are required; without
  `X-Requested-With` the server answers HTML or a redirect instead. Steps 8–9 answer 200, not
  302, and fake navigation with `history.pushState`.
- **Hotel names arrive HTML-escaped and mix U+3000 with ordinary spaces**, so raw equality
  against a skip list can miss.
- **A dead session** is a 302 to `/service_category/index` (which itself 404s), or
  「セッションがタイムアウトしました」 in a page, or `parent.location.href='/service_category/index'`
  in an AJAX body. Distinct from the 503 ban in §2.
- **「…ご指定の施設において空き部屋がございません。」** has no booking form, so a missing form
  action plus that string is an ordinary lost race — often this bot reading back its own hold.
- **`authenticity_token` and `<meta csrf-token>` are valid for the life of the session.** The
  per-render freshness is BREACH masking over a stateless unmask; there is no nonce store. This
  is what lets one token serve steps 6 and 7, and what `SCAN_REUSE_SESSION` depends on.
  **`__token__` (40 hex, email form only) is a different mechanism** — an app-level
  double-submit nonce, plausibly one use. Re-read it every time; do not conflate them.

## 5. The emailed leg

The mail is from `noreply@mail.its-kenpo.or.jp`, subject 「{施設名}申込手続きのご案内」, with
exactly one URL `/apply/new?c=<uuid4>`. **It names only the date it was sent**, never the stay
date, so a stay-date filter can never match; arrival time is the only honest discriminator.

`GET /apply/new?c=<uuid>` → one form to `/apply/confirm?c=<uuid>` with 15 controls, every one a trap:

| Trap | What is actually there |
|---|---|
| `_method` | `value="true"`, **not a verb**. Echo verbatim; `_method=patch` makes Rack rewrite the verb and the route 404s |
| `utf8` | **absent**, unlike every form in §4 |
| required-ness | no `required` attribute and 「必須」 nowhere — it is an `<img src=".../must-*.png" name="…">` in `<dd class="must">`. A guard keyed on `required` reports "0 unmapped" for a blank form, and a parser collecting every *named* element invents fields no browser submits |
| labels | proximity only, never `for=`. `apply[month]`'s preceding text is the tail of the year dropdown's options; `apply[state]`'s is `postal" /> （半角）`. **Match field name first, label only as a fallback** |
| options | `man`/`woman`, `myself`/`family`, prefectures `1`…`47`; birth is three **unpadded** selects with 和暦 labels (`平成12年(2000年)`). Matching 男/女/本人 needs the option *label* |

`POST /apply/confirm?c=<uuid>` → 申込内容確認画面, which **echoes 記号/番号/カナ氏名/生年月日/電話/住所
back as prose** — a dump of that page is a dump of somebody's insurance record.

`POST /apply/complete?c=<uuid>` → **never retried**; it files the application and sends
申込完了メール. The receipt renders as `<strong>申込受付番号：  10287126</strong>` — label and number
inside *one* tag, so a raw-markup regex reads it correctly only by luck; one tag between them and
`([0-9A-Za-z-]{4,})` captures `strong`. Search the tag-stripped text.

**When the hold lapses** the emailed link answers **200 with no form at all**:
「30分が経過しましたので、ご利用のURLは無効となりました。」 Only that text tells it apart from the
form's markup having changed under the parser.

### `POST /apply/confirm` refuses curl and accepts Chrome — open question

Measured 2026-08-19 against a live hold, unresolved. The POST is answered `302 →
/service_category/index`, empty body, `x-runtime ≈ 0.02` — a Rails `before_action`, bouncing
before the body is read. Served identically for:

- a valid `authenticity_token`, a corrupted one, and none at all; an empty body;
- `+ utf8=✓`, `+ commit=申込する`, `+ c=` in the body, `_method` omitted;
- `+ Origin`, `+ Sec-Fetch-*`, `+ sec-ch-ua*`, `+ Upgrade-Insecure-Requests`,
  `+ Cache-Control` — singly and all together;
- the form GET and the POST on **one** connection (`--next`).

The same POST — same URL, same 15 fields, same cookies — **succeeds from real Chrome**, both as
`form.submit()` and as an in-page `fetch()` with the identical body. `fetch()` sends none of the
navigation headers and still works, so the difference is not the request: it is the **client**.
What is left is curl's TLS fingerprint and the egress path.

**That measurement was taken inside a sandbox whose HTTPS proxy intercepts and may present a
different source address per request.** A session pinned to `request.remote_ip` would behave
exactly like this, including the GET always succeeding — the GET is what establishes the session.
**Check whether it reproduces off a proxied network before changing anything about the request.**
The 抽選処理 banner on these pages is site-wide layout (it appears on the 404 too), not evidence.

This is why the browser fallback exists, and why curl stays the primary path: the browser fires
only after curl was refused, so if the cause is environmental the fallback stops firing on its
own with nothing to undo.

## 6. Turnstile

Turnstile rejects **headless** Chrome and detects **Playwright**'s bundled Chromium; pydoll
driving real Chrome over CDP passes, and a **minimised** window still passes. The checkbox lives
in a cross-origin iframe about **28 px in from the `.cf-turnstile` box's left edge**, vertically
centred, reachable only by CDP mouse events — which cross the iframe boundary where a DOM click
does not. Only the checkbox path works: an escalated interactive challenge simply never yields a
token. `MAX_ATTEMPTS × TOKEN_TIMEOUT ≈ 105 s`, which must stay inside `CAPTCHA_TIMEOUT`.
