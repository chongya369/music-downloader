#!/bin/sh
# ============================================
# NetEase Cloud Music Downloader Build Script (Linux)
# Prerequisite: Python 3.10+ installed
# First run: chmod +x build_linux.sh
# Note: Linux binaries must be built on Linux (PyInstaller does not support cross-compilation)
# ============================================

set -e

cd "$(dirname "$0")"

echo "============================================"
echo " Build Script (Linux)"
echo "============================================"
echo ""

# Detect Python
PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "[ERROR] Python not found. Please install Python 3.10+"
    exit 1
fi

# Check Python version >= 3.10
PY_VERSION=$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "[ERROR] Python version too old ($PY_VERSION). Need 3.10+"
    exit 1
fi
echo "[INFO] Python $PY_VERSION found"

# Debian/Ubuntu require python3-venv first, otherwise the created venv misses pip
# and the get-pip.py fallback needs network access to bootstrap.pypa.io
if [ -f /etc/debian_version ] && ! "$PYTHON_CMD" -m ensurepip --version >/dev/null 2>&1; then
    echo "[INFO] Missing python3-venv. Attempting to install..."
    apt-get update >/dev/null 2>&1 && apt-get install -y python3-venv || true
fi
if ! "$PYTHON_CMD" -m pip --version >/dev/null 2>&1; then
    echo "[INFO] Missing pip. Attempting to install python3-pip..."
    apt-get update >/dev/null 2>&1 && apt-get install -y python3-pip || true
fi

# Detect unusable venv (e.g. copied from Windows: Scripts/ layout, no bin/python).
# Back it up and recreate instead of mistaking it for a ready environment.
VENV_OK=0
if [ -d ".venv" ]; then
    if .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
        VENV_OK=1
    else
        echo "[WARN] Found unusable .venv (possibly copied from another platform). Backing up and recreating"
        mv .venv ".venv.bak.$(date +%s)"
    fi
fi

# Create virtual environment if not exists
if [ "$VENV_OK" -eq 0 ]; then
    echo "[INFO] Creating Python virtual environment..."
    "$PYTHON_CMD" -m venv .venv || true
    if { [ ! -d ".venv" ] || [ ! -x .venv/bin/python ]; } && [ -f /etc/debian_version ]; then
        echo "[INFO] Trying to install python3-venv and recreate..."
        apt-get update >/dev/null 2>&1 && apt-get install -y python3-venv || true
        "$PYTHON_CMD" -m venv .venv || true
    fi
    if [ ! -d ".venv" ] || [ ! -x .venv/bin/python ]; then
        echo "[ERROR] Failed to create virtual environment"
        echo "       Debian/Ubuntu: apt-get install python3-venv"
        exit 1
    fi
fi

# Ensure pip is available in venv
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "[INFO] pip missing in venv. Installing..."
    if .venv/bin/python -m ensurepip --upgrade 2>/dev/null; then
        echo "[INFO] pip installed"
    else
        if command -v curl >/dev/null 2>&1; then
            curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
        elif command -v wget >/dev/null 2>&1; then
            wget -qO- https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
        else
            echo "[ERROR] Cannot download get-pip.py. Please install pip manually."
            exit 1
        fi
    fi
fi
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "[ERROR] Failed to install pip in venv"
    exit 1
fi

# Ensure build dependencies are present
echo "[INFO] Checking Python dependencies..."
MISSING_DEPS=0
for lib in flask flask_sqlalchemy apscheduler requests mutagen; do
    if ! .venv/bin/python -c "import $lib" >/dev/null 2>&1; then
        echo "[WARN] Missing dependency: $lib"
        MISSING_DEPS=1
    fi
done
if [ "$MISSING_DEPS" -eq 1 ]; then
    echo "[INFO] Installing missing dependencies (first run may be slow)..."
    .venv/bin/python -m pip install -r requirements.txt
    echo "[INFO] Dependencies installed"
else
    echo "[INFO] All dependencies ready"
fi

# Execute the build (explicitly using venv python)
echo ""
echo "[INFO] Starting build..."
.venv/bin/python build.py || { echo "[ERROR] Build failed"; exit 1; }

echo ""
echo "============================================"
echo "[DONE] Output directory: ./dist/music_downloader/"
echo "[INFO] Next steps:"
echo "  1. Put ncm-api-linux-x64 into ./dist/music_downloader/api/"
echo "  2. Run ./dist/music_downloader/music_downloader"
echo "  3. Visit http://localhost:45600"
echo "============================================"
