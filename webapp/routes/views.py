"""页面路由 - 渲染 HTML 模板

包含登录/登出/用户管理路由，以及受 @login_required 保护的页面路由。
"""

import threading
import time
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, session

from auth import login_required, admin_required, current_user
from models import User, db, PLATFORM_NAMES

views_bp = Blueprint("views", __name__)

# 登录失败限速（内存计数，键 username+IP）：失败 5 次锁 10 分钟。
# app.run(threaded=True)，计数必须加锁。
_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCK_SECONDS = 600
_login_failures: dict[str, tuple[int, float]] = {}
_login_lock = threading.Lock()


def _login_key(username: str) -> str:
    return f"{username}|{request.remote_addr or ''}"


def _login_blocked(key: str) -> bool:
    """该 key 是否处于锁定窗口内；窗口过期则清除记录"""
    with _login_lock:
        entry = _login_failures.get(key)
        if not entry:
            return False
        count, ts = entry
        if time.time() - ts >= _LOGIN_LOCK_SECONDS:
            del _login_failures[key]
            return False
        return count >= _LOGIN_MAX_FAILURES


def _record_login_failure(key: str) -> None:
    with _login_lock:
        count, ts = _login_failures.get(key, (0, 0.0))
        if time.time() - ts >= _LOGIN_LOCK_SECONDS:
            count = 0
        _login_failures[key] = (count + 1, time.time())


def _clear_login_failures(key: str) -> None:
    with _login_lock:
        _login_failures.pop(key, None)

# 当前已实现的前端可用平台（配置驱动，后续新增平台在此追加即可）
AVAILABLE_PLATFORMS = [
    {"key": "netease", "name": "网易云", "icon": "music-note-beamed"},
    {"key": "qq", "name": "QQ音乐", "icon": "music-note-list"},
]


# ======================================================================
# 登录 / 登出
# ======================================================================
@views_bp.route("/login", methods=["GET", "POST"])
def login():
    """用户登录"""
    # 已登录直接跳转总览
    if current_user():
        return redirect(url_for("views.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        key = _login_key(username)
        if _login_blocked(key):
            return render_template("login.html", error="失败次数过多，请 10 分钟后再试")
        user = User.query.filter_by(username=username).first()
        if user and user.enabled and user.check_password(password):
            _clear_login_failures(key)
            session["uid"] = user.id
            user.last_login_at = datetime.now()
            db.session.commit()
            # 支持 next 参数跳回原页面（拦协议相对 // 与反斜杠 /\\ 变体，防开放重定向）
            next_url = request.args.get("next")
            if (next_url and next_url.startswith("/")
                    and not next_url.startswith("//")
                    and not next_url.startswith("/\\")):
                return redirect(next_url)
            return redirect(url_for("views.dashboard"))
        _record_login_failure(key)
        return render_template("login.html", error="用户名或密码错误")
    return render_template("login.html")


@views_bp.route("/logout")
def logout():
    """退出登录"""
    session.clear()
    return redirect(url_for("views.login"))


# ======================================================================
# 业务页面（均需登录）
# ======================================================================
@views_bp.route("/")
@login_required()
def dashboard():
    """总览页"""
    return render_template("dashboard.html", active_page="dashboard", current_user=current_user())


@views_bp.route("/playlists")
@login_required()
def playlists():
    """歌单管理页"""
    return render_template(
        "playlists.html",
        active_page="playlists",
        current_user=current_user(),
        platforms=AVAILABLE_PLATFORMS,
        platform_names=PLATFORM_NAMES,
    )


@views_bp.route("/accounts")
@login_required()
def accounts():
    """账号管理页"""
    return render_template("accounts.html", active_page="accounts", current_user=current_user())


@views_bp.route("/history")
@login_required()
def history():
    """下载历史页"""
    return render_template("history.html", active_page="history", current_user=current_user())


@views_bp.route("/settings")
@login_required()
def settings():
    """设置页"""
    return render_template("settings.html", active_page="settings", current_user=current_user())


@views_bp.route("/users")
@login_required()
@admin_required
def users():
    """用户管理页（仅管理员）"""
    return render_template("users.html", active_page="users", current_user=current_user())
