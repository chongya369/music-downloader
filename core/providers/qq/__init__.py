"""QQ音乐 Provider 实现（QqProvider）

窄接口方法返回统一结构；旁路代理方法与 NeteaseProvider 签名对齐，
使用 *args/**kwargs 透传防签名漂移。

QQ API 能力限制（详见 client.py 模块注释与方案文档第八节）：
- 无歌词接口 → 相应方法返回空
- 账号信息经 /getUserInfo 获取（昵称/绿钻等级/到期时间）
"""

import logging

from ..base import MusicProvider
from .client import QqClient
from ._transform import is_vip_song as _is_vip_song

logger = logging.getLogger(__name__)


class QqProvider(MusicProvider):
    """QQ音乐 Provider

    实例为调用级对象：每次 get_provider() 返回全新实例，
    随调用结束丢弃，禁止跨调用缓存/复用（避免多账号 cookie 串号）。
    """

    platform = "qq"

    def __init__(self):
        self._client: QqClient | None = None
        self._cookie: str = ""
        self._custom_base_url: str = ""

    def _ensure_client(self) -> QqClient:
        """惰性创建 client（首次业务调用时）"""
        if self._client is None:
            self._client = QqClient(
                cookie=self._cookie,
                custom_base_url=self._custom_base_url,
            )
        return self._client

    # ------------------------------------------------------------------
    # 凭证注入
    # ------------------------------------------------------------------
    def set_cookie(self, cred: str) -> None:
        """设置 Cookie（调用顺序无关，可在 set_custom_base_url 前后）"""
        self._cookie = cred or ""
        if self._client is not None:
            self._client.set_cookie(self._cookie)

    def set_custom_base_url(self, url: str) -> None:
        """设置 API 服务地址（调用顺序无关）"""
        self._custom_base_url = (url or "").rstrip("/")
        # 若 client 已存在，需要重建以应用新 base_url
        if self._client is not None:
            self._client = None  # 强制重建

    # ------------------------------------------------------------------
    # 窄接口方法（返回统一结构）
    # ------------------------------------------------------------------
    def get_song_urls(self, song_ids: list[str], level: str) -> list[dict]:
        """批量获取歌曲下载链接（返回 UrlInfo 列表，与 song_ids 顺序对齐）"""
        client = self._ensure_client()
        return client.get_song_urls([str(s) for s in song_ids], level=level)

    def get_song_detail(self, song_ids: list[str]) -> list[dict]:
        """获取歌曲详情（转换为 SongMeta 列表，含专辑名/时长/封面/年代）"""
        client = self._ensure_client()
        return client.get_song_detail([str(s) for s in song_ids])

    def get_lyric(self, song_id: str) -> dict:
        """获取歌词（QQ 无歌词接口，返回空）"""
        client = self._ensure_client()
        return client.get_lyric(str(song_id))

    def is_vip_song(self, fee) -> bool:
        """判断歌曲是否 VIP"""
        return _is_vip_song(fee)

    # ------------------------------------------------------------------
    # 旁路代理方法（与 NeteaseProvider 签名对齐，*args/**kwargs 透传防签名漂移）
    # ------------------------------------------------------------------
    def get_user_info(self, *args, **kwargs):
        """获取账号信息（昵称/绿钻等级/到期时间，旁路代理）"""
        return self._ensure_client().get_user_info(*args, **kwargs)

    def search_songs(self, *args, **kwargs):
        """搜索单曲（旁路代理）"""
        return self._ensure_client().search_songs(*args, **kwargs)

    def search_albums(self, *args, **kwargs):
        """搜索专辑（旁路代理）"""
        return self._ensure_client().search_albums(*args, **kwargs)

    def get_album_songs(self, *args, **kwargs):
        """获取专辑内歌曲（旁路代理）"""
        return self._ensure_client().get_album_songs(*args, **kwargs)

    def get_toplists(self, *args, **kwargs):
        """获取排行榜列表（旁路代理）"""
        return self._ensure_client().get_toplists(*args, **kwargs)

    def get_hot_playlists(self, *args, **kwargs):
        """获取热门歌单（旁路代理）"""
        return self._ensure_client().get_hot_playlists(*args, **kwargs)

    def get_playlist_categories(self, *args, **kwargs):
        """获取歌单分类（旁路代理）"""
        return self._ensure_client().get_playlist_categories(*args, **kwargs)

    def get_playlist_detail(self, *args, **kwargs):
        """获取歌单/榜单详情（旁路代理，内部榜单/歌单分流）"""
        return self._ensure_client().get_playlist_detail(*args, **kwargs)
