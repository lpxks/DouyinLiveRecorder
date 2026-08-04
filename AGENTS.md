# Repository Guidelines

## Project Structure & Module Organization

`DouyinLiveRecorder` is a Python 3.10+ recorder that polls streaming APIs, extracts stream URLs, and records via FFmpeg.

```
main.py                  # Entry point: orchestration loop and recording
src/                     # Core package
  spider.py              # Platform-specific API calls (returns room data)
  stream.py              # Extracts m3u8/flv stream URLs
  room.py                # Room-info parsing
  ab_sign.py             # Douyin token generation
  proxy.py               # Proxy support for overseas platforms
  http_clients/          # Sync/async HTTP wrappers
  javascript/            # Platform decryption/signing scripts
config/                  # config.ini and URL_config.ini (user-edited)
i18n/                    # gettext translation catalogs (zh_CN)
demo.py                  # Single-platform smoke-test script
ffmpeg_install.py        # FFmpeg download/install helper
downloads/ logs/         # Recordings and runtime logs (gitignored)
```

Keep platform-specific logic in `src/` and decryption helpers under `src/javascript/` rather than inlining them in `main.py`.

## Build, Test, and Development Commands

```bash
uv sync                          # Install dependencies (or pip install -r requirements.txt)
uv run main.py                   # Run the recorder
uv run demo.py                   # Test one platform; edit the platform variable at the bottom
python ffmpeg_install.py         # Install FFmpeg (required for recording)
docker-compose up -d             # Run via Docker
pyinstaller DouyinLiveRecorder.spec   # Build the distributable package (dist/DouyinLiveRecorder/)
```

No linter, formatter, or CI test workflow is configured; run `uv run demo.py` after changes.

## Coding Style & Naming Conventions

- Follow PEP 8: 4-space indentation, ~88-100 character lines, snake_case for functions/variables, UPPER_CASE for constants.
- Keep module and class names descriptive (`spider.get_douyin_app_stream_data`).
- Preserve the existing bilingual (Chinese/English) comment style.
- Add new platform support as a function in `spider.py` returning consistent room/stream dicts.

## Testing Guidelines

There is no automated test suite. The de-facto test is `demo.py`: edit the platform variable, run `uv run demo.py`, and confirm the stream URL is extracted. When adding platform support, add an entry to `demo.py` and verify it before opening a PR. Bug fixes should include a description of the failure scenario they address.

## Commit & Pull Request Guidelines

History mixes English and Chinese, with `fix:`-style prefixes increasingly preferred. Use short imperative subjects (e.g., `fix: 修复URL去重逻辑`) with a body explaining what and why. One logical change per commit.

Open PRs against `master` (or `dev`) and fill out the template: title, description, change type, testing performed, and checklist (no new warnings, tests/docs updated, style followed).

## Agent-Specific Instructions

[CLAUDE.md](CLAUDE.md) contains architecture, data-flow, and command guidance for AI coding agents; consult it alongside this file. Never commit API tokens, proxy credentials, or user room URLs from `config/`.
