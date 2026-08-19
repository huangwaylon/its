# ITS Facility Booking — the published rules

The operational rules of the ITS 保養施設 booking system, as published by 関東IT
ソフトウェア健康保険組合. This is the ruleset the booker operates inside; nothing
in it is inferred from observing the site except where explicitly marked.

Everything below was read from these pages on 2026-08-19:

- [申し込みから利用まで](https://www.its-kenpo.or.jp/shisetsu/moushikomi.html)
  — the procedures, the deadlines, the cancellation terms, and the
  [ITS保養施設申込受付期間一覧表](https://www.its-kenpo.or.jp/shisetsu/moushikomi.html#kikan)
  schedule table.
- [直営保養施設](https://www.its-kenpo.or.jp/shisetsu/chokuei/) — the three
  directly-managed facilities.
- [通年保養施設](https://www.its-kenpo.or.jp/shisetsu/keiyaku/tsunen/) — the
  year-round contracted facilities.
- [夏季保養施設](https://www.its-kenpo.or.jp/shisetsu/keiyaku/kaki/) — the
  summer-season contracted facilities.
- [集中アクセス増加に伴う一定時間の遮断対応について](https://as.its-kenpo.or.jp/column_comments/notice_info?id=144)
  (notice id=144, dated 2022/08/30).

Where a fact is measured from this repository's own history rather than
published, it is labelled **measured**. Where something is not verified, it says
so.

---

## 1. Two application channels

> 保養施設の申込方法は抽選申込と空き照会申込の2通りあります。
>
> 抽選申込は利用月の2ヶ月前の抽選申込受付期間内に抽選申込を受け付け、抽選を実施
> します。その後、抽選で埋まらなかった日付を対象に、空き状況の照会開始日以降に
> 空き照会申込を受け付けます。

*There are two ways to apply: the lottery (抽選申込) and the vacancy enquiry
(空き照会申込). The lottery takes applications during a window two months before
the stay month and then draws. Whatever the draw does not fill is then offered,
from the 照会開始日 onwards, through the vacancy channel.*

**The booker automates only the second channel.** It drives
`/calendar_apply/*` and `/apply/*` on `as.its-kenpo.or.jp`, which is the
「直営・通年・夏季・冬季保養施設(空き照会)」 path. It has no notion of the
lottery at all. So it is competing for the residue of a draw it never entered.

---

## 2. 抽選申込 — the lottery

Not automated, and worth knowing precisely because it is the channel that
allocates most of the supply.

- **Window**: the 抽選申込受付期間 for a stay month falls in the month two
  months prior, roughly the 8th to the 13th. Exact dates per month in §3.
- **Cutoff within the window** — and it is a trap:
  > 受け付けは、抽選申込受付期間の最終日24時までとなります。ただしメールアドレス
  > を入力後、メール記載の申込用URLをクリックした時点で24時を経過している場合は
  > 申し込みできません。

  *Applications close at 24:00 on the last day of the window. If the clock
  passes 24:00 between entering your email address and clicking the URL in the
  mail, the application is refused.* Same emailed-link pattern as the vacancy
  channel (§7).
- **Per-member cap**:
  > 同一の記号番号によるひと月の抽選申込回数の上限は5回となります。

  *At most five lottery applications per month per insurance 記号番号.* Note it
  is per 記号番号, i.e. per membership, not per email address or per session.
- **Allocation is by chance, not by speed**:
  > 定員を超えた場合のご利用は、抽選により決定します。

  *Where applications exceed capacity, the allocation is decided by lot.* There
  is nothing for a fast client to win here.
- **Room type is selectable** in the lottery, which it is not in the vacancy
  channel's search-then-pick flow:
  > 抽選申込時にご希望の部屋タイプを選択いただけます(選択できる部屋タイプは施設に
  > より異なります)。
- **Results**: emailed from about 14:00 on the 抽選申込結果回答日.
  > 抽選申込結果回答日…の14時ごろよりメールにて随時送信いたします。

  A win is already a confirmed reservation — 「この時点で予約確定です」 — and is
  therefore already exposed to the cancellation fees in §6.

---

## 3. 令和8年度 申込受付期間一覧表 (FY2026 schedule)

Reproduced from the 申込受付期間一覧表 on the 申し込みから利用まで page. 令和8年度
runs April 2026 to March 2027; days in the 抽選申込受付期間 and 照会開始日 columns
are in the month(s) preceding the stay month.

| 申込対象期間 (stay dates) | 抽選申込受付期間 | 抽選申込結果回答日 | 空き状況の照会開始日 |
|---|---|---|---|
| 令和8年4月分 — 4/1〜4/30 | 2/8〜2/13 | 2/20 (Fri) | 2/27 (Fri) |
| 5月分 — 5/1〜5/31 | 3/8〜3/13 | 3/23 (Mon) | 3/27 (Fri) |
| 6月分 — 6/1〜6/30 | 4/8〜4/13 | 4/21 (Tue) | 4/27 (Mon) |
| 7月分 — 7/1〜7/31 | 5/8〜5/13 | 5/21 (Thu) | 5/27 (Wed) |
| 8月分 — 8/1〜8/31 | 6/10〜6/15 | 6/23 (Tue) | 6/27 (Sat) |
| 9月分 — 9/1〜9/30 | 7/8〜7/13 | 7/21 (Tue) | 7/27 (Mon) |
| 10月分 — 10/1〜10/31 | 8/8〜8/13 | 8/21 (Fri) | 8/27 (Thu) |
| 11月分 — 11/1〜11/30 | 9/9〜9/14 | 9/18 (Fri) | 9/27 (Sun) |
| 12月分 — 12/1〜12/31 | 10/8〜10/13 | 10/21 (Wed) | 10/27 (Tue) |
| 令和9年1月分 — 1/1〜1/31 | 11/8〜11/13 | 11/20 (Fri) | 11/27 (Fri) |
| 2月分 — 2/1〜2/28 | 12/9〜12/14 | 12/22 (Tue) | 12/27 (Sun) |
| 3月分 — 3/1〜3/31 | 1/8〜1/13 | 1/21 (Thu) | 1/27 (Wed) |

Two footnotes to the table, both load-bearing:

> 宿泊日が属する月毎の申し込みとなります。

*Applications are made per the month the stay date falls in.* This is why the
scanner is organised one thread per month: the site's own unit of availability is
the month.

> 空き照会は空き状況の照会開始日の午前0時より申し込みを受け付けます。

**Vacancy applications are accepted from 00:00 JST on the 照会開始日.** That is
the one instant in the cycle when the whole unfilled residue of a month becomes
bookable at once. Everything the lottery did not allocate drops then.

In 令和8年度 the 照会開始日 is the 27th of M−2 in all twelve rows. **Do not
hard-code that**: the 令和7年度 table on the same page has it on the 25th, 26th or
27th depending on the month, so the value is per-year editorial, not a rule.

---

## 4. Application cutoffs — when a date stops being bookable

This is the rule that decides whether polling a date is useful at all.

> 空き照会申込ができるのは直営保養施設(トスラブ3館)・ブルーベリーヒル勝浦・日光千
> 姫物語・熱海後楽園ホテル・夏季保養施設・冬季保養施設は利用日の4日前まで、ブルー
> ベリーヒル勝浦・日光千姫物語・熱海後楽園ホテルを除く通年保養施設は利用日の10日
> 前までとなります。

*Vacancy applications are accepted up to 4 days before use for the
directly-managed facilities (the three トスラブ), ブルーベリーヒル勝浦,
日光千姫物語, 熱海後楽園ホテル, and all 夏季 and 冬季 facilities; and up to 10 days
before use for every other 通年 facility.*

So there are exactly two cutoffs, **D−4** and **D−10**, and which applies is a
property of the facility. The classification below is what makes the rule usable.

### 直営保養施設 (3) — D−4

トスラブ箱根 ビオーレ, トスラブ箱根 和奏林, トスラブ館山 ルアーナ

### 夏季保養施設 (13) — D−4

グランドメルキュール 伊勢志摩リゾート＆スパ, スパリゾートハワイアンズ モノリス
タワー, フルーツパーク 富士屋ホテル, 定山渓 ゆらく草庵, 鎌倉パークホテル,
**NAGU勝浦**, プレジャーリゾート伊豆赤沢温泉, 軽井沢マリオットホテル, 蓼科東急
ホテル, 高山グリーンホテル, NASPAニューオータニ, アオアヲナルトリゾート,
ホテル日航アリビラ

### 通年保養施設 (24) — D−10, except three at D−4

ラビスタ熱海テラス, ホテルハーヴェスト鬼怒川, 鳴子温泉 湯元 吉祥, ホテルハーヴェ
スト那須, **日光千姫物語**, 草津温泉 ホテルヴィレッジ, 伊香保温泉 ホテル天坊,
ラビスタ横須賀観音崎テラス, ホテルオークラ東京ベイ, リソルの森,
**ブルーベリーヒル勝浦**, 和倉温泉 あえの風, ラビスタ富士河口湖, ホテルハーヴェス
ト斑尾, ホテルハーヴェスト 旧軽井沢, **熱海後楽園ホテル**, ホテルハーヴェスト伊東,
ホテルハーヴェスト浜名湖, ホテル琵琶レイクオーツカ, ホテル日航プリンセス京都,
ホテルハーヴェスト京都鷹峯, ホテルハーヴェスト有馬六彩, ホテルハーヴェスト南紀
田辺, ゆふいん山水館

The three in bold are the named exceptions and take D−4.

### 冬季保養施設 — D−4, roster not enumerated here

The rule covers 冬季保養施設 too. **Unverified**: no 冬季 roster page was read for
this document, so the membership of that class is not written down here. The
申し込みから利用まで page mentions トスラブ湯沢 in a 冬季 context, and
`config.SKIP_HOTELS` contains ホテルハーヴェスト スキージャム勝山 and
ラビスタ函館ベイANNEX, neither of which appears on the 通年 or 夏季 rosters — they
are presumably 冬季, but that is inference, not a citation.

### NAGU勝浦 is a summer facility

`config.PRIORITY_HOTELS = ['NAGU']` targets **NAGU勝浦, which is on the 夏季
roster**. Two consequences:

- Its cutoff is **D−4**, not D−10.
- The summer season ends at 9/30. The page states
  > 夏季保養施設の9/30～2泊と冬季保養施設の3/31～2泊は選択いただけません。

  *A two-night stay starting 9/30 cannot be selected at a summer facility (nor
  from 3/31 at a winter facility).* The sentence is from the 抽選申込 notes and
  strictly forbids only the 9/30 two-night selection; that the season itself ends
  9/30 follows from it, but the 夏季 roster page states no explicit season dates,
  so treat the boundary itself as inferred. Either way, NAGU勝浦 will not be
  listed for stays past that point.

### Month-end two-night stays

> 月の最終日からの2泊をご希望する場合、申し込みが出来るのは2泊目が属する月の空き
> 状況の照会開始日以降になります。

*For a two-night stay starting on the last day of a month, applications open only
from the 照会開始日 of the month the second night falls in.* A stay is not
governed solely by the month of its first night.

---

## 5. Only dates with vacancy are shown

> 空きが無い日付は表示されません。また日付が表示される場合でもご希望の条件に合わ
> ない場合、部屋は表示されません。

*Dates with no vacancy are not displayed. Even when a date is displayed, no rooms
are shown if they do not match the requested conditions.*

This is why the hotel-list page for a date is headed 「{date}に空きがある施設です」
and typically lists a handful of facilities rather than the full roster — the site
has already filtered. A date can appear, and still yield no room, for the guest
count or room count asked for.

---

## 6. Cancellation — the shape of the secondary supply

Cancellations are the only source of availability between one 照会開始日 and the
next, and the rules make them arrive at predictable times.

**Web self-service only until D−10.**

> 利用日の10日前まではWEB上でキャンセルが行えます。メールに記載のキャンセル用URL
> より手続きを行ってください。
>
> 利用日の9日前以降は健康増進サービスセンター(03-5925-5348)へ電話でご連絡くださ
> い。

*Cancellation can be done on the web up to 10 days before use, via the
cancellation URL in the confirmation mail. From 9 days before, you must telephone
the 健康増進サービスセンター.*

The centre's hours:

> 受付時間 月曜～金曜(祝日・年末年始を除く) 9:00～17:00
>
> 時間外は受け付けておりませんのでご了承ください。

*Mon–Fri excluding public holidays and the New Year period, 09:00–17:00. Outside
those hours nothing is accepted.*

**Fees.** Counting rule first — 「利用日の前日を1日と数えて」, *the day before use
counts as day 1*:

| Timing | Fee |
|---|---|
| 10日前まで (up to D−10) | 無料 (free) |
| 9日前から前日まで (D−9 to D−1) | 料金の50％ |
| 利用当日 (day of use) | 全額 |

> 自動的にキャンセルになることはありません。必ずキャンセルの手続きをしてください。

*Nothing is cancelled automatically; the cancellation must be performed.* And
reductions count as cancellations: 「利用人数の減員、泊数減もキャンセル料の対象と
なります。」

**No waitlist.** The page describes no キャンセル待ち mechanism anywhere — no way
to register interest in a full date and be notified. Polling is the only way to
see a release. (This is an absence in the published procedure rather than a
positive statement that no waitlist exists.)

**Measured consequence.** Free cancellation ends at D−10 and the fee jumps to 50%
the next day, so cancellation pressure concentrates immediately before that
boundary. Measured over this repository's own history — 629 debug dumps from
2026-04-03 to 2026-08-18 plus 20 historical versions of `bookings.json` — **22%
of observed availability episodes begin at exactly D−10, and 57% fall in the
D−9…D−14 band.** The published rule predicts the observed data. Note also that
because post-D−10 cancellations are phone-only within business hours, late
releases can physically only appear on weekdays between 09:00 and 17:00 JST.

---

## 7. The vacancy procedure, and the 30-minute hold

The 【空き照会申込手順】 list has twelve numbered steps. Steps 1–9 are the
application itself, ending at 予約確定; steps 10–12 are post-confirmation
formalities (register every guest, then download the 利用案内). Paraphrased:

1. Enter the WEB申請メニュー on or after the 照会開始日.
2. Choose 「直営・通年・夏季・冬季保養施設(空き照会)」.
3. Choose the facility, clear the image authentication (画像認証 — Cloudflare
   Turnstile in practice), then enter date, nights, guests and room count and
   search.
4. Pick a room from the results. For two nights, pick the same room number as
   the first night.
5. Press 「予約手続きに進む」 and consent to the personal-information terms.
   **The 30-minute clock starts here.**
6. Enter an email address and press 「送信」; a confirmation mail is sent
   automatically from `noreply@mail.its-kenpo.or.jp`.
7. Open the URL in that mail, fill in the required fields, press 「申込する」.
8. Check the details and press 「確認」.
9. The completion screen shows an 申込受付番号 and a completion mail is sent.
10.–12. Register all guests (including under-4s), then download the 利用案内 from
    the URL that arrives afterwards.

Two sentences from that list govern the whole design:

> 部屋を選択し、「予約手続きに進む」ボタンを押し、個人情報の取り扱いに同意します。
> 「予約手続きに進む」ボタンを押して以降、30分以内に申込手続きを完了してください。
> 完了しない場合、選択した部屋は無効となります。

*Select a room, press 「予約手続きに進む」, and consent. From the moment that
button is pressed you have 30 minutes to complete the application; if you do not,
the selected room becomes void.*

> 申込完了画面に遷移し申込受付番号が表示され、申込完了メールが自動送信されます。
> **この時点で申込手続き完了及び予約確定となります。**

*The completion screen appears with a reference number and a completion mail is
sent automatically. **At this point the application is complete and the
reservation is confirmed.***

So: **step 5 takes a 30-minute hold on the room; step 9 is the only thing that
confirms a reservation.** Nothing before step 9 is a booking.

**The booker implements six of the nine.** It performs steps 1–6 — up to and
including the email dispatch — and stops. Steps 7–9 require opening the URL in
the received mail, which nothing in this repository does. Consequently a
"successful" run leaves a 30-minute hold and an unread email, and a human has to
finish it inside that window or the room is released. `bookings.json` records
holds, not reservations.

---

## 8. Stay constraints

All from the 申し込み section of the same page.

> 1施設1回の利用で2泊3日までとなります。

*A single stay at one facility is at most two nights / three days.*

> 同施設で3連泊以上の申し込みはできません。

*Three or more consecutive nights cannot be applied for at the same facility.*
(This one is stated specifically in the 空き照会 notes.)

> 同施設で日程が重なる申し込みはできません(例: 1月1日より2泊、1月2日より1泊の同時
> 申し込みなど)。

*Overlapping applications at the same facility are not accepted* — the example
given is two nights from 1 January together with one night from 2 January.

> 1申込の同一日の「希望部屋数」は最大10部屋までとなります。

*At most 10 rooms per application for the same date.*

> 4歳以上は定員数に含まれます。3歳以下は最大定員プラス1名まで宿泊可能です。

*Guests aged 4 and over count towards the room capacity. Children of 3 and under
may stay up to the maximum capacity plus one.* Child pricing, separately, applies
to ages 4–12.

Also: use is limited to the insured member, dependants and their companions
(外部の方だけのご利用はできません), and the トスラブ 3 cannot be used by minors
alone. Booking two nights as two separate one-night applications counts as two
separate applications, with separate settlement and possibly a room move.

---

## 9. Vacancy search is suspended while the lottery is processed

The front page of `as.its-kenpo.or.jp` carries, when a draw is being processed:

> 現在、保養施設の抽選処理を実施しております。
>
> ご不便をおかけしますが、直営・通年・夏季保養施設の空き照会につきましては、抽選
> 処理が終了するまでしばらくお待ちください。

*A lottery is currently being processed. We apologise for the inconvenience;
please wait until processing finishes before making vacancy enquiries for the
directly-managed, year-round and summer facilities.*

This banner was live on 2026-08-19, two days before the 8/21 results date for
10月分 — so the suspension window is at least the run-up to a 抽選申込結果回答日,
and plausibly the stretch from the close of a 抽選申込受付期間 to its results date.
**Unverified**: the exact start and end of the suspension are not published, and
whether the `/calendar_apply` endpoints return errors or merely empty
availability during it has not been measured. The banner text above is the
detectable signal.

The booker has no notion of this state and polls straight through it.

---

## 10. The 集中アクセス notice — and why it is not the 503s

Notice id=144, dated 2022/08/30, in full:

> システムへの過負荷アラートが発生する集中アクセスが増加しています。
>
> システムセキュリティの観点から、不正と思われるアクセスに対して、一定時間の遮断
> を行うこととしました。
>
> ご利用に際し、ご不便をおかけすることになりますが、ご理解のほどよろしくお願いい
> たします。

*Concentrated access causing system overload alerts is increasing. For system
security reasons, we have decided to block access that appears illegitimate for a
period of time. We apologise for the inconvenience and ask for your
understanding.*

That block is real, it is published, and **this repository has been hit by it three
times**. It is enforced as a 503 carrying a page titled 「セキュリティアラート」:

> ご利用のIPアドレス（17.83.60.43）から、アクセス過多を検知しました。<br>
> システムセキュリティの観点より、<br>
> **約24時間**、一時的にシステムへのアクセスを遮断します。<br>
> お急ぎの場合、別のネットワークからアクセスしてください。

*Excessive access detected from your IP address. For system-security reasons, access
to the system is temporarily blocked for approximately 24 hours. If your need is
urgent, please access from a different network.*

**Measured**, from every non-empty dump recovered from git history:

- **467 of 467** are that page. There is no other captured page type with a title.
- Three episodes: **2026-04-07, 2026-04-12, 2026-08-17**, over three egress IPs
  (17.83.60.43 ×363, 17.83.160.158 ×98, 17.83.60.47 ×6).
- All 467 were served on the scanner's `calendar_get` — the most frequent request,
  not a special one.
- The duration is stated by the site itself: **約24時間**. The detector's reason
  string is `time error`.

So the block is IP-scoped, roughly a day long, and triggered by request rate. One ban
costs a full day of opportunity across *every* target date simultaneously, which makes
request volume the single most important operational constraint on the booker — more
important than detection latency, because a faster poll that earns a ban is strictly
worse than a slower poll that does not.

Two cautions for anyone re-deriving this. `DEBUG_DUMP_KEEP` prunes the dump directory
oldest-first, so the surviving corpus is **survivorship-biased and not a census**; an
earlier analysis inferred from dump timestamps alone that these 503s clustered in
office hours and were therefore ordinary site load, which the bodies flatly contradict.
And the site's own suggestion to use a different network is addressed to blocked
members, not an invitation to rotate egress IPs to defeat the control — the supported
response is to send fewer requests.

---

## What this implies for the booker

Consequences only. No designs.

- **A target date has a last bookable day, and it is not "today".** The cutoff is
  D−4 or D−10 depending on the facility. `_future_dates()` only drops dates that
  are strictly past, so the scanner spends requests on dates that no facility can
  still accept — and on a D−10 facility it does so for the last nine days of that
  date's life.
- **The cutoff is per-facility, so it is a filter on the hotel list, not just on
  the date.** Inside D−10 but outside D−4, only the D−4 classes can be applied for
  at all; the 通年 majority in the hotel list is unreachable. Nothing in the code
  knows which class a hotel is in.
- **`PRIORITY_HOTELS = ['NAGU']` is a 夏季 facility.** Its cutoff is D−4, and it
  disappears from the roster after 9/30. A configuration whose priority target
  cannot appear at all is worth noticing rather than polling around.
- **There is one instant per month that matters more than all the polling
  combined**: 00:00 JST on the 照会開始日, the 27th of M−2 in FY2026. The current
  design treats every moment identically.
- **The rest of the supply arrives on a schedule too.** D−10 is the modal start of
  an availability episode, and post-D−10 releases can only happen Mon–Fri
  09:00–17:00 JST. A uniform 24/7 poll rate spends most of its requests in bands
  where new supply is structurally impossible.
- **The booker does not complete a booking.** It implements steps 1–6 of nine;
  step 9 confirms the reservation and requires the emailed link. Everything
  `bookings.json` contains is a 30-minute hold, and every hold the code abandons
  after step 5 is a room removed from circulation for half an hour — including
  from the booker's own next scan, which then reads it back as
  「空き部屋がございません」.
- **The 30-minute hold sets a floor on any per-hotel retry interval.**
  `HOTEL_HOLD_COOLDOWN = 1800` matches it; anything shorter stacks a second hold
  on the same facility.
- **Two nights means the same room number on both nights, and a month-end stay is
  governed by the second night's month.** The current flow only ever books one
  night, so neither rule is currently reachable — but the max-2-nights and
  no-overlap rules mean that repeated applications for adjacent dates at one
  facility can be rejected as overlapping.
- **The service can be switched off underneath the booker**, during lottery
  processing, with only a front-page banner to say so. The scanner has no way to
  distinguish "suspended" from "nothing available", so a suspension looks like a
  quiet month.
- **`MAX_BOOKINGS_PER_DATE` is a hedge, not waste, while step 9 is manual.**
  Several applications for one date are several chances that a human opens one of
  the emails within 30 minutes. That calculus changes the moment the emailed leg
  is automated.
- **Volume restraint is justified by notice 144, not by the observed 503s.** The
  503s cluster in office hours regardless of request rate; tuning backoff against
  them as though they were a rate-limit response reads the wrong signal.
