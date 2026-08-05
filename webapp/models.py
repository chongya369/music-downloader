"""数据库模型 - SQLAlchemy + SQLite

表结构：
- Account：网易云账号（多账号管理）
- Playlist：关注的歌单/榜单
- Song：已下载歌曲记录（去重依据）
- DownloadTask：下载任务（实时进度 + 失败列表）
- Setting：配置项 key-value
"""

from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Account(db.Model):
    """网易云账号

    vip_type: 0=非会员, 11=黑胶VIP, 12=SVIP
    quota_limit: 总额度，0=不限制
    vip_expire_at: 会员到期时间（来自 /vip/info，可能为空）
    sort_order: 使用顺序（升序），账号选择器按此排序
    """
    __tablename__ = "accounts"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)      # 别名（如"主账号"）
    cookie = db.Column(db.Text, default="")
    nickname = db.Column(db.String(200), default="")      # 网易云昵称
    vip_type = db.Column(db.Integer, default=0)
    vip_expire_at = db.Column(db.DateTime)                # 会员到期时间
    quota_limit = db.Column(db.Integer, default=0)        # 总额度，0=不限制
    sort_order = db.Column(db.Integer, default=0)         # 使用顺序（升序）
    enabled = db.Column(db.Boolean, default=True)
    last_check_at = db.Column(db.DateTime)                # 上次登录校验时间
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self, monthly_downloaded: int | None = None) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "nickname": self.nickname,
            "vip_type": self.vip_type,
            "vip_text": {0: "非会员", 11: "黑胶VIP", 12: "SVIP"}.get(self.vip_type, f"vipType={self.vip_type}"),
            "vip_expire_at": self.vip_expire_at.strftime("%Y-%m-%d %H:%M:%S") if self.vip_expire_at else None,
            "quota_limit": self.quota_limit,
            "sort_order": self.sort_order,
            "monthly_downloaded": monthly_downloaded if monthly_downloaded is not None else 0,
            "enabled": self.enabled,
            "last_check_at": self.last_check_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_check_at else None,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


class Playlist(db.Model):
    """关注的歌单/榜单

    type: official(官方榜单) / user(用户自定义歌单)
    enabled: 是否启用自动下载
    """
    __tablename__ = "playlists"
    id = db.Column(db.Integer, primary_key=True)           # 网易云歌单/榜单 ID
    name = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(20), default="official")    # official / user
    enabled = db.Column(db.Boolean, default=True)
    limit_count = db.Column(db.Integer, default=100)
    last_synced_at = db.Column(db.DateTime)
    track_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)

    songs = db.relationship("Song", backref="playlist", lazy="dynamic")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "enabled": self.enabled,
            "limit_count": self.limit_count,
            "last_synced_at": self.last_synced_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_synced_at else None,
            "track_count": self.track_count,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


class Song(db.Model):
    """已下载歌曲记录（去重依据 + 下载历史）

    status: success / failed / skipped
    """
    __tablename__ = "songs"
    id = db.Column(db.Integer, primary_key=True)           # 网易云歌曲 ID
    name = db.Column(db.String(300), nullable=False)
    artists = db.Column(db.String(300), default="")
    album = db.Column(db.String(300), default="")
    duration_ms = db.Column(db.Integer, default=0)
    quality = db.Column(db.String(20), default="")
    file_path = db.Column(db.String(500), default="")
    file_size = db.Column(db.Integer, default=0)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id"), nullable=True)
    downloaded_at = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(20), default="success")   # success / failed / skipped
    error_msg = db.Column(db.String(500), default="")
    # failed 歌曲的来源歌单名（用于失败列表展示，避免 playlist_id 为空时丢失信息）
    source_name = db.Column(db.String(200), default="")
    # 记录用哪个账号下载的（用于本月下载额度统计）
    account_id = db.Column(db.Integer, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "artists": self.artists,
            "album": self.album,
            "duration_ms": self.duration_ms,
            "quality": self.quality,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist.name if self.playlist else self.source_name,
            "downloaded_at": self.downloaded_at.strftime("%Y-%m-%d %H:%M:%S") if self.downloaded_at else None,
            "status": self.status,
            "error_msg": self.error_msg,
            "account_id": self.account_id,
        }


class DownloadTask(db.Model):
    """下载任务（实时进度跟踪）

    status: pending / downloading / done / failed
    fee: 网易云歌曲费用类型 0=免费 1=VIP 4=购买专辑 8=低音质免费
    """
    __tablename__ = "download_tasks"
    pk = db.Column(db.Integer, primary_key=True, autoincrement=True)
    song_id = db.Column(db.Integer, nullable=False)
    song_name = db.Column(db.String(300), default="")
    artists = db.Column(db.String(300), default="")
    playlist_id = db.Column(db.Integer, nullable=True)
    playlist_name = db.Column(db.String(200), default="")
    status = db.Column(db.String(20), default="pending")   # pending/downloading/done/failed
    progress = db.Column(db.Integer, default=0)            # 0-100
    error_msg = db.Column(db.String(500), default="")
    account_id = db.Column(db.Integer, nullable=True)      # 本次下载用的账号
    fee = db.Column(db.Integer, default=0)                 # 歌曲费用类型，用于 VIP/非VIP 账号选择
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        return {
            "pk": self.pk,
            "song_id": self.song_id,
            "song_name": self.song_name,
            "artists": self.artists,
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
            "status": self.status,
            "progress": self.progress,
            "error_msg": self.error_msg,
            "account_id": self.account_id,
            "fee": self.fee,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


class Setting(db.Model):
    """配置项 key-value 存储"""
    __tablename__ = "settings"
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, default="")

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        item = db.session.get(cls, key)
        return item.value if item else default

    @classmethod
    def set(cls, key: str, value: str) -> None:
        item = db.session.get(cls, key)
        if item:
            item.value = value
        else:
            item = cls(key=key, value=value)
            db.session.add(item)
        db.session.commit()

    @classmethod
    def get_all(cls) -> dict:
        return {item.key: item.value for item in cls.query.all()}


class User(db.Model):
    """系统用户（Web 登录用）

    初始账号：admin / admin123
    is_admin: True=管理员（可管理用户），False=普通用户（仅可改自己密码）
    """
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=True)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    last_login_at = db.Column(db.DateTime)

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "is_admin": self.is_admin,
            "enabled": self.enabled,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "last_login_at": self.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_login_at else None,
        }


# 默认配置项
DEFAULT_SETTINGS = {
    "api_url": "http://localhost:3000",
    "web_port": "56700",
    "output_dir": "downloads",
    "level": "exhigh",
    "write_metadata": "true",
    "write_lyric": "true",
    "auto_sync_enabled": "true",
    # 同步时间点：多个 "HH:MM" 用逗号分隔
    "sync_times": "03:00,09:00,21:00",
    # 同步抖动延迟（秒，0-3600），触发后随机延迟 0~N 秒再执行
    "sync_jitter": "600",
    "max_retries": "3",
    # 添加歌单时的默认下载数量
    "default_playlist_limit": "50",
    # 多账号下载模式：fallback(接力) / round_robin(轮询)
    "download_mode": "fallback",
    # 优先使用非VIP账号下载（仅 VIP 歌曲才用 VIP 账号）
    "prefer_non_vip": "false",
    # 单账号每自然小时下载成功上限（0=不限制）
    "hourly_limit_per_account": "50",
    # 排除歌曲关键字（英文逗号分隔，如 "live,伴奏,remix"）
    "exclude_keywords": "",
    # 排除过滤应用范围：逗号分隔，如 "playlist,search"（两者都应用）/ "playlist" / "search" / ""（都不应用）
    "exclude_scope": "playlist,search",
}


def _column_exists(inspector, table: str, column: str) -> bool:
    """检查某列是否已存在（用于 ALTER TABLE 迁移）"""
    return column in {c["name"] for c in inspector.get_columns(table)}


def init_db(app, db_path: str = "downloads.db") -> None:
    """初始化数据库：配置 SQLAlchemy、创建表、写入默认配置、兼容迁移"""
    abs_db_path = Path(db_path).resolve()
    abs_db_path.parent.mkdir(parents=True, exist_ok=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{abs_db_path.as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    with app.app_context():
        db.create_all()
        # 兼容迁移：给旧表补充 account_id 列
        inspector = inspect(db.engine)
        if inspector.has_table("songs") and not _column_exists(inspector, "songs", "account_id"):
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE songs ADD COLUMN account_id INTEGER"))
        if inspector.has_table("download_tasks") and not _column_exists(inspector, "download_tasks", "account_id"):
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE download_tasks ADD COLUMN account_id INTEGER"))
        # 写入缺失的默认配置
        for key, value in DEFAULT_SETTINGS.items():
            if not db.session.get(Setting, key):
                db.session.add(Setting(key=key, value=value))
        db.session.commit()
        # 初始化默认管理员账号（仅当 users 表为空时）
        if not User.query.first():
            admin = User(username="admin", is_admin=True, enabled=True)
            admin.set_password("admin123")
            db.session.add(admin)
            db.session.commit()
            print("[init_db] 已创建初始管理员账号：admin / admin123")
