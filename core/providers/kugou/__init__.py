"""酷狗音乐 Provider 实现（KuGouProvider）

窄接口方法返回统一结构；旁路代理方法与 NeteaseProvider/QqProvider 签名对齐，
使用 *args/**kwargs 透传防签名漂移。

酷狗 API 能力限制（详见 client.py 模块注释与接口文档第五/六节）：
- /search?type=song 已失效 → 搜索单曲走专辑中转 + 歌手中转
- 歌单曲目接口 specialid 必 20010 → 传 global_collection_id 匿名可用；
  gcid 映射在浏览热门歌单时持久化缓存（_gcid_cache）
- 匿名音质封顶 128kbps；320/FLAC 需登录 Cookie（token+userid）
- 无翻译歌词 → tlyric 固定空串
- dfid 设备指纹由 client 自动注册管理，用户无需提供
"""

import logging

from ..base import MusicProvider
from .client import KuGouClient
from ._transform import is_vip_song as _is_vip_song

logger = logging.getLogger(__name__)


class KuGouProvider(MusicProvider):
    """酷狗音乐 Provider

    实例为调用级对象：每次 get_provider() 返回全新实例，
    随调用结束丢弃，禁止跨调用缓存/复用（避免多账号 cookie 串号）。
    """

    platform = "kugou"

    def __init__(self):
        self._client: KuGouClient | None = None
        self._cookie: str = ""
        self._custom_base_url: str = ""

    def _ensure_client(self) -> KuGouClient:
        """惰性创建 client（首次业务调用时）"""
        if self._client is None:
            self._client = KuGouClient(
                cookie=self._cookie,
                custom_base_url=self._custom_base_url,
            )
        return self._client

    # ------------------------------------------------------------------
    # 凭证注入
    # ------------------------------------------------------------------
    def set_cookie(self, cred: str) -> None:
        """设置 Cookie（调用顺序无关，可在 set_custom_base_url 前后）

        酷狗登录态 Cookie：token=xxx;userid=xxx
        （dfid 由 client 自动管理，不要求用户提供）
        """
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
        """获取歌词（两步链路 /search/lyric → /lyric，仅 lrc 无翻译）"""
        client = self._ensure_client()
        return client.get_lyric(str(song_id))

    def is_vip_song(self, fee) -> bool:
        """判断歌曲是否 VIP"""
        return _is_vip_song(fee)

    # ------------------------------------------------------------------
    # 旁路代理方法（与 NeteaseProvider/QqProvider 签名对齐，*args/**kwargs 透传防签名漂移）
    # ------------------------------------------------------------------
    def get_user_info(self, *args, **kwargs):
        """获取账号信息（昵称/VIP 状态，旁路代理；登录态字段待实测校准）"""
        return self._ensure_client().get_user_info(*args, **kwargs)

    def test_download_capability(self, *args, **kwargs):
        """实测当前 cookie 的 VIP 下载能力（高音质是否生效，旁路代理）"""
        return self._ensure_client().test_download_capability(*args, **kwargs)

    def create_qr_login(self, *args, **kwargs):
        """生成扫码登录二维码（旁路代理）"""
        return self._ensure_client().create_qr_login(*args, **kwargs)

    def check_qr_login(self, *args, **kwargs):
        """轮询扫码状态（status=4 时返回 token+userid Cookie，旁路代理）"""
        return self._ensure_client().check_qr_login(*args, **kwargs)

    def search_songs(self, *args, **kwargs):
        """搜索单曲（专辑中转 + 歌手中转，旁路代理）"""
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
        """获取热门歌单（/top/playlist + 分类映射，旁路代理）"""
        return self._ensure_client().get_hot_playlists(*args, **kwargs)

    def get_playlist_categories(self, *args, **kwargs):
        """获取歌单分类（/playlist/tags，旁路代理）"""
        return self._ensure_client().get_playlist_categories(*args, **kwargs)

    def get_playlist_detail(self, *args, **kwargs):
        """获取榜单/歌单详情（rankid→榜单，specialid→gcid 曲目，旁路代理）"""
        return self._ensure_client().get_playlist_detail(*args, **kwargs)
