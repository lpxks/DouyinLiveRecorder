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

**`url_parser.py`** (~330 lines) — `URL_config.ini` line parsing. `parse_line()` splits a line into a `LineInfo` (quality, url, name, has_legacy_priority, spec, duration, duration_raw); `split_url_line()` is the legacy 5-tuple wrapper (still used by `find_comment_target_line`). The check-interval marker (any single letter `A`–`Z` case-insensitively, OR a bare number of seconds in `1..MAX_INTERVAL_SECONDS`), the segment marker (`dur: <number><h|m|s>`, `dur: off|0` to disable, stripped so it never leaks into the anchor name) and the legacy `,优先: 是` marker are all stripped BEFORE field splitting so their commas can't break alignment, and the URL field is located positionally (`contains_url`) so lines without a quality prefix still parse. `dedup_marker_action()` decides how the interval marker survives URL dedup (a marker on a commented line is a "pause annotation" — kept, never activated); `resolve_check_interval()` maps a letter through the configured table / uses an inline number directly (missing, unconfigured letters and out-of-range numbers → default level C, never below 1s); `resolve_jitter()` keeps the old bands (A ±1s, others ±5s; inline numbers use ±1 up to `JITTER_SMALL_INTERVAL`, then ±5); `interval_spec_warning()` / `duration_spec_warning()` return the "fell back to system config" hints that main.py prints once per marker; `find_writeback_index()` locates the active (non-comment) line for anchor-name writeback, falling back to the annotation comment only when no active line matches.

**`ffmpeg_guard.py`** — FFmpeg output takeover and abnormal-process guards. `FfmpegOutputGuard(process, log_path, storm_path, ...)` spawns a reader thread over `stdout=PIPE` (split on `\r`/`\n` because FFmpeg stats use carriage returns), writes lines to `logs/ffmpeg.log` rate-limited to 50 lines/s (5 MB rotation), and counts lines per second. Two independent kill paths: (1) log storm — `LOG_STORM_LINES_PER_SEC` (300) exceeded for `LOG_STORM_SECONDS` (2) consecutive seconds → dump the last 200 lines to `logs/ffmpeg_storm.log` and `process.kill()`; (2) empty output — `output_bytes()` still 0 after `EMPTY_OUTPUT_TIMEOUT` (120 s) → kill. Storm/empty kills make `check_subprocess()` return False so the normal stream-interruption retry (4–5 s) takes over. Rationale: FFmpeg 6.1 can loop forever on "read error → log → retry" when a FLV response body is truncated (`FFERROR_REDO` in `ff_read_packet()`, plus `avio_feof()` resetting and refilling), which floods the terminal (the process used to inherit stdout) and pegs a core; `-nostats` is passed to keep stats out of the pipe.

**`src/file_organizer.py`** — Periodic recording-file organizer (feature off by default). `run_once(settings)` runs four steps in order: flatten video/audio files from every subdirectory of the recording root into the root; remove the now-empty directories bottom-up (root itself never removed); delete root-level media whose duration is below `min_duration_seconds` (0 = off) OR size is below `min_size_bytes` (0 = off; the size check runs first and skips ffprobe); archive root-level media into `target_dir` (empty = skip; a target nested inside the root is excluded from scans, so it can't be re-flattened). `update_settings()` swaps an immutable `OrganizerSettings` snapshot under a lock and lazily starts the single daemon thread (`name='file-organizer'`); the loop ticks every second and re-reads the snapshot, so config edits apply within one main-loop round (~3 s) and the thread never touches `configparser`. Safety: a ref-counted busy registry (`register_busy_dir`/`register_busy_file`, used by `check_subprocess()` for the ffmpeg output directory and by `converts_mp4`/`converts_m4a`/`segment_video`/`direct_download_stream` for the files they touch) plus an mtime grace period (`写入宽限期(秒)`) keep files that are still being written out of the way; directories that `flatten_media()` just emptied are tracked in `touched_dirs` so they can be deleted in the same cycle despite their fresh mtime, while freshly created (not yet written) recording directories are preserved. Duration probing prefers `ffprobe` (PATH, or next to ffmpeg for Windows builds), falls back to parsing `Duration:` from `ffmpeg -i` stderr, and keeps the file when the duration is unknown. Moves use `shutil.move` (cross-device copy), resolve collisions with `_1`/`_2` suffixes, skip symlinks, and only process `MEDIA_EXTENSIONS` — subtitle/sidecar files stay where they are, so their folders never become empty.

**`src/tiktok_fetch.py`** — TikTok fetch strategy. `get_stream_data(live_stream, url, video_quality)` tries the mobile API (`fetch_app_stream_data`) first, retrying up to `APP_API_ATTEMPTS` (3) times with `APP_API_BACKOFF` (1s, 2s), then falls back to the web page (`fetch_web_stream_data`). Rationale: TikTok serves a SlardarWAF JS challenge page (~1.5 KB, no `SIGI_STATE`) to datacenter/shared IPs, so the web path raises a misleading `ConnectionError("Please check if your network can access the TikTok website normally")`; the mobile API returns 200 JSON roughly half the time through the same proxy, so retries make it reliable (measured ~5/6 end-to-end). A valid `data` payload — including a not-live room (`is_live=False`) — returns immediately and is never retried; only transport/parse failures retry; a semantic error from `fetch_stream_url` (e.g. age gate) propagates without retry. `get_tiktok_stream_data()` in `spider.py` is a thin wrapper. Recommend C-level (90 s) polling for TikTok links to limit 429s.

**`retry.py`** — `retry_delay(retry_index)` returns the randomized wait before a stream-interruption retry: 4–5s for retries 0–4, 5–6s for retries 5–9 (retry count capped by the `直播断流重试次数` config).

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

- `config/config.ini` — All settings: recording format (ts/mkv/flv/mp4), quality, save paths, proxy, segmenting, push channels, per-platform cookies, account credentials for platforms that need login (SOOP, FlexTV, PopkonTV, TwitCasting), and `[检查时长等级]` (`<letter>等级检查时长(秒)`, defaults A/B/C = 5/30/90, floor 1) for the per-link check-interval letters — users may add more letters (`D等级检查时长(秒) = 120`) and `read_level_intervals()` discovers them dynamically (configparser lowercases option names; reads use the same case-insensitive lookup, so the on-disk case does not matter). `循环时间(秒)` and `[优先监控]` were removed — the level system replaced them. Missing sections/options are auto-created on first read via `read_config_value()` (that rewrite normalizes the whole file and drops comments).
- `config/URL_config.ini` — Live room URLs, one per line. Prefix a line with `#` to skip it. Prepending a quality label (e.g., `超清，https://...`) sets per-room quality. URLs can also specify an anchor name with a second comma (e.g., `原画，https://...，主播名`). A trailing interval marker sets that room's polling interval: either a single letter (`A`…`Z`, looked up in `[检查时长等级]`, e.g. `https://live.douyin.com/277869507858,主播: MMA盼盼,A`) or a bare number of seconds (`…,45`); an anchor name that is a single letter or a bare number must be written as `主播: A` / `主播: 45`. A `dur:` marker overrides that room's segment length (`…,dur: 30m` / `dur: 1.5h` / `dur: 90s`; `dur: off` or `dur: 0` disables segmenting for that room; a unit is mandatory). When several markers appear on one line the last one wins. The legacy `,优先: 是` marker is auto-migrated to `,A` on the first parse round. Unknown/unrecognized URLs are auto-commented with `#`. Duplicate lines are removed automatically (first line wins).
- `[文件整理]` section in `config.ini` (optional, auto-created on first read, feature off by default) — `自动整理是否开启(是/否)` (default 否), `整理间隔(分钟)` (1–10080, default 60), `整理源目录(留空则用直播保存路径)`, `归档目标目录(留空则不归档)`, `删除小于该时长的视频(秒)` (0 = off), `删除小于该大小的视频(MB)` (default 2, 0 = off; size is checked first and needs no probing), `写入宽限期(秒)` (default 120). The main loop reads them every round and pushes an `OrganizerSettings` snapshot into `file_organizer.update_settings()`; the organizer thread only starts on the first `enabled = 是`, so a disabled install has no extra thread. The first read triggers the usual `config.ini` rewrite (comments dropped) because the options were missing.

### Key patterns

- **Async under sync**: Spider functions are `async` and called via `asyncio.run()` from threaded `start_record()` loops. Each recording session is a thread, and platform data fetching uses a `threading.Semaphore` to limit concurrent API calls.
- **Dynamic throttling**: `adjust_max_request()` monitors error rate in a sliding 10-second window and adjusts the semaphore count up/down, between 1 and the configured max.
- **Check-interval markers (letter or seconds)**: each `URL_config.ini` line may carry a marker — a single letter mapped through `[检查时长等级]`, or a bare number of seconds (1~86400). Unmarked links use the default level C. The `interval_spec_by_url` dict is rebuilt from scratch on each config parse round and swapped in atomically (`interval_spec_by_url = new_interval_spec_by_url`) so worker threads never see a half-built dict; worker threads read it each round, so editing a marker takes effect on the next poll. Jitter: A ±1s, other letters ±5s, inline seconds ±1 up to 5s then ±5. Unknown letters / out-of-range numbers fall back to the default level with a one-time warning (`warned_interval_specs` keeps the parse loop from spamming it every round). A marker on a commented line is a pause annotation: it survives dedup but never activates polling, and it is NOT inherited by an unmarked active line for the same URL (the active line's absent marker wins) — see `dedup_marker_action`. Legacy `,优先: 是` lines are rewritten to `,A` (explicit marker wins) during the parse round.
- **Per-link segment duration (`dur:`)**: `duration_spec_by_url` maps URL → seconds (0 = disabled) and is rebuilt/swapped atomically exactly like `interval_spec_by_url`. `resolve_record_segment()` (main.py) is the single decision point: no marker → global `分段录制是否开启` + `视频分段时间(秒)`; positive marker → segmentation on with that length EVEN IF the global switch is off; `dur: off|0` → segmentation off for that link even if global is on. `start_record()` computes `seg_enabled`/`seg_seconds` once per recording session and every ffmpeg branch (`_%03d` naming, `-segment_time`, `segment_video()` fallbacks) plus `check_subprocess()` (new `segment_enabled` param, also forwarded to custom scripts) must use those locals — never the globals, or a per-link override silently reverts to the global setting. A `dur:` on a commented line is an annotation and is never activated; on duplicate URLs the first line wins and a missing `dur` is merged from the dropped line (`duration_mark()`), mirroring the interval `merge` behavior. Malformed values (missing unit, `1h30m`, out of 1..604800s) are stripped from the field list (so they never leak into the anchor name), reported once via `warned_duration_specs`, and fall back to the system config. Changing a marker only affects the NEXT recording start — an already-running ffmpeg keeps its `-segment_time`.
- **FFmpeg subprocess**: Recording uses `subprocess.Popen` with stdin control. On Windows, sending `b'q'` to stdin gracefully stops FFmpeg; on Linux, `SIGINT`. Post-processing (segmenting, MP4 conversion) uses `subprocess.check_output`. stdout/stderr are piped through `FfmpegOutputGuard` (never inherited by the terminal): it rate-limits `logs/ffmpeg.log`, kills the process on a log storm (≥300 lines/s for 2 s) or after 120 s with zero bytes written, and dumps the last 200 lines to `logs/ffmpeg_storm.log` on a storm. Keep `stdout=subprocess.PIPE` and the guard wired in `check_subprocess()` — reverting to an inherited stdout reintroduces the FFmpeg 6.1 log-storm CPU spike.
- **Recording-file organizer**: `src/file_organizer.py` runs on its own daemon thread (started lazily by `update_settings()` the first time `[文件整理] 自动整理是否开启 = 是`) and executes flatten → remove empty dirs → delete short → archive. Any code that writes or converts a recording must bracket the path: `check_subprocess()` registers the ffmpeg output DIRECTORY (segment templates `_%03d` keep creating files there), and `converts_mp4()` / `converts_m4a()` / `segment_video()` / `direct_download_stream()` register the file they touch — otherwise the organizer may move a file out from under ffmpeg. Never rely on the mtime grace window alone for in-process work; it only covers writers outside this process. The archive target may live inside the recording root — it is excluded from walks, empty-directory cleanup, and deletion, so files can't bounce back and forth.
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
