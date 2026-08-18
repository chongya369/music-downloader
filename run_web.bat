@echo off
REM ============================================
REM NeteaseCloudMusicApi Downloader Web (Windows)
REM Prerequisite: Python 3.10+ installed
REM DIAGNOSTIC BUILD: STEP markers added, pause removed
REM ============================================

cd /d "%~dp0"
echo [STEP] START

REM Locate Python
set PYTHON_CMD=
where python3 >nul 2>nul
if not errorlevel 1 (
    set PYTHON_CMD=python3
) else (
    where python >nul 2>nul
    if not errorlevel 1 (
        set PYTHON_CMD=python
    )
)
echo [STEP] Locate Python done: PYTHON_CMD=%PYTHON_CMD%

if "%PYTHON_CMD%"=="" (
    echo [ERRO] Python not found. Please install Python 3.10+
    exit /b 1
)

REM Check Python version >= 3.10
echo [STEP] Check Python version...
for /f "delims=" %%i in ('%PYTHON_CMD% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PY_VERSION=%%i
for /f "delims=. tokens=1,2" %%a in ("%PY_VERSION%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)
echo [STEP] PY_VERSION=%PY_VERSION% PY_MAJOR=%PY_MAJOR% PY_MINOR=%PY_MINOR%

set /a PY_VER_ERR=0
if %PY_MAJOR% LSS 3 set PY_VER_ERR=1
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 set PY_VER_ERR=1
if %PY_VER_ERR% EQU 1 echo [ERRO] Python version too old: %PY_VERSION% (need 3.10+)
if %PY_VER_ERR% EQU 1 exit /b 1
echo [INFO] Python %PY_VERSION% found

REM Check system Python has venv and pip
echo [STEP] Check venv/pip...
%PYTHON_CMD% -m venv --help >nul 2>nul
if errorlevel 1 (
    echo [ERRO] venv module missing. Please reinstall Python.
    exit /b 1
)
%PYTHON_CMD% -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [ERRO] pip module missing. Please reinstall Python.
    exit /b 1
)
echo [STEP] venv/pip check done

REM Create virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Failed to create virtual environment
        exit /b 1
    )
)
echo [STEP] venv ready

REM Ensure pip in venv
".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [ERRO] pip missing in venv. Please recreate it.
    exit /b 1
)

REM Check dependencies
echo [STEP] Checking Python dependencies...
set MISSING_DEPS=0

".venv\Scripts\python.exe" -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo [WARN] Missing dependency: flask
    set MISSING_DEPS=1
)

".venv\Scripts\python.exe" -c "import flask_sqlalchemy" >nul 2>nul
if errorlevel 1 (
    echo [WARN] Missing dependency: flask_sqlalchemy
    set MISSING_DEPS=1
)

".venv\Scripts\python.exe" -c "import apscheduler" >nul 2>nul
if errorlevel 1 (
    echo [WARN] Missing dependency: apscheduler
    set MISSING_DEPS=1
)

".venv\Scripts\python.exe" -c "import requests" >nul 2>nul
if errorlevel 1 (
    echo [WARN] Missing dependency: requests
    set MISSING_DEPS=1
)

".venv\Scripts\python.exe" -c "import mutagen" >nul 2>nul
if errorlevel 1 (
    echo [WARN] Missing dependency: mutagen
    set MISSING_DEPS=1
)
echo [STEP] Dependency check done: MISSING_DEPS=%MISSING_DEPS%

if "%MISSING_DEPS%"=="1" (
    echo [INFO] Installing missing dependencies...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [ERRO] Dependency install failed
        exit /b 1
    )
    echo [INFO] Dependencies installed
) else (
    echo [INFO] All dependencies ready
)

REM Check Node.js runtime (API server requires Node.js 18+)
set NODE_OK=1
where node >nul 2>nul
if not errorlevel 1 (
    echo [INFO] Node.js found
) else (
    echo [WARN] Node.js not found! Please install Node.js 18+ from https://nodejs.org/
    echo [WARN] API features will be unavailable without Node.js.
    echo [WARN] First run will auto-install API dependencies if Node.js is present.
    set NODE_OK=0
)
echo [STEP] Node.js check done: NODE_OK=%NODE_OK%

echo.
echo ============================================
echo [STEP] About to start Web service...
echo [INFO] Starting Web service...
echo [INFO] URL: http://localhost:45600
echo [INFO] Press Ctrl+C to stop
echo ============================================
echo.

".venv\Scripts\python.exe" webapp\app.py
echo [STEP] Web service exited with code %errorlevel%