"""网易云音乐 API 客户端 - 调用内置 NeteaseCloudMusicApi-enhanced 服务

服务二进制由 core.node_bridge 管理（自动拉起，监听 127.0.0.1 随机端口），
API 地址每次请求时经 base_url 属性动态解析，无需手动指定。

会员鉴权通过 Cookie 中的 MUSIC_U 实现，所有需要会员权限的接口会自动带上 Cookie。
"""

import logging
import re
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# 官方常驻榜单 ID
OFFICIAL_TOPLISTS = {
    19723756: "飙升榜",
    3779629: "新歌榜",
    3778678: "热歌榜",
    2884035: "原创榜",
    10520166: "电音榜",
    60198: "ACG新歌榜",
    60131: "欧美热歌榜",
    180106: "UK排行榜周榜",
    27135204: "美国Billboard周榜",
    3812895: "Beatport全球电子舞曲榜",
    71385702: "KTV嗨榜",
    71384007: "法国 NRJ Vos Hits 周榜",
    112504: "日本Oricon周榜",
    112463: "iTunes榜",
}

# 音质等级 -> NeteaseCloudMusicApi level 参数值
QUALITY_LEVEL = {
    "standard": "standard",  # 标准 128kbps
    "higher": "higher",      # 较高 192kbps
    "exhigh": "exhigh",      # 极高 320kbps MP3
    "lossless": "lossless",  # 无损 FLAC
    "hires": "hires",        # Hi-Res
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def parse_playlist_id(text: str) -> int | None:
    """从用户输入解析歌单 ID

    支持以下格式：
        - 纯数字：3778678
        - 网易云分享链接：https://music.163.com/playlist?id=3778678
        - 短链接：https://y.music.163.com/m/playlist?id=3778678
        - 分享文案：「...」https://y.music.163.com/.../3778678/...

    Returns:
        歌单 ID，解析失败返回 None
    """
    text = text.strip()
    if not text:
        return None
    # 纯数字
    if text.isdigit():
        return int(text)
    # URL 中的 id= 参数
    m = re.search(r"[?&]id=(\d+)", text)
    if m:
        return int(m.group(1))
    # 路径中的数字（短链接格式 /playlist/xxx 或末尾数字）
    m = re.search(r"/(\d{5,})(?:/|\s|$)", text)
    if m:
        return int(m.group(1))
    return None


class NeteaseClient:
    """网易云音乐 API 客户端

    通过内置 NeteaseCloudMusicApi-enhanced 服务调用网易云接口。
    Cookie 用于会员鉴权，需包含 MUSIC_U。
    """

    def __init__(self, cookie: str = "", custom_base_url: str = ""):
        """持有 bridge 引用；API 地址经 base_url 属性动态解析

        Args:
            cookie: Cookie 字符串，必须包含 MUSIC_U（会员鉴权）
            custom_base_url: 自定义API服务URL（设置后直接使用，不通过内置bridge）
        """
        from core import node_bridge
        self._bridge = node_bridge.get_bridge()
        self._custom_base_url = custom_base_url.rstrip("/") if custom_base_url else ""
        self.session = requests.Session()
        # session 默认 trust_env=True 会读主进程 http_proxy/https_proxy，
        # 目标 http://127.0.0.1:port 在内网直连，显式关闭避免业务请求全走代理
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": _UA})
        if cookie:
            self.set_cookie(cookie)

    @property
    def base_url(self) -> str:
        """API 服务地址（动态解析，每次请求重新取值）

        - 自定义URL优先：设置后直接返回自定义地址
        - 内置服务：Web 停止→重启后端口会变（随机空闲端口），缓存旧地址会导致请求失败
        - 服务未运行时经 start() 幂等拉起（与 auto_start=false 的
          "程序启动不拉起、用到再拉"语义一致）
        - _request 自带 3 次重试，拉起期间请求可自然恢复
        """
        if self._custom_base_url:
            return self._custom_base_url
        return self._bridge.start().rstrip("/")

    def set_cookie(self, cookie: str) -> None:
        """设置 Cookie（写入 session.headers 供所有请求携带）"""
        self.session.headers["Cookie"] = cookie

    @property
    def has_login(self) -> bool:
        return "MUSIC_U" in self.session.headers.get("Cookie", "")

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    def _request(
        self,
        path: str,
        method: str = "GET",
        params: dict | None = None,
        data: dict | None = None,
        retries: int = 3,
        timeout: int = 15,
    ) -> dict:
        """调用 NeteaseCloudMusicApi 接口"""
        # url 在循环内每次重新解析：停止→重启后端口漂移时，
        # 正在重试的请求也能经 base_url 属性取到新地址
        for attempt in range(1, retries + 1):
            url = f"{self.base_url}{path}"
            try:
                if method.upper() == "GET":
                    resp = self.session.get(url, params=params, timeout=timeout)
                else:
                    resp = self.session.post(url, params=params, data=data, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as e:
                logger.warning("请求 %s 第 %d 次失败: %s", path, attempt, e)
                if attempt < retries:
                    time.sleep(1.5 * attempt)
        logger.error("请求 %s 失败，已重试 %d 次", path, retries)
        return {"code": -1, "msg": "request failed"}

    # ------------------------------------------------------------------
    # 业务接口
    # ------------------------------------------------------------------
    def login_status(self) -> dict:
        """检查登录状态（快速检查：5s 超时，不重试）"""
        return self._request("/login/status", timeout=5, retries=1)

    def get_account_info(self) -> dict:
        """获取当前登录账号详情（快速检查：5s 超时，不重试）"""
        return self._request("/user/account", timeout=5, retries=1)

    def get_vip_info(self) -> dict:
        """获取会员权益信息（含到期时间）

        /vip/info 返回的 data 结构：
            {
                "associator":  {"vipCode": 11, "expireTime": ms, "vipLevel": 1},  # 黑胶VIP
                "musicPackage": {"vipCode": 0,  "expireTime": 0,   "vipLevel": 0},  # 音乐包
                "redplus":     {"vipCode": 12, "expireTime": ms, "vipLevel": 1}   # SVIP
            }
        优先级：SVIP(12) > 黑胶VIP(11) > 音乐包 > 非会员(0)

        Returns:
            {"vip_type": int, "expire_time": int(ms)|None}
            expire_time 为 None 表示无到期信息（未开通/永久/接口失败）
            接口失败返回 {}
        """
        try:
            result = self._request("/vip/info", timeout=5, retries=1)
        except Exception as e:
            logger.warning("获取会员信息失败: %s", e)
            return {}
        if result.get("code") != 200:
            return {}
        data = result.get("data") or {}

        # 按优先级遍历会员类型：redplus(SVIP) > associator(黑胶VIP) > musicPackage(音乐包)
        # 选第一个 vipCode > 0 的作为当前有效会员
        for key in ("redplus", "associator", "musicPackage"):
            pkg = data.get(key) or {}
            vip_code = int(pkg.get("vipCode") or 0)
            if vip_code > 0:
                expire_ms = pkg.get("expireTime")
                # expireTime 为 0 表示永久或未真正开通，统一转为 None
                if not expire_ms or expire_ms <= 0:
                    expire_ms = None
                return {"vip_type": vip_code, "expire_time": expire_ms}

        # 无任何会员
        return {"vip_type": 0, "expire_time": None}

    def get_all_toplists(self) -> list[dict]:
        """获取所有官方榜单列表"""
        result = self._request("/toplist")
        if result.get("code") != 200:
            logger.error("获取榜单列表失败: %s", result.get("msg"))
            return []
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "description": item.get("description", ""),
                "update_frequency": item.get("updateFrequency", ""),
            }
            for item in result.get("list", [])
        ]

    def get_playlist_detail(self, playlist_id: int, limit: int = 200) -> dict:
        """获取歌单/榜单详情，包含歌曲列表

        Args:
            playlist_id: 歌单 ID 或榜单 ID（榜单本质也是歌单）
            limit: 取前 N 首

        Returns:
            {"name","track_count","tracks":[{"id","name","artists","album","duration_ms"}]}
        """
        result = self._request("/playlist/detail", params={"id": playlist_id, "n": 1000, "s": 8})
        if result.get("code") != 200:
            logger.error("获取歌单 %s 详情失败: %s", playlist_id, result.get("msg"))
            return {}

        playlist = result.get("playlist", {})
        tracks = []
        for t in playlist.get("tracks", [])[:limit]:
            artists = "/".join(ar.get("name", "") for ar in t.get("ar", []))
            album = (t.get("al") or {}).get("name", "")
            tracks.append(
                {
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "artists": artists,
                    "album": album,
                    "duration_ms": t.get("dt", 0),
                    # fee: 0=免费 1=VIP 4=购买专辑 8=低音质免费
                    "fee": t.get("fee", 0),
                }
            )
        return {
            "id": playlist.get("id"),
            "name": playlist.get("name"),
            "track_count": playlist.get("trackCount", len(tracks)),
            "tracks": tracks,
        }

    def get_song_urls(self, song_ids: list[int], level: str = "exhigh") -> list[dict]:
        """批量获取歌曲下载链接"""
        ids_str = ",".join(str(s) for s in song_ids)
        result = self._request(
            "/song/url/v1",
            params={"id": ids_str, "level": QUALITY_LEVEL.get(level, "exhigh")},
        )
        if result.get("code") != 200:
            logger.error("获取歌曲下载链接失败: %s", result.get("msg"))
            return []
        return result.get("data", [])

    def get_song_detail(self, song_ids: list[int]) -> list[dict]:
        """获取歌曲详情（含封面、专辑、发行时间）"""
        ids_str = ",".join(str(s) for s in song_ids)
        result = self._request("/song/detail", params={"ids": ids_str})
        if result.get("code") != 200:
            logger.error("获取歌曲详情失败: %s", result.get("msg"))
            return []
        return result.get("songs", [])

    def get_lyric(self, song_id: int) -> dict:
        """获取歌词"""
        result = self._request("/lyric", params={"id": song_id})
        if result.get("code") != 200:
            return {"lrc": "", "tlyric": ""}
        return {
            "lrc": (result.get("lrc") or {}).get("lyric", ""),
            "tlyric": (result.get("tlyric") or {}).get("lyric", ""),
        }

    def search_songs(self, keyword: str, limit: int = 50, offset: int = 0) -> dict:
        """搜索单曲（type=1，可按歌曲名或歌手搜索）

        Args:
            keyword: 搜索关键词（歌曲名或歌手名）
            limit: 返回数量（最大 100）
            offset: 偏移量（用于翻页）

        Returns:
            {"items":[{"id","name","artists","album","fee"}], "total": N}
            fee: 0=免费 1=VIP 4=购买专辑 8=低音质免费
        """
        if not keyword:
            return {"items": [], "total": 0}
        result = self._request("/search", params={
            "keywords": keyword,
            "type": 1,        # 1=单曲
            "limit": min(limit, 100),
            "offset": offset,
        }, timeout=10)
        if result.get("code") != 200:
            logger.warning("搜索单曲失败: %s", result.get("msg"))
            return {"items": [], "total": 0}
        body = result.get("result") or {}
        songs = body.get("songs", [])
        total = body.get("songCount", len(songs))
        out = []
        for s in songs:
            artists = "/".join(ar.get("name", "") for ar in s.get("artists", []))
            album = (s.get("album") or {}).get("name", "")
            out.append({
                "id": s.get("id"),
                "name": s.get("name", ""),
                "artists": artists,
                "album": album,
                "fee": s.get("fee", 0),
            })
        return {"items": out, "total": total}

    def search_albums(self, keyword: str, limit: int = 50, offset: int = 0) -> dict:
        """搜索专辑（type=10）

        Args:
            keyword: 搜索关键词
            limit: 返回数量（最大 100）
            offset: 偏移量（用于翻页）

        Returns:
            {"items":[{"id","name","artist","size","publish_time"}], "total": N}
            size: 专辑内歌曲数量
        """
        if not keyword:
            return {"items": [], "total": 0}
        result = self._request("/search", params={
            "keywords": keyword,
            "type": 10,       # 10=专辑
            "limit": min(limit, 100),
            "offset": offset,
        }, timeout=10)
        if result.get("code") != 200:
            logger.warning("搜索专辑失败: %s", result.get("msg"))
            return {"items": [], "total": 0}
        body = result.get("result") or {}
        albums = body.get("albums", [])
        total = body.get("albumCount", len(albums))
        out = []
        for a in albums:
            artist = "/".join(ar.get("name", "") for ar in a.get("artists", []))
            out.append({
                "id": a.get("id"),
                "name": a.get("name", ""),
                "artist": artist,
                "size": a.get("size", 0),
                "publish_time": a.get("publishTime", 0),
            })
        return {"items": out, "total": total}

    def get_album_songs(self, album_id: int) -> list[dict]:
        """获取专辑内全部歌曲

        Args:
            album_id: 专辑 ID

        Returns:
            [{"id","name","artists","fee"}]
            fee: 0=免费 1=VIP 4=购买专辑 8=低音质免费
        """
        result = self._request("/album", params={"id": album_id}, timeout=10)
        if result.get("code") != 200:
            logger.warning("获取专辑 %s 失败: %s", album_id, result.get("msg"))
            return []
        songs = (result.get("songs") or [])
        out = []
        for s in songs:
            # /album 接口歌曲字段是 ar/al（非 artists/album）
            artists = "/".join(ar.get("name", "") for ar in s.get("ar", []))
            out.append({
                "id": s.get("id"),
                "name": s.get("name", ""),
                "artists": artists,
                "fee": s.get("fee", 0),
            })
        return out

    # ------------------------------------------------------------------
    # 发现接口（排行榜 / 热门歌单 / 分类）
    # ------------------------------------------------------------------
    def get_toplists(self) -> list[dict]:
        """获取所有官方排行榜列表

        Returns:
            [{"id","name","description","update_frequency","cover_img_url"}, ...]
        """
        result = self._request("/toplist")
        if result.get("code") != 200:
            logger.error("获取排行榜列表失败: %s", result.get("msg"))
            return []
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "description": item.get("description", ""),
                "update_frequency": item.get("updateFrequency", ""),
                "cover_img_url": item.get("coverImgUrl", ""),
                "track_count": item.get("trackCount", 0),
            }
            for item in result.get("list", [])
        ]

    def get_hot_playlists(self, cat: str = "全部", limit: int = 30, order: str = "hot", offset: int = 0) -> tuple[list[dict], int]:
        """获取热门/分类歌单（支持分页）

        Args:
            cat: 分类名（全部/华语/流行/摇滚/电子/民谣/说唱/轻音乐/爵士等）
            limit: 每页数量
            order: 排序 hot(热门) / new(最新)
            offset: 偏移量 = (page-1) * limit

        Returns:
            (playlists, total)：歌单列表和该分类下的歌单总数
        """
        params = {"cat": cat, "limit": limit, "order": order, "offset": offset}
        result = self._request("/top/playlist", params=params)
        if result.get("code") != 200:
            logger.error("获取热门歌单失败: %s", result.get("msg"))
            return [], 0
        total = result.get("total", 0) or 0
        playlists = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "cover_img_url": item.get("coverImgUrl", ""),
                "play_count": item.get("playCount", 0),
                "track_count": item.get("trackCount", 0),
                "creator": (item.get("creator") or {}).get("nickname", ""),
                "description": item.get("description", "") or "",
            }
            for item in result.get("playlists", [])
        ]
        return playlists, total

    def get_playlist_categories(self) -> list[dict]:
        """获取所有歌单分类

        Returns:
            [{"name","resource_type","category_group","hot"}, ...]
        """
        result = self._request("/playlist/catlist")
        if result.get("code") != 200:
            logger.error("获取歌单分类失败: %s", result.get("msg"))
            return []
        return [
            {
                "name": item.get("name"),
                "resource_type": item.get("resourceType", ""),
                "category_group": item.get("categoryGroup", ""),
                "hot": item.get("hot", False),
            }
            for item in result.get("sub", [])
        ]
