"""网易云音乐 API 客户端 - 调用内置 NeteaseCloudMusicApi-enhanced 服务

服务二进制由 bridge 模块管理（自动拉起，监听 127.0.0.1 随机端口），
API 地址每次请求时经 base_url 属性动态解析，无需手动指定。

会员鉴权通过 Cookie 中的 MUSIC_U 实现，所有需要会员权限的接口会自动带上 Cookie。

扫码登录：create_qr_login / check_qr_login 走上游 /login/qr/* 原生路由，
成功时 Cookie 清洗（_extract_login_cookie）后与手工录入共用 set_cookie 链。
"""

import logging
import re
import time
from typing import Any

import requests

from . import bridge

logger = logging.getLogger(__name__)


def _to_int(value) -> int:
    """宽容整型转换（None/脏数据 → 0；网易云 cd 字段为字符串碟号）"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
        self._bridge = bridge.get_bridge()
        self._custom_base_url = custom_base_url.rstrip("/") if custom_base_url else ""
        # 专辑元数据缓存 {album_id: {"albumartist": str, "tracks": {song_id: (no, cd)}}}
        # 网易云 /song/detail 不含音轨号，经 /album 补全；同专辑整批下载只调一次
        self._album_meta_cache: dict[str, dict] = {}
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
    # 扫码登录
    # ------------------------------------------------------------------
    def create_qr_login(self) -> dict:
        """生成网易云扫码登录二维码（/login/qr/key + /login/qr/create 两步链）

        key 上游为 /api/login/qrcode/unikey 生成的 UUID；二维码由 API 服务
        本地渲染（qrcode.toDataURL），不碰网易云上游（实测伪 key 也能出图）。

        Returns:
            {"ok": bool, "key": str, "qr_img": str, "msg": str}
            key 用于轮询（check_qr_login）；qr_img 可直接用于 <img src>
        """
        body = self._request("/login/qr/key", timeout=15)
        if (body or {}).get("code") != 200:
            return {"ok": False, "key": "", "qr_img": "",
                    "msg": f"二维码 key 生成失败（code={(body or {}).get('code')}）"}
        data = body.get("data") or {}
        # 实测形态 data.unikey；兼容历史嵌套形态 data.data.unikey
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        key = str((inner or {}).get("unikey") or "")
        if not key:
            return {"ok": False, "key": "", "qr_img": "",
                    "msg": "二维码 key 生成失败（上游未返回 unikey）"}
        body2 = self._request("/login/qr/create",
                              params={"key": key, "qrimg": "true"}, timeout=15)
        if (body2 or {}).get("code") != 200:
            return {"ok": False, "key": key, "qr_img": "",
                    "msg": f"二维码图片渲染失败（code={(body2 or {}).get('code')}）"}
        d = body2.get("data") or {}
        # 兼容两层 data 嵌套（data.data.qrimg 与 data.qrimg 两种历史形态）
        inner2 = d.get("data") if isinstance(d.get("data"), dict) else d
        img = str((inner2 or {}).get("qrimg") or "")
        if not img:
            return {"ok": False, "key": key, "qr_img": "",
                    "msg": "二维码图片渲染失败（上游未返回 qrimg）"}
        return {"ok": True, "key": key, "qr_img": img, "msg": ""}

    def check_qr_login(self, key: str) -> dict:
        """轮询扫码状态（/login/qr/check）

        上游 code：801=等待扫码 802=已扫码待确认 803=授权成功（cookie 下发）
        800=二维码过期/不存在（HTTP 恒 200，无异常路径）。
        映射为酷狗语义 status（api 层与前端复用同一状态机）：
        801→1 等待 802→2 已扫 803→4 成功 800→0 过期。
        网易云无"用户拒绝授权"态（QQ 的 REFUSE），status 3 不会出现。

        参数带 noCookie=true（官方文档建议：扫码后状态异常时加此参数；
        同时避免 enhanced 服务把上游匿名 cookie Set-Cookie 进本地会话）
        与 timestamp 防缓存。

        成功时 body.cookie 为 Set-Cookie 数组 join(';') 串，混有
        Max-Age/Expires/Path 属性片段，经 _extract_login_cookie 清洗为
        纯 k=v 对（MUSIC_U 必含），落库格式与手工录入一致。

        Returns:
            {"ok": bool, "status": int, "cookie": str, "msg": str}
            status=4 时 cookie 非空，可直接填入账号 Cookie 字段
        """
        empty = {"ok": False, "status": -1, "cookie": "", "msg": ""}
        key = str(key or "").strip()
        if not key:
            empty["msg"] = "缺少二维码 key"
            return empty
        body = self._request("/login/qr/check",
                             params={"key": key, "noCookie": "true",
                                     "timestamp": int(time.time() * 1000)},
                             timeout=15)
        if not isinstance(body, dict) or not body:
            empty["msg"] = "扫码状态查询失败"
            return empty
        try:
            code = int(body.get("code"))
        except (TypeError, ValueError):
            code = -1
        # 诊断日志：上游原始 code 落 INFO——"扫码后恒 801"类问题需凭
        # 日志判定是未扫码（801）还是查询失败（-1/异常体）。仅扫码登录
        # 轮询期间产生（约 0.4 行/秒），量可控
        logger.info("网易云扫码状态: key=%s… code=%s", key[:6], code)
        status = {801: 1, 802: 2, 800: 0, 803: 4}.get(code, -1)
        if status < 0:
            logger.warning("网易云扫码未知状态码，原始响应: code=%s body=%s", code, body)
            empty["msg"] = f"未知扫码状态码（code={code}）"
            return empty
        if status != 4:
            return {"ok": True, "status": status, "cookie": "", "msg": ""}
        cookie = _extract_login_cookie(str(body.get("cookie") or ""))
        if not cookie:
            return {"ok": False, "status": 4, "cookie": "",
                    "msg": "授权成功但响应未包含 MUSIC_U，请重试或改用手动填入"}
        return {"ok": True, "status": 4, "cookie": cookie, "msg": ""}

    # ------------------------------------------------------------------
    # 业务接口
    # ------------------------------------------------------------------
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
        选择策略：遍历 redplus(SVIP) / associator(黑胶VIP) / musicPackage(音乐包)，
        取到期时间最晚的会员（用户可能同时持有多种权益，最晚到期时间才是实际失效时间）

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

        # 遍历所有会员类型，取到期时间最晚的会员
        # （用户可能同时持有多种权益，如黑胶VIP 2026 到期 + SVIP 2027 到期，
        #   实际失效时间应取最晚的，而非按类型优先级取第一个）
        best_vip_code = 0
        best_expire_ms = None
        for key in ("redplus", "associator", "musicPackage"):
            pkg = data.get(key) or {}
            vip_code = int(pkg.get("vipCode") or 0)
            if vip_code <= 0:
                continue
            try:
                expire_ms = int(pkg.get("expireTime") or 0)
            except (TypeError, ValueError):
                expire_ms = 0
            # expireTime <= 0 表示永久或未真正开通，跳过
            if expire_ms <= 0:
                continue
            if best_expire_ms is None or expire_ms > best_expire_ms:
                best_vip_code = vip_code
                best_expire_ms = expire_ms

        if best_expire_ms is not None:
            return {"vip_type": best_vip_code, "expire_time": best_expire_ms}

        # vipCode > 0 但均无有效到期时间（永久/未真正开通），按类型优先级返回
        for key in ("redplus", "associator", "musicPackage"):
            pkg = data.get(key) or {}
            vip_code = int(pkg.get("vipCode") or 0)
            if vip_code > 0:
                return {"vip_type": vip_code, "expire_time": None}

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
                "cover_img_url": (
                    item.get("picUrl")
                    or item.get("coverImgUrl")
                    or item.get("coverUrl")
                    or ""
                ),
            }
            for item in result.get("list", [])
        ]

    def get_playlist_detail(self, playlist_id: int, limit: int = 200) -> dict:
        """获取歌单/榜单详情，包含歌曲列表

        Args:
            playlist_id: 歌单 ID 或榜单 ID（榜单本质也是歌单）
            limit: 取前 N 首（/playlist/detail 单次最多返回 1000 首，
                   limit 超过 1000 时经 /playlist/track/all 分页补齐）

        Returns:
            {"name","track_count","tracks":[{"id","name","artists","album","duration_ms"}]}
        """
        result = self._request("/playlist/detail", params={"id": playlist_id, "n": 1000, "s": 8})
        if result.get("code") != 200:
            logger.error("获取歌单 %s 详情失败: %s", playlist_id, result.get("msg"))
            return {}

        playlist = result.get("playlist", {})
        track_count = playlist.get("trackCount", 0)

        # 曲目来源：limit <= 1000 时 detail 一次拿全；
        # 超过 1000 时 detail 只带前 1000 首，改用 /playlist/track/all 分页拉取
        raw_tracks: list[dict] = []
        if limit > 1000:
            raw_tracks = self._fetch_all_tracks(playlist_id, limit, track_count)
        if not raw_tracks:
            # 分页接口不可用/失败时回退 detail 自带的前 1000 首
            raw_tracks = playlist.get("tracks", [])

        tracks = [self._parse_track(t) for t in raw_tracks[:limit]]
        return {
            "id": playlist.get("id"),
            "name": playlist.get("name"),
            "track_count": track_count or len(tracks),
            "tracks": tracks,
        }

    def _fetch_all_tracks(self, playlist_id: int, limit: int, track_count: int) -> list[dict]:
        """经 /playlist/track/all 分页拉取歌单曲目（突破 1000 首上限）

        Args:
            playlist_id: 歌单 ID
            limit: 需要的最大曲目数
            track_count: 歌单总曲目数（用于提前终止，0/未知时忽略）

        Returns:
            原始曲目列表（接口失败时可能为空或不完整，调用方回退 detail）
        """
        raw_tracks: list[dict] = []
        page_size = 500
        offset = 0
        while offset < limit:
            result = self._request("/playlist/track/all", params={
                "id": playlist_id, "limit": page_size, "offset": offset})
            if result.get("code") != 200:
                logger.warning("分页拉取歌单 %s 曲目失败(offset=%d): %s",
                               playlist_id, offset, result.get("msg"))
                break
            songs = result.get("songs") or []
            if not songs:
                break
            raw_tracks.extend(songs)
            # 实收不足一页或已达歌单总数 → 后续无更多曲目
            if len(songs) < page_size or (track_count and len(raw_tracks) >= track_count):
                break
            offset += page_size
        return raw_tracks

    @staticmethod
    def _parse_track(t: dict) -> dict:
        """解析曲目结构（/playlist/detail 与 /playlist/track/all 字段一致：ar/al/dt/fee）"""
        artists = "/".join(ar.get("name", "") for ar in t.get("ar", []))
        album = (t.get("al") or {}).get("name", "")
        return {
            "id": t.get("id"),
            "name": t.get("name"),
            "artists": artists,
            "album": album,
            "duration_ms": t.get("dt", 0),
            # fee: 0=免费 1=VIP 4=购买专辑 8=低音质免费
            "fee": t.get("fee", 0),
        }

    def get_song_urls(self, song_ids: list[int], level: str = "exhigh") -> list[dict]:
        """批量获取歌曲下载链接"""
        ids_str = ",".join(str(s) for s in song_ids)
        result = self._request(
            "/song/url/v1",
            params={"id": ids_str, "level": QUALITY_LEVEL.get(level, "exhigh")},
        )
        if result.get("code") != 200:
            msg = result.get("msg") or result.get("message") or ""
            # 接口整体失败：返回带 err 诊断的占位结构（与 song_ids 等长），
            # 不再吞成空列表——否则上层把接口失败伪装成"无版权或需VIP"
            logger.error("获取歌曲下载链接失败: code=%s msg=%s", result.get("code"), msg)
            return [
                {
                    "id": sid,
                    "url": None,
                    "ext": "mp3",
                    "size": None,
                    "freeTrialInfo": None,
                    "code": result.get("code"),
                    "err": f"api:{result.get('code')}:{msg}",
                }
                for sid in song_ids
            ]
        return result.get("data", [])

    def get_song_detail(self, song_ids: list[int]) -> list[dict]:
        """获取歌曲详情（含封面、专辑、发行时间）"""
        ids_str = ",".join(str(s) for s in song_ids)
        result = self._request("/song/detail", params={"ids": ids_str})
        if result.get("code") != 200:
            logger.error("获取歌曲详情失败: %s", result.get("msg"))
            return []
        return result.get("songs", [])

    def get_album_meta(self, album_id) -> dict:
        """获取专辑元数据（专辑歌手 + 音轨号/碟号映射），按专辑 ID 缓存

        网易云 /song/detail 不含音轨号/碟号，/album 响应的 songs[].no
        （整数音轨号）、songs[].cd（字符串碟号）与 album.artist.name
        （专辑主歌手）为权威来源。失败静默返回空 dict，不阻断下载。
        """
        key = str(album_id or "")
        if not key:
            return {}
        if key not in self._album_meta_cache:
            entry: dict = {"albumartist": "", "tracks": {}}
            try:
                result = self._request("/album", params={"id": album_id}, timeout=10)
                if result.get("code") == 200:
                    album = result.get("album") or {}
                    entry["albumartist"] = ((album.get("artist") or {}).get("name")) or ""
                    for s in (result.get("songs") or []):
                        sid = str(s.get("id") or "")
                        if sid:
                            entry["tracks"][sid] = (
                                _to_int(s.get("no")),
                                _to_int(s.get("cd")),
                            )
            except Exception as e:
                logger.warning("获取专辑元数据失败 (album_id=%s): %s", key, e)
            self._album_meta_cache[key] = entry
        return self._album_meta_cache[key]

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
                "cover_img_url": (
                    item.get("picUrl")
                    or item.get("coverImgUrl")
                    or item.get("coverUrl")
                    or ""
                ),
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
                "cover_img_url": (
                    item.get("picUrl")
                    or item.get("coverImgUrl")
                    or item.get("coverUrl")
                    or ""
                ),
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


# ======================================================================
# 扫码登录辅助
# ======================================================================
# 扫码成功 Cookie 清洗白名单：仅保留登录态必需/常用的纯 k=v 对。
# 上游 /login/qr/check 的 cookie 字段是 Set-Cookie 数组 join(';')，
# 混有 Max-Age/Expires/Path 等属性片段（实测 NMTID 一条就带四个属性），
# 整串入库会污染 Cookie，必须清洗。MUSIC_U 是登录态核心，缺失即失败。
_LOGIN_COOKIE_KEYS = ("MUSIC_U", "__csrf", "NMTID", "os", "appver")

# Set-Cookie 属性键（非键值对，出现即丢弃）
_COOKIE_ATTR_KEYS = {
    "Max-Age", "Expires", "Path", "Domain", "HttpOnly", "Secure",
    "SameSite", "Version", "Comment", "Priority", "Partitioned",
}


def _extract_login_cookie(raw: str) -> str:
    """从 Set-Cookie 拼接串中提取登录态所需的纯 k=v 对

    上游 803（授权成功）时 body.cookie 形如：
        "MUSIC_U=xxx; Expires=...; Max-Age=...; Path=/;
         __csrf=yyy; ...; NMTID=zzz; Max-Age=...; Expires=...; Path=/;"

    按 _LOGIN_COOKIE_KEYS 白名单逐片段提取（"k=v" 首个 '=' 分割，容忍
    base64 值中的 '='）；MUSIC_U 缺失返回空串（视为登录态不完整）。
    """
    if not raw:
        return ""
    keep: list[str] = []
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        v = v.strip()
        if not k or not v or k in _COOKIE_ATTR_KEYS:
            continue
        if k in _LOGIN_COOKIE_KEYS:
            keep.append(f"{k}={v}")
    if not any(p.startswith("MUSIC_U=") for p in keep):
        return ""
    return ";".join(keep)
