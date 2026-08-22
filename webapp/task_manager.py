"""下载任务调度器

职责：
1. APScheduler 定时扫描已启用的歌单，发现新歌入队
2. 后台下载工作线程串行处理任务队列（避免网易云风控）
3. 多账号管理：接力模式 / 轮询模式
4. 失败任务记录到 songs 表（status=failed），支持重试
5. 进度通过 DownloadTask 表实时更新，前端轮询读取

线程安全说明：
- Flask 多线程下 SQLAlchemy session 需在子线程内创建/销毁
- 使用 app.app_context() 确保子线程内可访问数据库
"""

import logging
import queue
import random
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func

from models import Account, DownloadTask, Playlist, Setting, Song, db, get_custom_api_url
from core.downloader import Downloader, build_filename, sanitize_filename
from core.metadata import write_tags
from core.providers.netease import NeteaseProvider
from core.providers import get_provider

logger = logging.getLogger(__name__)

# 项目根目录（code/client），用于解析相对路径的 output_dir
# frozen: PyInstaller 打包后用 exe 同级目录作为根目录
if getattr(sys, "frozen", False):
    _ROOT = Path(sys.executable).resolve().parent
else:
    _ROOT = Path(__file__).resolve().parent.parent

# 所有账号达每小时限额时，暂停下载的时长（秒）
_HOURLY_PAUSE_SECONDS = 1800


def _month_start() -> datetime:
    """本月 1 号 0 点（用于额度统计）"""
    now = datetime.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _hour_start() -> datetime:
    """当前自然小时的 0 分 0 秒（用于每小时限额统计）"""
    now = datetime.now()
    return now.replace(minute=0, second=0, microsecond=0)


def _parse_sync_times(raw: str) -> list[tuple[int, int]]:
    """解析 "03:00,09:00,21:00" 为 [(3,0),(9,0),(21,0)]

    非法格式会被跳过，返回去重后的列表（按时间顺序）。
    """
    result = []
    seen = set()
    for part in (raw or "").split(","):
        part = part.strip()
        m = re.match(r"^(\d{1,2}):(\d{1,2})$", part)
        if not m:
            continue
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            continue
        if (h, mi) in seen:
            continue
        seen.add((h, mi))
        result.append((h, mi))
    result.sort()
    return result


class AccountSelector:
    """账号选择器：管理多账号下载调度

    接力模式（fallback）：维护当前账号指针，达额度或失败时切换到下一个
    轮询模式（round_robin）：维护全局计数器，每个新任务按顺序分配账号

    账号会被跳过的条件（任一满足）：
    - 月额度已满（quota_limit>0 且本月成功数已达）
    - 小时限额已满（hourly_limit>0 且本自然小时成功数已达）
    """

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        # 接力模式：当前账号 ID 指针（None 表示尚未初始化）
        self._fallback_current_id: int | None = None
        # 轮询模式：全局计数器
        self._rr_counter = 0

    def _get_enabled_accounts(self, platform: str = "netease") -> list[Account]:
        """获取指定平台所有启用的账号（按 sort_order 升序排序）"""
        with self.app.app_context():
            return Account.query.filter_by(platform=platform, enabled=True).order_by(Account.sort_order, Account.id).all()

    def _filter_by_vip_preference(self, accounts: list[Account], prefer_non_vip: bool, fee: int) -> list[Account]:
        """按 VIP 偏好过滤账号列表

        - prefer_non_vip=False：不做过滤，返回原列表
        - prefer_non_vip=True + fee=1（VIP 歌曲）：只保留 VIP 账号（vip_type>0）
        - prefer_non_vip=True + fee!=1（非 VIP 歌曲）：优先非 VIP 账号；
          若无非 VIP 账号，回退到 VIP 账号（避免无账号可用）
        """
        if not prefer_non_vip:
            return accounts
        if fee == 1:
            # VIP 歌曲：只用 VIP 账号
            vip_acc = [a for a in accounts if a.vip_type > 0]
            return vip_acc if vip_acc else accounts  # 无 VIP 账号回退到全部
        # 非 VIP 歌曲：优先非 VIP 账号
        non_vip = [a for a in accounts if a.vip_type == 0]
        return non_vip if non_vip else accounts  # 无非 VIP 账号回退到全部

    def _hourly_limit(self) -> int:
        """读取每小时单账号下载限额配置（0=不限制）"""
        with self.app.app_context():
            return int(Setting.get("hourly_limit_per_account", "50"))

    def is_quota_exceeded(self, account_id: int) -> bool:
        """检查账号本月是否已达额度

        quota_limit == 0 表示不限制，永远不超
        """
        with self.app.app_context():
            acc = Account.query.get(account_id)
            if not acc or acc.quota_limit <= 0:
                return False
            month_start = _month_start()
            count = db.session.query(func.count(Song.id)).filter(
                Song.account_id == account_id,
                Song.status == "success",
                Song.downloaded_at >= month_start,
            ).scalar() or 0
            return count >= acc.quota_limit

    def is_hourly_exceeded(self, account_id: int) -> bool:
        """检查账号当前自然小时是否已达下载上限

        hourly_limit == 0 表示不限制
        """
        limit = self._hourly_limit()
        if limit <= 0:
            return False
        with self.app.app_context():
            hour_start = _hour_start()
            count = db.session.query(func.count(Song.id)).filter(
                Song.account_id == account_id,
                Song.status == "success",
                Song.downloaded_at >= hour_start,
            ).scalar() or 0
            return count >= limit

    def _is_available(self, account_id: int) -> bool:
        """账号是否可用（月额度和小时限额均未满）"""
        return not self.is_quota_exceeded(account_id) and not self.is_hourly_exceeded(account_id)

    def all_hourly_limited(self, platform: str = "netease") -> bool:
        """指定平台所有启用账号是否都因小时限额满而不可用

        用于决定是否触发 30 分钟暂停。月额度满的账号不算"因小时限额满"。
        返回 False 的情况：无账号、或至少有一个账号月额度未满但小时限额也未满。
        """
        accounts = self._get_enabled_accounts(platform=platform)
        if not accounts:
            return False
        for a in accounts:
            # 月额度满的账号本来就不能用，不计入
            if self.is_quota_exceeded(a.id):
                continue
            # 月额度未满但小时限额未满 → 还有可用账号
            if not self.is_hourly_exceeded(a.id):
                return False
        # 所有"月额度未满"的账号都因小时限额满 → 触发暂停
        return True

    def pick_for_fallback(self, prefer_non_vip: bool = False, fee: int = 0, platform: str = "netease") -> Account | None:
        """接力模式：取当前账号，若不可用则切到下一个

        Args:
            prefer_non_vip: 是否优先非VIP账号
            fee: 歌曲费用类型（1=VIP歌曲）
            platform: 平台标识，默认 netease

        Returns:
            可用的 Account，全部不可用返回 None
        """
        with self._lock:
            accounts = self._get_enabled_accounts(platform=platform)
            if not accounts:
                return None
            # 按 VIP 偏好过滤
            accounts = self._filter_by_vip_preference(accounts, prefer_non_vip, fee)
            if not accounts:
                return None

            # 初始化指针
            if self._fallback_current_id is None:
                self._fallback_current_id = accounts[0].id

            # 从当前指针开始遍历一轮
            ids = [a.id for a in accounts]
            try:
                start_idx = ids.index(self._fallback_current_id)
            except ValueError:
                start_idx = 0
                self._fallback_current_id = ids[0]

            for offset in range(len(ids)):
                idx = (start_idx + offset) % len(ids)
                aid = ids[idx]
                if self._is_available(aid):
                    self._fallback_current_id = aid
                    with self.app.app_context():
                        return Account.query.get(aid)
            return None

    def switch_to_next(self, current_id: int, prefer_non_vip: bool = False, fee: int = 0, platform: str = "netease") -> Account | None:
        """接力模式：强制切到下一个账号（失败时调用）

        Args:
            current_id: 当前账号 ID
            prefer_non_vip: 是否优先非VIP账号
            fee: 歌曲费用类型（1=VIP歌曲）
            platform: 平台标识，默认 netease

        Returns:
            下一个可用账号，无则 None
        """
        with self._lock:
            accounts = self._get_enabled_accounts(platform=platform)
            if not accounts:
                return None
            accounts = self._filter_by_vip_preference(accounts, prefer_non_vip, fee)
            if not accounts:
                return None
            ids = [a.id for a in accounts]
            try:
                idx = ids.index(current_id)
            except ValueError:
                idx = -1
            # 从下一个开始遍历一圈
            for offset in range(1, len(ids) + 1):
                next_idx = (idx + offset) % len(ids)
                aid = ids[next_idx]
                if self._is_available(aid):
                    self._fallback_current_id = aid
                    with self.app.app_context():
                        return Account.query.get(aid)
            return None

    def pick_for_round_robin(self, prefer_non_vip: bool = False, fee: int = 0, platform: str = "netease") -> Account | None:
        """轮询模式：按计数器取下一个账号，跳过不可用的

        Args:
            prefer_non_vip: 是否优先非VIP账号
            fee: 歌曲费用类型（1=VIP歌曲）
            platform: 平台标识，默认 netease

        Returns:
            可用 Account，全部不可用返回 None
        """
        with self._lock:
            accounts = self._get_enabled_accounts(platform=platform)
            if not accounts:
                return None
            accounts = self._filter_by_vip_preference(accounts, prefer_non_vip, fee)
            if not accounts:
                return None
            ids = [a.id for a in accounts]
            n = len(ids)
            for offset in range(n):
                idx = (self._rr_counter + offset) % n
                aid = ids[idx]
                if self._is_available(aid):
                    self._rr_counter = (idx + 1) % n
                    with self.app.app_context():
                        return Account.query.get(aid)
            return None


class TaskManager:
    """下载任务管理器：调度 + 下载工作线程 + 多账号"""

    def __init__(self, app):
        self.app = app
        self._task_queue: queue.Queue[int] = queue.Queue()  # 存放 DownloadTask.pk
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._scheduler = BackgroundScheduler()
        self._started = False
        self._account_selector = AccountSelector(app)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动调度器和工作线程"""
        if self._started:
            return
        self._started = True
        self._stop_event.clear()

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="download-worker")
        self._worker_thread.start()

        self._scheduler.start()
        self._refresh_schedule()
        logger.info("TaskManager 已启动")

    def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        self._scheduler.shutdown(wait=False)
        self._started = False
        logger.info("TaskManager 已停止")

    def _refresh_schedule(self) -> None:
        """根据设置重建定时同步任务（多时间点 cron + 抖动延迟）

        - sync_times: "03:00,09:00,21:00" → 每个时间点一个 cron job
        - sync_jitter: 触发后随机延迟 0~N 秒再执行
        """
        with self.app.app_context():
            enabled = Setting.get("auto_sync_enabled", "true") == "true"
            times_raw = Setting.get("sync_times", "03:00,09:00,21:00")
            jitter = int(Setting.get("sync_jitter", "600"))

        # 清掉旧的同步任务（旧版单 job id=auto_sync + 新版多 job auto_sync_N）
        try:
            self._scheduler.remove_job("auto_sync")
        except Exception:
            pass
        for j in self._scheduler.get_jobs():
            if j.id.startswith("auto_sync_"):
                self._scheduler.remove_job(j.id)

        if not enabled:
            logger.info("定时同步已禁用")
            return

        time_points = _parse_sync_times(times_raw)
        if not time_points:
            logger.warning("定时同步已启用但 sync_times 解析为空，未添加任务: %s", times_raw)
            return

        for i, (h, mi) in enumerate(time_points):
            self._scheduler.add_job(
                self._sync_all_playlists_with_jitter,
                "cron",
                hour=h,
                minute=mi,
                second=0,
                args=[jitter],
                id=f"auto_sync_{i}",
                replace_existing=True,
            )
        logger.info(
            "定时同步已启用，共 %d 个时间点: %s，抖动 0~%ds",
            len(time_points),
            ", ".join(f"{h:02d}:{mi:02d}" for h, mi in time_points),
            jitter,
        )

    def _sync_all_playlists_with_jitter(self, jitter: int = 0) -> None:
        """带抖动延迟的同步入口（供 cron 调用）

        先随机延迟 0~jitter 秒（jitter<=0 时不延迟），再执行同步。
        延迟期间若服务停止会被打断（_stop_event 置位后线程退出）。
        """
        if jitter and jitter > 0:
            delay = random.randint(0, jitter)
            logger.info("定时同步触发，抖动延迟 %ds 后执行", delay)
            # 分段 sleep 以便及时响应停止
            end = time.time() + delay
            while time.time() < end:
                if self._stop_event.is_set():
                    logger.info("抖动延迟期间服务停止，放弃本次同步")
                    return
                time.sleep(min(1.0, end - time.time()))
        self._sync_all_playlists()

    # ------------------------------------------------------------------
    # 客户端构建
    # ------------------------------------------------------------------
    def _get_client_for_account(self, account: Account) -> NeteaseProvider:
        """用指定账号的 cookie 创建 provider（仅用已传入 cookie，不查库）

        P1 薄包装：返回已注入凭证与自定义地址的 provider。
        按账号所属平台分发，避免不同平台请求互相串用。
        """
        p = get_provider(account.platform or "netease")
        p.set_cookie(account.cookie or "")
        with self.app.app_context():
            p.set_custom_base_url(get_custom_api_url())
        return p

    def _get_client_default(self, platform: str = "netease") -> NeteaseProvider:
        """用第一个启用的指定平台账号 cookie 创建 provider

        用于同步歌单等公开接口。无启用账号时用空 cookie。
        此函数被调度线程调用（_sync_all_playlists 等），后台线程无 request
        context，Account.query 必须包 app_context。
        """
        with self.app.app_context():
            acc = Account.query.filter_by(platform=platform, enabled=True).order_by(Account.sort_order, Account.id).first()
            cookie = acc.cookie if acc else ""
            custom_url = get_custom_api_url()
        p = get_provider(platform)
        p.set_cookie(cookie or "")
        p.set_custom_base_url(custom_url)
        return p

    def _get_downloader(self) -> Downloader:
        with self.app.app_context():
            output_dir = Setting.get("output_dir", "downloads")
            max_retries = int(Setting.get("max_retries", "3"))
        p = Path(output_dir)
        if not p.is_absolute():
            p = _ROOT / output_dir
        return Downloader(output_dir=p, max_retries=max_retries)

    # ------------------------------------------------------------------
    # 同步歌单
    # ------------------------------------------------------------------
    def _sync_all_playlists(self) -> None:
        """定时任务：扫描所有已启用的歌单，把新歌加入下载队列"""
        with self.app.app_context():
            playlists = Playlist.query.filter_by(enabled=True).all()
            if not playlists:
                logger.info("没有已启用的歌单，跳过同步")
                return
            pl_list = [(p.id, p.name, p.platform or "netease") for p in playlists]

        # 按平台分组同步，每组用对应平台的默认客户端（不需要鉴权，歌单详情公开）
        groups: dict[str, list[tuple[int, str]]] = {}
        for pl_id, pl_name, pl_platform in pl_list:
            groups.setdefault(pl_platform, []).append((pl_id, pl_name))
        for platform, group in groups.items():
            client = self._get_client_default(platform=platform)
            for pl_id, pl_name in group:
                try:
                    self._sync_playlist(client, pl_id, platform=platform)
                except Exception as e:
                    logger.exception("同步歌单 %s 失败: %s", pl_name, e)

    def _sync_playlist(self, client: NeteaseProvider, playlist_id: int, platform: str = "netease") -> int:
        """同步单个歌单：拉取歌曲列表，过滤已下载，入队

        Args:
            client: Provider 实例
            playlist_id: 歌单 ID
            platform: 平台标识，默认 netease
        """
        with self.app.app_context():
            pl = Playlist.query.get(playlist_id)
            if not pl:
                return 0
            limit = pl.limit_count
            pl_name = pl.name

        detail = client.get_playlist_detail(playlist_id, limit=limit)
        if not detail:
            return 0
        tracks = detail.get("tracks", [])

        with self.app.app_context():
            pl = Playlist.query.get(playlist_id)
            if pl:
                pl.track_count = detail.get("track_count", len(tracks))
                pl.last_synced_at = datetime.now()
                db.session.commit()

            new_tracks = []
            excluded_count = 0
            for t in tracks:
                sid = t["id"]
                existing = Song.query.filter_by(id=sid, status="success").first()
                if existing:
                    # 已下载：在当前歌单记录一条"已下载"任务（不重复下载）
                    already = DownloadTask.query.filter_by(
                        song_id=sid, playlist_id=playlist_id, status="skipped"
                    ).first()
                    if not already:
                        task = DownloadTask(
                            platform=platform,
                            song_id=sid,
                            song_name=t["name"],
                            artists=t.get("artists", ""),
                            playlist_id=playlist_id,
                            playlist_name=pl_name,
                            status="skipped",
                            progress=100,
                        )
                        db.session.add(task)
                        db.session.commit()
                    continue
                pending = DownloadTask.query.filter(
                    DownloadTask.song_id == sid,
                    DownloadTask.status.in_(["pending", "downloading"]),
                ).first()
                if pending:
                    continue
                # 排除关键字过滤（仅当 scope 包含 playlist 时）
                if self._exclude_enabled("playlist") and self._should_exclude(t.get("name", ""), t.get("artists", "")):
                    excluded_count += 1
                    logger.info("歌单同步跳过(命中排除关键字): %s - %s", t.get("artists", ""), t.get("name", ""))
                    continue
                new_tracks.append(t)

            for t in new_tracks:
                task = DownloadTask(
                    platform=platform,
                    song_id=t["id"],
                    song_name=t["name"],
                    artists=t.get("artists", ""),
                    playlist_id=playlist_id,
                    playlist_name=pl_name,
                    status="pending",
                    fee=t.get("fee", 0),
                )
                db.session.add(task)
                db.session.commit()
                self._task_queue.put(task.pk)

            logger.info("歌单 [%s] 新增 %d 首到下载队列（排除 %d 首）", pl_name, len(new_tracks), excluded_count)
            return len(new_tracks)

    def sync_playlist(self, playlist_id: int, platform: str = "netease") -> int:
        """同步单个歌单

        Args:
            playlist_id: 歌单 ID
            platform: 平台标识（歌单记录已有平台时以记录为准）

        Returns:
            新增到下载队列的歌曲数
        """
        # 优先从 Playlist 记录读取平台归属
        with self.app.app_context():
            pl = Playlist.query.get(playlist_id)
            if pl:
                platform = pl.platform or platform or "netease"
        client = self._get_client_default(platform=platform)
        return self._sync_playlist(client, playlist_id, platform=platform)

    def sync_all(self, platform: str = "netease") -> int:
        """同步所有已启用的歌单

        Args:
            platform: 平台标识（歌单记录已有平台时以记录为准）

        Returns:
            新增到下载队列的歌曲总数
        """
        with self.app.app_context():
            playlists = Playlist.query.filter_by(enabled=True).all()
            pl_list = [(p.id, p.name, p.platform or platform or "netease") for p in playlists]
        if not pl_list:
            return 0
        # 按平台分组，每组用对应平台的默认客户端
        groups: dict[str, list[tuple[int, str]]] = {}
        for pl_id, pl_name, pl_platform in pl_list:
            groups.setdefault(pl_platform, []).append((pl_id, pl_name))
        total = 0
        for plat, group in groups.items():
            client = self._get_client_default(platform=plat)
            for pl_id, pl_name in group:
                total += self._sync_playlist(client, pl_id, platform=plat)
        return total

    # ------------------------------------------------------------------
    # 排除关键字过滤 + 搜索下载
    # ------------------------------------------------------------------
    def _should_exclude(self, name: str, artists: str = "") -> bool:
        """检查歌曲是否命中排除关键字

        读取 exclude_keywords 配置（英文逗号分隔），对每个关键字做
        大小写不敏感子串匹配，同时检查歌名和歌手名，命中任意一个返回 True。
        """
        keywords_str = Setting.get("exclude_keywords", "")
        if not keywords_str:
            return False
        # 按英文逗号分割，去除空白，忽略空字符串
        keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
        if not keywords:
            return False
        haystack = f"{name} {artists}".lower()
        for kw in keywords:
            if kw.lower() in haystack:
                return True
        return False

    def _exclude_enabled(self, scope: str) -> bool:
        """检查指定场景是否启用排除过滤

        Args:
            scope: "playlist" 或 "search"

        Returns:
            True 表示该场景应应用排除过滤

        说明：
            exclude_scope 配置为逗号分隔字符串（如 "playlist,search"）。
            兼容旧值 "both"（自动当作两者都启用）。
        """
        setting_scope = Setting.get("exclude_scope", "playlist,search")
        # 兼容旧值 both
        if setting_scope == "both":
            return True
        # 按逗号分割成列表，去除空白，检查 scope 是否在列表中
        scopes = [s.strip() for s in setting_scope.split(",") if s.strip()]
        return scope in scopes

    def search_and_download(self, keyword: str, limit: int = 50, offset: int = 0, platform: str = "netease") -> dict:
        """搜索歌手歌曲并入队下载

        Args:
            keyword: 搜索关键词（如歌手名或歌曲名）
            limit: 搜索结果数量（最大 100）
            offset: 偏移量（用于下载指定页）
            platform: 平台标识，默认 netease

        Returns:
            {"enqueued": int, "excluded": int, "skipped": int, "total": int}
        """
        client = self._get_client_default(platform=platform)
        search_res = client.search_songs(keyword, limit=limit, offset=offset)
        tracks = search_res.get("items", [])
        total = len(tracks)
        if total == 0:
            return {"enqueued": 0, "excluded": 0, "skipped": 0, "total": 0}

        enqueued = 0
        excluded = 0
        skipped = 0
        pl_name = f"搜索: {keyword}"
        search_scope_enabled = self._exclude_enabled("search")

        with self.app.app_context():
            for t in tracks:
                sid = t.get("id")
                if not sid:
                    continue
                # 排除关键字过滤（仅当 scope 包含 search 时）
                if search_scope_enabled and self._should_exclude(t.get("name", ""), t.get("artists", "")):
                    excluded += 1
                    logger.info("搜索下载跳过(命中排除关键字): %s - %s", t.get("artists", ""), t.get("name", ""))
                    continue
                # 过滤已下载成功
                existing = Song.query.filter_by(id=sid, status="success").first()
                if existing:
                    skipped += 1
                    continue
                # 过滤进行中任务
                pending = DownloadTask.query.filter(
                    DownloadTask.song_id == sid,
                    DownloadTask.status.in_(["pending", "downloading"]),
                ).first()
                if pending:
                    skipped += 1
                    continue
                task = DownloadTask(
                    platform=platform,
                    song_id=sid,
                    song_name=t.get("name", ""),
                    artists=t.get("artists", ""),
                    playlist_id=None,
                    playlist_name=pl_name,
                    status="pending",
                    fee=t.get("fee", 0),
                )
                db.session.add(task)
                db.session.commit()
                self._task_queue.put(task.pk)
                enqueued += 1

        logger.info(
            "搜索 [%s] 共 %d 首：入队 %d，排除 %d，跳过(已下载/进行中) %d",
            keyword, total, enqueued, excluded, skipped,
        )
        return {"enqueued": enqueued, "excluded": excluded, "skipped": skipped, "total": total}

    def download_album(self, album_id: int, album_name: str = "", platform: str = "netease") -> dict:
        """下载专辑内全部歌曲（应用搜索场景排除过滤）

        Args:
            album_id: 专辑 ID
            album_name: 专辑名（用于任务记录）
            platform: 平台标识，默认 netease

        Returns:
            {"enqueued": int, "excluded": int, "skipped": int, "total": int}
        """
        client = self._get_client_default(platform=platform)
        tracks = client.get_album_songs(album_id)
        total = len(tracks)
        if total == 0:
            return {"enqueued": 0, "excluded": 0, "skipped": 0, "total": 0}

        enqueued = 0
        excluded = 0
        skipped = 0
        pl_name = f"专辑: {album_name}" if album_name else f"专辑ID: {album_id}"
        search_scope_enabled = self._exclude_enabled("search")

        with self.app.app_context():
            for t in tracks:
                sid = t.get("id")
                if not sid:
                    continue
                # 排除关键字过滤（与搜索下载一致，受 search 场景配置控制）
                if search_scope_enabled and self._should_exclude(t.get("name", ""), t.get("artists", "")):
                    excluded += 1
                    logger.info("专辑下载跳过(命中排除关键字): %s - %s", t.get("artists", ""), t.get("name", ""))
                    continue
                existing = Song.query.filter_by(id=sid, status="success").first()
                if existing:
                    skipped += 1
                    continue
                pending = DownloadTask.query.filter(
                    DownloadTask.song_id == sid,
                    DownloadTask.status.in_(["pending", "downloading"]),
                ).first()
                if pending:
                    skipped += 1
                    continue
                task = DownloadTask(
                    platform=platform,
                    song_id=sid,
                    song_name=t.get("name", ""),
                    artists=t.get("artists", ""),
                    playlist_id=None,
                    playlist_name=pl_name,
                    status="pending",
                    fee=t.get("fee", 0),
                )
                db.session.add(task)
                db.session.commit()
                self._task_queue.put(task.pk)
                enqueued += 1

        logger.info(
            "专辑 [%s](id=%s) 共 %d 首：入队 %d，排除 %d，跳过(已下载/进行中) %d",
            album_name, album_id, total, enqueued, excluded, skipped,
        )
        return {"enqueued": enqueued, "excluded": excluded, "skipped": skipped, "total": total}

    def download_single_song(self, song_id: int, name: str, artists: str, fee: int = 0, platform: str = "netease") -> bool:
        """下载单首歌曲（用户主动选择，不应用排除过滤）

        Args:
            song_id: 歌曲ID
            name: 歌曲名
            artists: 歌手名
            fee: 费用类型（0=免费 1=VIP 4=购买专辑 8=低音质免费）
            platform: 平台标识，默认 netease

        Returns:
            True=已入队，False=已存在或失败
        """
        with self.app.app_context():
            existing = Song.query.filter_by(id=song_id, status="success").first()
            if existing:
                return False
            pending = DownloadTask.query.filter(
                DownloadTask.song_id == song_id,
                DownloadTask.status.in_(["pending", "downloading"]),
            ).first()
            if pending:
                return False
            task = DownloadTask(
                platform=platform,
                song_id=song_id,
                song_name=name,
                artists=artists,
                playlist_id=None,
                playlist_name="搜索单曲",
                status="pending",
                fee=fee,
            )
            db.session.add(task)
            db.session.commit()
            self._task_queue.put(task.pk)
        logger.info("单首下载入队: %s - %s (id=%s)", artists, name, song_id)
        return True

    # ------------------------------------------------------------------
    # 重试
    # ------------------------------------------------------------------
    def retry_failed(self, song_ids: list[int] | None = None) -> int:
        with self.app.app_context():
            query = Song.query.filter_by(status="failed")
            if song_ids:
                query = query.filter(Song.id.in_(song_ids))
            failed_songs = query.all()

            count = 0
            for song in failed_songs:
                pending = DownloadTask.query.filter(
                    DownloadTask.song_id == song.id,
                    DownloadTask.status.in_(["pending", "downloading"]),
                ).first()
                if pending:
                    continue
                task = DownloadTask(
                    platform=song.platform or "netease",
                    song_id=song.id,
                    song_name=song.name,
                    artists=song.artists,
                    playlist_id=song.playlist_id,
                    playlist_name=song.source_name or "",
                    status="pending",
                )
                db.session.add(task)
                db.session.commit()
                self._task_queue.put(task.pk)
                count += 1

            logger.info("重试 %d 首失败歌曲", count)
            return count

    # ------------------------------------------------------------------
    # 下载工作线程
    # ------------------------------------------------------------------
    def _worker_loop(self) -> None:
        logger.info("下载工作线程已启动")
        while not self._stop_event.is_set():
            try:
                pk = self._task_queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._process_task(pk)
            except Exception as e:
                logger.exception("处理任务 %s 异常: %s", pk, e)
                try:
                    self._mark_failed_by_pk(pk, f"处理异常: {e}")
                except Exception:
                    logger.exception("标记任务 %s 失败状态时再次异常", pk)
            finally:
                self._task_queue.task_done()

    def _process_task(self, task_pk: int) -> None:
        """处理单个下载任务（支持多账号）"""
        with self.app.app_context():
            task = DownloadTask.query.get(task_pk)
            if not task:
                return
            task.status = "downloading"
            task.progress = 0
            db.session.commit()

            sid = task.song_id
            sname = task.song_name
            artists = task.artists
            pl_id = task.playlist_id
            pl_name = task.playlist_name
            fee = task.fee or 0
            platform = task.platform or "netease"

            # 读取配置
            level = Setting.get("level", "exhigh")
            write_meta = Setting.get("write_metadata", "true") == "true"
            write_lyric = Setting.get("write_lyric", "true") == "true"
            mode = Setting.get("download_mode", "fallback")
            prefer_non_vip = Setting.get("prefer_non_vip", "false") == "true"

        # 选择账号（按 VIP 偏好过滤）
        if mode == "round_robin":
            account = self._account_selector.pick_for_round_robin(prefer_non_vip, fee, platform=platform)
        else:
            # 接力模式
            account = self._account_selector.pick_for_fallback(prefer_non_vip, fee, platform=platform)

        if not account:
            # 无可用账号：区分"全部因小时限额满"和"无账号/月额度满"
            if self._account_selector.all_hourly_limited(platform=platform):
                # 所有账号当前自然小时下载限额已满，暂停 30 分钟后重新入队
                logger.warning(
                    "所有账号当前自然小时下载限额已满，暂停 %d 秒后继续下载",
                    _HOURLY_PAUSE_SECONDS,
                )
                # 先把任务状态回退为 pending，避免前端一直显示 downloading
                with self.app.app_context():
                    t = DownloadTask.query.get(task_pk)
                    if t:
                        t.status = "pending"
                        t.progress = 0
                        db.session.commit()
                # 暂停（停止事件置位时立即中断）
                self._stop_event.wait(_HOURLY_PAUSE_SECONDS)
                # 暂停结束后把任务重新放回队列
                self._task_queue.put(task_pk)
                return
            self._mark_failed(task_pk, sid, sname, artists, pl_id, pl_name, "无可用账号（全部达额度或未配置）", platform=platform)
            return

        if mode == "round_robin":
            # 轮询模式：单账号失败不切换，直接标记失败
            self._download_with_account(task_pk, account, sid, sname, artists, pl_id, pl_name,
                                        level, write_meta, write_lyric, switch_on_fail=False,
                                        prefer_non_vip=prefer_non_vip, fee=fee)
        else:
            # 接力模式：失败或达额度时切换到下一个账号
            self._download_with_account(task_pk, account, sid, sname, artists, pl_id, pl_name,
                                        level, write_meta, write_lyric, switch_on_fail=True,
                                        prefer_non_vip=prefer_non_vip, fee=fee)

    def _download_with_account(
        self,
        task_pk: int,
        account: Account,
        sid: int,
        sname: str,
        artists: str,
        pl_id: int | None,
        pl_name: str,
        level: str,
        write_meta: bool,
        write_lyric: bool,
        switch_on_fail: bool,
        prefer_non_vip: bool = False,
        fee: int = 0,
    ) -> None:
        """用指定账号下载一首歌

        Args:
            switch_on_fail: True=接力模式（失败时切换下一个账号重试），False=轮询模式（失败直接标记）
            prefer_non_vip: 是否优先非VIP账号
            fee: 歌曲费用类型（1=VIP歌曲）
        """
        client = self._get_client_for_account(account)
        logger.info("下载 [%s - %s] 使用账号: %s", artists, sname, account.name)

        # 获取下载链接
        url_info_list = client.get_song_urls([str(sid)], level=level)
        url_info = url_info_list[0] if url_info_list else {}
        url = url_info.get("url")

        if not url:
            reason = "试听片段" if url_info.get("is_trial") else "无版权或需VIP"
            if switch_on_fail:
                # 接力模式：切换下一个账号
                next_acc = self._account_selector.switch_to_next(account.id, prefer_non_vip, fee)
                if next_acc and next_acc.id != account.id:
                    logger.info("账号 %s 失败(%s)，切换到 %s 重试", account.name, reason, next_acc.name)
                    self._download_with_account(task_pk, next_acc, sid, sname, artists, pl_id, pl_name,
                                                level, write_meta, write_lyric, switch_on_fail=True,
                                                prefer_non_vip=prefer_non_vip, fee=fee)
                    return
            self._mark_failed(task_pk, sid, sname, artists, pl_id, pl_name, reason, account_id=account.id, platform=account.platform)
            return

        # 只有有 url 时才取扩展名和大小
        ext = url_info.get("ext", "mp3")
        size = url_info.get("size")

        # 获取歌曲详情
        details = client.get_song_detail([str(sid)])
        meta = details[0] if details else {}
        cover_url = meta.get("cover_url", "")
        album_name = meta.get("album", "")
        duration_ms = meta.get("duration_ms", 0)
        year = meta.get("year", "")
        lyric = ""
        if write_lyric:
            lyric = client.get_lyric(str(sid)).get("lrc", "")

        # 取主歌手作为下载子目录（多歌手取第一个，文件名仍保留全部歌手）
        primary_artist = meta.get("artist", "群星")
        primary_artist = sanitize_filename(primary_artist) if primary_artist.strip() else "群星"

        # 下载文件
        downloader = self._get_downloader()
        filename = build_filename(artists, sname, ext)

        last = {"pct": -1, "ts": 0.0}

        def progress_cb(downloaded: int, total: int | None) -> None:
            if not total:
                return
            now = time.time()
            pct = int(downloaded * 100 / total)
            if pct - last["pct"] >= 2 or now - last["ts"] >= 1.0:
                last["pct"] = pct
                last["ts"] = now
                with self.app.app_context():
                    t = DownloadTask.query.get(task_pk)
                    if t:
                        t.progress = min(99, pct)
                        t.account_id = account.id
                        db.session.commit()

        try:
            path = downloader.download(
                url=url,
                sub_dir=primary_artist,
                filename=filename,
                expected_size=size,
                progress_callback=progress_cb,
            )
        except OSError as e:
            logger.error("下载 %s - %s 时 [Errno %d]: %s", artists, sname, e.errno or 0, e)
            self._mark_failed(task_pk, sid, sname, artists, pl_id, pl_name,
                              f"下载异常 [Errno {e.errno}]: {e}", account_id=account.id, platform=account.platform)
            return

        if not path:
            if switch_on_fail:
                next_acc = self._account_selector.switch_to_next(account.id, prefer_non_vip, fee)
                if next_acc and next_acc.id != account.id:
                    logger.info("账号 %s 下载失败，切换到 %s 重试", account.name, next_acc.name)
                    self._download_with_account(task_pk, next_acc, sid, sname, artists, pl_id, pl_name,
                                                level, write_meta, write_lyric, switch_on_fail=True,
                                                prefer_non_vip=prefer_non_vip, fee=fee)
                    return
            self._mark_failed(task_pk, sid, sname, artists, pl_id, pl_name, "下载失败（重试耗尽）", account_id=account.id, platform=account.platform)
            return

        # 写入元数据
        if write_meta:
            write_tags(
                path,
                {
                    "title": sname,
                    "artist": artists,
                    "album": album_name,
                    "year": year,
                    "cover_url": cover_url,
                    "lyric": lyric,
                },
            )

        # 标记成功（记录 account_id 用于额度统计）
        with self.app.app_context():
            Song.query.filter_by(id=sid, status="failed").delete()
            song = Song(
                id=sid,
                platform=account.platform,
                name=sname,
                artists=artists,
                album=album_name,
                duration_ms=duration_ms,
                quality=level,
                file_path=str(path),
                file_size=path.stat().st_size if path.exists() else 0,
                playlist_id=pl_id,
                source_name=pl_name,
                status="success",
                account_id=account.id,
            )
            db.session.merge(song)

            task = DownloadTask.query.get(task_pk)
            if task:
                task.status = "done"
                task.progress = 100
                task.account_id = account.id
                db.session.commit()
            logger.info("任务完成: %s - %s (账号: %s)", artists, sname, account.name)

    def _mark_failed(
        self,
        task_pk: int,
        sid: int,
        name: str,
        artists: str,
        pl_id: int | None,
        pl_name: str,
        reason: str,
        account_id: int | None = None,
        platform: str = "netease",
    ) -> None:
        """标记任务为失败，并记录到 songs 表

        Args:
            platform: 平台标识，默认 netease
        """
        with self.app.app_context():
            Song.query.filter_by(id=sid).delete()
            song = Song(
                id=sid,
                platform=platform,
                name=name,
                artists=artists,
                playlist_id=pl_id,
                source_name=pl_name,
                status="failed",
                error_msg=reason,
                account_id=account_id,
            )
            db.session.merge(song)

            task = DownloadTask.query.get(task_pk)
            if task:
                task.status = "failed"
                task.error_msg = reason
                task.account_id = account_id
                db.session.commit()
            logger.warning("任务失败: %s - %s (%s)", artists, name, reason)

    def _mark_failed_by_pk(self, task_pk: int, reason: str) -> None:
        with self.app.app_context():
            task = DownloadTask.query.get(task_pk)
            if not task:
                return
            sid = task.song_id
            sname = task.song_name
            artists = task.artists
            pl_id = task.playlist_id
            pl_name = task.playlist_name
            account_id = task.account_id
            platform = task.platform or "netease"
        self._mark_failed(task_pk, sid, sname, artists, pl_id, pl_name, reason, account_id=account_id, platform=platform)

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    def get_active_tasks(self) -> list[dict]:
        with self.app.app_context():
            tasks = DownloadTask.query.filter(
                DownloadTask.status.in_(["pending", "downloading"])
            ).order_by(DownloadTask.created_at).all()
            # 关联账号名
            result = []
            for t in tasks:
                d = t.to_dict()
                if t.account_id:
                    acc = Account.query.get(t.account_id)
                    d["account_name"] = acc.name if acc else None
                else:
                    d["account_name"] = None
                result.append(d)
            return result

    def refresh_schedule(self) -> None:
        self._refresh_schedule()
