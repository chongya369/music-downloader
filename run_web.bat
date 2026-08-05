@echo off
REM ============================================
REM 网易云音乐下载器 Web 一键启动脚本 (Windows)
REM 前提：已安装 Python 3.10+ 和 Node.js
REM ============================================

cd /d "%~dp0"

REM 检测 Python
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

if "%PYTHON_CMD%"=="" (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    echo        下载地址：https://www.python.org
    pause
    exit /b 1
)

REM 校验 Python 版本 >= 3.10
for /f "delims=" %%i in ('%PYTHON_CMD% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PY_VERSION=%%i
for /f "delims=. tokens=1,2" %%a in ("%PY_VERSION%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)

if %PY_MAJOR% LSS 3 (
    echo [错误] Python 版本过低（当前 %PY_VERSION%），需要 3.10+
    pause
    exit /b 1
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 (
    echo [错误] Python 版本过低（当前 %PY_VERSION%），需要 3.10+
    pause
    exit /b 1
)
echo [信息] 检测到 Python %PY_VERSION%

REM 检测系统 Python 是否具备 venv 和 pip 模块
%PYTHON_CMD% -m venv --help >nul 2>nul
if errorlevel 1 (
    echo [错误] 系统中缺少 venv 模块，请先安装 Python 完整版
    pause
    exit /b 1
)
%PYTHON_CMD% -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [错误] 系统中缺少 pip 模块，请先安装 Python 完整版
    pause
    exit /b 1
)

REM 检测 Node API 服务是否可用
echo [信息] 检测 NeteaseCloudMusicApi 服务...
set API_AVAILABLE=0
powershell -Command "try { Invoke-WebRequest -Uri 'http://localhost:3000' -TimeoutSec 2 -UseBasicParsing >$null; exit 0 } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 (
    echo [信息] NeteaseCloudMusicApi 服务正常
) else (
    echo [警告] NeteaseCloudMusicApi 服务未启动
    echo [信息] 请先启动 Node 服务，否则发现页和下载功能不可用
    echo.
    choice /c YN /m "是否仍要继续启动 Web 服务？"
    if errorlevel 2 exit /b 0
)

REM 创建虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo [信息] 创建 Python 虚拟环境...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
)

REM 确保虚拟环境中有 pip
".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [错误] 虚拟环境中缺少 pip，请重新创建虚拟环境
    pause
    exit /b 1
)

REM 检测必需依赖是否已安装
echo [信息] 检查 Python 依赖...
set MISSING_DEPS=0

".venv\Scripts\python.exe" -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo [警告] 缺少依赖: flask
    set MISSING_DEPS=1
)

".venv\Scripts\python.exe" -c "import flask_sqlalchemy" >nul 2>nul
if errorlevel 1 (
    echo [警告] 缺少依赖: flask_sqlalchemy
    set MISSING_DEPS=1
)

".venv\Scripts\python.exe" -c "import apscheduler" >nul 2>nul
if errorlevel 1 (
    echo [警告] 缺少依赖: apscheduler
    set MISSING_DEPS=1
)

".venv\Scripts\python.exe" -c "import requests" >nul 2>nul
if errorlevel 1 (
    echo [警告] 缺少依赖: requests
    set MISSING_DEPS=1
)

".venv\Scripts\python.exe" -c "import mutagen" >nul 2>nul
if errorlevel 1 (
    echo [警告] 缺少依赖: mutagen
    set MISSING_DEPS=1
)

if "%MISSING_DEPS%"=="1" (
    echo [信息] 安装缺失依赖（首次运行较慢）...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
    echo [信息] 依赖安装完成
) else (
    echo [信息] 所有依赖已就绪
)

echo.
echo ============================================
echo [信息] 启动 Web 服务...
echo [信息] 访问地址: http://localhost:56700
echo [信息] 按 Ctrl+C 停止服务
echo ============================================
echo.

".venv\Scripts\python.exe" webapp\app.py
pause