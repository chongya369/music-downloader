@echo off
REM ============================================
REM 网易云音乐下载器 Web 服务一键启动脚本
REM 前置：已安装 Python 3.10+ 和 Node.js
REM ============================================

cd /d "%~dp0"

REM 检查 Python
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装：https://www.python.org
    pause
    exit /b 1
)

REM 检查 Node API 服务是否运行
echo [信息] 检查 NeteaseCloudMusicApi 服务...
python -c "import requests; requests.get('http://localhost:3000', timeout=2)" >nul 2>nul
if errorlevel 1 (
    echo [警告] NeteaseCloudMusicApi 服务未启动！
    echo [信息] 请先双击运行 code/api_server/start.bat 启动 Node 服务
    echo [信息] 或继续启动 Web 服务（功能将受限）
    choice /c YN /m "是否继续启动 Web 服务"
    if errorlevel 2 exit /b 0
)

REM 首次运行创建虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo [信息] 创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
)

REM 检查 Flask 是否已安装（作为依赖是否就绪的标志）
".venv\Scripts\python.exe" -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo [信息] 安装 Python 依赖，请稍候...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
)

echo.
echo ============================================
echo [信息] 启动 Web 服务...
echo [信息] 访问地址: http://localhost:56700
echo [信息] 按 Ctrl+C 可停止服务
echo ============================================
echo.

".venv\Scripts\python.exe" webapp\app.py
pause
