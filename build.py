"""一键打包脚本：将 source/ 内源代码打包为单个可执行文件（onefile 模式，跨平台）

所有依赖（Python 运行时 + 第三方库 + templates/static 资源）全部打包进
单个可执行文件内，用户双击（Windows）或 ./ 运行（Linux）即可启动。

用法：
    Windows: 双击 build_win.bat（或 python build.py）
    Linux:   执行 ./build_linux.sh（或 python3 build.py）

产物：
    Windows: ./dist/music_downloader/music_downloader.exe
    Linux:   ./dist/music_downloader/music_downloader

注意：PyInstaller 不支持交叉编译，Linux 产物必须在 Linux 上构建，
Windows 产物必须在 Windows 上构建。

打包后产物（dist/NeteaseMusicDownloader/）会连带复制 api/ncm/（esbuild 构建的
API 服务端，排除 node_modules/public），用户安装 Node.js 18+ 后首次运行
自动安装 4 个运行时依赖包即可。
"""

import shutil
import subprocess
import sys
from pathlib import Path

# 脚本所在目录 = 项目根目录（CI 上 checkout 根即 source，本地 source/ 即工作目录）
SOURCE_DIR = Path(__file__).resolve().parent
ROOT = SOURCE_DIR

ENTRY = SOURCE_DIR / "webapp" / "app.py"
ICON = SOURCE_DIR / "icon.ico"
VERSION_FILE = SOURCE_DIR / "version.txt"

APP_NAME = "music_downloader"
# dist / build 放在项目根，避免污染 source
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
SPEC_FILE = SOURCE_DIR / f"{APP_NAME}.spec"
# onefile 下可执行文件输出到 dist/<APP_NAME>/，用户数据也放这里
DIST_APP_DIR = DIST_DIR / APP_NAME

# Windows 下 --add-data 的源;目的分隔符是 ;（Linux 是 :，已跨平台）
SEP = ";" if sys.platform == "win32" else ":"


# ---------------- 平台工具 ----------------

def is_win() -> bool:
    return sys.platform == "win32"


def exe_suffix() -> str:
    """可执行文件后缀：Windows 为 .exe，Linux 无后缀。
    仅用于产物检查与提示文案；PyInstaller --name 不得拼此后缀。"""
    return ".exe" if is_win() else ""


def venv_python() -> Path:
    """项目虚拟环境解释器路径（两平台布局不同）"""
    if is_win():
        return SOURCE_DIR / ".venv" / "Scripts" / "python.exe"
    return SOURCE_DIR / ".venv" / "bin" / "python"


def resolve_python() -> str:
    """返回用于打包的 Python 解释器：优先项目 .venv，找不到回退当前解释器"""
    venv_py = venv_python()
    if venv_py.exists():
        print(f"[INFO] 使用项目虚拟环境: {venv_py}")
        return str(venv_py)
    print(f"[WARN] 未找到 {venv_py}，回退使用当前解释器: {sys.executable}")
    return sys.executable


def ensure_pyinstaller(py: str) -> None:
    """确保打包用 Python 环境已安装 PyInstaller，缺失则安装"""
    check_cmd = [py, "-c", "import PyInstaller"]
    if subprocess.call(check_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
        print("[INFO] PyInstaller 已安装")
        return
    print("[INFO] 未检测到 PyInstaller，开始安装...")
    rc = subprocess.call([py, "-m", "pip", "install", "pyinstaller"])
    if rc != 0:
        print("[ERROR] PyInstaller 安装失败，请手动执行: pip install pyinstaller")
        sys.exit(1)
    print("[INFO] PyInstaller 安装完成")


def clean_old_build() -> None:
    """清理上次的构建产物（dist / build / .spec）"""
    for p in (DIST_DIR, BUILD_DIR, SPEC_FILE):
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    p.unlink()
                except OSError:
                    pass
    print("[INFO] 已清理旧的 build / dist / .spec")


def runtime_hook_path() -> Path:
    """动态生成 PyInstaller runtime hook，返回其路径。

    hook 在解压结束后、主脚本运行前执行，先打印启动提示，
    避免用户看到黑屏误以为卡住。hook 文件在打包时自动写入
    build/ 临时目录，无需单独维护，也不随源码分发。
    """
    hook = BUILD_DIR / "runtime_hook.py"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        'import sys\n'
        '\n'
        'print("============================================")\n'
        'print("  music_downloader 正在启动，请等待...")\n'
        'print("  启动完成后请访问: http://localhost:45600")\n'
        'print("============================================")\n'
        'sys.stdout.flush()\n',
        encoding="utf-8",
    )
    return hook


def run_pyinstaller(py: str) -> None:
    """调用 PyInstaller 执行 onefile 打包（所有依赖打进单个可执行文件）

    py: 用于打包的 Python 解释器（应为项目 .venv，确保依赖完整收集）
    """
    # --add-data: src;dest（onefile 运行时 dest 相对解压根 _MEIPASS）
    # 入口为 webapp/app.py，但 PyInstaller 以顶层模块名 "app" 运行，
    # 其 __file__ 是 _MEIPASS/app.py，故 Flask 的 root_path = _MEIPASS，
    # 模板/静态必须放到 _MEIPASS 下的 templates/ 与 static/。
    add_data = [
        f"{SOURCE_DIR / 'webapp' / 'templates'}{SEP}templates",
        f"{SOURCE_DIR / 'webapp' / 'static'}{SEP}static",
        # version.txt 打进可执行文件内（dest 用 . 放到解压根 _MEIPASS/）
        f"{VERSION_FILE}{SEP}.",
    ]

    # PyInstaller 静态分析难以发现的动态导入
    hidden_imports = [
        "apscheduler.triggers.cron",
        "apscheduler.schedulers.background",
        "apscheduler.executors.pool",
        "apscheduler.jobstores.memory",
        "sqlalchemy.dialects.sqlite",
        "sqlalchemy.dialects.sqlite.pysqlite",
    ]

    # 排除无关大库，减小产物体积
    excludes = [
        "tkinter",
        "pytest",
        "pydoc",
        "doctest",
        "unittest",
        "xmlrpc",
    ]

    # 注意：--name 两平台都保持 APP_NAME，PyInstaller 在 Windows 会自动补 .exe，
    # 此处绝不能拼 exe_suffix()，否则产物会变成 xxx.exe.exe
    cmd = [
        py, "-m", "PyInstaller",
        "--onefile",
        "--name", APP_NAME,
        "--noconfirm",
        "--clean",
        f"--distpath={DIST_APP_DIR}",
        f"--workpath={BUILD_DIR}",
        f"--specpath={SOURCE_DIR}",
        "--paths", str(SOURCE_DIR),
        "--paths", str(SOURCE_DIR / "webapp"),
    ]

    # 图标：仅 Windows 有效（.ico 对 Linux ELF 无作用，避免告警）
    if is_win() and ICON.exists():
        cmd.append(f"--icon={ICON}")

    # runtime hook：解压结束后、主脚本运行前打印启动提示，避免黑屏误以为卡住
    hook = runtime_hook_path()
    cmd.append("--runtime-hook")
    cmd.append(str(hook))

    for d in add_data:
        cmd.append(f"--add-data={d}")

    for h in hidden_imports:
        cmd.append(f"--hidden-import={h}")

    for e in excludes:
        cmd.append(f"--exclude-module={e}")

    # 收集 webapp 包的子模块（代码模块；数据文件已由 --add-data 处理）
    cmd.append("--collect-submodules=webapp")

    # 入口脚本
    cmd.append(str(ENTRY))

    print("[INFO] 执行 PyInstaller 命令:")
    print(" ".join(cmd))
    print("-" * 60)
    rc = subprocess.call(cmd, cwd=str(SOURCE_DIR))
    if rc != 0:
        print("[ERROR] PyInstaller 打包失败")
        sys.exit(1)
    print("-" * 60)
    print("[INFO] PyInstaller 打包完成")


def post_pack() -> None:
    """打包后处理：检查产物、复制 ncm 目录、创建 downloads/ 占位目录

    onefile 模式下可执行文件是单个文件，用户数据（api 源码、数据库、
    下载目录）放在可执行文件同级目录。ncm 目录复制到 exe 同级 api/ncm/，
    运行时 bridge.py 的 get_bridge() frozen 分支指向同一路径。
    - 排除 node_modules / public（node_modules 首次运行时安装；public 是
      NeteaseCloudMusicApi 自带静态站，本项目只用 HTTP 调用 API 路由）
    - 额外复制 ncm/data/*.txt → api/data/（修复 app.js 读 ../data/china_ip_ranges.txt）
    - 生成 package.runtime.json（仅 4 个外部依赖，供运行时 staging 安装）
    """
    if not DIST_APP_DIR.exists():
        DIST_APP_DIR.mkdir(parents=True, exist_ok=True)

    # 产物检查须带平台后缀（Linux 产物无 .exe）
    bin_path = DIST_APP_DIR / (APP_NAME + exe_suffix())
    if not bin_path.exists():
        print(f"[ERROR] 未找到产物: {bin_path}")
        sys.exit(1)

    # 复制 ncm 目录（排除 node_modules / public）
    ncm_src = SOURCE_DIR / "api" / "ncm"
    ncm_dst = DIST_APP_DIR / "api" / "ncm"
    if ncm_src.exists():
        ncm_dst.mkdir(parents=True, exist_ok=True)  # 提前创建目标目录，确保 copy2 的父路径存在
        for item in ncm_src.iterdir():
            if item.name in ("node_modules", "public"):
                continue  # node_modules 首次运行时安装；public 约 14MB 本项目未用
            dest = ncm_dst / item.name
            if item.is_dir():
                shutil.copytree(
                    item, dest,
                    ignore=shutil.ignore_patterns("node_modules", "__pycache__"),
                )
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)  # 双重保险，应对嵌套子目录下的文件
                shutil.copy2(item, dest)
        print(f"[INFO] 已复制 ncm 目录到 {ncm_dst}（排除 node_modules/public，首次运行时自动安装 4 个依赖包）")

        # 修复 china_ip_ranges.txt 路径错位：app.js 读 path.join(__dirname, "../data/china_ip_ranges.txt")
        # __dirname = ncm 目录，即需要文件存在于 api/data/（ncm 的上一级 data 目录）
        data_src = ncm_src / "data"
        data_dst = DIST_APP_DIR / "api" / "data"
        if data_src.exists():
            for f in data_src.glob("*.txt"):
                dest_file = data_dst / f.name
                if not dest_file.parent.exists():
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest_file)
            print(f"[INFO] 已复制 ncm/data/*.txt 到 {data_dst}（修复 IP 表路径）")

        # 生成精简运行时 package.runtime.json：仅 4 个 esbuild 未内联的外部依赖
        # 版本范围与 ncm/package.json 严格对齐（unblockmusic-utils ^0.4.0）
        import json as _json
        runtime_pkg = {
            "name": "ncm-runtime",
            "private": True,
            "dependencies": {
                "@neteasecloudmusicapienhanced/unblockmusic-utils": "^0.4.0",
                "jsdom": "^24.1.3",
                "pac-proxy-agent": "^7.2.0",
                "tunnel": "^0.0.6",
            },
        }
        runtime_pkg_path = ncm_dst / "package.runtime.json"
        runtime_pkg_path.write_text(
            _json.dumps(runtime_pkg, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"[INFO] 已生成精简运行时依赖清单: {runtime_pkg_path}")
    else:
        print("[WARN] 未找到 api/ncm 目录，API 服务端将不可用")

    # 创建空 downloads/ 占位目录
    (DIST_APP_DIR / "downloads").mkdir(exist_ok=True)
    print("[INFO] 已创建空 downloads/ 占位目录")

    # API 使用说明
    readme = DIST_APP_DIR / "api" / "README.txt"
    readme.write_text(
        "首次使用说明:\n"
        "1. 请安装 Node.js 18+ (https://nodejs.org/)\n"
        "2. 首次运行程序时会自动安装 API 依赖（仅 4 个外部包，需要联网）\n"
        "3. 如自动安装失败，请重试启动程序即可（自动安装已保证只装 4 包）\n"
        "4. 极少数无法自动安装时，可手动执行（注意：此手工命令在 api/ncm 下会\n"
        "   连带安装完整 package.json 依赖，仅作兜底）:\n"
        "   cd api\\ncm && npm install --ignore-scripts --omit=dev "
        "@neteasecloudmusicapienhanced/unblockmusic-utils jsdom pac-proxy-agent tunnel\n",
        encoding="utf-8",
    )
    print("[INFO] 已创建 API 使用说明: " + str(DIST_APP_DIR / "api" / "README.txt"))

    # Linux 下给可执行文件加执行权限（Windows 无此概念）
    if not is_win():
        bin_path.chmod(0o755)
        print(f"[INFO] 已设置可执行权限: {bin_path}")


def main() -> None:
    # Windows(cp1252) / Debian 容器(C locale) 默认输出编码非 UTF-8，
    # 强制切换避免中文 print 抛 UnicodeEncodeError
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 60)
    print("[STEP] 一键打包开始 (onefile 模式, 跨平台)")
    print("=" * 60)

    py = resolve_python()
    ensure_pyinstaller(py)
    clean_old_build()
    run_pyinstaller(py)
    post_pack()

    bin_name = APP_NAME + exe_suffix()
    print("=" * 60)
    print("[DONE] 打包完成!")
    print(f"[INFO] 产物目录: {DIST_APP_DIR}")
    print(f"[INFO] 启动程序: {DIST_APP_DIR / bin_name}")
    print("[INFO] 下一步:")
    print(f"  1. 确保已安装 Node.js 18+ (https://nodejs.org/)")
    print(f"  2. 首次运行时会自动安装 API 依赖（仅 4 个外部包，需联网）")
    if is_win():
        print(f"  3. 双击 {bin_name}")
    else:
        print(f"  3. 运行 ./{bin_name}")
    print("  4. 访问 http://localhost:45600")
    print("=" * 60)


if __name__ == "__main__":
    main()
