"""JSON API 路由

接口列表：
- GET    /api/playlists           获取关注的歌单列表
- GET    /api/toplists            获取网易云所有官方榜单（供添加选择）
- POST   /api/playlists           添加歌单（支持分享链接自动解析 ID）
- PUT    /api/playlists/<id>      更新歌单设置（启用/limit）
- DELETE /api/playlists/<id>      取消关注
- POST   /api/sync/<id>           立即同步某歌单
- POST   /api/sync-all            同步所有已启用歌单
- GET    /api/songs               分页查询下载历史
- DELETE /api/songs/<id>          删除记录
- POST   /api/retry               重试失败歌曲（支持单首/全部）
- GET    /api/tasks               获取当前活跃任务进度
- GET    /api/stats               获取统计数据（总览页用）
- GET    /api/settings            获取配置
- PUT    /api/settings            保存配置
- POST   /api/test-login          测试 Cookie 是否有效
"""

import logging
from datetime import datetime, timedelta
import json as _json

from flask import Blueprint, current_app, jsonify, request, Response, session
from sqlalchemy import func

from auth import current_user, admin_required, login_required
from models import Account, Playlist, Setting, Song, DownloadTask, User, db, get_custom_api_url, PLATFORMS, PLATFORM_NAMES
from core.providers.netease import NeteaseProvider
from core.providers.netease.client import OFFICIAL_TOPLISTS
from core.providers.netease.parse_links import parse_playlist_id
from core.providers import get_provider
from core.providers.netease import bridge

api_bp = Blueprint("api", __name__)
logger = logging.getLogger(__name__)


# ======================================================================
# 全局登录态校验：所有 /api/* 请求均需登录
# ======================================================================
@api_bp.before_request
def _api_require_login():
    """所有 API 请求均需登录，未登录返回 401"""
    # 放行 OPTIONS 预检请求
    if request.method == "OPTIONS":
        return None
    uid = session.get("uid")
    if not uid:
        return jsonify({"code": 401, "msg": "未登录或登录已过期"}), 401
    user = User.query.get(uid)
    if not user or not user.enabled:
        session.clear()
        return jsonify({"code": 401, "msg": "用户已被禁用或不存在"}), 401
    return None


def _month_start() -> datetime:
    """本月 1 号 0 点"""
    now = datetime.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _monthly_downloaded(account_id: int) -> int:
    """统计账号本月成功下载数"""
    return db.session.query(func.count(Song.id)).filter(
        Song.account_id == account_id,
        Song.status == "success",
        Song.downloaded_at >= _month_start(),
    ).scalar() or 0


def _refresh_account_info(acc: Account, cookie: str | None = None) -> None:
    """刷新账号信息：昵称、会员类型、会员到期时间

    仅 netease 平台支持自动刷新；QQ/酷狗等平台跳过。

    Args:
        acc: 账号对象（会被原地修改，但不 commit）
        cookie: 用指定 cookie 测试；None 时用 acc.cookie
    """
    if acc.platform != "netease":
        return  # 非网易云平台暂不支持自动刷新
    use_cookie = cookie if cookie is not None else (acc.cookie or "")
    if not use_cookie:
        return
    try:
        client = _create_client(cookie=use_cookie)
        info = client.get_account_info()
        if info.get("code") == 200:
            a = info.get("account") or {}
            acc.nickname = a.get("userName") or a.get("nickname") or ""
            acc.vip_type = a.get("vipType", 0)
            acc.last_check_at = datetime.now()
        # 获取会员到期时间（接口失败则不更新该字段）
        vip_info = client.get_vip_info()
        if vip_info:
            expire_ms = vip_info.get("expire_time")
            if expire_ms:
                try:
                    acc.vip_expire_at = datetime.fromtimestamp(int(expire_ms) / 1000)
                except (TypeError, ValueError, OSError, OverflowError):
                    pass
    except Exception as e:
        logger.warning("刷新账号信息失败 (%s): %s", acc.name, e)


def _get_task_manager():
    return current_app.config["TASK_MANAGER"]


def _create_client(cookie: str = "", platform: str = "netease") -> NeteaseProvider:
    """创建指定平台的 Provider（返回 provider，自动应用自定义API服务URL设置）"""
    p = get_provider(platform)
    p.set_custom_base_url(get_custom_api_url())
    if cookie:
        p.set_cookie(cookie)
    return p


def _get_client(platform: str = "netease") -> NeteaseProvider:
    """用第一个启用的指定平台账号 cookie 创建 provider（用于发现页/添加歌单等公开接口）

    无启用账号时用空 cookie。
    """
    acc = Account.query.filter_by(platform=platform, enabled=True).order_by(Account.sort_order, Account.id).first()
    cookie = acc.cookie if acc else ""
    return _create_client(cookie=cookie or "", platform=platform)


def _req_platform() -> str:
    """从请求中读取平台标识（POST 取 body.platform，GET 取 query.platform），默认 netease"""
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        return (data.get("platform") or "").strip().lower() or "netease"
    return (request.args.get("platform") or "").strip().lower() or "netease"


def _safe_int(value, default: int, lo: int | None = None, hi: int | None = None) -> int:
    """安全解析整数：非法值返回 default，可选范围限制"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    if lo is not None and n < lo:
        n = lo
    if hi is not None and n > hi:
        n = hi
    return n


# ======================================================================
# 歌单管理
# ======================================================================
@api_bp.route("/playlists")
def get_playlists():
    """获取已关注的歌单列表"""
    playlists = Playlist.query.order_by(Playlist.created_at).all()
    return jsonify({"code": 0, "data": [p.to_dict() for p in playlists]})


@api_bp.route("/toplists")
def get_toplists():
    """获取网易云所有官方榜单（从 API 实时拉取）"""
    try:
        client = _get_client()
        lists = client.get_all_toplists()
        if lists:
            return jsonify({"code": 0, "data": lists})
    except Exception as e:
        logger.warning("从 API 获取榜单失败，返回本地常驻列表: %s", e)
    # 回退到本地常驻列表
    data = [{"id": k, "name": v, "description": "", "update_frequency": ""} for k, v in OFFICIAL_TOPLISTS.items()]
    return jsonify({"code": 0, "data": data})


@api_bp.route("/playlists", methods=["POST"])
def add_playlist():
    """添加歌单

    请求体：
        {"source": "3778678" 或 "https://music.163.com/playlist?id=xxx" 或榜单ID,
         "type": "official" 或 "user",
         "limit": 100,
         "platform": "netease" / "qq" / "kugou"}
    """
    data = request.get_json(force=True)
    source = data.get("source", "").strip()
    pl_type = data.get("type", "user")
    limit = _safe_int(data.get("limit", 100), 100, lo=1, hi=1000)
    platform = (data.get("platform") or "").strip().lower() or "netease"

    if not source:
        return jsonify({"code": 1, "msg": "请输入歌单 ID 或链接"})

    pid = parse_playlist_id(source)
    if pid is None:
        return jsonify({"code": 1, "msg": "无法解析歌单 ID，请检查输入"})

    # 检查是否已存在
    existing = Playlist.query.get(pid)
    if existing:
        return jsonify({"code": 1, "msg": f"歌单已存在: {existing.name}"})

    # 拉取歌单信息确认有效
    try:
        client = _get_client(platform)
        detail = client.get_playlist_detail(pid, limit=1)
        if not detail:
            return jsonify({"code": 1, "msg": "无法获取歌单信息，请检查 ID 或 Cookie"})
        name = detail.get("name", str(pid))
        track_count = detail.get("track_count", 0)
    except ValueError as e:
        return jsonify({"code": 1, "msg": str(e)})
    except Exception as e:
        return jsonify({"code": 1, "msg": f"获取歌单信息失败: {e}"})

    pl = Playlist(
        id=pid,
        platform=platform,
        name=name,
        type=pl_type,
        enabled=True,
        limit_count=limit,
        track_count=track_count,
    )
    db.session.add(pl)
    db.session.commit()
    logger.info("添加歌单: %s (id=%s)", name, pid)
    return jsonify({"code": 0, "data": pl.to_dict(), "msg": f"已添加: {name}"})


@api_bp.route("/playlists/<int:pid>", methods=["PUT"])
def update_playlist(pid: int):
    """更新歌单设置（enabled / limit_count / name）"""
    pl = Playlist.query.get(pid)
    if not pl:
        return jsonify({"code": 1, "msg": "歌单不存在"})

    data = request.get_json(force=True)
    if "enabled" in data:
        pl.enabled = bool(data["enabled"])
    if "limit_count" in data:
        pl.limit_count = _safe_int(data["limit_count"], pl.limit_count, lo=1, hi=1000)
    if "name" in data:
        pl.name = data["name"]
    db.session.commit()
    return jsonify({"code": 0, "data": pl.to_dict()})


@api_bp.route("/playlists/<int:pid>", methods=["DELETE"])
def delete_playlist(pid: int):
    """取消关注歌单（不删除已下载的歌曲记录）"""
    pl = Playlist.query.get(pid)
    if not pl:
        return jsonify({"code": 1, "msg": "歌单不存在"})
    name = pl.name
    db.session.delete(pl)
    db.session.commit()
    logger.info("删除歌单: %s (id=%s)", name, pid)
    return jsonify({"code": 0, "msg": f"已删除: {name}"})


# ======================================================================
# 同步
# ======================================================================
@api_bp.route("/sync/<int:pid>", methods=["POST"])
def sync_playlist(pid: int):
    """立即同步某个歌单"""
    pl = Playlist.query.get(pid)
    if not pl:
        return jsonify({"code": 1, "msg": "歌单不存在"})
    tm = _get_task_manager()
    count = tm.sync_playlist(pid)
    return jsonify({"code": 0, "msg": f"已加入 {count} 首新歌到下载队列"})


@api_bp.route("/sync-all", methods=["POST"])
def sync_all():
    """同步所有已启用的歌单"""
    tm = _get_task_manager()
    count = tm.sync_all()
    return jsonify({"code": 0, "msg": f"已加入 {count} 首新歌到下载队列"})


# ======================================================================
# 下载历史
# ======================================================================
@api_bp.route("/songs")
def get_songs():
    """分页查询下载历史

    数据源改为 download_tasks 表（支持同一首歌在多个歌单多条记录）。
    通过 LEFT JOIN songs 表补充文件信息（file_path/file_size/quality）。

    参数：
        page (默认 1)
        per_page (默认 20)
        status (可选: success/failed/skipped)
        keyword (可选: 搜索歌名/歌手)
    """
    page = _safe_int(request.args.get("page", 1), 1, lo=1)
    per_page = _safe_int(request.args.get("per_page", 20), 20, lo=1, hi=500)
    status = request.args.get("status", "")
    keyword = request.args.get("keyword", "").strip()

    # download_tasks.status: done/skipped/failed/pending/downloading
    # 前端筛选 status: success/failed/skipped
    # 映射：success → done, skipped → skipped, failed → failed
    query = db.session.query(
        DownloadTask, Song
    ).outerjoin(
        Song, DownloadTask.song_id == Song.id
    )

    if status:
        if status == "success":
            query = query.filter(DownloadTask.status == "done")
        elif status == "skipped":
            query = query.filter(DownloadTask.status == "skipped")
        elif status == "failed":
            query = query.filter(DownloadTask.status == "failed")

    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            db.or_(DownloadTask.song_name.like(like), DownloadTask.artists.like(like))
        )

    query = query.order_by(DownloadTask.updated_at.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    data = []
    for task, song in pagination.items:
        # 状态映射回前端：done → success
        display_status = "success" if task.status == "done" else task.status
        # 获取平台信息：优先从 task 获取，其次从 song 获取，最后默认为 netease
        platform = task.platform or (song.platform if song else "netease") or "netease"
        data.append({
            "id": task.song_id,
            "pk": task.pk,
            "platform": platform,
            "platform_name": PLATFORM_NAMES.get(platform, platform),
            "name": task.song_name,
            "artists": task.artists,
            "album": song.album if song else "",
            "duration_ms": song.duration_ms if song else 0,
            "quality": song.quality if song else "",
            "file_path": song.file_path if song else "",
            "file_size": song.file_size if song else 0,
            "playlist_id": task.playlist_id,
            "playlist_name": task.playlist_name,
            "downloaded_at": task.updated_at.strftime("%Y-%m-%d %H:%M:%S") if task.updated_at else None,
            "status": display_status,
            "error_msg": task.error_msg,
            "account_id": task.account_id,
        })

    return jsonify({
        "code": 0,
        "data": data,
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page,
    })


@api_bp.route("/songs/<int:pk>", methods=["DELETE"])
def delete_song(pk: int):
    """删除下载记录（按 download_tasks.pk 删除，仅删数据库记录，不删文件）"""
    task = DownloadTask.query.get(pk)
    if not task:
        return jsonify({"code": 1, "msg": "记录不存在"})
    db.session.delete(task)
    db.session.commit()
    return jsonify({"code": 0, "msg": "已删除"})


# ======================================================================
# 重试
# ======================================================================
@api_bp.route("/retry", methods=["POST"])
def retry_failed():
    """重试失败的歌曲

    请求体：
        {"song_ids": [1,2,3]}  指定重试
        {} 或 {"song_ids": null}  全部重试
    """
    data = request.get_json(force=True, silent=True) or {}
    song_ids = data.get("song_ids")
    tm = _get_task_manager()
    count = tm.retry_failed(song_ids)
    if count == 0:
        return jsonify({"code": 0, "msg": "没有需要重试的歌曲"})
    return jsonify({"code": 0, "msg": f"已加入 {count} 首到重试队列"})


# ======================================================================
# 任务进度
# ======================================================================
@api_bp.route("/tasks")
def get_tasks():
    """获取当前活跃任务（pending + downloading）"""
    tm = _get_task_manager()
    return jsonify({"code": 0, "data": tm.get_active_tasks()})


# ======================================================================
# 统计数据
# ======================================================================
@api_bp.route("/stats")
def get_stats():
    """总览页统计数据"""
    total = Song.query.count()
    success = Song.query.filter_by(status="success").count()
    failed = Song.query.filter_by(status="failed").count()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = Song.query.filter(
        Song.downloaded_at >= today,
        Song.status == "success",
    ).count()
    active_playlists = Playlist.query.filter_by(enabled=True).count()
    total_playlists = Playlist.query.count()

    # 当前队列数
    pending = DownloadTask.query.filter_by(status="pending").count()
    downloading = DownloadTask.query.filter_by(status="downloading").count()

    return jsonify({
        "code": 0,
        "data": {
            "total": total,
            "success": success,
            "failed": failed,
            "today": today_count,
            "active_playlists": active_playlists,
            "total_playlists": total_playlists,
            "pending": pending,
            "downloading": downloading,
        }
    })


# ======================================================================
# 设置
# ======================================================================
@api_bp.route("/settings")
def get_settings():
    """获取配置"""
    from models import DEFAULT_SETTINGS
    data = {}
    for key in DEFAULT_SETTINGS:
        data[key] = Setting.get(key, DEFAULT_SETTINGS[key])
    return jsonify({"code": 0, "data": data})


@api_bp.route("/settings", methods=["PUT"])
def save_settings():
    """保存配置

    请求体：key-value 字典，仅更新提交的字段。
    web_port 修改后需重启服务才生效。
    ncm_api_port 修改需在API服务停止状态下进行。
    """
    from models import DEFAULT_SETTINGS
    data = request.get_json(force=True)
    allowed = set(DEFAULT_SETTINGS.keys())

    # 端口变化时校验：API服务运行中禁止修改端口
    if str(data.get("ncm_api_port", "")) != Setting.get("ncm_api_port", ""):
        if bridge.get_bridge()._is_alive():
            return jsonify({"code": 1,
                            "msg": "API服务运行中，请先停止服务再修改端口"}), 400

    port_changed = False
    for key, value in data.items():
        if key not in allowed:
            continue
        if key == "web_port" and str(value) != Setting.get("web_port", ""):
            port_changed = True
        Setting.set(key, str(value))

    # 刷新调度
    tm = _get_task_manager()
    tm.refresh_schedule()
    msg = "设置已保存"
    if port_changed:
        msg += "（Web监听地址修改需重启服务生效）"
    return jsonify({"code": 0, "msg": msg})


# ======================================================================
# 内置 API 服务（NeteaseCloudMusicApi-enhanced）控制
# ======================================================================
@api_bp.route("/ncm/status")
def ncm_status():
    """获取内置 API 服务状态"""
    return jsonify({"code": 0, "data": bridge.get_bridge().status()})


@api_bp.route("/ncm/start", methods=["POST"])
def ncm_start():
    """启动内置 API 服务（幂等）"""
    try:
        url = bridge.get_bridge().start()
        return jsonify({"code": 0, "msg": "网易云API服务已启动", "data": {"base_url": url}})
    except RuntimeError as e:
        return jsonify({"code": 1, "msg": str(e)}), 500


@api_bp.route("/ncm/stop", methods=["POST"])
def ncm_stop():
    """停止内置 API 服务（幂等）"""
    bridge.get_bridge().stop()
    return jsonify({"code": 0, "msg": "网易云API服务已停止"})


# ======================================================================
# 账号管理（多账号）
# ======================================================================
@api_bp.route("/accounts")
def get_accounts():
    """获取所有账号列表（按平台+sort_order排序，含本月下载量）

    可选参数：platform=netease/qq/kugou，仅返回指定平台账号。
    """
    platform = request.args.get("platform", "").strip()
    if platform and platform in PLATFORMS:
        accounts = Account.query.filter_by(platform=platform).order_by(Account.sort_order, Account.id).all()
    else:
        accounts = Account.query.order_by(Account.platform, Account.sort_order, Account.id).all()
    data = []
    for acc in accounts:
        d = acc.to_dict(monthly_downloaded=_monthly_downloaded(acc.id))
        data.append(d)
    return jsonify({"code": 0, "data": data})


@api_bp.route("/accounts", methods=["POST"])
def add_account():
    """添加账号

    请求体：{"platform", "name", "cookie", "quota_limit"}
    platform: netease(网易云,默认) / qq(QQ音乐) / kugou(酷狗音乐)
    网易云添加后自动测试登录，回填昵称/会员信息；其他平台暂不自动登录。
    """
    data = request.get_json(force=True)
    platform = data.get("platform", "netease").strip() or "netease"
    if platform not in PLATFORMS:
        return jsonify({"code": 1, "msg": f"不支持的平台: {platform}，可选: {PLATFORM_NAMES}"})
    name = data.get("name", "").strip()
    cookie = data.get("cookie", "").strip()
    quota_limit = int(data.get("quota_limit", 0))

    if not name:
        return jsonify({"code": 1, "msg": "请填写账号别名"})

    # 网易云要求 Cookie 含 MUSIC_U；其他平台暂不强校验
    if platform == "netease":
        if not cookie or "MUSIC_U" not in cookie:
            return jsonify({"code": 1, "msg": "Cookie 必须包含 MUSIC_U"})

    # 新账号的 sort_order = 当前平台内最大值 + 1
    max_order = db.session.query(db.func.max(Account.sort_order)).filter(
        Account.platform == platform
    ).scalar() or 0
    acc = Account(
        platform=platform,
        name=name,
        cookie=cookie,
        quota_limit=quota_limit,
        sort_order=max_order + 1,
        enabled=True,
        last_check_at=datetime.now() if platform == "netease" else None,
    )
    # 网易云自动刷新账号信息
    if platform == "netease":
        _refresh_account_info(acc, cookie=cookie)
    db.session.add(acc)
    db.session.commit()
    platform_name = PLATFORM_NAMES.get(platform, platform)
    extra = f" ({acc.nickname})" if acc.nickname else ""
    logger.info("添加账号: [%s] %s (nickname=%s, vip=%s, sort_order=%d)", platform_name, name, acc.nickname, acc.vip_type, acc.sort_order)
    return jsonify({
        "code": 0,
        "data": acc.to_dict(monthly_downloaded=0),
        "msg": f"已添加: [{platform_name}] {name}" + extra,
    })


@api_bp.route("/accounts/<int:aid>", methods=["PUT"])
def update_account(aid: int):
    """更新账号信息

    请求体：{"name","cookie","quota_limit","enabled"} 中任意字段
    platform 在添加后不可修改；cookie 非空时重新测试登录（仅网易云）。
    """
    acc = Account.query.get(aid)
    if not acc:
        return jsonify({"code": 1, "msg": "账号不存在"})

    data = request.get_json(force=True)
    if "name" in data:
        acc.name = data["name"]
    if "quota_limit" in data:
        acc.quota_limit = int(data["quota_limit"])
    if "enabled" in data:
        acc.enabled = bool(data["enabled"])
    if "cookie" in data and data["cookie"]:
        acc.cookie = data["cookie"]
        _refresh_account_info(acc)

    db.session.commit()
    return jsonify({
        "code": 0,
        "data": acc.to_dict(monthly_downloaded=_monthly_downloaded(acc.id)),
    })


@api_bp.route("/accounts/<int:aid>", methods=["DELETE"])
def delete_account(aid: int):
    """删除账号（已下载记录的 account_id 置 NULL，不删歌曲）"""
    acc = Account.query.get(aid)
    if not acc:
        return jsonify({"code": 1, "msg": "账号不存在"})
    name = acc.name
    # 解除歌曲和任务的关联
    Song.query.filter_by(account_id=aid).update({"account_id": None})
    DownloadTask.query.filter_by(account_id=aid).update({"account_id": None})
    db.session.delete(acc)
    db.session.commit()
    logger.info("删除账号: %s (id=%s)", name, aid)
    return jsonify({"code": 0, "msg": f"已删除: {name}"})


@api_bp.route("/accounts/<int:aid>/move", methods=["POST"])
def move_account(aid: int):
    """调整账号使用顺序（与同平台相邻账号交换 sort_order）

    请求体：{"direction": "up"|"down"}
    """
    acc = Account.query.get(aid)
    if not acc:
        return jsonify({"code": 1, "msg": "账号不存在"})
    direction = (request.get_json(force=True) or {}).get("direction", "")
    if direction not in ("up", "down"):
        return jsonify({"code": 1, "msg": "direction 必须为 up 或 down"})

    # 按平台内 sort_order 升序拿到同平台账号
    all_acc = Account.query.filter_by(platform=acc.platform).order_by(Account.sort_order, Account.id).all()
    idx = next((i for i, a in enumerate(all_acc) if a.id == aid), -1)
    if idx < 0:
        return jsonify({"code": 1, "msg": "账号不存在"})

    if direction == "up":
        if idx == 0:
            return jsonify({"code": 1, "msg": "已是该平台第一个账号"})
        target = all_acc[idx - 1]
    else:
        if idx == len(all_acc) - 1:
            return jsonify({"code": 1, "msg": "已是该平台最后一个账号"})
        target = all_acc[idx + 1]

    # 交换 sort_order
    acc.sort_order, target.sort_order = target.sort_order, acc.sort_order
    db.session.commit()
    logger.info("账号顺序调整: [%s] %s <-> %s", acc.platform, acc.name, target.name)
    return jsonify({"code": 0, "msg": "顺序已更新"})


@api_bp.route("/accounts/import", methods=["POST"])
def import_accounts():
    """导入账号信息（JSON 格式）

    请求体：{"accounts":[{"platform","name","cookie","nickname","vip_type","vip_expire_at",
                         "quota_limit","sort_order","enabled"}, ...]}
    platform 缺省为 netease；旧版导出文件无 platform 字段时自动归为网易云。
    返回：{"code":0, "data":{"imported":N, "skipped":N, "total":N}, "msg":"..."}
    """
    data = request.get_json(force=True) or {}
    accounts = data.get("accounts") or []
    if not isinstance(accounts, list):
        return jsonify({"code": 1, "msg": "accounts 字段必须是数组"})

    imported = 0
    skipped = 0
    for a in accounts:
        name = (a.get("name") or "").strip()
        cookie = (a.get("cookie") or "").strip()
        if not name or not cookie:
            skipped += 1
            continue
        platform = (a.get("platform") or "netease").strip() or "netease"
        if platform not in PLATFORMS:
            platform = "netease"
        # 跳过同平台+同名已存在的账号（避免重复导入）
        if Account.query.filter_by(platform=platform, name=name).first():
            skipped += 1
            continue
        acc = Account(
            platform=platform,
            name=name,
            cookie=cookie,
            nickname=a.get("nickname", ""),
            vip_type=a.get("vip_type", 0),
            quota_limit=a.get("quota_limit", 0),
            sort_order=a.get("sort_order", 0),
            enabled=a.get("enabled", True),
        )
        # 解析会员到期时间
        expire_str = a.get("vip_expire_at")
        if expire_str:
            try:
                acc.vip_expire_at = datetime.strptime(expire_str, "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                pass
        db.session.add(acc)
        imported += 1
    db.session.commit()

    msg = f"共 {len(accounts)} 个：导入 {imported}，跳过(无 cookie/同名) {skipped}"
    logger.info("导入账号: %s", msg)
    return jsonify({
        "code": 0,
        "data": {"imported": imported, "skipped": skipped, "total": len(accounts)},
        "msg": msg,
    })


@api_bp.route("/accounts/export")
def export_accounts():
    """导出所有账号信息为 JSON 文件（含 cookie，敏感）

    返回 attachment 文件下载，文件名 accounts_export_YYYYMMDD_HHMMSS.json
    """
    accounts = Account.query.order_by(Account.platform, Account.sort_order, Account.id).all()
    payload = {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "0.5.0",
        "accounts": [
            {
                "platform": a.platform,
                "name": a.name,
                "cookie": a.cookie or "",
                "nickname": a.nickname or "",
                "vip_type": a.vip_type,
                "vip_expire_at": a.vip_expire_at.strftime("%Y-%m-%d %H:%M:%S") if a.vip_expire_at else None,
                "quota_limit": a.quota_limit,
                "sort_order": a.sort_order,
                "enabled": a.enabled,
            }
            for a in accounts
        ],
    }
    filename = f"accounts_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    body = _json.dumps(payload, ensure_ascii=False, indent=2)
    resp = Response(body, mimetype="application/json")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    logger.info("导出账号信息: %d 个账号", len(accounts))
    return resp


@api_bp.route("/accounts/<int:aid>/test", methods=["POST"])
def test_account_login(aid: int):
    """测试指定账号的登录状态，并刷新会员信息

    仅 netease 平台支持自动登录测试；其他平台返回暂不支持。
    """
    acc = Account.query.get(aid)
    if not acc:
        return jsonify({"code": 1, "msg": "账号不存在"})

    if acc.platform != "netease":
        return jsonify({
            "code": 1,
            "msg": f"该平台（{PLATFORM_NAMES.get(acc.platform, acc.platform)}）暂不支持登录测试",
        })

    try:
        client = _create_client(cookie=acc.cookie)
        info = client.get_account_info()
        if info.get("code") != 200:
            return jsonify({"code": 1, "msg": f"Cookie 无效: {info.get('msg', '未知错误')}"})
        # 刷新昵称/会员类型/到期时间
        _refresh_account_info(acc)
        db.session.commit()
        vip_text = {0: "非会员", 11: "黑胶VIP", 12: "SVIP"}.get(acc.vip_type, f"vipType={acc.vip_type}")
        return jsonify({
            "code": 0,
            "msg": f"登录成功: {acc.nickname or '未知'} ({vip_text})",
            "data": acc.to_dict(monthly_downloaded=_monthly_downloaded(acc.id)),
        })
    except Exception as e:
        return jsonify({"code": 1, "msg": f"连接网易云API服务失败: {e}"})


@api_bp.route("/accounts/stats")
def accounts_stats():
    """所有账号的本月下载统计（总览页账号卡片用）"""
    accounts = Account.query.filter_by(enabled=True).order_by(Account.platform, Account.sort_order, Account.id).all()
    data = []
    for acc in accounts:
        downloaded = _monthly_downloaded(acc.id)
        data.append({
            "id": acc.id,
            "platform": acc.platform,
            "platform_name": PLATFORM_NAMES.get(acc.platform, acc.platform),
            "name": acc.name,
            "nickname": acc.nickname,
            "vip_text": {0: "非会员", 11: "黑胶VIP", 12: "SVIP"}.get(acc.vip_type, f"vipType={acc.vip_type}"),
            "vip_expire_at": acc.vip_expire_at.strftime("%Y-%m-%d %H:%M:%S") if acc.vip_expire_at else None,
            "monthly_downloaded": downloaded,
            "quota_limit": acc.quota_limit,
            "unlimited": acc.quota_limit == 0,
        })
    return jsonify({"code": 0, "data": data})


# ======================================================================
# 发现接口（排行榜 / 热门歌单 / 分类）
# ======================================================================
@api_bp.route("/discover/toplists")
def discover_toplists():
    """获取官方排行榜列表"""
    try:
        client = _get_client(_req_platform())
        lists = client.get_toplists()
        return jsonify({"code": 0, "data": lists})
    except Exception as e:
        logger.exception("获取排行榜失败: %s", e)
        return jsonify({"code": 1, "msg": str(e)})


@api_bp.route("/discover/playlists")
def discover_playlists():
    """获取热门/分类歌单（支持分页）

    参数：
        cat: 分类名（默认"全部"）
        limit: 每页数量（默认 20）
        order: hot / new
        page: 页码（从 1 开始，默认 1）
    返回：
        {data: [...], total, page, limit, pages}
    """
    cat = request.args.get("cat", "全部")
    limit = int(request.args.get("limit", 20))
    order = request.args.get("order", "hot")
    page = max(1, int(request.args.get("page", 1)))
    offset = (page - 1) * limit
    try:
        client = _get_client(_req_platform())
        playlists, total = client.get_hot_playlists(cat=cat, limit=limit, order=order, offset=offset)
        pages = (total + limit - 1) // limit if limit > 0 else 0
        return jsonify({
            "code": 0,
            "data": playlists,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": pages,
        })
    except Exception as e:
        logger.exception("获取热门歌单失败: %s", e)
        return jsonify({"code": 1, "msg": str(e)})


@api_bp.route("/discover/categories")
def discover_categories():
    """获取所有歌单分类"""
    try:
        client = _get_client(_req_platform())
        cats = client.get_playlist_categories()
        # 只返回分类名列表，前端用
        names = [c["name"] for c in cats if c.get("name")]
        return jsonify({"code": 0, "data": names})
    except Exception as e:
        logger.exception("获取歌单分类失败: %s", e)
        # 回退到常用分类
        fallback = ["全部", "华语", "流行", "摇滚", "民谣", "电子", "说唱", "轻音乐", "爵士", "乡村", "古典"]
        return jsonify({"code": 0, "data": fallback})


@api_bp.route("/discover/search", methods=["POST"])
def discover_search():
    """搜索歌曲或专辑（展示用，不下载，不应用排除过滤）

    请求体：{"keyword":"周杰伦", "type":"song|artist|album", "limit":50, "offset":0}
        - type=song/artist: 搜索单曲（type=1）
        - type=album: 搜索专辑（type=10）

    返回歌曲模式：
        {"code":0, "data":{"items":[{"id","name","artists","album","fee","downloaded"}],
                            "total":N, "page":P, "pages":P, "type":"song"}}
    返回专辑模式：
        {"code":0, "data":{"items":[{"id","name","artist","size","publish_time"}],
                            "total":N, "page":P, "pages":P, "type":"album"}}
    """
    data = request.get_json(force=True) or {}
    keyword = (data.get("keyword") or "").strip()
    search_type = (data.get("type") or "song").strip()
    if search_type not in ("song", "artist", "album"):
        search_type = "song"
    limit = _safe_int(data.get("limit", 50), 50, lo=1, hi=100)
    offset = _safe_int(data.get("offset", 0), 0, lo=0)
    page = offset // limit + 1 if limit > 0 else 1
    if not keyword:
        return jsonify({"code": 1, "msg": "请输入搜索关键词"})

    try:
        client = _get_client(_req_platform())
        if search_type == "album":
            res = client.search_albums(keyword, limit=limit, offset=offset)
            items = res.get("items", [])
            total = res.get("total", 0)
            pages = (total + limit - 1) // limit if limit > 0 else 0
            return jsonify({
                "code": 0,
                "data": {
                    "items": items,
                    "total": total,
                    "page": page,
                    "pages": pages,
                    "type": "album",
                },
            })
        else:
            res = client.search_songs(keyword, limit=limit, offset=offset)
            items = res.get("items", [])
            total = res.get("total", 0)
            pages = (total + limit - 1) // limit if limit > 0 else 0
    except Exception as e:
        logger.exception("搜索失败: %s", e)
        return jsonify({"code": 1, "msg": str(e)})

    # 标记已下载/进行中（仅歌曲模式需要）
    with db.session.no_autoflush:
        downloaded_ids = set()
        if items:
            ids = [t["id"] for t in items if t.get("id")]
            rows = db.session.query(Song.id).filter(
                Song.id.in_(ids), Song.status == "success"
            ).all()
            downloaded_ids = {r[0] for r in rows}
            pending_rows = db.session.query(DownloadTask.song_id).filter(
                DownloadTask.song_id.in_(ids),
                DownloadTask.status.in_(["pending", "downloading"]),
            ).all()
            downloaded_ids |= {r[0] for r in pending_rows}

    result_items = []
    for t in items:
        t2 = dict(t)
        t2["downloaded"] = t.get("id") in downloaded_ids
        result_items.append(t2)
    return jsonify({
        "code": 0,
        "data": {
            "items": result_items,
            "total": total,
            "page": page,
            "pages": pages,
            "type": "song",
        },
    })


@api_bp.route("/discover/search-download", methods=["POST"])
def discover_search_download():
    """搜索并批量下载当前页歌曲（应用搜索场景的排除过滤）

    请求体：{"keyword":"周杰伦", "limit":50, "offset":0}
    返回：{"code":0, "data":{"enqueued","excluded","skipped","total"}, "msg":"..."}
    """
    data = request.get_json(force=True) or {}
    keyword = (data.get("keyword") or "").strip()
    limit = _safe_int(data.get("limit", 50), 50, lo=1, hi=100)
    offset = _safe_int(data.get("offset", 0), 0, lo=0)
    if not keyword:
        return jsonify({"code": 1, "msg": "请输入搜索关键词"})

    tm = _get_task_manager()
    try:
        result = tm.search_and_download(keyword, limit=limit, offset=offset, platform=_req_platform())
    except ValueError as e:
        return jsonify({"code": 1, "msg": str(e)})
    msg = f"共 {result['total']} 首：入队 {result['enqueued']}，排除 {result['excluded']}，跳过 {result['skipped']}"
    return jsonify({"code": 0, "data": result, "msg": msg})


@api_bp.route("/discover/album-download", methods=["POST"])
def discover_album_download():
    """下载专辑内全部歌曲（应用搜索场景的排除过滤）

    请求体：{"album_id":123, "album_name":"范特西"}
    返回：{"code":0, "data":{"enqueued","excluded","skipped","total"}, "msg":"..."}
    """
    data = request.get_json(force=True) or {}
    album_id = data.get("album_id")
    album_name = (data.get("album_name") or "").strip()
    if not album_id:
        return jsonify({"code": 1, "msg": "缺少 album_id"})

    tm = _get_task_manager()
    try:
        result = tm.download_album(int(album_id), album_name, platform=_req_platform())
    except ValueError as e:
        return jsonify({"code": 1, "msg": str(e)})
    msg = f"专辑共 {result['total']} 首：入队 {result['enqueued']}，排除 {result['excluded']}，跳过 {result['skipped']}"
    return jsonify({"code": 0, "data": result, "msg": msg})


@api_bp.route("/discover/download-song", methods=["POST"])
def discover_download_song():
    """下载单首歌曲（用户主动选择，不应用排除过滤）

    请求体：{"song_id":123, "name":"...", "artists":"...", "fee":0}
    """
    data = request.get_json(force=True) or {}
    song_id = data.get("song_id")
    name = (data.get("name") or "").strip()
    artists = (data.get("artists") or "").strip()
    fee = _safe_int(data.get("fee", 0), 0, lo=0, hi=100)
    if not song_id:
        return jsonify({"code": 1, "msg": "缺少 song_id"})

    tm = _get_task_manager()
    ok = tm.download_single_song(int(song_id), name, artists, fee, platform=_req_platform())
    if ok:
        return jsonify({"code": 0, "msg": f"已加入下载队列: {name}"})
    return jsonify({"code": 1, "msg": "该歌曲已下载或正在下载中"})


# ======================================================================
# 用户管理接口（仅管理员）
# ======================================================================
def _require_admin():
    """校验当前用户是否为管理员，失败返回 (response, status) 元组"""
    user = current_user()
    if not user or not user.is_admin:
        return jsonify({"code": 403, "msg": "仅管理员可访问"}), 403
    return None


@api_bp.route("/users")
def list_users():
    """获取用户列表（仅管理员）"""
    err = _require_admin()
    if err:
        return err
    users = User.query.order_by(User.id).all()
    return jsonify({"code": 0, "data": [u.to_dict() for u in users]})


@api_bp.route("/users", methods=["POST"])
def add_user():
    """创建用户（仅管理员）

    请求体：{"username":"xxx", "password":"xxx", "is_admin":false}
    """
    err = _require_admin()
    if err:
        return err
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    is_admin = bool(data.get("is_admin", False))
    if not username or not password:
        return jsonify({"code": 1, "msg": "用户名和密码不能为空"})
    if len(password) < 6:
        return jsonify({"code": 1, "msg": "密码长度至少 6 位"})
    if User.query.filter_by(username=username).first():
        return jsonify({"code": 1, "msg": f"用户名 '{username}' 已存在"})

    user = User(username=username, is_admin=is_admin, enabled=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    logger.info("创建用户: %s (管理员=%s)", username, is_admin)
    return jsonify({"code": 0, "msg": f"用户 '{username}' 已创建", "data": user.to_dict()})


@api_bp.route("/users/<int:uid>", methods=["PUT"])
def update_user(uid: int):
    """更新用户信息（仅管理员）

    请求体（任选字段）：
        {"is_admin":bool, "enabled":bool, "password":"新密码"}
    注意：管理员可重置任意用户密码；当前用户不能取消自己的管理员身份
    """
    err = _require_admin()
    if err:
        return err
    user = User.query.get(uid)
    if not user:
        return jsonify({"code": 1, "msg": "用户不存在"})

    data = request.get_json(force=True) or {}
    me = current_user()

    # 修改密码
    if "password" in data:
        new_pwd = data["password"] or ""
        if len(new_pwd) < 6:
            return jsonify({"code": 1, "msg": "密码长度至少 6 位"})
        user.set_password(new_pwd)

    # 修改管理员身份（不能取消自己的管理员身份）
    if "is_admin" in data:
        new_is_admin = bool(data["is_admin"])
        if me and me.id == user.id and not new_is_admin:
            return jsonify({"code": 1, "msg": "不能取消自己的管理员身份"})
        user.is_admin = new_is_admin

    # 修改启用状态（不能禁用自己）
    if "enabled" in data:
        new_enabled = bool(data["enabled"])
        if me and me.id == user.id and not new_enabled:
            return jsonify({"code": 1, "msg": "不能禁用自己的账号"})
        user.enabled = new_enabled

    db.session.commit()
    logger.info("更新用户: %s", user.username)
    return jsonify({"code": 0, "msg": "用户信息已更新", "data": user.to_dict()})


@api_bp.route("/users/<int:uid>", methods=["DELETE"])
def delete_user(uid: int):
    """删除用户（仅管理员）

    限制：不能删除自己；不能删除最后一个管理员
    """
    err = _require_admin()
    if err:
        return err
    user = User.query.get(uid)
    if not user:
        return jsonify({"code": 1, "msg": "用户不存在"})

    me = current_user()
    if me and me.id == user.id:
        return jsonify({"code": 1, "msg": "不能删除自己"})

    # 不能删除最后一个管理员
    if user.is_admin:
        admin_count = User.query.filter_by(is_admin=True, enabled=True).count()
        if admin_count <= 1:
            return jsonify({"code": 1, "msg": "不能删除最后一个管理员"})

    db.session.delete(user)
    db.session.commit()
    logger.info("删除用户: %s", user.username)
    return jsonify({"code": 0, "msg": f"用户 '{user.username}' 已删除"})
