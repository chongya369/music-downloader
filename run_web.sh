#!/usr/bin/env bash
# ============================================
# 网易云音乐下载器 Web 一键启动脚本 (Linux)
# 前提：已安装 Python 3.10+ 和 Node.js
# 首次运行请执行：chmod +x run_web.sh
# ============================================

set -e

cd "$(dirname "$0")"

echo "============================================"
echo " 网易云音乐下载器 Web 启动脚本"
echo "============================================"
echo ""

# 检测 Python
PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "[错误] 未检测到 Python，请先安装 Python 3.10+"
    echo "       下载地址：https://www.python.org"
    exit 1
fi

# 校验 Python 版本 >= 3.10
PY_VERSION=$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "[错误] Python 版本过低（当前 $PY_VERSION），需要 3.10+"
    exit 1
fi
echo "[信息] 检测到 Python $PY_VERSION"

# 检测 Node API 服务是否可用
echo "[信息] 检测 NeteaseCloudMusicApi 服务..."
if "$PYTHON_CMD" -c "import requests; requests.get('http://localhost:3000', timeout=2)" 2>/dev/null; then
    echo "[信息] NeteaseCloudMusicApi 服务正常"
else
    echo "[警告] NeteaseCloudMusicApi 服务未启动"
    echo "[信息] 请先启动 Node 服务，否则「发现页」和下载功能不可用"
    echo ""
    read -r -p "是否仍要继续启动 Web 服务？[Y/N] " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "已取消启动"
        exit 0
    fi
fi

# 创建虚拟环境
if [ ! -d ".venv" ]; then
    echo "[信息] 创建 Python 虚拟环境..."
    "$PYTHON_CMD" -m venv .venv
    if [ ! -d ".venv" ]; then
        echo "[错误] 创建虚拟环境失败"
        exit 1
    fi
fi

# 检测 Flask 是否已安装（以是否能 import flask 为标志）
if ! .venv/bin/python -c "import flask" &>/dev/null; then
    echo "[信息] 安装 Python 依赖（首次运行较慢）..."
    .venv/bin/pip install -r requirements.txt
    if ! .venv/bin/python -c "import flask" &>/dev/null; then
        echo "[错误] 依赖安装失败"
        exit 1
    fi
fi

echo ""
echo "============================================"
echo "[信息] 启动 Web 服务..."
echo "[信息] 访问地址: http://localhost:56700"
echo "[信息] 按 Ctrl+C 停止服务"
echo "============================================"
echo ""

.venv/bin/python webapp/app.py
