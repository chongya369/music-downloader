"""版本号统一管理：从项目根目录 version.txt 文件读取"""
import sys
from pathlib import Path

# frozen: PyInstaller 打包后 version.txt 已打入 exe 内，从解压根 _MEIPASS 读取
if getattr(sys, "frozen", False):
    _ROOT = Path(getattr(sys, "_MEIPASS", sys.executable))
else:
    _ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = _ROOT / "version.txt"

try:
    __version__ = _VERSION_FILE.read_text(encoding="utf-8").strip()
except (FileNotFoundError, OSError):
    __version__ = "0.0.0"  # 文件丢失时的兜底


def get_version() -> str:
    return __version__
