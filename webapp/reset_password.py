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
from models import init_db, User, db, DEFAULT_ADMIN_PASSWORD

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

# 目标库不存在时直接报错退出：init_db 会静默新建空库（并创建默认 admin），
# 误报"重置成功"会让用户误以为真实库已改密
if not _db_file.is_file():
    print(f"[错误] 数据库文件不存在: {_db_file}")
    print("       请检查 --data-dir / APP_DATA_DIR 是否指向真实数据目录")
    sys.exit(1)

init_db(app, str(_db_file))

DEFAULT_USERNAME = "admin"
# 默认口令引用 models 常量（单一来源）；注：重置为该公开默认值后，admin
# 账号下次登录会被 F4 检测重新置位强制改密（符合"公开默认口令必须改"语义）
DEFAULT_PASSWORD = DEFAULT_ADMIN_PASSWORD


def reset(username: str, new_password: str) -> None:
    """重置指定用户密码"""
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if not user:
            print(f"[错误] 用户 '{username}' 不存在")
            sys.exit(1)
        user.set_password(new_password)
        # 管理员主动重置即视为脱离默认密码态，不触发首登强制改密
        user.must_change_password = False
        db.session.commit()
        print(f"[成功] 用户 '{username}' 密码已重置为 '{new_password}'")


def main() -> None:
    reset(_args.username, _args.password)


if __name__ == "__main__":
    main()
