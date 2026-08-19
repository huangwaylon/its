# ITS Facility Booking — the published rules

The rules of the ITS 保養施設 booking system as published by 関東ITソフトウェア健康保険組合, read on
2026-08-19 from [申し込みから利用まで](https://www.its-kenpo.or.jp/shisetsu/moushikomi.html) — procedures,
deadlines, cancellation terms, and the [申込受付期間一覧表](https://www.its-kenpo.or.jp/shisetsu/moushikomi.html#kikan) —
the [直営](https://www.its-kenpo.or.jp/shisetsu/chokuei/) / [通年](https://www.its-kenpo.or.jp/shisetsu/keiyaku/tsunen/) /
[夏季](https://www.its-kenpo.or.jp/shisetsu/keiyaku/kaki/) roster pages, and
[notice id=144](https://as.its-kenpo.or.jp/column_comments/notice_info?id=144) (2022/08/30). Facts measured
from this repository's history rather than published are labelled **measured**; anything unverified says so.

---

## 1. Two application channels

> 保養施設の申込方法は抽選申込と空き照会申込の2通りあります。

*There are two ways to apply: the lottery (抽選申込) and the vacancy enquiry (空き照会申込).* The booker
automates only the second — `/calendar_apply/*` and `/apply/*` on `as.its-kenpo.or.jp`, the
「直営・通年・夏季・冬季保養施設(空き照会)」 path.

The lottery is neither automated nor automatable: 「定員を超えた場合のご利用は、抽選により決定します。」 — over
capacity, allocation is **by lot**, so there is nothing for a fast client to win. It runs in a window two
months before the stay month (§2), capped at five applications per month per insurance 記号番号
(「同一の記号番号によるひと月の抽選申込回数の上限は5回」), not per email or session, and a win is already
予約確定, already exposed to the fees in §5. **Whatever the draw does not fill is exactly what drops at the
照会開始日** — the residue of a draw the booker never entered is all it ever competes for.

---

## 2. 令和8年度 申込受付期間一覧表 (FY2026 schedule)

令和8年度 runs April 2026 to March 2027; days in the 抽選申込受付期間 and 照会開始日 columns fall in the
month(s) preceding the stay month.

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

Two footnotes to the table, both load-bearing. 「宿泊日が属する月毎の申し込みとなります。」 — *applications are
made per the month the stay date falls in*; the site's own unit of availability is the month, which is why the
scanner runs one thread per month. And 「空き照会は空き状況の照会開始日の午前0時より申し込みを受け付けます。」 —
**vacancy applications are accepted from 00:00 JST on the 照会開始日**, the one instant in the cycle when a
month's whole unfilled residue becomes bookable at once.

In 令和8年度 the 照会開始日 is the 27th of M−2 in all twelve rows. **Do not hard-code that**: the 令和7年度
table on the same page has it on the 25th, 26th or 27th depending on the month — per-year editorial, not a rule.

---

## 3. Application cutoffs — when a date stops being bookable

> 空き照会申込ができるのは直営保養施設(トスラブ3館)・ブルーベリーヒル勝浦・日光千
> 姫物語・熱海後楽園ホテル・夏季保養施設・冬季保養施設は利用日の4日前まで、ブルー
> ベリーヒル勝浦・日光千姫物語・熱海後楽園ホテルを除く通年保養施設は利用日の10日
> 前までとなります。

*Vacancy applications are accepted up to 4 days before use for the directly-managed facilities (the three
トスラブ), ブルーベリーヒル勝浦, 日光千姫物語, 熱海後楽園ホテル and all 夏季/冬季 facilities; up to 10 days
before use for every other 通年 facility.* So there are exactly two cutoffs, **D−4** and **D−10**, and which
applies is a property of the facility. The rosters below are what make that usable — the only place in the
repository where a `SKIP_HOTELS` or `PRIORITY_HOTELS` entry can be classified.

**直営保養施設 (3) — D−4:** トスラブ箱根 ビオーレ, トスラブ箱根 和奏林, トスラブ館山 ルアーナ

**夏季保養施設 (13) — D−4:** グランドメルキュール 伊勢志摩リゾート＆スパ, スパリゾートハワイアンズ モノリスタワー, フルーツパーク 富士屋ホテル,
定山渓 ゆらく草庵, 鎌倉パークホテル, **NAGU勝浦**, プレジャーリゾート伊豆赤沢温泉, 軽井沢マリオットホテル, 蓼科東急ホテル,
高山グリーンホテル, NASPAニューオータニ, アオアヲナルトリゾート, ホテル日航アリビラ

**通年保養施設 (24) — D−10, except the three in bold, which take D−4:** ラビスタ熱海テラス, ホテルハーヴェスト鬼怒川,
鳴子温泉 湯元 吉祥, ホテルハーヴェスト那須, **日光千姫物語**, 草津温泉 ホテルヴィレッジ, 伊香保温泉 ホテル天坊,
ラビスタ横須賀観音崎テラス, ホテルオークラ東京ベイ, リソルの森, **ブルーベリーヒル勝浦**, 和倉温泉 あえの風, ラビスタ富士河口湖,
ホテルハーヴェスト斑尾, ホテルハーヴェスト 旧軽井沢, **熱海後楽園ホテル**, ホテルハーヴェスト伊東, ホテルハーヴェスト浜名湖,
ホテル琵琶レイクオーツカ, ホテル日航プリンセス京都, ホテルハーヴェスト京都鷹峯, ホテルハーヴェスト有馬六彩,
ホテルハーヴェスト南紀田辺, ゆふいん山水館

**冬季保養施設 — D−4, roster not enumerated. Unverified**: no 冬季 roster page was read. 申し込みから利用まで
mentions トスラブ湯沢 in a 冬季 context, and `config.SKIP_HOTELS` holds ホテルハーヴェスト スキージャム勝山
and ラビスタ函館ベイANNEX, on neither the 通年 nor 夏季 roster — presumably 冬季, but that is inference.

**NAGU勝浦 is a summer facility.** `config.PRIORITY_HOTELS = ['NAGU']` targets NAGU勝浦, on the 夏季 roster:
its cutoff is **D−4**, not D−10, and it is not listed for stays past 9/30, per
「夏季保養施設の9/30～2泊と冬季保養施設の3/31～2泊は選択いただけません。」 — *a two-night stay starting 9/30
cannot be selected at a summer facility (nor from 3/31 at a winter one)*. That strictly forbids only the 9/30
two-night selection and the roster page gives no season dates, so the boundary itself is inferred.

**Month-end two-night stays:** 「月の最終日からの2泊をご希望する場合、申し込みが出来るのは2泊目が属する月の
空き状況の照会開始日以降になります。」 *Applications for a two-night stay starting on a month's last day open
only from the 照会開始日 of the month the second night falls in* — a stay is not governed solely by the month
of its first night.

---

## 4. Only dates with vacancy are shown

> 空きが無い日付は表示されません。また日付が表示される場合でもご希望の条件に合わ
> ない場合、部屋は表示されません。

*Dates with no vacancy are not displayed. Even when a date is displayed, no rooms are shown if they do not
match the requested conditions.* Hence the hotel-list page for a date is headed 「{date}に空きがある施設です」
— the header the extractor depends on — and typically lists a handful of facilities rather than the full
roster: the site has already filtered. A date can appear and still yield no room for the guest or room count.

---

## 5. Cancellation — the shape of the secondary supply

Cancellations are the only source of availability between one 照会開始日 and the next, and the rules make them
arrive at predictable times. **Web self-service only until D−10:**

> 利用日の10日前まではWEB上でキャンセルが行えます。メールに記載のキャンセル用URL
> より手続きを行ってください。
>
> 利用日の9日前以降は健康増進サービスセンター(03-5925-5348)へ電話でご連絡くださ
> い。

*Cancellation can be done on the web up to 10 days before use, via the cancellation URL in the confirmation
mail; from 9 days before you must telephone the 健康増進サービスセンター*, whose hours are
「月曜～金曜(祝日・年末年始を除く) 9:00～17:00」 — Mon–Fri excluding public holidays and the New Year period,
and 「時間外は受け付けておりません」. **Fees** count 「利用日の前日を1日と数えて」 (*the day before use is day
1*): free up to D−10, 料金の50％ from D−9 to the day before, 全額 on the day of use. Nothing lapses on its own
(「自動的にキャンセルになることはありません。」) and reductions count as cancellations
(「利用人数の減員、泊数減もキャンセル料の対象となります。」).

**No waitlist.** The page describes no キャンセル待ち mechanism anywhere — no way to register interest in a
full date and be notified, so polling is the only way to see a release. (An absence in the published
procedure rather than a positive statement.)

**Measured consequences**, over 629 debug dumps from 2026-04-03 to 2026-08-18 plus 20 historical versions of
`bookings.json`. Free cancellation ends at D−10 and the fee jumps to 50% the next day, so pressure
concentrates immediately before that boundary:

- **22% of observed availability episodes begin at exactly D−10, and 57% fall in the D−9…D−14 band.** The
  published rule predicts the observed data.
- **Episodes are not seconds long.** Of 104 measured intra-episode gaps, 38 are 20–21 s (the date was still
  available a full scan cycle later) and 66 are ≥1800 s. Design for a half-hour window, not 200 ms.

Because post-D−10 cancellations are phone-only within business hours, late releases can physically only appear
on weekdays between 09:00 and 17:00 JST.

---

## 6. The vacancy procedure, and the 30-minute hold

The 【空き照会申込手順】 list has twelve numbered steps; 1–9 are the application itself, ending at 予約確定,
and 10–12 are post-confirmation formalities. Paraphrased:

1. Enter the WEB申請メニュー on or after the 照会開始日.
2. Choose 「直営・通年・夏季・冬季保養施設(空き照会)」.
3. Choose the facility, clear the image authentication (画像認証 — Cloudflare Turnstile in practice), then enter
   date, nights, guests and room count and search.
4. Pick a room from the results; for two nights, the same room number as the first night.
5. Press 「予約手続きに進む」 and consent to the personal-information terms. **The 30-minute clock starts here.**
6. Enter an email address and press 「送信」; a confirmation mail is sent from `noreply@mail.its-kenpo.or.jp`.
7. Open the URL in that mail, fill in the required fields, press 「申込する」.
8. Check the details and press 「確認」.
9. The completion screen shows an 申込受付番号 and a completion mail is sent.
10.–12. Register all guests (including under-4s), then download the 利用案内 from the URL that arrives after.

Two sentences from that list govern the whole design:

> 「予約手続きに進む」ボタンを押して以降、30分以内に申込手続きを完了してください。
> 完了しない場合、選択した部屋は無効となります。
>
> 申込完了画面に遷移し申込受付番号が表示され、申込完了メールが自動送信されます。
> **この時点で申込手続き完了及び予約確定となります。**

*From the moment 「予約手続きに進む」 is pressed there are 30 minutes to complete the application, or the
selected room becomes void. At the completion screen, with its reference number and automatic mail, the
application is complete and the reservation is confirmed.*

So: **step 5 takes a 30-minute hold on the room; step 9 is the only thing that confirms a reservation**, and
nothing before step 9 is a booking. `bookings.json` records step-5 holds; `reservations.json` records step-9
申込受付番号, and only those are real reservations with a real cancellation liability.

The booker covers all nine: `book_hotels.py` drives steps 1–6 over curl, ending at send_complete;
`confirm_booking.py` runs steps 7–9 off the link in the emailed confirmation; `browser_apply.py` finishes
「申込する」 in real Chrome when curl's POST to `/apply/confirm` is refused. When neither can finish, the run
still ends with the hold taken and the mail sent — the state a human can complete from inside the 30 minutes.
**The site refuses a second application at a facility it already holds a room for**, so nothing here tracks the
hold clock: a stacked re-attempt is rejected by the site rather than becoming a second hold. What a leaked hold
still costs is visibility — the room is out of circulation for half an hour, including from the booker's own
next scan, which reads it back as 「空き部屋がございません」.

---

## 7. Stay constraints

- 「1施設1回の利用で2泊3日までとなります。」 / 「同施設で3連泊以上の申し込みはできません。」 — at most **two
  nights** per stay at one facility.
- 「同施設で日程が重なる申し込みはできません」 — **no overlapping applications** at the same facility (the
  example given is two nights from 1 January plus one night from 2 January), so repeated applications for
  adjacent dates at one facility can be rejected as overlapping.
- 「1申込の同一日の「希望部屋数」は最大10部屋までとなります。」 — at most **10 rooms** per application per date.

---

## 8. Vacancy search is suspended while the lottery is processed

When a draw is being processed the front page of `as.its-kenpo.or.jp` carries 「現在、保養施設の抽選処理を実施
しております。…空き照会につきましては、抽選処理が終了するまでしばらくお待ちください。」 The banner was live on
2026-08-19, two days before the 8/21 results date for 10月分. **Unverified**: the suspension's exact start and
end are not published, and whether `/calendar_apply` then errors or merely reports nothing available has not
been measured. The booker polls straight through it, so a suspension looks like a quiet month.

---

## 9. The 集中アクセス notice — and the 503s that enforce it

Notice id=144 (2022/08/30) announces 「不正と思われるアクセスに対して、一定時間の遮断を行うこととしました」 —
access that looks illegitimate will be blocked for a period of time. That block is enforced as a 503 carrying
a page titled 「セキュリティアラート」, which states the duration itself:

> ご利用のIPアドレス（…）から、アクセス過多を検知しました。<br>
> システムセキュリティの観点より、<br>
> **約24時間**、一時的にシステムへのアクセスを遮断します。<br>
> お急ぎの場合、別のネットワークからアクセスしてください。

*Excessive access detected from your IP address. For system-security reasons, access is temporarily blocked for
approximately 24 hours. If your need is urgent, please access from a different network.*

**Measured**, from every non-empty dump recovered from git history: all 467 are that page, across three
episodes — **2026-04-07, 2026-04-12 and 2026-08-17** — over three egress IPs, every one served on the
scanner's `calendar_get`, simply the most frequent request. The detector's own reason string is `time error`.
So the block is IP-scoped, about a day long, and rate-triggered: one ban costs a full day of every target date
at once, which makes request volume the dominant operational constraint — a faster poll that earns a ban is
strictly worse than a slower poll that does not.

Two cautions. `DEBUG_DUMP_KEEP` prunes the dump directory oldest-first, so the surviving corpus is
**survivorship-biased and not a census**; an earlier analysis inferred from dump timestamps alone that these
503s clustered in office hours and were therefore ordinary site load, which the bodies flatly contradict —
**read the bodies, not the timestamps**. And the site's suggestion to use a different network addresses blocked
members; it is not an invitation to rotate egress IPs. The supported response is to send fewer requests.
