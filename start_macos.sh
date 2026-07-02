#!/bin/bash
# DouyinLiveRecorder macOS 启动脚本
# 解决 Gatekeeper 权限问题

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR"

# 移除隔离属性
echo "正在移除安全限制..."
xattr -cr "$APP_DIR" 2>/dev/null

# 赋予执行权限
chmod +x "$APP_DIR/DouyinLiveRecorder" 2>/dev/null
chmod +x "$APP_DIR/ffmpeg/ffmpeg" 2>/dev/null
chmod +x "$APP_DIR/node/node" 2>/dev/null

# 启动应用
echo "正在启动 DouyinLiveRecorder..."
cd "$APP_DIR"
./DouyinLiveRecorder
