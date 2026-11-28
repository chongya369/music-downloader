"""认证模块

提供登录态校验装饰器和当前用户获取函数。
- 页面请求未登录：重定向到 /login
- API 请求未登录：返回 401 JSON（前端 api() 拦截后跳转登录）
"""

import logging
import os
import time
from functools import wraps
from typing import Optional

from flask import session, redirect, url_for, request, jsonify
from sqlalchemy.exc import IntegrityError

from models import User, db

logger = logging.getLogger("webapp")

# 主动登出后抑制网关 SSO 自动登录的时长（秒）。
# 若用户恰好与飞牛账号同名，登出后的下一次请求会被可信头立即登回，
# 表现为「点了退出没反应」；抑制窗口内落回登录页，30 分钟后自动恢复免密。
SSO_OPTOUT_TTL = 1800

# 网关身份漂移处理策略（P2，0.7.10）：
#   rebind（默认）—— 清会话后由 gateway_auto_login 按当前飞牛身份重建，
#                    换号者无感进入自己的账号；新身份未绑定时自然落登录页；
#   strict         —— 清会话并写入 sso_optout，停在登录页要求手动登录。
# 环境变量 FNNAS_SSO_DRIFT 可覆盖，非法值回落 rebind。
GATEWAY_DRIFT_POLICY = (os.environ.get("FNNAS_SSO_DRIFT") or "rebind").strip().lower()
if GATEWAY_DRIFT_POLICY not in ("rebind", "strict"):
    GATEWAY_DRIFT_POLICY = "rebind"


def gateway_identity_drift() -> bool:
    """网关身份漂移校验：SSO 会话的飞牛身份变了就废弃旧会话

    背景：gateway_auto_login 只在无本地会话时工作，会话一旦建立就再也不看
    X-Trim-* 头。若用户 A 未登出便在飞牛侧切换到账号 B，B 会静默继承 A 的
    宿主会话（下载记录、绑定的音乐平台 Cookie、管理员权限）。

    仅对 SSO 会话生效（auth_source == "gateway"，或升级前的旧会话=有
    fnos_uid 但无 auth_source）。密码登录会话一律不动——否则"手动登录账号
    A + 飞牛账号 B 打开页面"会被顶号。

    返回 True 表示 strict 模式下已抑制本次 SSO（调用方应跳过自动登录）；
    rebind 模式清空会话后返回 False，交由 gateway_auto_login 按新身份重建。

    边界：请求头缺 X-Trim-Userid 时不做任何处理——网关偶发不注入头时
    不能把在线用户集体踢下线。
    """
    # 1) 只处理 SSO 会话（含升级前的旧会话兜底）
    is_sso = session.get("auth_source") == "gateway" or (
        not session.get("auth_source") and session.get("fnos_uid"))
    if not is_sso:
        return False

    # 2) 主动登出的抑制窗口优先：尊重"我刚点了退出"
    optout_at = session.get("sso_optout")
    if optout_at:
        try:
            if time.time() - float(optout_at) < SSO_OPTOUT_TTL:
                return False
        except (TypeError, ValueError):
            session.pop("sso_optout", None)

    # 3) 无当前身份头 -> 不处理，避免网关丢头造成误登出
    current = (request.headers.get("X-Trim-Userid") or "").strip()
    if not current:
        return False

    bound = (session.get("fnos_uid") or "").strip()
    if not bound or current == bound:
        return False

    logger.warning("网关身份漂移：会话绑定 uid=%s，当前请求 uid=%s，废弃旧会话（策略: %s）",
                   bound, current, GATEWAY_DRIFT_POLICY)
    session.clear()
    if GATEWAY_DRIFT_POLICY == "strict":
        # 抑制自动登录，停在登录页让用户显式选择账号
        session["sso_optout"] = time.time()
        return True
    # rebind：以下一次 gateway_auto_login 按新身份重建（未绑定则落登录页）
    return False


def gateway_auto_login() -> bool:
    """网关模式专用：用统一网关注入的可信头建立宿主会话（免密 SSO）

    调用前提：仅 app.py 在 GATEWAY_SOCKET（Unix Socket）模式下注册调用。
    TCP 模式绝不可调用——否则客户端可伪造 X-Trim-Username 提权。
    统一网关在转发前已校验 fnOS 会话并注入以下头（见飞牛《统一网关》）：
    X-Trim-Userid / X-Trim-Isadmin / X-Trim-Username。

    匹配策略（两级）：
    1. 首选 users.fnos_uid == X-Trim-Userid：UID 稳定，任一侧改名都不断链；
    2. 回退 users.username == X-Trim-Username：存量账号兼容，命中且未绑定时
       自动回写 fnos_uid（自愈式绑定），此后改名也不再影响免密登录。

    返回 True 表示本次新建了会话；头缺失、用户不存在或被禁用时不做任何事
    （落回常规登录页），返回 False。
    """
    if session.get("uid"):
        return False
    # 主动登出后的抑制窗口：避免密码登录用户被可信头立刻登回
    optout_at = session.get("sso_optout")
    if optout_at:
        try:
            if time.time() - float(optout_at) < SSO_OPTOUT_TTL:
                return False
        except (TypeError, ValueError):
            session.pop("sso_optout", None)

    username = (request.headers.get("X-Trim-Username") or "").strip()
    userid = (request.headers.get("X-Trim-Userid") or "").strip()
    if not username or not userid:
        return False

    # 一级：飞牛 UID（稳定匹配键）
    user = User.query.filter_by(fnos_uid=userid).first()
    matched = "fnos_uid" if user else ""
    if not user:
        # 二级：用户名（存量兼容）——命中即补写 UID，完成一次性绑定
        user = User.query.filter_by(username=username).first()
        matched = "username" if user else ""
        # 仅为启用账号建立绑定：禁用账号回写 UID 会让"重新启用"瞬间获得免密入口
        if user and user.enabled and not user.fnos_uid:
            user.fnos_uid = userid
            try:
                db.session.commit()
                logger.info("网关免密登录：自动绑定 fnOS 用户 %s(uid=%s) -> 宿主账号 %s",
                            username, userid, user.username)
            except IntegrityError:
                # 并发绑定同一 UID：放弃回写即可，不影响本次登录
                db.session.rollback()
                logger.warning("网关免密登录：UID %s 绑定冲突，已跳过自动绑定", userid)

    if not user or not user.enabled:
        # DEBUG 级：未映射用户每次请求都会走到这里，避免噪声
        logger.debug("网关免密登录跳过：fnOS 用户 %s(uid=%s) 无对应宿主账号或已禁用",
                     username, userid)
        return False

    session["uid"] = user.id
    # 会话级认证来源：模板据此渲染「免密登录」标记 / 「退出登录」入口，
    # 不能再用部署级 is_gateway 判断（那是常量，与本次会话无关）
    session["auth_source"] = "gateway"
    session["fnos_uid"] = userid
    session["fnos_username"] = username
    logger.info("网关免密登录：fnOS 用户 %s(uid=%s) -> 宿主账号 %s（匹配方式: %s）",
                username, userid, user.username, matched)
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
