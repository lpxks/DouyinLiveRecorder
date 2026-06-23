# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for DouyinLiveRecorder.
Build with: pyinstaller DouyinLiveRecorder.spec
Uses one-directory mode for compatibility with execjs + bundled data files.
"""

import sys
from pathlib import Path

# Collect i18n locale files
_i18n_datas = []
_i18n_dir = Path.cwd() / 'i18n'
if _i18n_dir.exists():
    for _f in _i18n_dir.rglob('*'):
        if _f.is_file() and '.gitkeep' not in _f.name:
            _dest = str(_f.parent.relative_to(Path.cwd()))
            _i18n_datas.append((str(_f), _dest))

# Collect JavaScript files for execjs (anti-crawler signing)
_js_datas = []
_js_dir = Path.cwd() / 'src' / 'javascript'
if _js_dir.exists():
    for _f in _js_dir.rglob('*.js'):
        _dest = str(_f.parent.relative_to(Path.cwd()))
        _js_datas.append((str(_f), _dest))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=_i18n_datas + _js_datas,
    hiddenimports=[
        'src',
        'src.spider',
        'src.stream',
        'src.utils',
        'src.logger',
        'src.proxy',
        'src.room',
        'src.ab_sign',
        'src.initializer',
        'src.http_clients',
        'src.http_clients.async_http',
        'src.http_clients.sync_http',
        'msg_push',
        'ffmpeg_install',
        'i18n',
        'execjs',
        'loguru',
        'httpx',
        'Crypto',
        'distro',
        'tqdm',
        'configparser',
        'requests',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'pandas',
        'PIL', 'cv2', 'django', 'flask', 'sqlalchemy',
    ],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DouyinLiveRecorder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    target_arch=None,
)

# One-directory mode: collect all into dist/DouyinLiveRecorder/
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='DouyinLiveRecorder',
)
