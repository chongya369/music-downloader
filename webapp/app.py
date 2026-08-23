"""Flask 应用入口

启动方式：
    python webapp/app.py
访问：http://localhost:45600
"""

import atexit
import logging
import os
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

from flask import Flask

from models import init_db, Setting
from task_manager import TaskManager
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


@app.context_processor
def inject_version():
    """把版本号注入所有模板上下文，供 {{ version }} 使用"""
    return {"version": __version__}

# 初始化数据库
DB_PATH = _ROOT / "downloads.db"
init_db(app, str(DB_PATH))

# 注册蓝图
app.register_blueprint(views_bp)
app.register_blueprint(api_bp, url_prefix="/api")

# 初始化任务管理器
task_manager = TaskManager(app)
app.config["TASK_MANAGER"] = task_manager


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
        auto_start = Setting.get("ncm_api_auto_start", "false") == "true"
        try:
            ncm_api_port = int(Setting.get("ncm_api_port", "45601"))
        except (TypeError, ValueError):
            ncm_api_port = 45601
        qq_auto_start = Setting.get("qq_api_auto_start", "false") == "true"
        try:
            qq_api_port = int(Setting.get("qq_api_port", "45602"))
        except (TypeError, ValueError):
            qq_api_port = 45602
    ncm_bridge = bridge.get_bridge(auto_start=auto_start, port=ncm_api_port)
    # atexit 注册必须写在 main() 内（此时单例已用真实 auto_start 创建）；
    # 若放模块顶层会在 import 时以默认 auto_start=True 先建单例，忽略用户配置
    atexit.register(ncm_bridge.stop)
    qq_bridge_inst = qq_bridge.get_bridge(auto_start=qq_auto_start, port=qq_api_port)
    atexit.register(qq_bridge_inst.stop)
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


if __name__ == "__main__":
    main()
