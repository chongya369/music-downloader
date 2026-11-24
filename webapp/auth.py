"""认证模块

提供登录态校验装饰器和当前用户获取函数。
- 页面请求未登录：重定向到 /login
- API 请求未登录：返回 401 JSON（前端 api() 拦截后跳转登录）
"""

import logging
from functools import wraps
from typing import Optional

from flask import session, redirect, url_for, request, jsonify

from models import User

logger = logging.getLogger("webapp")


def gateway_auto_login() -> bool:
    """网关模式专用：用统一网关注入的可信头建立宿主会话（免密 SSO）

    调用前提：仅 app.py 在 GATEWAY_SOCKET（Unix Socket）模式下注册调用。
    TCP 模式绝不可调用——否则客户端可伪造 X-Trim-Username 提权。
    统一网关在转发前已校验 fnOS 会话并注入以下头（见飞牛《统一网关》）：
    X-Trim-Userid / X-Trim-Isadmin / X-Trim-Username。

    返回 True 表示本次新建了会话；头缺失、用户不存在或被禁用时不做任何事
    （落回常规登录页），返回 False。
    """
    if session.get("uid"):
        return False
    username = (request.headers.get("X-Trim-Username") or "").strip()
    userid = (request.headers.get("X-Trim-Userid") or "").strip()
    if not username or not userid:
        return False
    user = User.query.filter_by(username=username).first()
    if not user or not user.enabled:
        # DEBUG 级：未映射用户每次请求都会走到这里，避免噪声
        logger.debug("网关免密登录跳过：fnOS 用户 %s(uid=%s) 无对应宿主账号或已禁用",
                     username, userid)
        return False
    session["uid"] = user.id
    logger.info("网关免密登录：fnOS 用户 %s(uid=%s) -> 宿主账号 %s",
                username, userid, user.username)
    return True


def login_required(allow_api_login: bool = False):
    """登录态校验装饰器

    Args:
        allow_api_login: 已废弃参数，保留兼容性（登录接口本身不应使用此装饰器）
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            uid = session.get("uid")
            if not uid:
                if request.path.startswith("/api/"):
                    return jsonify({"code": 401, "msg": "未登录或登录已过期"}), 401
                return redirect(url_for("views.login"))
            # 校验用户仍存在且启用
            user = User.query.get(uid)
            if not user or not user.enabled:
                session.clear()
                if request.path.startswith("/api/"):
                    return jsonify({"code": 401, "msg": "用户已被禁用或不存在"}), 401
                return redirect(url_for("views.login"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def admin_required(fn):
    """管理员权限校验装饰器（需配合 @login_required 使用，放在其下方）"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.is_admin:
            if request.path.startswith("/api/"):
                return jsonify({"code": 403, "msg": "仅管理员可访问"}), 403
            return redirect(url_for("views.dashboard"))
        return fn(*args, **kwargs)
    return wrapper


def current_user() -> Optional[User]:
    """获取当前登录用户，未登录返回 None"""
    uid = session.get("uid")
    return User.query.get(uid) if uid else None
