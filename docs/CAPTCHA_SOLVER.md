# reCAPTCHA v2 Solver — Technical Writeup

## Overview

`captcha_solver.py` is a fully automated Google reCAPTCHA v2 solver that combines **Playwright** browser automation with **ollama vision AI** (`qwen3-vl:8b`) to solve image challenges on Japanese websites. It integrates with the ITS Calendar Booker for the `as.its-kenpo.or.jp` facility booking system.

Tested against:
- **Production:** `https://as.its-kenpo.or.jp/` (reCAPTCHA Enterprise, no billing)
- **Demo:** `https://2captcha.com/ja/demo/recaptcha-v2-enterprise`

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Playwright  │────▶│ captcha_     │────▶│ ollama          │
│  (Chromium)  │◀────│ solver.py    │◀────│ qwen3-vl:8b     │
│              │     │              │     │ (localhost:11434)│
└─────────────┘     └──────────────┘     └─────────────────┘
   Browser CDP         Orchestrator         Vision Model
   via pipes           async Python         curl subprocess
```

**Why Playwright over pydoll/selenium:**
- Playwright communicates with Chromium via **pipes**, not WebSockets
- This makes it immune to network sandbox restrictions (important for Apple Claude Code environments)
- pydoll uses aiohttp (WebSocket) for Chrome DevTools Protocol, which was blocked by macOS sandbox
- Playwright's `frame_locator()` API handles cross-origin reCAPTCHA iframes natively

**Why curl subprocess over httpx:**
- ollama sends malformed HTTP responses with duplicate `Transfer-Encoding` headers under load
- httpx/httpcore rejects these with `RemoteProtocolError: multiple Transfer-Encoding headers`
- curl tolerates malformed headers and processes them fine
- Payload is written to a temp file (`-d @/tmp/file.json`) to avoid stdin pipe truncation with large base64 images

## ITS Site Integration

The solver automates the full flow on `as.its-kenpo.or.jp`:

```
Homepage → Click "カレンダーから探す" → Captcha page → Solve reCAPTCHA → Click "次へ" → Calendar page
```

### `get_calendar_url()` function

1. Navigate to `https://as.its-kenpo.or.jp/`
2. Click the "カレンダーから探す" link
3. Wait for the captcha page (`/calendar_apply?s=...`)
4. Call `solve_recaptcha(page)` to solve the reCAPTCHA
5. Click the "次へ" (Next) button
6. Extract the resulting calendar URL (`/calendar_apply/calendar_select?s=...`)
7. Save to `calendar_url_cache.txt` for use by `main.py`

### ITS-specific considerations
- The captcha page has a simple layout: checkbox + "次へ" button
- The "次へ" button selector: `input[value="次へ"], button:has-text("次へ"), a:has-text("次へ")`
- The calendar URL contains a unique session token that changes each time
- The reCAPTCHA is Enterprise (no billing), same challenge types as standard v2
- **Session timeout:** The ITS captcha page expires after ~6-7 minutes. All solve attempts must complete within this window.

## reCAPTCHA v2 Challenge Types Handled

### 1. Checkbox-only solve
Sometimes clicking the checkbox is sufficient. The solver detects this by checking for `.recaptcha-checkbox-checked` after clicking.

### 2. Static image grid (3x3)
Each tile is an independent photo. Select all tiles matching the prompt object.
- Common prompts: バス (buses), 自転車 (bicycles), 信号機 (traffic lights), 自動車 (cars), 消火栓 (fire hydrants), 横断歩道 (crosswalks)

### 3. Dynamic replacement challenges (3x3)
After selecting matching tiles, they fade to white and get replaced with new candidate images. Must keep selecting until no more matches remain, then click verify.
- Detected by observing `.rc-imageselect-dynamic-selected` class (animating tiles) and `img.rc-image-tile-11` (replacement images)

### 4. 4x4 grids (auto-skipped)
4x4 challenges (16 tiles) are **automatically skipped** by clicking reload. They are too slow for the 8B model — classifying 16 tiles serially takes 90-200s, during which ollama degrades and drops connections. Reloading to get a 3x3 challenge is much faster.

### 5. "Select more" error recovery
When the solver misses tiles and clicks verify, reCAPTCHA shows "please select all matching images" (一致する画像をすべて選択してください). The solver re-analyzes only unselected tiles and tries again.

## DOM Structure

reCAPTCHA v2 uses two cross-origin iframes:

| Component | Selector | Purpose |
|-----------|----------|---------|
| Anchor iframe | `iframe[title="reCAPTCHA"]` | Contains the checkbox |
| Challenge iframe | `iframe[src*="bframe"]` | Contains the image grid |
| Checkbox | `.recaptcha-checkbox-border` | Click target |
| Solved indicator | `.recaptcha-checkbox-checked` | Appears when solved |
| Prompt text | `.rc-imageselect-desc` | Challenge description |
| Image tiles | `td.rc-imageselect-tile` | Clickable grid cells |
| Selected tiles | `td.rc-imageselect-tileselected` | Already-clicked cells |
| 4x4 grid | `table.rc-imageselect-table-44` | Presence = 4x4 layout |
| Verify button | `#recaptcha-verify-button` | Submit answer |
| Reload button | `#recaptcha-reload-button` | Get new challenge |
| Replacement image | `img.rc-image-tile-11` | New tile after dynamic fade |
| Animating tile | `.rc-imageselect-dynamic-selected` | Tile currently fading |
| Token | `textarea[name="g-recaptcha-response"]` | On host page (not iframe) |

## Tile Classification Strategies

### Strategy 1: Full Grid Screenshot (preferred, single attempt)

Screenshots the `.rc-imageselect-challenge` area, **resizes to max 300px**, and converts to JPEG before sending to the vision model. The model sees all tiles simultaneously and returns a JSON array of matching tile numbers.

- **Speed:** ~11-13 seconds when it works (one API call)
- **Resize to 300px:** Reduces visual tokens for ollama, lowering the chance of `curl exit 56` (connection dropped). At 300px, a 3x3 grid has ~100px tiles — still clear enough for classification.
- **Single attempt only:** Retrying wastes 13-15s per retry. With ollama degrading over time, early retries have the best chance; later ones just burn the session timer.
- **JPEG compression:** PNG→JPEG quality 85 via Pillow reduces payload from ~150-300KB to ~20-25KB base64.
- **`/no_think`:** Disables qwen3's chain-of-thought mode which consumed all tokens on `<think>` tags.

```
"There are two possible layouts:
  A) Each tile is a separate independent photo.
  B) One single large photo is divided across the grid cells.
For (A), select tiles whose photo matches the object.
For (B), select every cell that contains ANY part of the object,
even if only a small portion is visible in that cell."
```

### Strategy 2: Individual Tile Classification (reliable fallback)

Screenshots each tile individually and asks the model a binary yes/no question per tile. Used when Strategy 1 returns empty or fails.

- **Speed:** ~22-27 seconds for 9 tiles (parallelized with semaphore=2)
- **Reliability:** Near 100% — individual tiles are 4-5KB JPEG, which ollama handles consistently
- **Parallelization:** All tiles screenshotted sequentially (Playwright requirement), then vision calls fired via `asyncio.gather()` with `Semaphore(2)`

### Dynamic Round Strategy

After initial tile selection, dynamic replacement rounds use **per-tile classification** for replacement tiles only. Full-grid re-screenshots were tested but proved unreliable (`curl exit 56` increases as ollama processes more images during a session).

- Limited to **5 rounds** max
- Only new replacement tiles (containing `img.rc-image-tile-11`) are classified — unchanged tiles are skipped
- Each round takes ~5-10s for 2-4 replacement tiles

## Performance Results

### Successful solve on ITS site (attempt 4, "消火栓" / fire hydrants):

| Phase | Duration |
|-------|----------|
| Browser launch + ITS navigation | 14s |
| Checkbox click + challenge load | 6s |
| Attempt 1 (横断歩道, 3x3 dynamic, failed) | 82s |
| Attempts 2-3 (自転車, 4x4, auto-skipped) | 4s |
| Attempt 4 Strategy 1 classification | **12s** |
| Click 3 tiles | 1s |
| Dynamic round 1 (per-tile, found [7]) | 8s |
| Dynamic round 2 (per-tile, empty) | 6s |
| Click verify + confirm | 4s |
| Click 次へ + page load | 4s |
| **Total session** | **~148s** |

### Key timing observations:
- Strategy 1 (full grid 300px, when it works): **~12s** per call
- Strategy 2 (per-tile, 9 tiles, semaphore=2): **~22-27s**
- Dynamic round (per-tile, 2-4 tiles): **~5-10s** per round
- 4x4 skip + reload: **~2s** (vs 90-200s if attempted)
- ollama per single tile: **~2-7s** when healthy
- **Session budget:** ~6-7 minutes before ITS session expires

### Why 4x4 challenges are skipped:
- 16 tiles × ~7s average = 112s just for classification
- ollama degrades under sustained load: tiles 10-16 can take 20-70s each due to `curl exit 56` retries
- Total 4x4 attempt: 200-350s, exceeding the session timeout
- Reloading to get a 3x3 costs only 2s

## Key Technical Decisions

### 1. curl subprocess over httpx
ollama frequently sends malformed HTTP responses with duplicate `Transfer-Encoding` headers, especially under load. httpx/httpcore's strict HTTP parsing rejects these outright. Switching to `curl` subprocess (which tolerates malformed headers) eliminated the class of `RemoteProtocolError` failures. Payloads are written to temp files (`-d @/tmp/file.json`) rather than piped via stdin to avoid truncation with large base64 images.

### 2. Image resize + JPEG compression
Playwright screenshots are PNG (110-294KB for a challenge grid, 6-15KB for individual tiles). Two optimizations are applied:
- **JPEG conversion** (quality 85): 5-10x size reduction
- **Resize to 300px max dimension** (Strategy 1 only): Reduces ollama visual token count, improving stability

Size comparison:
| Image | PNG | JPEG 85 | Resized 300px + JPEG 85 |
|-------|-----|---------|-------------------------|
| Challenge grid (386×390) | 238KB | 41KB / 55KB b64 | 21KB / 28KB b64 |
| Individual tile (130×130) | 26KB | 4KB / 6KB b64 | — |

### 3. 30s per-call timeout
Each curl call has `--max-time 30` plus a 35s `asyncio.wait_for` guard. This prevents a single degraded ollama call from blocking the session. If a call exceeds 30s, it's killed and treated as a failure.

### 4. Auto-skip 4x4 challenges
4x4 grids are detected via `table.rc-imageselect-table-44` and immediately reloaded. This saves 90-200s per 4x4 encounter at a cost of ~2s per reload + 1 attempt slot.

### 5. `/no_think` for Strategy 1
qwen3-vl:8b defaults to chain-of-thought mode. For complex prompts (full grid with layout instructions), the model spent all `num_predict` tokens on `<think>` reasoning and produced empty output. Appending `/no_think` disables this.

### 6. Semaphore(2) for concurrency
Two concurrent ollama requests balances throughput with stability. Higher concurrency (3-4) causes ollama to drop connections under load.

### 7. Scroll-into-view before clicking
After multiple challenge attempts, the captcha iframe can scroll out of the viewport (observed bounding box y=-9776). The solver scrolls the bframe iframe into view before clicking tiles.

### 8. Session timeout handling
All Playwright interactions (verify click, tile screenshots) are wrapped in try/except to gracefully handle ITS session expiry (`TargetClosedError`). The solver returns `None` instead of crashing.

## Debug Output

All runs produce:
- Timestamped console logs with `[elapsed_seconds]` prefix
- Per-tile YES/no classification results with raw model output
- Screenshots in `/tmp/captcha_debug/`:
  - `its_captcha_page.png` — ITS captcha page before solving
  - `its_calendar_page.png` — calendar page after solving
  - `challenge_N.png` — each challenge grid
  - `after_click_N.png` — state after tile clicks
  - `strategy1.png` — screenshot sent to Strategy 1
  - `tile_N.png` — individual tile screenshots (Strategy 2)
  - `dynamic_rN_tileN.png` — replacement tile screenshots during dynamic rounds

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_URL` | `http://localhost:11434` | ollama API endpoint |
| `OLLAMA_MODEL` | `qwen3-vl:8b` | Vision model to use |
| `DEBUG_DIR` | `/tmp/captcha_debug` | Screenshot output directory |
| `max_attempts` | `8` | Max challenge retries before giving up |
| Semaphore | `2` | Max concurrent ollama requests |
| `num_predict` | `500` (no_think) / `2000` (thinking) | Token limit per vision call |
| JPEG quality | `85` | Compression for ollama payloads |
| Strategy 1 resize | `300px` max dimension | Reduces visual tokens for ollama |
| curl `--max-time` | `30s` | Per-call timeout |

## Dependencies

```
playwright>=1.58.0    # Browser automation
Pillow                # Image compression (PNG → JPEG)
```

Install:
```bash
uv pip install --python .venv/bin/python playwright Pillow
.venv/bin/python -m playwright install chromium
```

## Usage

### Solve ITS captcha (default)
```bash
.venv/bin/python captcha_solver.py
```

### Test against 2captcha demo
```bash
.venv/bin/python captcha_solver.py --demo
```

### As a module
```python
from captcha_solver import get_calendar_url, solve_recaptcha

# Full ITS flow: navigate + solve + save URL
url = await get_calendar_url()

# Or solve on any page with reCAPTCHA:
from playwright.async_api import async_playwright
async with async_playwright() as pw:
    browser = await pw.chromium.launch(headless=False)
    page = await browser.new_page()
    await page.goto('https://example.com/page-with-recaptcha')
    token = await solve_recaptcha(page)
```

## Known Issues

### 1. Strategy 1 intermittent failures
The full-grid screenshot (even at 300px JPEG) triggers `curl exit 56` (connection dropped) roughly 50% of the time. ollama appears to drop connections when processing images above ~20KB. When Strategy 1 fails, the solver falls back to the reliable per-tile Strategy 2, adding ~20-25s.

### 2. ollama degradation under sustained load
After processing ~15-20 images, ollama becomes progressively unstable — response times increase from 2-7s to 20-70s, and `curl exit 56` errors become more frequent. This is why 4x4 challenges (16 tiles) are skipped and the session timer is critical.

### 3. Model accuracy (~80-90% per tile)
The 8B vision model occasionally misidentifies tiles. This means some attempts fail and the solver needs 2-4 tries. Dynamic challenges (which require multiple correct classifications in sequence) are harder to pass.

### 4. Headless mode
reCAPTCHA is more likely to present harder challenges in headless mode. The solver uses `headless=False` by default.

## Potential Improvements

- **Larger vision model:** `qwen3-vl:32b` for better per-tile accuracy (fewer failed attempts)
- **`OLLAMA_NUM_PARALLEL=4`:** Set this environment variable when running `ollama serve` to enable true server-side parallel inference
- **Audio fallback:** If image challenges prove too difficult, switch to audio challenges via `#recaptcha-audio-button`
- **ollama restart between attempts:** Kill and restart ollama when degradation is detected (response times >15s), to reset GPU state
- **Prompt caching:** Cache vision results for common tile images to avoid redundant API calls
