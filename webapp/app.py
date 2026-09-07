"""Flask 应用入口

启动方式：
    python webapp/app.py
访问：http://localhost:45600
"""

import argparse
import atexit
import logging
import os
import shutil
import sys
from pathlib import Path

# 把项目根目录（code/client）和 webapp 目录加入 sys.path
# 使 core、webapp 内的模块（models/task_manager）均可导入
# frozen: PyInstaller 打包后用 exe 同级目录作为根目录
if getattr(sys, "frozen", False):
    _ROOT = Path(sys.executable).resolve().parent
else:
    _ROOT = Path(__file__).resolve().parent.parent
_WEBAPP = Path(__file__).resolve().parent
for p in (str(_ROOT), str(_WEBAPP)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _parse_data_dir_arg(argv=None):
    """解析 --data-dir 启动参数；未知参数忽略，不影响其他启动方式"""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", help="数据目录（数据库为 <目录>/downloads.db）")
    args, _ = parser.parse_known_args(argv)
    return args


from flask import Flask

from models import init_db, Setting
from task_manager import TaskManager
from core.providers.kugou import bridge as kugou_bridge
from core.providers.netease import bridge
from core.providers.qq import bridge as qq_bridge
from routes.api import api_bp
from routes.views import views_bp
from version import get_version

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("webapp")

# 客户端版本号（从项目根目录 VERSION 文件读取，统一管理）
__version__ = get_version()

app = Flask(__name__)
# Session 签名密钥：优先使用环境变量，未设置则用默认值
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "netease-downloader-secret-key-v060")
# 版本号入 config（账号导出文件等处经 current_app.config 读取）
app.config["APP_VERSION"] = __version__


@app.context_processor
def inject_version():
    """把版本号注入所有模板上下文，供 {{ version }} 使用"""
    return {"version": __version__}


@app.context_processor
def inject_static_v():
    """静态文件带 mtime 缓存戳的 URL：{{ static_v('js/xxx.js') }}

    文件一修改 URL 即变化，浏览器缓存自动失效（解决改 JS 后页面
    不生效的问题）；文件不存在时回退 ?v=0。
    """
    def static_v(filename: str) -> str:
        from flask import url_for
        p = _WEBAPP / "static" / filename
        try:
            ts = int(p.stat().st_mtime)
        except OSError:
            ts = 0
        return f"{url_for('static', filename=filename)}?v={ts}"
    return {"static_v": static_v}

# 初始化数据库：位置优先级 --data-dir > APP_DATA_DIR 环境变量 > 缺省(exe同目录/项目根)
_data_dir = _parse_data_dir_arg().data_dir
if _data_dir:
    DB_PATH = Path(_data_dir).expanduser().resolve() / "downloads.db"
elif os.environ.get("APP_DATA_DIR"):
    DB_PATH = Path(os.environ["APP_DATA_DIR"]).expanduser().resolve() / "downloads.db"
else:
    DB_PATH = _ROOT / "downloads.db"   # 缺省：保持原行为

# 旧库迁移：数据位置被指定、且老库仍在程序目录时，自动搬迁（含 SQLite 附属文件）
_old_db = _ROOT / "downloads.db"
if _old_db.exists() and not DB_PATH.exists():
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)   # init_db 之前目标目录可能尚不存在
        shutil.move(str(_old_db), str(DB_PATH))
        for _suffix in ("-journal", "-wal", "-shm"):
            _side = _ROOT / f"downloads.db{_suffix}"
            if _side.exists():
                _side.replace(DB_PATH.with_name(DB_PATH.name + _suffix))
        logger.info("已迁移旧数据库: %s -> %s", _old_db, DB_PATH)
    except OSError as e:
        logger.warning("旧数据库迁移失败，将使用新库启动: %s", e)

init_db(app, str(DB_PATH))
logger.info("数据库文件: %s", DB_PATH)

# 注册蓝图
app.register_blueprint(views_bp)
app.register_blueprint(api_bp, url_prefix="/api")

# 初始化任务管理器
task_manager = TaskManager(app)
app.config["TASK_MANAGER"] = task_manager


@app.after_request
def _no_cache_html(resp):
    """HTML 页面禁止浏览器缓存：页面永远取最新（JS/CSS 由 static_v
    的 mtime 缓存戳控制失效），避免改代码后浏览器仍用旧页面/旧脚本"""
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.template_filter("filesize")
def filesize_filter(size: int) -> str:
    """文件大小格式化"""
    if not size:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _read_web_bind() -> tuple:
    """从设置读取 web 服务监听地址（启动时调用）

    支持格式: "host:port"、":port"、"port"、"*:port"
    返回 (host, port) 元组
    """
    with app.app_context():
        raw = str(Setting.get("web_port", "*:45600")).strip()

    # 纯端口号，默认监听所有网卡
    if ":" not in raw:
        try:
            return ("0.0.0.0", int(raw))
        except (TypeError, ValueError):
            return ("0.0.0.0", 45600)

    host, _, port_str = raw.rpartition(":")
    if host == "" or host == "*":
        host = "0.0.0.0"
    try:
        return (host, int(port_str))
    except (TypeError, ValueError):
        return ("0.0.0.0", 45600)


def main() -> None:
    host, port = _read_web_bind()
    # Setting.get 需在 app context 内调用；读出后显式传入，
    # get_bridge 自身不碰数据库
    with app.app_context():
        auto_start = Setting.get("ncm_api_auto_start", "true") == "true"
        try:
            ncm_api_port = int(Setting.get("ncm_api_port", "45601"))
        except (TypeError, ValueError):
            ncm_api_port = 45601
        qq_auto_start = Setting.get("qq_api_auto_start", "true") == "true"
        try:
            qq_api_port = int(Setting.get("qq_api_port", "45602"))
        except (TypeError, ValueError):
            qq_api_port = 45602
        kugou_auto_start = Setting.get("kugou_api_auto_start", "true") == "true"
        try:
            kugou_api_port = int(Setting.get("kugou_api_port", "45603"))
        except (TypeError, ValueError):
            kugou_api_port = 45603
    ncm_bridge = bridge.get_bridge(auto_start=auto_start, port=ncm_api_port)
    # atexit 注册必须写在 main() 内（此时单例已用真实 auto_start 创建）；
    # 若放模块顶层会在 import 时以默认 auto_start=True 先建单例，忽略用户配置
    atexit.register(ncm_bridge.stop)
    qq_bridge_inst = qq_bridge.get_bridge(auto_start=qq_auto_start, port=qq_api_port)
    atexit.register(qq_bridge_inst.stop)
    kugou_bridge_inst = kugou_bridge.get_bridge(auto_start=kugou_auto_start, port=kugou_api_port)
    atexit.register(kugou_bridge_inst.stop)
    if ncm_bridge.auto_start:
        try:
            ncm_bridge.start()
            logger.info("网易云API服务就绪: %s", ncm_bridge.base_url)
        except RuntimeError as e:
            logger.warning("网易云API服务启动失败: %s", e)
    if qq_bridge_inst.auto_start:
        try:
            qq_bridge_inst.start()
            logger.info("QQ音乐API服务就绪: %s", qq_bridge_inst.base_url)
        except RuntimeError as e:
            logger.warning("QQ音乐API服务启动失败: %s", e)
    if kugou_bridge_inst.auto_start:
        try:
            kugou_bridge_inst.start()
            logger.info("酷狗音乐API服务就绪: %s", kugou_bridge_inst.base_url)
        except RuntimeError as e:
            logger.warning("酷狗音乐API服务启动失败: %s", e)
    task_manager.start()
    logger.info("=" * 50)
    logger.info("Deen音乐下载器 Web 服务启动 (v%s)", __version__)
    logger.info("访问地址: http://localhost:%d", port)
    logger.info("=" * 50)
    try:
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
    finally:
        task_manager.stop()
        ncm_bridge.stop()          # 主程序退出 -> 自动关闭 API 服务
        qq_bridge_inst.stop()      # 主程序退出 -> 自动关闭 QQ音乐API 服务
        kugou_bridge_inst.stop()   # 主程序退出 -> 自动关闭 酷狗音乐API 服务


if __name__ == "__main__":
    main()
