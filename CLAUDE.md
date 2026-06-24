# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

There is no test suite, linter, or formatter configured. Python 3.10+ is required (per `pyproject.toml`).

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

### Key modules

**`main.py`** (~2154 lines) — The orchestration hub. Loads `config/config.ini` and `config/URL_config.ini`, then enters a `start_record()` loop per URL on a thread. Handles: URL parsing (platform detection by domain + `if/elif` chain against `platform_host`/`overseas_platform_host` lists), FFmpeg subprocess lifecycle, video post-processing (TS→MP4 conversion, H264 re-encoding, segmenting, subtitle generation), dynamic request throttling based on error rate, and message push routing. Config is read via `read_config_value()` which auto-creates missing sections/options with defaults.

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

- `config/config.ini` — All settings: recording format (ts/mkv/flv/mp4), quality, save paths, proxy, segmenting, push channels, per-platform cookies, and account credentials for platforms that need login (SOOP, FlexTV, PopkonTV, TwitCasting). Missing sections/options are auto-created on first read via `read_config_value()`.
- `config/URL_config.ini` — Live room URLs, one per line. Prefix a line with `#` to skip it. Prepending a quality label (e.g., `超清，https://...`) sets per-room quality. URLs can also specify an anchor name with a second comma (e.g., `原画，https://...，主播名`). Unknown/unrecognized URLs are auto-commented with `#`. Duplicate lines are removed automatically.

### Key patterns

- **Async under sync**: Spider functions are `async` and called via `asyncio.run()` from threaded `start_record()` loops. Each recording session is a thread, and platform data fetching uses a `threading.Semaphore` to limit concurrent API calls.
- **Dynamic throttling**: `adjust_max_request()` monitors error rate in a sliding 10-second window and adjusts the semaphore count up/down, between 1 and the configured max.
- **FFmpeg subprocess**: Recording uses `subprocess.Popen` with stdin control. On Windows, sending `b'q'` to stdin gracefully stops FFmpeg; on Linux, `SIGINT`. Post-processing (segmenting, MP4 conversion) uses `subprocess.check_output`.
- **URL comment toggling**: Adding `#` at the start of a URL line in `URL_config.ini` stops monitoring and recording for that room at the next loop iteration without removing the URL.
- **Config auto-backup**: A daemon thread (`backup_file_start()`) checks config file MD5 hashes every 10 minutes and backs up changed files to `backup_config/`, keeping the 6 most recent copies.
- **Platform detection**: `start_record()` uses `record_url.find(<domain>) > -1` in a long `if/elif` chain. `main.py` also maintains `platform_host` and `overseas_platform_host` lists to validate URLs during config loading — unrecognized hosts get auto-commented.
- **Quality names**: Video quality is mapped from Chinese labels: `原画`→OD, `蓝光`→BD, `超清`→UHD, `高清`→HD, `标清`→SD, `流畅`→LD.
- **Proxy per-platform**: The `使用代理录制的平台` config comma-separated list determines which platforms use the proxy; `start_record()` checks if the URL contains a platform name from this list before enabling the proxy.

### GitHub Actions workflows

- **`.github/workflows/build-release.yml`** — Builds Windows distributable packages (x64, x86/32-bit, ARM64) using PyInstaller on tag pushes (`v*`) and manual dispatch. Each job: installs Python deps + PyInstaller, downloads architecture-matched ffmpeg + Node.js binaries, runs `pyinstaller DouyinLiveRecorder.spec`, assembles the final package (copies ffmpeg + node binaries, default config files), and uploads to the GitHub Release via `softprops/action-gh-release@v2`. Uses `windows-latest` for x64/x86, `windows-11-arm64` for ARM64.
- **`.github/workflows/build-image.yml`** — Builds and pushes multi-arch Docker image (linux/amd64, linux/arm64) to Docker Hub on tag pushes. Also triggerable via manual dispatch.
- **`.github/workflows/sync.yml`** — Daily upstream fork sync using `Fork-Sync-With-Upstream-action`. Only runs on forks.
- **`.github/workflows/issue-translator.yml`** — Auto-translates non-English issue bodies/comments to English using `issues-translate-action`.
