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
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
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

# 检测系统 Python 是否具备 venv 和 pip 模块
if ! "$PYTHON_CMD" -m venv --help >/dev/null 2>&1; then
    echo "[信息] 系统中缺少 venv 模块，尝试安装 python3-venv..."
    apt-get update >/dev/null 2>&1 && apt-get install -y python3-venv || true
fi
if ! "$PYTHON_CMD" -m pip --version >/dev/null 2>&1; then
    echo "[信息] 系统中缺少 pip，尝试安装 python3-pip..."
    apt-get update >/dev/null 2>&1 && apt-get install -y python3-pip || true
fi

# 检测 Node API 服务是否可用
echo "[信息] 检测 NeteaseCloudMusicApi 服务..."
API_AVAILABLE=0
if command -v curl >/dev/null 2>&1; then
    if curl -s --connect-timeout 2 http://localhost:3000 >/dev/null 2>&1; then
        API_AVAILABLE=1
    fi
fi

if [ "$API_AVAILABLE" -eq 1 ]; then
    echo "[信息] NeteaseCloudMusicApi 服务正常"
else
    echo "[警告] NeteaseCloudMusicApi 服务未启动"
    echo "[信息] 请先启动 Node 服务，否则发现页和下载功能不可用"
    echo ""
    printf "是否仍要继续启动 Web 服务？[Y/N] "
    read confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "已取消启动"
        exit 0
    fi
fi

# 创建虚拟环境
if [ ! -d ".venv" ]; then
    echo "[信息] 创建 Python 虚拟环境..."
    "$PYTHON_CMD" -m venv .venv
    if [ ! -d ".venv" ]; then
        # Debian/Ubuntu 系统通常需要单独安装 python3-venv
        if [ -f /etc/debian_version ]; then
            echo "[信息] 尝试安装 python3-venv..."
            apt-get update >/dev/null 2>&1 && apt-get install -y python3-venv
            "$PYTHON_CMD" -m venv .venv
        fi
    fi
    if [ ! -d ".venv" ]; then
        echo "[错误] 创建虚拟环境失败"
        echo "       Debian/Ubuntu 请执行: apt-get install python3-venv"
        exit 1
    fi
fi

# 确保虚拟环境中有 pip（无论 venv 是否刚创建）
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "[信息] 虚拟环境中缺少 pip，正在安装..."
    if .venv/bin/python -m ensurepip --upgrade 2>/dev/null; then
        echo "[信息] pip 安装成功"
    else
        echo "[信息] ensurepip 不可用，尝试通过 get-pip.py 安装..."
        if command -v curl >/dev/null 2>&1; then
            curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
        elif command -v wget >/dev/null 2>&1; then
            wget -qO- https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
        else
            echo "[错误] 无法下载 get-pip.py，请手动安装 pip"
            exit 1
        fi
    fi
fi
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "[错误] 虚拟环境中 pip 安装失败"
    exit 1
fi

# 检测必需依赖是否已安装
echo "[信息] 检查 Python 依赖..."
MISSING_DEPS=0
for lib in flask flask_sqlalchemy apscheduler requests mutagen; do
    if ! .venv/bin/python -c "import $lib" >/dev/null 2>&1; then
        echo "[警告] 缺少依赖: $lib"
        MISSING_DEPS=1
    fi
done

if [ "$MISSING_DEPS" -eq 1 ]; then
    echo "[信息] 安装缺失依赖（首次运行较慢）..."
    .venv/bin/python -m pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "[错误] 依赖安装失败"
        exit 1
    fi
    echo "[信息] 依赖安装完成"
else
    echo "[信息] 所有依赖已就绪"
fi

echo ""
echo "============================================"
echo "[信息] 启动 Web 服务..."
echo "[信息] 访问地址: http://localhost:56700"
echo "[信息] 按 Ctrl+C 停止服务"
echo "============================================"
echo ""

.venv/bin/python webapp/app.py
