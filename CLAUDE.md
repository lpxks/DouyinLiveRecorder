# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Complementary agent guidance lives in `AGENTS.md` (repo root): commit/PR conventions and coding style.

## Commands

```bash
# Install dependencies (uv manages venv + Python version automatically)
uv sync                        # Recommended — reads pyproject.toml
pip install -r requirements.txt  # Alternative

# Run the recorder
uv run main.py                 # or: python main.py

# Test a single platform's stream extraction
uv run demo.py                 # Edit the `platform` variable at the bottom to pick a platform

# Build distributable package (requires PyInstaller + ffmpeg + Node.js on PATH)
pip install pyinstaller
pyinstaller DouyinLiveRecorder.spec   # Output in dist/DouyinLiveRecorder/

# Docker
docker-compose up              # Start (add -d for background)
docker-compose stop            # Stop
docker build -t douyin-live-recorder:latest .
```

No linter or formatter is configured. Python 3.10+ is required (per `pyproject.toml`).

### Testing

Tests are stdlib `unittest` (no test dependencies) under `tests/`. Run all with `uv run python -m unittest discover -s tests`; individual files are also directly runnable (`uv run python tests/test_url_parser.py`). Note: `tests/test_read_config_value.py` never imports `main.py` — it extracts the real `read_config_value` function source via AST and execs it in a bare namespace, so keep that function free of module-level side effects if you refactor it.

## Architecture

This is a multi-platform live streaming recorder that polls ~40 streaming platforms in a loop, spawns FFmpeg to record active streams, and pushes status notifications. The codebase is Python 3.10+ async, single-process with threads for concurrent recording.

### Data flow

```
URL_config.ini → main.py (orchestration loop)
  → spider.py (platform-specific API calls, returns room JSON)
  → stream.py (extracts m3u8/flv stream URLs from JSON)
  → main.py (spawns FFmpeg subprocess to record)
  → main.py (post-processing: segment, convert to MP4, run custom scripts)
  → msg_push.py (push start/stop notifications via DingTalk/Telegram/Email/Bark/ntfy/PushPlus)
```

Note: `StreamCap/` in the repo root is a git-ignored copy of the sibling StreamCap project (see README "相关项目") — not part of this codebase; don't read or modify it as if it were.

### Key modules

**`main.py`** (~2280 lines) — The orchestration hub. Loads `config/config.ini` and `config/URL_config.ini`, then enters a `start_record()` loop per URL on a thread. Handles: URL parsing (platform detection by domain + `if/elif` chain against `platform_host`/`overseas_platform_host` lists), FFmpeg subprocess lifecycle, video post-processing (TS→MP4 conversion, H264 re-encoding, segmenting, subtitle generation), dynamic request throttling based on error rate, and message push routing. Config is read via `read_config_value()` which auto-creates missing sections/options with defaults.

**`url_parser.py`** (~200 lines) — `URL_config.ini` line parsing. `split_url_line()` splits a line into (quality, url, name, has_legacy_priority, level): the check-interval level marker (`A`/`B`/`C`, any position, case-insensitive) and the legacy `,优先: 是` marker are both stripped BEFORE field splitting so their commas can't break alignment, and the URL field is located positionally (`contains_url`) so lines without a quality prefix still parse. `dedup_marker_action()` decides how the level marker survives URL dedup (a marker on a commented line is a "pause annotation" — kept, never activated); `resolve_check_interval()`/`resolve_jitter()` map a level to seconds and jitter (missing/invalid → default level C, never below 1s); `find_writeback_index()` locates the active (non-comment) line for anchor-name writeback, falling back to the annotation comment only when no active line matches.

**`retry.py`** — `retry_delay(retry_index)` returns the randomized wait before a stream-interruption retry: 2–5s for retries 0–4, 5–8s for retries 5–9 (retry count capped by the `直播断流重试次数` config).

**`src/spider.py`** (~3394 lines) — Platform-specific API clients. Each platform gets one or more async functions (e.g., `get_douyin_app_stream_data`, `get_tiktok_stream_data`). They call platform APIs, handle anti-crawler signing (a_bogus for Douyin, custom JS crypto for others), and return normalized dicts with status, stream URLs, title, and anchor name. Some platforms (SOOP, FlexTV, PopkonTV) auto-login and refresh credentials.

**`src/stream.py`** (~445 lines) — Stream URL extraction layer. Takes the dict from spider and maps quality settings (`原画`→`OD`, `超清`→`UHD`, etc.) to actual `m3u8_url`/`flv_url`/`record_url` values. Handles quality fallback when the requested quality stream is unavailable.

**`src/room.py`** — Douyin-specific URL resolution: short link expansion, room ID / sec_user_id extraction, X-bogus signature generation (calls JS via `execjs`), unique ID lookup.

**`src/ab_sign.py`** — Douyin's a_bogus anti-crawler algorithm: SM3 hash mixing, RC4 encryption, base64 encoding. Pure Python implementation.

**`src/utils.py`** — Shared utilities: `Color` class for terminal output, `trace_error_decorator` for function-level error catching, MD5 checksums, cookie dict-to-string conversion, config file read/write/update helpers, emoji removal, disk capacity checking, JSONP parsing, URL query param extraction, and proxy address normalization.

**`src/logger.py`** — Loguru setup with two rotating log sinks: `logs/streamget.log` (DEBUG, no INFO filter) and `logs/PlayURL.log` (INFO-only). Both rotate at 300 KB with 1-day retention.

**`src/http_clients/`** — `async_http.py` wraps `httpx.AsyncClient` for async requests with proxy support. `sync_http.py` wraps `urllib.request`/`requests` for sync code paths. The `abroad` flag switches between proxied and direct connections.

**`src/proxy.py`** — Detects system proxy settings (Windows registry `ProxyEnable`/`ProxyServer`, Linux env vars `http_proxy`/`https_proxy`).

**`src/initializer.py`** — Auto-downloads and installs Node.js on first run (needed by `execjs` to evaluate JS signing scripts).

**`src/javascript/`** — JavaScript files executed via `execjs`: `x-bogus.js` (Douyin signing, ~50KB), `crypto-js.min.js`, and platform-specific scripts (`haixiu.js`, `liveme.js`, `migu.js`, `taobao-sign.js`, `laixiu.js`).

**`msg_push.py`** — Notification push to 7 channels: DingTalk webhook, WeChat (xizhi), Telegram Bot, SMTP email, Bark, ntfy, PushPlus.

**`demo.py`** — Maps 45+ platform names to spider functions via `LIVE_STREAM_CONFIG` dict. Useful for testing a single platform's stream extraction in isolation: change the `platform` variable at the bottom and run `python demo.py`.

**`i18n.py`** — gettext-based i18n with `zh_CN` and `en` locales. Monkey-patches `builtins.print` to translate messages from `src/` modules. Activated when `language` config is `zh_cn`.

**`ffmpeg_install.py`** — Detects if FFmpeg is on PATH, auto-downloads FFmpeg binary for Windows, provides install guidance for Linux/macOS.

**`index.html`** — Standalone web-based M3U8/FLV video player (uses hls.js + flv.js). Useful for playing recorded stream files in a browser.

**`StopRecording.vbs`** — Windows VBScript for gracefully stopping recordings: terminates `ffmpeg.exe` processes first, then terminates the main Python/DouyinLiveRecorder process 10 seconds later.

**`DouyinLiveRecorder.spec`** — PyInstaller spec for one-directory mode. Declares the hidden imports (`src.*`, `msg_push`, `ffmpeg_install`, `i18n`, third-party packages), data files to bundle (JS scripts under `src/javascript/`, i18n locale files under `i18n/`), and packages to exclude (`tkinter`, `numpy`, `pandas`, etc.). The resulting `dist/DouyinLiveRecorder/` directory must also include `ffmpeg` and `node` binaries — those are copied in separately by the CI workflows, not by PyInstaller.

### Configuration

- `config/config.ini` — All settings: recording format (ts/mkv/flv/mp4), quality, save paths, proxy, segmenting, push channels, per-platform cookies, account credentials for platforms that need login (SOOP, FlexTV, PopkonTV, TwitCasting), and `[检查时长等级]` (`A/B/C等级检查时长(秒)`, defaults 5/30/90, floor 1) for the per-link check-interval levels (configparser lowercases option names; reads use the same case-insensitive lookup, so the on-disk case does not matter). `循环时间(秒)` and `[优先监控]` were removed — the level system replaced them. Missing sections/options are auto-created on first read via `read_config_value()` (that rewrite normalizes the whole file and drops comments).
- `config/URL_config.ini` — Live room URLs, one per line. Prefix a line with `#` to skip it. Prepending a quality label (e.g., `超清，https://...`) sets per-room quality. URLs can also specify an anchor name with a second comma (e.g., `原画，https://...，主播名`). A trailing level marker `A`/`B`/`C` sets that room's polling interval (e.g. `https://live.douyin.com/277869507858,主播: MMA盼盼,A`); an anchor literally named `A`/`B`/`C` must be written as `主播: A`. The legacy `,优先: 是` marker is auto-migrated to `,A` on the first parse round. Unknown/unrecognized URLs are auto-commented with `#`. Duplicate lines are removed automatically (first line wins).

### Key patterns

- **Async under sync**: Spider functions are `async` and called via `asyncio.run()` from threaded `start_record()` loops. Each recording session is a thread, and platform data fetching uses a `threading.Semaphore` to limit concurrent API calls.
- **Dynamic throttling**: `adjust_max_request()` monitors error rate in a sliding 10-second window and adjusts the semaphore count up/down, between 1 and the configured max.
- **Check-interval levels (A/B/C)**: each `URL_config.ini` line may carry a level marker; the level maps to a polling interval from `[检查时长等级]` (A=5s, B=30s, C=90s by default) and unmarked links use the default level C. The `level_by_url` dict is rebuilt from scratch on each config parse round and swapped in atomically (`level_by_url = new_level_by_url`) so worker threads never see a half-built dict; worker threads read it each round, so editing a level takes effect on the next poll. Jitter keeps the old behavior (A ±1s, B/C ±5s) via `resolve_jitter()`. A marker on a commented line is a pause annotation: it survives dedup but never activates polling (see `dedup_marker_action`). Legacy `,优先: 是` lines are rewritten to `,A` (explicit level wins) during the parse round.
- **FFmpeg subprocess**: Recording uses `subprocess.Popen` with stdin control. On Windows, sending `b'q'` to stdin gracefully stops FFmpeg; on Linux, `SIGINT`. Post-processing (segmenting, MP4 conversion) uses `subprocess.check_output`.
- **URL comment toggling**: Adding `#` at the start of a URL line in `URL_config.ini` stops monitoring and recording for that room at the next loop iteration without removing the URL.
- **Config auto-backup**: A daemon thread (`backup_file_start()`) checks config file MD5 hashes every 10 minutes and backs up changed files to `backup_config/`, keeping the 6 most recent copies.
- **Platform detection**: `start_record()` uses `record_url.find(<domain>) > -1` in a long `if/elif` chain. `main.py` also maintains `platform_host` and `overseas_platform_host` lists to validate URLs during config loading — unrecognized hosts get auto-commented.
- **Quality names**: Video quality is mapped from Chinese labels: `原画`→OD, `蓝光`→BD, `超清`→UHD, `高清`→HD, `标清`→SD, `流畅`→LD.
- **Proxy per-platform**: The `使用代理录制的平台` config comma-separated list determines which platforms use the proxy; `start_record()` checks if the URL contains a platform name from this list before enabling the proxy.
- **Anchor-name writeback**: When a `URL_config.ini` line has no anchor name, the recording thread reports `record_url|record_url,主播: <name>` to the global `need_update_line_list` (gated by `run_once` per recording session). The main loop consumes this list each iteration and rewrites the config line. The consumer dedups inline — skips when the target line already contains `主播:` (e.g. the anchor name came from an un-commented annotation line) — so repeat reports can't duplicate; keep this check if touching the consumer. When the same URL exists as both an active line and an annotation comment, writeback must target the ACTIVE line — `url_parser.find_writeback_index()` implements this, since rewriting the comment would churn the annotation every round. Rationale: writeback is a lost-update race with the user's editor when the config is edited while running — a one-shot report + one-shot pop would permanently lose the anchor when the editor's save clobbers the write; per-round reporting self-heals it. Pitfall: `update_file()` matches the old line by substring (`old_str in line`) and rewrites the whole file when there's no match — it now logs a `logger.warning` on no-match instead of failing silently — keep the report and consume stages decoupled and don't remove the consumer-side dedup.

### GitHub Actions workflows

- **`.github/workflows/build-release.yml`** — Builds Windows distributable packages (x64, x86/32-bit, ARM64) using PyInstaller on tag pushes (`v*`) and manual dispatch. Each job: installs Python deps + PyInstaller, downloads architecture-matched ffmpeg + Node.js binaries, runs `pyinstaller DouyinLiveRecorder.spec`, assembles the final package (copies ffmpeg + node binaries, default config files), and uploads to the GitHub Release via `softprops/action-gh-release@v2`. Uses `windows-latest` for x64/x86, `windows-11-arm64` for ARM64.
- **`.github/workflows/sync.yml`** — Daily upstream fork sync using `Fork-Sync-With-Upstream-action`. Only runs on forks.
- **`.github/workflows/issue-translator.yml`** — Auto-translates non-English issue bodies/comments to English using `issues-translate-action`.
