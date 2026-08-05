"""版本号统一管理：从项目根目录 VERSION 文件读取"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = _ROOT / "VERSION"

try:
    __version__ = _VERSION_FILE.read_text(encoding="utf-8").strip()
except (FileNotFoundError, OSError):
    __version__ = "0.0.0"  # 文件丢失时的兜底


def get_version() -> str:
    return __version__
