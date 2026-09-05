@echo off
REM ============================================
REM NeteaseCloudMusicApi Downloader Web (Windows)
REM Prerequisite: Python 3.10+ installed
REM ============================================

cd /d "%~dp0"
echo [STEP] START

REM ---- Locate Python ----
REM Order: PATH (python3/python/py) - registry (PythonCore) - common folders.
REM PYTHON_CMD always ends up as a full executable path, so it is quoted below.
set "PYTHON_CMD="

for %%P in (python3 python py) do if not defined PYTHON_CMD (
    for /f "delims=" %%F in ('where %%P 2^>nul') do if not defined PYTHON_CMD set "PYTHON_CMD=%%F"
)

REM Drop a candidate that cannot actually run (e.g. the Microsoft Store stub)
if defined PYTHON_CMD (
    "%PYTHON_CMD%" --version >nul 2>nul
    if errorlevel 1 set "PYTHON_CMD="
)

REM Registry: python.org and the Python install manager both write PythonCore
if not defined PYTHON_CMD (
    for /f "tokens=2*" %%a in ('reg query "HKCU\SOFTWARE\Python\PythonCore" /s /v ExecutablePath 2^>nul ^| findstr /i "ExecutablePath"') do set "PYTHON_CMD=%%b"
)
if not defined PYTHON_CMD (
    for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\Python\PythonCore" /s /v ExecutablePath 2^>nul ^| findstr /i "ExecutablePath"') do set "PYTHON_CMD=%%b"
)

REM Common install folders as a last resort
if not defined PYTHON_CMD for /d %%D in ("%LocalAppData%\Python\pythoncore-*-64") do set "PYTHON_CMD=%%D\python.exe"
if not defined PYTHON_CMD for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do set "PYTHON_CMD=%%D\python.exe"
if not defined PYTHON_CMD for /d %%D in ("%ProgramFiles%\Python3*") do set "PYTHON_CMD=%%D\python.exe"

echo [STEP] Locate Python done: PYTHON_CMD=%PYTHON_CMD%

if not defined PYTHON_CMD (
    echo [ERRO] Python not found. Please install Python 3.10+
    echo        from https://www.python.org/downloads/ or the Microsoft Store,
    echo        or set PYTHON_CMD to the full path of python.exe first.
    exit /b 1
)

REM ---- Check Python version ----
echo [STEP] Check Python version...
set "PY_VERSION="
"%PYTHON_CMD%" -V > "%TEMP%\py_version_check.txt" 2>nul
if not errorlevel 1 (
    for /f "usebackq tokens=2" %%i in ("%TEMP%\py_version_check.txt") do set "PY_VERSION=%%i"
)
if not defined PY_VERSION (
    echo [ERRO] "%PYTHON_CMD%" exists but failed to run.
    exit /b 1
)
for /f "tokens=1,2 delims=." %%a in ("%PY_VERSION%") do (
    set "PY_MAJOR=%%a"
    set "PY_MINOR=%%b"
)
echo [STEP] PY_VERSION=%PY_VERSION% PY_MAJOR=%PY_MAJOR% PY_MINOR=%PY_MINOR%

set /a PY_VER_ERR=0
if %PY_MAJOR% LSS 3 set /a PY_VER_ERR=1
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 set /a PY_VER_ERR=1
if %PY_VER_ERR% EQU 1 (
    echo [ERRO] Python version too old: %PY_VERSION% ^(need 3.10+^)
    exit /b 1
)
echo [INFO] Python %PY_VERSION% found

REM ---- Check venv and pip ----
echo [STEP] Check venv/pip...
"%PYTHON_CMD%" -m venv --help >nul 2>nul
if errorlevel 1 (
    echo [ERRO] venv module missing. Please reinstall Python.
    exit /b 1
)
"%PYTHON_CMD%" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [ERRO] pip module missing. Please reinstall Python.
    exit /b 1
)
echo [STEP] venv/pip check done

REM ---- Create virtual environment ----
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    "%PYTHON_CMD%" -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Failed to create virtual environment
        exit /b 1
    )
)
echo [STEP] venv ready

".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [ERRO] pip missing in venv. Please recreate it.
    exit /b 1
)

REM ---- Check dependencies ----
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
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
    if errorlevel 1 (
        echo [ERRO] Dependency install failed
        exit /b 1
    )
    echo [INFO] Dependencies installed
) else (
    echo [INFO] All dependencies ready
)

REM ---- Check built-in API binaries (multi-platform) ----
set NCM_BIN=api\ncm-api-win-x64.exe
set QQ_BIN=api\qqmusic-api-win-x64.exe
set KG_BIN=api\kugou_api_win.exe
set NCM_OK=1
set QQ_OK=1
set KG_OK=1
if not exist "%NCM_BIN%" echo [WARN] Missing API binary: %NCM_BIN% (NetEase feature may be unavailable) & set NCM_OK=0
if not exist "%QQ_BIN%" echo [WARN] Missing API binary: %QQ_BIN% (QQMusic feature may be unavailable) & set QQ_OK=0
if not exist "%KG_BIN%" echo [WARN] Missing API binary: %KG_BIN% (KuGou feature may be unavailable) & set KG_OK=0
echo [STEP] API binary check done: NCM=%NCM_OK% QQ=%QQ_OK% KG=%KG_OK%

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
