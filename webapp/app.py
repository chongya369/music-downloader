"""Flask 应用入口

启动方式：
    python webapp/app.py
访问：http://localhost:56700
"""

import logging
import os
import sys
from pathlib import Path

# 把项目根目录（code/client）和 webapp 目录加入 sys.path
# 使 core、webapp 内的模块（models/task_manager）均可导入
_ROOT = Path(__file__).resolve().parent.parent
_WEBAPP = Path(__file__).resolve().parent
for p in (str(_ROOT), str(_WEBAPP)):
    if p not in sys.path:
        sys.path.insert(0, p)

from flask import Flask

from models import init_db, Setting
from task_manager import TaskManager
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


def _read_web_port() -> int:
    """从设置读取 web 服务端口（启动时调用）"""
    with app.app_context():
        try:
            return int(Setting.get("web_port", "56700"))
        except (TypeError, ValueError):
            return 56700


def main() -> None:
    port = _read_web_port()
    task_manager.start()
    logger.info("=" * 50)
    logger.info("网易云音乐下载器 Web 服务启动 (v%s)", __version__)
    logger.info("访问地址: http://localhost:%d", port)
    logger.info("=" * 50)
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
    finally:
        task_manager.stop()


if __name__ == "__main__":
    main()
