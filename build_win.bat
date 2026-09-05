@echo off
REM ============================================
REM Build (Windows) - onefile mode
REM Prerequisite: Python 3.10+ installed
REM Run this script to pack source/ into single exe
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
    pause
    exit /b 1
)

REM Check Python version >= 3.10
echo [STEP] Check Python version...
for /f "delims=" %%i in ('%PYTHON_CMD% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PY_VERSION=%%i
for /f "tokens=1 delims=." %%a in ("%PY_VERSION%") do set PY_MAJOR=%%a
for /f "tokens=2 delims=." %%b in ("%PY_VERSION%") do set PY_MINOR=%%b
set /a PY_VER_ERR=0
if %PY_MAJOR% LSS 3 set /a PY_VER_ERR=1
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 set /a PY_VER_ERR=1
if %PY_VER_ERR% NEQ 1 goto pyver_ok
echo [ERRO] Python version too old: %PY_VERSION% (need 3.10+)
pause & exit /b 1
:pyver_ok
echo [INFO] Python %PY_VERSION% found

REM Check and ensure virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [WARN] .venv not found, creating...
    "%PYTHON_CMD%" -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo [ERRO] Failed to create virtual environment
        pause
        exit /b 1
    )
)
echo [INFO] Using virtual environment: .venv

REM Check dependencies in venv
echo [STEP] Check dependencies...
.venv\Scripts\python.exe -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installing dependencies from requirements.txt...
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERRO] Failed to install dependencies
        pause
        exit /b 1
    )
)
echo [INFO] Dependencies ready

REM Run build script
echo [STEP] Running build.py ...
".venv\Scripts\python.exe" build.py
set RC=%errorlevel%
echo [STEP] build.py exited with code %RC%
if %RC% NEQ 0 (
    echo [ERRO] Build failed
    pause
    exit /b %RC%
)

echo.
echo ============================================
echo [DONE] Build succeeded
echo [INFO] See .\dist\music_downloader\
echo [INFO] API binaries already bundled into .\dist\music_downloader\api\
echo ============================================
pause
