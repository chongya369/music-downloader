"""认证模块

提供登录态校验装饰器和当前用户获取函数。
- 页面请求未登录：重定向到 /login
- API 请求未登录：返回 401 JSON（前端 api() 拦截后跳转登录）
"""

from functools import wraps
from typing import Optional

from flask import session, redirect, url_for, request, jsonify

from models import User


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
