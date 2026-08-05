"""命令行重置用户密码

用法：
    # 重置 admin 密码为默认值 admin123
    python webapp/reset_password.py

    # 重置指定用户密码为默认值 admin123
    python webapp/reset_password.py 张三

    # 重置指定用户密码为指定值
    python webapp/reset_password.py 张三 newpass456

使用场景：忘记 Web 登录密码时，在服务器命令行执行即可恢复访问。
"""

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

app = Flask(__name__)
app.config["SECRET_KEY"] = "reset-password-script"
init_db(app, str(_ROOT / "downloads.db"))

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
    args = sys.argv[1:]
    if len(args) == 0:
        # 无参数：重置 admin 为默认密码
        reset(DEFAULT_USERNAME, DEFAULT_PASSWORD)
    elif len(args) == 1:
        # 一个参数：重置指定用户为默认密码
        reset(args[0], DEFAULT_PASSWORD)
    elif len(args) == 2:
        # 两个参数：重置指定用户为指定密码
        reset(args[0], args[1])
    else:
        print("用法：python webapp/reset_password.py [用户名] [新密码]")
        print(f"默认：重置 {DEFAULT_USERNAME} 为 {DEFAULT_PASSWORD}")
        sys.exit(1)


if __name__ == "__main__":
    main()
