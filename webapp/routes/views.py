"""页面路由 - 渲染 HTML 模板

包含登录/登出/用户管理路由，以及受 @login_required 保护的页面路由。
"""

from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, session

from auth import login_required, admin_required, current_user
from models import User, db, PLATFORM_NAMES

views_bp = Blueprint("views", __name__)

# 当前已实现的前端可用平台（配置驱动，后续新增平台在此追加即可）
AVAILABLE_PLATFORMS = [{"key": "netease", "name": "网易云", "icon": "music-note-beamed"}]


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
        user = User.query.filter_by(username=username).first()
        if user and user.enabled and user.check_password(password):
            session["uid"] = user.id
            user.last_login_at = datetime.now()
            db.session.commit()
            # 支持 next 参数跳回原页面
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
            return redirect(url_for("views.dashboard"))
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
