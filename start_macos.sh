#!/bin/bash
# DouyinLiveRecorder macOS 启动脚本
# 解决 Gatekeeper 权限问题

set -e  # 遇到错误立即退出

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR"
APP_NAME="DouyinLiveRecorder"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}[信息] DouyinLiveRecorder macOS 启动脚本${NC}"
echo ""

# 检查主程序是否存在
if [ ! -f "$APP_DIR/$APP_NAME" ]; then
    echo -e "${RED}[错误] 找不到主程序: $APP_DIR/$APP_NAME${NC}"
    echo "请确保此脚本与 DouyinLiveRecorder 在同一目录"
    exit 1
fi

# 检查 ffmpeg 是否存在
if [ ! -f "$APP_DIR/ffmpeg/ffmpeg" ]; then
    echo -e "${YELLOW}[警告] 找不到 ffmpeg，录制功能可能无法使用${NC}"
fi

# 检查 node 是否存在
if [ ! -f "$APP_DIR/node/node" ]; then
    echo -e "${YELLOW}[警告] 找不到 node，部分功能可能无法使用${NC}"
fi

# 仅移除 DouyinLiveRecorder 目录的隔离属性
echo -e "${GREEN}[步骤 1/3] 移除安全限制...${NC}"
if xattr -cr "$APP_DIR" 2>/dev/null; then
    echo -e "${GREEN}  ✓ 已移除隔离属性${NC}"
else
    echo -e "${YELLOW}  ⚠ 移除隔离属性失败（可能需要管理员权限）${NC}"
    echo -e "${YELLOW}  请手动执行: sudo xattr -cr \"$APP_DIR\"${NC}"
fi

# 赋予执行权限（仅限特定文件）
echo -e "${GREEN}[步骤 2/3] 设置执行权限...${NC}"
chmod +x "$APP_DIR/$APP_NAME" 2>/dev/null && echo -e "${GREEN}  ✓ $APP_NAME${NC}" || true
chmod +x "$APP_DIR/ffmpeg/ffmpeg" 2>/dev/null && echo -e "${GREEN}  ✓ ffmpeg/ffmpeg${NC}" || true
chmod +x "$APP_DIR/node/node" 2>/dev/null && echo -e "${GREEN}  ✓ node/node${NC}" || true

# 启动应用
echo -e "${GREEN}[步骤 3/3] 启动应用...${NC}"
echo ""
cd "$APP_DIR"
exec "./$APP_NAME"
