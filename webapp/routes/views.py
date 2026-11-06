"""页面路由 - 渲染 HTML 模板

包含登录/登出/用户管理路由，以及受 @login_required 保护的页面路由。
"""

import threading
import time
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, session

from auth import login_required, admin_required, current_user
from models import User, db, PLATFORM_NAMES, DEFAULT_ADMIN_PASSWORD

views_bp = Blueprint("views", __name__)

# 登录失败限速（内存计数，键 username+IP）：失败 5 次锁 10 分钟。
# app.run(threaded=True)，计数必须加锁。
_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCK_SECONDS = 600
_login_failures: dict[str, tuple[int, float]] = {}
_login_lock = threading.Lock()


def _login_key(username: str) -> str:
    return f"{username}|{request.remote_addr or ''}"


def _safe_internal_path(next_url) -> bool:
    """站内路径校验（#8）：必须以单个 / 开头且 urlsplit 解析无 scheme/netloc。

    覆盖 //evil.com、/\\evil.com、/\\t//evil.com 等协议相对与反斜杠/控制
    字符变体；非字符串或解析异常一律视为不安全。
    """
    if not next_url or not isinstance(next_url, str):
        return False
    if not next_url.startswith("/") or next_url.startswith("//"):
        return False
    if "\\" in next_url:
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in next_url):
        # 控制字符（含 \t \r \n 与 DEL）：可被部分客户端解析器忽略或截断，
        # 制造校验与实际跳转目标不一致的开放重定向缝隙，一律拒绝
        return False
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(next_url)
    except ValueError:
        return False
    return not parts.scheme and not parts.netloc


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
    {"key": "kugou", "name": "酷狗音乐", "icon": "vinyl"},
]


# ======================================================================
# 登录 / 登出
# ======================================================================
@views_bp.before_request
def _force_password_change():
    """首登强制改密：默认密码未修改前，除登录/登出/改密页外一律重定向

    仅拦页面路由（/api/* 由 routes/api.py 的 _api_require_login 拦截并
    返回 403+must_change_password 标记，前端据此跳改密页）。
    """
    if request.path.startswith("/api/"):
        return None
    user = current_user()
    if not user or not user.must_change_password:
        return None
    allowed = {"views.login", "views.logout", "views.change_password"}
    if request.endpoint in allowed:
        return None
    return redirect(url_for("views.change_password"))


@views_bp.route("/login", methods=["GET", "POST"])
def login():
    """用户登录"""
    # 已登录直接跳转；尚在强制改密期则跳改密页（防 login→dashboard→改密页 循环）
    _user = current_user()
    if _user:
        if _user.must_change_password:
            return redirect(url_for("views.change_password"))
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
            # F4 存量默认口令补置：仅 admin 账号——口令仍为公开默认值时补置
            # 首登强制改密（覆盖升级前从未改密的存量管理员）。置位一次后靠
            # flag 短路。判断用明文比较而非 check_password(DEFAULT)：上一行
            # check_password(password) 已通过 ⇒ hash 对应明文即 password，
            # password != 默认值时二次校验恒 False（冗余 scrypt），两者等价。
            # 普通用户不在检测范围（零额外开销）。
            if user.is_admin and not user.must_change_password \
                    and password == DEFAULT_ADMIN_PASSWORD:
                user.must_change_password = True
            db.session.commit()
            # 支持 next 参数跳回原页面（urlsplit 校验 scheme/netloc 为空，
            # 覆盖协议相对与反斜杠/控制字符变体，防开放重定向 #8）
            next_url = request.args.get("next")
            if _safe_internal_path(next_url):
                return redirect(next_url)
            return redirect(url_for("views.dashboard"))
        _record_login_failure(key)
        return render_template("login.html", error="用户名或密码错误")
    return render_template("login.html")


@views_bp.route("/change_password", methods=["GET", "POST"])
def change_password():
    """修改密码页（首登强制改密的落地页；正常用户也可自助改密）

    服务端表单处理（与 login 风格一致，不依赖 JS）：校验旧密码、
    新密码长度、两次输入一致；成功后清除强制改密标记并跳总览。
    """
    user = current_user()
    if not user:
        return redirect(url_for("views.login"))
    if request.method == "POST":
        old_pwd = request.form.get("old_password", "")
        new_pwd = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not user.check_password(old_pwd):
            return render_template("change_password.html", error="旧密码错误", user=user)
        if len(new_pwd) < 6:
            return render_template("change_password.html", error="新密码至少 6 位", user=user)
        if new_pwd != confirm:
            return render_template("change_password.html", error="两次输入的新密码不一致", user=user)
        if new_pwd == old_pwd:
            return render_template("change_password.html", error="新密码不能与旧密码相同", user=user)
        user.set_password(new_pwd)
        user.must_change_password = False
        db.session.commit()
        # 身份已验证（旧密码校验通过）即清登录限速计数：避免此前同用户名+IP
        # 累积的失败记录在改密后立即登录时误伤（「失败次数过多」）
        _clear_login_failures(_login_key(user.username))
        return redirect(url_for("views.dashboard"))
    return render_template("change_password.html", user=user)


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
