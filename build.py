"""一键打包脚本：将 source/ 内源代码打包为单个可执行文件（onefile 模式，跨平台）

所有依赖（Python 运行时 + 第三方库 + templates/static 资源）全部打包进
单个可执行文件内，用户双击（Windows）或 ./ 运行（Linux）即可启动。

用法：
    Windows: 双击 build_win.bat（或 python build.py）
    Linux:   执行 ./build_linux.sh（或 python3 build.py）

产物：
    Windows: ../dist/NeteaseMusicDownloader/NeteaseMusicDownloader.exe
    Linux:   ../dist/NeteaseMusicDownloader/NeteaseMusicDownloader

注意：PyInstaller 不支持交叉编译，Linux 产物必须在 Linux 上构建，
Windows 产物必须在 Windows 上构建。

打包后用户需手动把 api 二进制（ncm-api-win-x64.exe / ncm-api-linux-x64）
放到 dist/NeteaseMusicDownloader/api/ 目录。
"""

import shutil
import subprocess
import sys
from pathlib import Path

# 脚本所在目录 = source 目录
SOURCE_DIR = Path(__file__).resolve().parent
# 项目根目录（source 的上一级）
ROOT = SOURCE_DIR.parent

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


def api_binary_name() -> str:
    """内置 API 二进制文件名，须与 core/node_bridge.py 的 _BINARIES 保持一致"""
    return "ncm-api-win-x64.exe" if is_win() else "ncm-api-linux-x64"


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
    """打包后处理：检查产物、创建空 api/ 与 downloads/ 占位目录

    onefile 模式下可执行文件是单个文件，用户数据（api 二进制、数据库、
    下载目录）放在可执行文件同级目录。version.txt 已打入文件内，无需复制。
    """
    if not DIST_APP_DIR.exists():
        DIST_APP_DIR.mkdir(parents=True, exist_ok=True)

    # 产物检查须带平台后缀（Linux 产物无 .exe）
    bin_path = DIST_APP_DIR / (APP_NAME + exe_suffix())
    if not bin_path.exists():
        print(f"[ERROR] 未找到产物: {bin_path}")
        sys.exit(1)

    # 创建空 api/ 占位目录（用户手动放入二进制）
    # 占位提示的文件名与内容均按平台参数化，与 node_bridge._BINARIES 对齐
    api_dir = DIST_APP_DIR / "api"
    api_dir.mkdir(exist_ok=True)
    api_bin = api_binary_name()
    (api_dir / f"Please put {api_bin} here.txt").write_text(
        f"Please download {api_bin} from official release and place it here.\n",
        encoding="utf-8",
    )
    print("[INFO] 已创建空 api/ 占位目录")

    # 创建空 downloads/ 占位目录
    (DIST_APP_DIR / "downloads").mkdir(exist_ok=True)
    print("[INFO] 已创建空 downloads/ 占位目录")


def main() -> None:
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
    print(f"  1. 把 {api_binary_name()} 放到 {DIST_APP_DIR / 'api'}")
    if is_win():
        print(f"  2. 双击 {bin_name}")
    else:
        print(f"  2. 运行 ./{bin_name}")
    print("  3. 访问 http://localhost:45600")
    print("=" * 60)


if __name__ == "__main__":
    main()
