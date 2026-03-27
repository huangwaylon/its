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

## reCAPTCHA v2 Challenge Types Handled

### 1. Checkbox-only solve
Sometimes clicking the checkbox is sufficient. The solver detects this by checking for `.recaptcha-checkbox-checked` after clicking.

### 2. Static image grid (3x3 or 4x4)
Each tile is an independent photo. Select all tiles matching the prompt object.
- Common prompts: バス (buses), 自転車 (bicycles), 信号機 (traffic lights), 自動車 (cars)

### 3. Single-image divided grid (typically 4x4)
One large photograph divided across all grid cells. Select every cell containing any part of the target object.
- Common prompts: 消火栓 (fire hydrants), オートバイ (motorcycles), 階段 (stairs), 横断歩道 (crosswalks)

### 4. Dynamic replacement challenges (3x3)
After selecting matching tiles, they fade to white and get replaced with new candidate images. Must keep selecting until no more matches remain, then click verify.
- Detected by observing `.rc-imageselect-dynamic-selected` class (animating tiles) and `img.rc-image-tile-11` (replacement images)

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

### Strategy 1: Full Grid Screenshot (preferred)

Screenshots the `.rc-imageselect-challenge` area and sends it to the vision model as a single JPEG image. The model sees all tiles simultaneously and returns a JSON array of matching tile numbers.

- **Speed:** ~11-13 seconds when it works (one API call)
- **Key optimization:** Uses `/no_think` suffix to disable qwen3's chain-of-thought mode, which otherwise consumed all `num_predict` tokens on `<think>` tags and returned empty answers
- **JPEG compression:** Screenshots are converted from PNG to JPEG (quality 85) via Pillow before base64 encoding. This reduces payload from ~150-400KB to ~20-50KB, critical for ollama stability (see Known Issues).

```
"There are two possible layouts:
  A) Each tile is a separate independent photo.
  B) One single large photo is divided across the grid cells.
For (A), select tiles whose photo matches the object.
For (B), select every cell that contains ANY part of the object,
even if only a small portion is visible in that cell."
```

### Strategy 2: Individual Tile Classification (fallback)

Screenshots each tile individually and asks the model a binary yes/no question per tile. Used when Strategy 1 returns empty.

- **Speed:** ~22-31 seconds for 9 tiles, ~45-55s for 16 tiles (parallelized)
- **Parallelization:** All tiles screenshotted sequentially (Playwright requirement), then all vision calls fired simultaneously via `asyncio.gather()`
- **Concurrency limit:** `asyncio.Semaphore(3)` prevents overwhelming ollama
- **Weakness:** Poor at 4x4 divided images — individual tile fragments (e.g., a section of a staircase) often don't look like the target object

### Dynamic Round Strategy

After initial tile selection, dynamic replacement rounds use a **full-grid re-screenshot** approach (same as Strategy 1) rather than per-tile classification. This is faster (one API call vs N calls) and provides better context since the model can see which tiles have checkmarks vs. new images.

- Limited to **3 rounds** max to prevent timeout
- The prompt tells the model which tiles are already selected and asks only about new/unselected tiles

## Performance Results

### Successful solve on ITS site (attempt 2, "自転車" / bicycles):

| Phase | Duration |
|-------|----------|
| Browser launch + ITS navigation | 16s |
| Checkbox click + challenge load | 6s |
| Attempt 1 (自動車, failed) | ~95s |
| Attempt 2 Strategy 2 classification | 31s |
| Click 4 tiles | 1.3s |
| Dynamic round 1 (full grid, found [1,6]) | 28s |
| Dynamic round 2 (full grid, empty) | 17s |
| Click verify + confirm | 4s |
| Click 次へ + page load | 4s |
| **Total session** | **~230s** |

### Key timing observations:
- Strategy 1 (full grid, when it works): **~11-13s** per call
- Strategy 2 individual tile (parallel, 9 tiles): **~22-31s**
- Strategy 2 individual tile (parallel, 16 tiles): **~45-55s**
- Dynamic round (full grid re-screenshot): **~13-28s** per round
- ollama `qwen3-vl:8b` per single tile: **~2-10s**
- Per-tile classification includes per-tile debug logging (YES/no + raw model output)

## Key Technical Decisions

### 1. curl subprocess over httpx
ollama frequently sends malformed HTTP responses with duplicate `Transfer-Encoding` headers, especially under load. httpx/httpcore's strict HTTP parsing rejects these outright. Switching to `curl` subprocess (which tolerates malformed headers) eliminated the class of `RemoteProtocolError` failures. Payloads are written to temp files (`-d @/tmp/file.json`) rather than piped via stdin to avoid truncation with large base64 images.

### 2. JPEG compression for ollama payloads
Playwright screenshots are PNG (110-294KB for a challenge grid, 6-15KB for individual tiles). Base64 encoding inflates this further. ollama drops connections (`curl exit 56`) when processing large payloads. Converting to JPEG quality 85 via Pillow reduces payload by 5-10x, making the API calls more reliable. This was the key fix that made dynamic round full-grid analysis work.

### 3. `/no_think` for Strategy 1
qwen3-vl:8b defaults to chain-of-thought mode. For complex prompts (full grid with layout instructions), the model spent all 300 `num_predict` tokens on `<think>` reasoning and produced empty output. Appending `/no_think` to the prompt disables this, yielding direct JSON array responses like `[1, 4, 5]`.

### 4. Semaphore-based concurrency limiting
`asyncio.Semaphore(3)` limits concurrent ollama requests, balancing throughput with server stability.

### 5. Scroll-into-view before clicking
After multiple challenge attempts, the captcha iframe can scroll out of the viewport (observed bounding box y=-9776). The solver now scrolls the bframe iframe into view before clicking tiles to prevent `Element is outside of the viewport` crashes.

### 6. Observation-based dynamic detection
Instead of relying solely on prompt text parsing to detect dynamic challenges, the solver observes whether `img.rc-image-tile-11` or `.rc-imageselect-dynamic-selected` actually appear after clicking tiles. This catches dynamic behavior regardless of prompt language or wording.

### 7. `force=True` clicks
Tile clicks use `force=True` to bypass Playwright's actionability checks. The reCAPTCHA tiles are inside cross-origin iframes with potential overlay elements; forced clicks ensure registration.

### 8. Fail-fast retries
Vision API calls retry at most once (2 attempts total) instead of 3 to avoid wasting time on persistent failures. With 8 max challenge attempts, it's better to move on to a new challenge than retry a failing API call.

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
  - `dynamic_round_N.png` — full grid screenshots during dynamic rounds

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_URL` | `http://localhost:11434` | ollama API endpoint |
| `OLLAMA_MODEL` | `qwen3-vl:8b` | Vision model to use |
| `DEBUG_DIR` | `/tmp/captcha_debug` | Screenshot output directory |
| `max_attempts` | `8` | Max challenge retries before giving up |
| Semaphore | `3` | Max concurrent ollama requests |
| `num_predict` | `500` (no_think) / `2000` (thinking) | Token limit per vision call |
| JPEG quality | `85` | Compression for ollama payloads |

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

### 1. Strategy 1 `curl exit 56` failures
The full-grid screenshot (even after JPEG compression) consistently triggers `curl exit 56` (connection dropped) on the first call of each attempt. ollama appears to drop connections under sustained load or when processing images above ~30KB. Individual tiles (~3-5KB JPEG) rarely trigger this. This forces fallback to the slower Strategy 2 for initial classification, though dynamic rounds (which reuse the same approach) succeed more often, possibly because ollama has "warmed up" by then.

### 2. Accuracy on divided images (4x4)
The 8B vision model performs poorly on 4x4 divided-image challenges when using Strategy 2. Individual tile fragments (e.g., a cropped section of stairs or a crosswalk) are often not recognizable. Strategy 1 would handle these better but is unreliable due to issue #1.

### 3. Speed
Each attempt takes 50-100s (Strategy 1 failure + Strategy 2 fallback + dynamic rounds). With ~50% first-attempt accuracy, successful solves typically require 2 attempts (~150-230s total).

### 4. Headless mode
reCAPTCHA is more likely to present harder challenges in headless mode. The solver uses `headless=False` by default.

## Potential Improvements

- **Fix Strategy 1 reliability:** Investigate why ollama drops connections for ~30KB JPEG payloads but handles ~5KB tiles fine. Options: resize grid image to smaller dimensions, use a different ollama API endpoint, or pre-warm the model.
- **Larger vision model:** `qwen3-vl:32b` or similar for better per-tile accuracy
- **`OLLAMA_NUM_PARALLEL=4`:** Set this environment variable when running `ollama serve` to enable true server-side parallel inference
- **Audio fallback:** If image challenges prove too difficult, switch to audio challenges via `#recaptcha-audio-button`
- **Prompt caching:** Cache vision results for common tile images to avoid redundant API calls
