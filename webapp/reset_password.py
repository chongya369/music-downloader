"""命令行重置用户密码

用法：
    # 重置 admin 密码为默认值 admin123
    python webapp/reset_password.py

    # 重置指定用户密码为默认值 admin123
    python webapp/reset_password.py 张三

    # 重置指定用户密码为指定值
    python webapp/reset_password.py 张三 newpass456

    # 数据库不在项目根时，指定数据目录（也可用 APP_DATA_DIR 环境变量）
    python webapp/reset_password.py --data-dir /data/tool 张三 newpass456

使用场景：忘记 Web 登录密码时，在服务器命令行执行即可恢复访问。
"""

import argparse
import os
import sys
from pathlib import Path

# 把项目根目录（code/client）和 webapp 目录加入 sys.path
_ROOT = Path(__file__).resolve().parent.parent
_WEBAPP = Path(__file__).resolve().parent
for p in (str(_ROOT), str(_WEBAPP)):
    if p not in sys.path:
        sys.path.insert(0, p)

from flask import Flask
from models import init_db, User, db

# 数据库位置与主程序同一套规则：--data-dir > APP_DATA_DIR 环境变量 > 缺省(项目根)
_parser = argparse.ArgumentParser(description="重置 Web 登录密码")
_parser.add_argument("username", nargs="?", default="admin")
_parser.add_argument("password", nargs="?", default="admin123")
_parser.add_argument("--data-dir", help="数据目录（数据库为 <目录>/downloads.db）")
_args = _parser.parse_args()

app = Flask(__name__)
app.config["SECRET_KEY"] = "reset-password-script"

if _args.data_dir:
    _db_file = Path(_args.data_dir).expanduser().resolve() / "downloads.db"
elif os.environ.get("APP_DATA_DIR"):
    _db_file = Path(os.environ["APP_DATA_DIR"]).expanduser().resolve() / "downloads.db"
else:
    _db_file = _ROOT / "downloads.db"
init_db(app, str(_db_file))

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"


def reset(username: str, new_password: str) -> None:
    """重置指定用户密码"""
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if not user:
            print(f"[错误] 用户 '{username}' 不存在")
            sys.exit(1)
        user.set_password(new_password)
        db.session.commit()
        print(f"[成功] 用户 '{username}' 密码已重置为 '{new_password}'")


def main() -> None:
    reset(_args.username, _args.password)


if __name__ == "__main__":
    main()
