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
import weakref
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


def _join_artist_names(items) -> str:
    """歌手数组（ar / artists）→ 'a/b/c' 文本，容忍 name 为 null 与非 dict 元素

    上游对已下架/失效曲目会返回 {"ar": [{"name": null}]}：dict.get(k, default)
    只在「键缺失」时给默认值，值为 null 时仍返回 None，直接 join 会抛
    TypeError: sequence item 0: expected str instance, NoneType found。
    此处统一收敛为 str 并整条剔除空名（'' 混入 join 会拼出 'a//b'）。
    """
    if not isinstance(items, list):
        return ""
    names = []
    for a in items:
        if not isinstance(a, dict):
            continue
        name = a.get("name")
        if name is None:
            continue
        name = str(name).strip()
        if name:
            names.append(name)
    return "/".join(names)


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
# 前 5 档为普通档（走 /song/url/v1 试听接口，现状零回归）；
# 后 5 档为高级权益档（试听接口无 url 时逐歌调 /song/download/url/v1 补链）
QUALITY_LEVEL = {
    "standard": "standard",  # 标准 128kbps
    "higher": "higher",      # 较高 192kbps
    "exhigh": "exhigh",      # 极高 320kbps MP3
    "lossless": "lossless",  # 无损 FLAC
    "hires": "hires",        # Hi-Res
    "jymaster": "jymaster",  # 超清母带（SVIP；进统一降级链）
    "jyeffect": "jyeffect",  # 高清臻音（SVIP）
    "dolby": "dolby",        # 杜比全景声（SVIP）
    "vivid": "vivid",        # 臻音全景声（SVIP）
    "sky": "sky",            # 沉浸环绕声（SVIP）
}

# 需要 download 接口补链的高级档集合（这些档位的音源仅下载接口下发）
_HI_LEVELS = frozenset({"jymaster", "jyeffect", "dolby", "vivid", "sky"})

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
        # 工厂语义（get_provider 禁止缓存单例）：实例随调用结束丢弃，
        # GC 时自动关闭连接池，避免每个调用点都要记得 close()
        weakref.finalize(self, self.session.close)
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
        """设置 Cookie（写入 session.headers 供所有请求携带）

        统一规范化为 "k=v; k=v"（分号后带空格）格式：内置 ncm-api 服务端
        按 /;\\s+/ 正则解析请求 Cookie 头，无空格分隔时整串会被当成单个
        cookie，MUSIC_U 丢失导致所有请求降级为匿名态（实测确认）。
        """
        parts = [p.strip() for p in str(cookie or "").split(";") if p.strip()]
        self.session.headers["Cookie"] = "; ".join(parts)

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
                # 返回点单点归一为 dict：上游改写成数组/字符串时调用点
                # (body or {}).get(...) 兜不住（[] or {} 得 []），list 无 .get
                body = resp.json()
                return body if isinstance(body, dict) else {}
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
        # 官方要求 key/create/check 链路均带 timestamp，防止 CDN/代理缓存
        body = self._request("/login/qr/key",
                             params={"timestamp": int(time.time() * 1000)}, timeout=15)
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
                              params={"key": key, "qrimg": "true", "platform": "web",
                                      "timestamp": int(time.time() * 1000)}, timeout=15)
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
        # 803 后登录态校验：用下发的 cookie 调 /login/status 确认（官方流程
        # 要求），拦截"MUSIC_U 未生效"的假成功。注意响应把 code/account/
        # profile 包在 data 键里（实测 {"data":{"code":200,"profile":...}}），
        # 需读内层。retries=1 保证 803 响应在下一轮轮询（2.5s）前返回，
        # 避免下一轮查同一 key 得 800 过期、前端提前隐藏面板的竞态。
        # 注意：MUSIC_U 只能经 set_cookie 写入 session header 供校验请求携带
        # （_request 用 session.get/post 发送，无 header 即匿名校验、profile
        # 必空），故 set_cookie 必须在校验之前、顺序不可调整。
        self.set_cookie(cookie)
        inner: dict = {}
        # _request 已把 RequestException/ValueError 吞成 {"code": -1}，网络抖动
        # 与 API 服务重启都表现为 code==-1；此处对网络瞬态重试一次，避免被
        # code != 200 误判为「MUSIC_U 无效」而误导用户重新扫码
        for _try in range(2):
            verify = self._request("/login/status",
                                   params={"timestamp": int(time.time() * 1000)},
                                   timeout=5, retries=1)
            if isinstance(verify, dict) and isinstance(verify.get("data"), dict):
                inner = verify.get("data")
            elif isinstance(verify, dict):
                inner = verify
            else:
                inner = {}
            if inner.get("code") != -1:
                break
        profile = inner.get("profile") if isinstance(inner, dict) else None
        if inner.get("code") == -1:
            # 网络瞬态而非凭证无效：提示重试，不误导重新扫码。
            # （ok=false 时路由 ncm_qr_check 拦截，cookie 不会透传给前端）
            return {"ok": False, "status": 4, "cookie": "",
                    "msg": "登录态校验网络异常，请检查 API 服务后点击重试"}
        if inner.get("code") != 200 or not isinstance(profile, dict) or not profile:
            return {"ok": False, "status": 4, "cookie": cookie,
                    "msg": "扫码成功但登录态未生效（MUSIC_U 无效），请重新扫码或改用手动填入"}
        return {"ok": True, "status": 4, "cookie": cookie, "msg": ""}

    # ------------------------------------------------------------------
    # 业务接口
    # ------------------------------------------------------------------
    def get_account_info(self) -> dict:
        """获取当前登录账号详情（快速检查：5s 超时，不重试）"""
        return self._request("/user/account", timeout=5, retries=1)

    def get_vip_info(self) -> dict:
        """获取会员权益信息（含到期时间）

        /vip/info 返回的 data 结构（vipCode 为真实实测值）：
            {
                "associator":  {"vipCode": 100, "expireTime": ms, "vipLevel": 7},  # 黑胶VIP
                "musicPackage": {"vipCode": 220, "expireTime": ms, "vipLevel": 7},  # 音乐包
                "redplus":     {"vipCode": 300, "expireTime": ms, "vipLevel": 7}   # 黑胶VIP+
            }
        注意 vipCode（100/220/300）与 account.vipType（0/11/12）是两套编码，
        前者不用于展示映射；此处返回的 vip_type 仅供内部参考。
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
                "name": item.get("name") or "",
                "description": item.get("description") or "",
                "update_frequency": item.get("updateFrequency") or "",
                "cover_img_url": (
                    item.get("picUrl")
                    or item.get("coverImgUrl")
                    or item.get("coverUrl")
                    or ""
                ),
            }
            for item in result.get("list") or []
            if isinstance(item, dict)
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

        playlist = result.get("playlist")
        if not isinstance(playlist, dict):
            # playlist 缺失/为 null：视为「没拿到歌单」，返回空 dict 与 code!=200 同语义
            # （不可用 or {} 伪装成"有效空歌单"——那会让调用方把 track_count 覆盖成 0）
            logger.warning("歌单 %s 详情响应缺少 playlist（上游给出 %s），按获取失败处理",
                           playlist_id, type(playlist).__name__)
            return {}
        track_count = playlist.get("trackCount", 0)

        # 曲目来源：limit <= 1000 时 detail 一次拿全；
        # 超过 1000 时 detail 只带前 1000 首，改用 /playlist/track/all 分页拉取
        raw_tracks: list[dict] = []
        if limit > 1000:
            raw_tracks = self._fetch_all_tracks(playlist_id, limit, track_count)
        if not raw_tracks:
            # 分页接口不可用/失败时回退 detail 自带的前 1000 首
            raw_tracks = playlist.get("tracks") or []

        # 逐首解析：单首脏数据不得中断整张歌单（原列表推导式无 per-item 容错），
        # 且无 id 的失效占位曲目直接剔除（str(None) == "None" 会写进 Song 主键，
        # 把该歌永久卡成"已下载"，整张歌单再也刷不进这首歌）
        tracks = []
        for t in raw_tracks[:limit]:
            if not isinstance(t, dict):
                continue
            meta = self._parse_track(t)
            if not meta.get("id"):
                logger.warning("歌单 %s 存在无 id 曲目，已跳过: name=%r",
                               playlist_id, meta.get("name"))
                continue
            tracks.append(meta)
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
        """解析曲目结构（/playlist/detail 与 /playlist/track/all 字段一致：ar/al/dt/fee）

        全字段 null 归一（现场：/api/sync/18398083374 抛
        TypeError: sequence item 0: expected str instance, NoneType found）。
        上游失效/下架曲目会给出「键存在但值为 null」的结构，dict.get 的默认值
        兜不住，故此处显式收敛：文本字段一律 str，数值字段一律 int，
        保证返回值可直接入库（Song.name 为 NOT NULL 列，None 会抛 IntegrityError）。
        """
        al = t.get("al")
        return {
            "id": t.get("id"),
            "name": str(t.get("name") or ""),
            "artists": _join_artist_names(t.get("ar")),
            "album": str((al.get("name") if isinstance(al, dict) else "") or ""),
            "duration_ms": _to_int(t.get("dt")),
            "fee": _to_int(t.get("fee")),
        }

    def get_song_urls(self, song_ids: list[int], level: str = "exhigh") -> list[dict]:
        """批量获取歌曲下载链接

        主路径恒为 /song/url/v1 试听接口批量（普通档与现状完全一致，零回归）；
        高级档（_HI_LEVELS）下，试听接口未返回 url 的曲目再逐歌调
        /song/download/url/v1 补链——该接口上游只接受单 id（2026-09-19 实测
        逗号分隔多 id 返回「参数错误」），且 data 为单对象而非数组，
        故必须逐歌请求（请求数 = 1 + 缺失曲目数）。

        返回原始 data 列表（形状与现状一致，供 _transform.transform_song_urls
        解析：url/type/size/freeTrialInfo/code/fee/level 字段齐备）。
        """
        if not song_ids:
            return []
        lv = QUALITY_LEVEL.get(level, "exhigh")
        ids_str = ",".join(str(s) for s in song_ids)
        result = self._request(
            "/song/url/v1",
            params={"id": ids_str, "level": lv},
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
        data = result.get("data") or []
        if not isinstance(data, list):
            # 防御：服务版本差异导致 data 非数组时退回空列表（同"缺项"语义）
            logger.warning("歌曲下载链接响应 data 非数组: %s", type(data).__name__)
            return []
        if level in _HI_LEVELS:
            self._fill_hi_level_urls(data, lv)
        return data

    def _fill_hi_level_urls(self, data: list[dict], level: str) -> None:
        """高级档补链：对 data 中无 url 的曲目逐歌调下载接口，成功则就地回填

        - 下载接口上游单 id 语义（批量实测返回参数错误），且响应 data 为
          单对象；此处仍兼容数组形态做防御；
        - 补链失败保留试听接口原项（通常无 url）：jymaster 由上层降级链
          继续向低档回退，sky/vivid/dolby/jyeffect 不在链内 → 失败提示
          （不静默降级）；
        - 回填前校验 id 一致，防上游串号污染结果。
        """
        for idx, item in enumerate(data):
            if not isinstance(item, dict) or item.get("url"):
                continue
            sid = item.get("id")
            if sid is None:
                continue
            one = self._request("/song/download/url/v1",
                                params={"id": sid, "level": level})
            if not isinstance(one, dict) or one.get("code") != 200:
                continue
            fresh = one.get("data")
            if isinstance(fresh, list):      # 兼容数组形态
                fresh = fresh[0] if fresh else None
            if not isinstance(fresh, dict) or not fresh.get("url"):
                continue
            if fresh.get("id") is not None and str(fresh.get("id")) != str(sid):
                logger.warning("下载接口返回 id 与请求不一致，忽略补链: %s != %s",
                               fresh.get("id"), sid)
                continue
            data[idx] = fresh

    def get_song_detail(self, song_ids: list[int]) -> list[dict]:
        """获取歌曲详情（含封面、专辑、发行时间）"""
        ids_str = ",".join(str(s) for s in song_ids)
        result = self._request("/song/detail", params={"ids": ids_str})
        if result.get("code") != 200:
            logger.error("获取歌曲详情失败: %s", result.get("msg"))
            return []
        return result.get("songs") or []

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
        # lyric 键存在但值为 null（纯音乐/无歌词）→ or "" 归一，避免 None 透传到 mutagen
        return {
            "lrc": ((result.get("lrc") or {}).get("lyric") or "")
            if isinstance(result.get("lrc"), dict) else "",
            "tlyric": ((result.get("tlyric") or {}).get("lyric") or "")
            if isinstance(result.get("tlyric"), dict) else "",
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
        songs = body.get("songs") or []
        total = body.get("songCount", len(songs))
        out = []
        for s in songs:
            if not isinstance(s, dict):
                continue
            artists = _join_artist_names(s.get("artists"))
            album = ((s.get("album") or {}).get("name") or "") if isinstance(s.get("album"), dict) else ""
            out.append({
                "id": s.get("id"),
                "name": s.get("name") or "",
                "artists": artists,
                "album": str(album),
                "fee": _to_int(s.get("fee")),
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
        albums = body.get("albums") or []
        total = body.get("albumCount", len(albums))
        out = []
        for a in albums:
            if not isinstance(a, dict):
                continue
            artist = _join_artist_names(a.get("artists"))
            out.append({
                "id": a.get("id"),
                "name": a.get("name") or "",
                "artist": artist,
                "size": _to_int(a.get("size")),
                "publish_time": _to_int(a.get("publishTime")),
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
            if not isinstance(s, dict):
                continue
            out.append({
                "id": s.get("id"),
                "name": s.get("name") or "",
                "artists": _join_artist_names(s.get("ar")),
                "fee": _to_int(s.get("fee")),
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
                "name": item.get("name") or "",
                "description": item.get("description") or "",
                "update_frequency": item.get("updateFrequency") or "",
                "cover_img_url": (
                    item.get("picUrl")
                    or item.get("coverImgUrl")
                    or item.get("coverUrl")
                    or ""
                ),
                "track_count": _to_int(item.get("trackCount")),
            }
            for item in result.get("list") or []
            if isinstance(item, dict)
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
                "name": item.get("name") or "",
                "cover_img_url": (
                    item.get("picUrl")
                    or item.get("coverImgUrl")
                    or item.get("coverUrl")
                    or ""
                ),
                "play_count": _to_int(item.get("playCount")),
                "track_count": _to_int(item.get("trackCount")),
                "creator": ((item.get("creator") or {}).get("nickname") or "")
                if isinstance(item.get("creator"), dict) else "",
                "description": item.get("description") or "",
            }
            for item in result.get("playlists") or []
            if isinstance(item, dict)
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
                "name": item.get("name") or "",
                "resource_type": item.get("resourceType") or "",
                "category_group": item.get("categoryGroup") or "",
                "hot": item.get("hot", False),
            }
            for item in result.get("sub") or []
            if isinstance(item, dict)
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
    以 "; "（分号+空格）连接：内置 ncm-api 服务端按 /;\\s+/ 正则解析
    Cookie 头，无空格分隔时整串被当成单个 cookie，MUSIC_U 无法被识别。
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
    return "; ".join(keep)
