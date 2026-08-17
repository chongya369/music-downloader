"""网易云 Provider 实现（NeteaseProvider）

窄接口方法走 _transform 转换为统一结构；
旁路代理方法保持原始返回，使用 *args/**kwargs 透传防签名漂移。
"""

import logging

from ..base import MusicProvider
from .client import NeteaseClient, OFFICIAL_TOPLISTS
from ._transform import (
    transform_song_urls,
    transform_song_detail,
    transform_account_info,
    is_vip_song as _is_vip_song,
)

logger = logging.getLogger(__name__)


class NeteaseProvider(MusicProvider):
    """网易云音乐 Provider

    实例为调用级对象：每次 get_provider() 返回全新实例，
    随调用结束丢弃，禁止跨调用缓存/复用。
    """

    platform = "netease"

    def __init__(self):
        self._client: NeteaseClient | None = None
        self._cookie: str = ""
        self._custom_base_url: str = ""

    def _ensure_client(self) -> NeteaseClient:
        """惰性创建 client（首次业务调用时）"""
        if self._client is None:
            self._client = NeteaseClient(
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
        """设置自定义 API 地址（调用顺序无关）"""
        self._custom_base_url = (url or "").rstrip("/")
        # 若 client 已存在，需要重建以应用新 base_url
        if self._client is not None:
            self._client = None  # 强制重建

    # ------------------------------------------------------------------
    # 窄接口方法（走 _transform 转换为统一结构）
    # ------------------------------------------------------------------
    def verify_account(self, cred: str) -> dict:
        """验证凭证并返回账号信息

        临时建带 cred 的 client，调 get_account_info + get_vip_info，
        产出含 vip_text 的 AccountInfo。
        """
        client = NeteaseClient(cookie=cred)
        info = client.get_account_info()
        if info.get("code") != 200:
            return {"ok": False, "nickname": "", "vip_type": 0, "vip_expire_at": None, "vip_text": "凭证无效"}
        account_data = info.get("account") or {}
        vip_info = client.get_vip_info()
        return transform_account_info(account_data, vip_info)

    def get_song_urls(self, song_ids: list[str], level: str) -> list[dict]:
        """批量获取歌曲下载链接（转换为 UrlInfo 列表）"""
        client = self._ensure_client()
        int_ids = [int(sid) for sid in song_ids]
        raw_list = client.get_song_urls(int_ids, level=level)
        return transform_song_urls(raw_list, song_ids)

    def get_song_detail(self, song_ids: list[str]) -> list[dict]:
        """获取歌曲详情（转换为 SongMeta 列表）"""
        client = self._ensure_client()
        int_ids = [int(sid) for sid in song_ids]
        raw_list = client.get_song_detail(int_ids)
        return transform_song_detail(raw_list, song_ids)

    def get_lyric(self, song_id: str) -> dict:
        """获取歌词（直接返回，无需 transform）"""
        client = self._ensure_client()
        return client.get_lyric(int(song_id))

    def is_vip_song(self, fee) -> bool:
        """判断歌曲是否 VIP"""
        return _is_vip_song(fee)

    # ------------------------------------------------------------------
    # 旁路代理方法（原始返回，*args/**kwargs 透传防签名漂移）
    # ------------------------------------------------------------------
    def get_account_info(self, *args, **kwargs):
        """获取账号详情（旁路代理）"""
        return self._ensure_client().get_account_info(*args, **kwargs)

    def get_vip_info(self, *args, **kwargs):
        """获取会员信息（旁路代理）"""
        return self._ensure_client().get_vip_info(*args, **kwargs)

    def get_all_toplists(self, *args, **kwargs):
        """获取官方榜单列表（旁路代理）"""
        return self._ensure_client().get_all_toplists(*args, **kwargs)

    def get_toplists(self, *args, **kwargs):
        """获取排行榜列表（旁路代理）"""
        return self._ensure_client().get_toplists(*args, **kwargs)

    def get_playlist_detail(self, *args, **kwargs):
        """获取歌单详情（旁路代理）"""
        return self._ensure_client().get_playlist_detail(*args, **kwargs)

    def get_hot_playlists(self, *args, **kwargs):
        """获取热门歌单（旁路代理）"""
        return self._ensure_client().get_hot_playlists(*args, **kwargs)

    def get_playlist_categories(self, *args, **kwargs):
        """获取歌单分类（旁路代理）"""
        return self._ensure_client().get_playlist_categories(*args, **kwargs)

    def search_albums(self, *args, **kwargs):
        """搜索专辑（旁路代理）"""
        return self._ensure_client().search_albums(*args, **kwargs)

    def search_songs(self, *args, **kwargs):
        """搜索单曲（旁路代理）"""
        return self._ensure_client().search_songs(*args, **kwargs)

    def get_album_songs(self, *args, **kwargs):
        """获取专辑内歌曲（旁路代理）"""
        return self._ensure_client().get_album_songs(*args, **kwargs)

    def login_status(self, *args, **kwargs):
        """检查登录状态（旁路代理）"""
        return self._ensure_client().login_status(*args, **kwargs)
