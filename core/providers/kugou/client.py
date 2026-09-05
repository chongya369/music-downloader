"""酷狗音乐 API 客户端 - 调用内置 kugou-api 服务

服务二进制由 bridge 模块管理（自动拉起，监听 127.0.0.1 默认 45603 端口），
API 地址每次请求时经 base_url 属性动态解析；配置外部服务地址时经
use_custom_kugou_api_url + kugou_api_base_url 设置项注入 custom_base_url 覆盖。

song_id 选型（接口文档 §9）：统一采用 album_audio_id（VARCHAR 可存），
取流必需的各档位 hash 由模块级缓存维护（_song_cache），
冷缓存时经 /krm/audio → /album/songs 两步恢复。

已知能力限制（接口文档 §5/§6，2026-09-04 实测）：
- /search?type=song 已失效（error_code 152）→ 搜索单曲走专辑中转（方案 A）
  + 歌手中转补充（方案 B）
- 歌单曲目接口 specialid 必 20010 → 传 global_collection_id（热门歌单
  列表响应携带，_gcid_cache 持久化映射）匿名可用
- 匿名（无登录 Cookie）音质封顶 128kbps MP3（v5 匿名全拒绝，走 v6）
- 无翻译歌词 → get_lyric 的 tlyric 固定空串

取流双端点路由（2026-09-04 实测，见 _get_one_url）：
- 登录态 → v5 /song/url：真实高音质（FLAC/320），匿名 status=2 全拒绝
- 匿名/登录失效兜底 → v6 /song/url/new：128 封顶（对部分 VIP 账号
  任意传参组合一律降级 128，同凭证 v5 却能拿 FLAC）
"""

import json
import logging
import re
import threading
import time

import requests

from . import bridge
from ._transform import fix_cover_url, map_fee

logger = logging.getLogger(__name__)

# token 字段判定：键名精确匹配 (?:^|;\s*)token=，兼容 token 为首个 Cookie 字段
# （扫码登录返回 "token=xxx;userid=xxx"），且避免误匹配 vip_token=。
_TOKEN_RE = re.compile(r"(?:^|;\s*)token=")

# 统一音质等级（网易云语义）-> 酷狗音质档
# 匿名状态下服务端一律降级 128kbps MP3（接口文档 §5.2 + 2026-09-04 实测：
# 任意档位 hash 匿名请求均 _errno=0，但 quality/extname/bitrate 恒为 128/mp3/128，
# 无脏文件）；320/FLAC/Hi-Res 需真实登录态（token+userid+vip_token，
# 见 _fetch_full_credential）。
# 曲目列表已含全档 hash（_QUALITY_HASH_KEY 提取），登录态经 hashes.get(quality)
# 直接取用；_get_one_url 对匿名强制 128、登录态高音质失败自动降级 128 重试。
QUALITY_LEVEL = {
    "standard": "128",   # 标准 128kbps mp3
    "higher":   "320",   # 较高 320kbps mp3（匿名降级 128）
    "exhigh":   "320",   # 极高 320kbps mp3（匿名降级 128）
    "lossless": "flac",  # 无损 flac（匿名降级 128）
    "hires":    "high",  # Hi-Res（匿名降级 128）
}

# 音质档 -> 曲目列表响应中的 hash 字段名（三种响应形态命名一致）
_QUALITY_HASH_KEY = {
    "128": "hash_128",
    "320": "hash_320",
    "flac": "hash_flac",
    "high": "hash_high",
}

# 接口白名单（P0 防护二道防线）：只允许调用实测可用的路由，
# 白名单外直接抛 ValueError，防止误触崩溃/失效接口。
# 明确禁用：/song/auth /song/url/auth/merge
# 取流双端点分工（2026-09-04 实测）：
# - /song/url(v5)：登录态专用。返回真实高音质（FLAC/320），
#   但匿名（仅 dfid）status=2 连 128 都拒绝
# - /song/url/new(v6)：匿名兜底 + 登录态失效兜底（128 可下）。
#   对部分 VIP 账号任意传参组合一律降级 128kbps（cookie 全量/
#   显式 token+userid+vip_token/vipType=6/7 均无效），
#   同凭证 v5 却返回真实 FLAC → 登录态主路径走 v5
# /search/complex /search/mixed（type=song 参数级失效由调用侧保证）
# 注：/playlist/track/all 2026-09-04 复测——specialid 必 20010，
# 但传 global_collection_id（collection_3_{suid}_{slid}_0）匿名可用，
# 已纳入白名单（仅 gcid 形式，specialid 由 _gcid_cache 解析）
# 注：/login/token 为扫码后补 VIP 凭证用（二维码登录上游只回 token+userid，
# 缺 vip_token/vip_type，取流 VIP 歌会被判非 VIP → _errno=6）
_ALLOWED_ROUTES = {
    "/register/dev",
    "/search", "/search/suggest", "/search/hot", "/search/lyric",
    "/lyric",
    "/audio", "/krm/audio", "/song/url", "/song/url/new",
    "/album", "/album/detail", "/album/songs", "/top/album",
    "/artist/detail", "/artist/audios", "/artist/albums", "/singer/list",
    "/rank/list", "/rank/info", "/rank/audio", "/rank/top",
    "/top/playlist", "/playlist/tags", "/playlist/track/all",
    "/login/qr/key", "/login/qr/create", "/login/qr/check",
    "/login/token",
    "/server/now",
    "/user/detail",
}

# dfid 失效错误码（接口文档 §4）：命中后重新注册 dfid 并重试
_DFID_STALE_CODE = 152

# 模块级 dfid 缓存：{base_url: dfid}
# dfid 是设备维度公开标识（与账号无关），缓存不违反工厂语义；
# 实测同一 dfid 在百余次请求中持续有效
_dfid_cache: dict[str, str] = {}
_dfid_lock = threading.Lock()

# 模块级歌曲缓存（公开数据，非账号数据，不违反工厂语义）：
# {album_audio_id: {"hashes": {档位: hash}, "meta": SongMeta, "name": str,
#                   "artists": str, "album": str, "duration_ms": int, "ts": float}}
# 搜索/专辑/榜单流程写入 hash，窄接口（取流/详情/歌词）共享读取，
# 一首歌全程只打一次 /krm/audio
_song_cache: dict[str, dict] = {}
_song_cache_lock = threading.Lock()
_SONG_TTL = 3600          # 秒：曲目信息基本不变，1 小时足够
_SONG_CACHE_MAX = 3000    # 容量上限（FIFO 淘汰），防榜单批量任务撑爆内存

# 模块级榜单缓存：{base_url: {"list": [rank 条目], "ts": float}}
# 供 get_toplists 与 get_playlist_detail 的 rankid 白名单校验共享
_rank_cache: dict[str, dict] = {}
_rank_cache_lock = threading.Lock()
_RANK_TTL = 3600

# 模块级 specialid → global_collection_id 映射（歌单曲目接口的钥匙）：
# /playlist/track/all 只认 gcid（collection_3_{suid}_{slid}_0），上游无
# specialid→gcid 换算接口；gcid 仅在歌单列表响应中出现，故在浏览热门
# 歌单时顺手缓存。映射需跨 Web 重启存活（已添加歌单的每日同步依赖），
# 落盘到 api/ 目录 JSON 文件（bridge.bin_dir 同级二进制所在目录）
_gcid_cache: dict[int, str] = {}
_gcid_lock = threading.Lock()
_GCID_CACHE_MAX = 20000   # 容量上限（FIFO 淘汰），防映射无限膨胀
_gcid_file_loaded = False

# 模块级歌单分类缓存：{base_url: {"map": {分类名: tag_id}, "ts": float}}
_tags_cache: dict[str, dict] = {}
_tags_lock = threading.Lock()
_TAGS_TTL = 86400         # 分类基本不变，24h


def _gcid_cache_file() -> "object":
    """specialid→gcid 映射落盘文件（api/ 目录，与二进制同级）"""
    return bridge.get_bridge(auto_start=False).bin_dir / ".kugou_gcid_cache.json"


def _load_gcid_cache() -> None:
    """首次使用时从磁盘加载映射（进程内只加载一次）"""
    global _gcid_file_loaded
    if _gcid_file_loaded:
        return
    _gcid_file_loaded = True
    try:
        f = _gcid_cache_file()
        if f.exists():
            raw = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    try:
                        _gcid_cache[int(k)] = str(v)
                    except (TypeError, ValueError):
                        continue
    except (OSError, ValueError) as e:
        logger.warning("加载酷狗 gcid 映射缓存失败（忽略，冷启动重建）: %s", e)


def _save_gcid_cache() -> None:
    """把映射写回磁盘（调用方须持有 _gcid_lock；写失败仅告警不影响业务）"""
    try:
        f = _gcid_cache_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({str(k): v for k, v in _gcid_cache.items()},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(f)
    except OSError as e:
        logger.warning("保存酷狗 gcid 映射缓存失败（不影响本次业务）: %s", e)


def _cache_gcid_mappings(items: list[dict]) -> None:
    """从歌单列表条目批量提取 specialid→gcid 映射（浏览即缓存）"""
    dirty = False
    with _gcid_lock:
        _load_gcid_cache()
        for it in items:
            try:
                sid = int(it.get("specialid"))
            except (TypeError, ValueError):
                continue
            gcid = str(it.get("global_collection_id") or "").strip()
            if gcid and _gcid_cache.get(sid) != gcid:
                _gcid_cache[sid] = gcid
                dirty = True
        # 容量控制：超限时按插入序淘汰最旧条目
        overflow = len(_gcid_cache) - _GCID_CACHE_MAX
        if overflow > 0:
            for k in list(_gcid_cache)[:overflow]:
                _gcid_cache.pop(k, None)
            dirty = True
        if dirty:
            _save_gcid_cache()


def _resolve_gcid(special_id: int) -> str:
    """specialid → global_collection_id（miss 返回空串）"""
    with _gcid_lock:
        _load_gcid_cache()
        return _gcid_cache.get(special_id, "")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class KuGouClient:
    """酷狗音乐 API 客户端

    通过内置 kugou-api 服务调用酷狗音乐接口（可被 custom_base_url 覆盖）。
    登录 Cookie（token=xxx;userid=xxx）经查询参数 cookie 透传，
    dfid 设备指纹由本客户端自动注册并携带（用户无需提供）。
    """

    def __init__(self, cookie: str = "", custom_base_url: str = ""):
        """持有 bridge 引用；API 地址经 base_url 属性动态解析

        Args:
            cookie: 登录 Cookie 字符串（token=xxx;userid=xxx;...）
            custom_base_url: 自定义API服务URL（设置后直接使用，不通过内置bridge）
        """
        self._bridge = bridge.get_bridge()
        self._custom_base_url = custom_base_url.rstrip("/") if custom_base_url else ""
        self._cookie = cookie or ""
        self.session = requests.Session()
        # 显式关闭代理环境变量读取，避免本机 API 请求走代理
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": _UA})

    @property
    def base_url(self) -> str:
        """API 服务地址（动态解析，每次请求重新取值）

        - 自定义URL优先：设置后直接返回自定义地址
        - 内置服务：Web 停止→重启后端口会变（45603 被占用时回退随机端口），
          缓存旧地址会导致请求失败
        - 服务未运行时经 start() 幂等拉起
        - _request 自带 3 次重试，拉起期间请求可自然恢复
        """
        if self._custom_base_url:
            return self._custom_base_url
        return self._bridge.start().rstrip("/")

    def set_cookie(self, cookie: str) -> None:
        """设置登录 Cookie（酷狗登录态核心字段：token + userid）"""
        self._cookie = cookie or ""

    # ------------------------------------------------------------------
    # dfid 设备指纹管理（接口文档 §4）
    # ------------------------------------------------------------------
    def _register_dfid(self) -> str:
        """调 /register/dev 注册设备，返回 dfid"""
        url = f"{self.base_url}/register/dev"
        try:
            resp = self.session.get(url, timeout=15)
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            raise RuntimeError(f"酷狗API设备注册失败: {e}") from e
        dfid = (data.get("data") or {}).get("dfid")
        if not dfid:
            raise RuntimeError("酷狗API设备注册失败（响应无 dfid）")
        return str(dfid)

    def _ensure_dfid(self) -> str:
        """获取 dfid（按 base_url 分桶缓存，miss 时注册）"""
        key = self.base_url
        with _dfid_lock:
            dfid = _dfid_cache.get(key)
            if dfid:
                return dfid
            dfid = self._register_dfid()
            _dfid_cache[key] = dfid
            return dfid

    def _invalidate_dfid(self) -> None:
        """废弃缓存的 dfid（遇到 152 时调用，下次请求重新注册）"""
        with _dfid_lock:
            _dfid_cache.pop(self.base_url, None)

    def _build_cookie(self) -> str:
        """合并登录 Cookie 与 dfid（服务端对客户端已提供的 dfid 不覆盖）"""
        dfid = self._ensure_dfid()
        if self._cookie:
            return f"{self._cookie};dfid={dfid}"
        return f"dfid={dfid}"

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    def _request(self, path: str, params: dict | None = None,
                 retries: int = 3, timeout: int = 15,
                 bust_cache: bool = False) -> dict:
        """调用酷狗 API 接口，返回上游原始 JSON body

        - 白名单外路由直接抛 ValueError（P0 防护）
        - Cookie（登录态 + dfid）经查询参数 cookie 透传（服务端自动与平台
          标识 Cookie 合并）
        - 注意：上游业务失败时 kugou-api 会把 HTTP 状态码改为 502 并返回
          原始 JSON body（如匿名 /user/detail 的 20018），因此不能靠
          raise_for_status 判断成败，必须解析 body 中的 error_code
        - dfid 失效（error_code 152）：废弃缓存并重新注册后重试
        - bust_cache：取流类请求追加随机 timestamp 参数，绕过服务端
          2 分钟响应缓存（CDN 链接带时间戳目录，缓存复用可能已失效）

        连接失败抛 RuntimeError（中文提示，供上层捕获展示）；
        其他失败记日志返回 {}。
        """
        if path not in _ALLOWED_ROUTES:
            raise ValueError(f"酷狗API接口不在白名单，禁止调用: {path}")
        # url 在循环内每次重新解析：停止→重启后端口漂移时，
        # 正在重试的请求也能经 base_url 属性取到新地址
        for attempt in range(1, retries + 1):
            url = f"{self.base_url}{path}"
            p = dict(params or {})
            try:
                p["cookie"] = self._build_cookie()
            except RuntimeError as e:
                # dfid 注册失败（服务未就绪等）：重试或抛出
                if attempt < retries:
                    time.sleep(1.5 * attempt)
                    continue
                raise
            if bust_cache:
                p["timestamp"] = int(time.time() * 1000) + attempt
            try:
                resp = self.session.get(url, params=p, timeout=timeout)
                try:
                    data = resp.json()
                except ValueError:
                    logger.warning("请求 %s 第 %d 次返回非 JSON（HTTP %s）",
                                   path, attempt, resp.status_code)
                    if attempt < retries:
                        time.sleep(1.5 * attempt)
                        continue
                    return {}
                # dfid 失效：废弃后重试（下轮 _build_cookie 会重新注册）
                if data.get("error_code") == _DFID_STALE_CODE:
                    logger.info("dfid 失效(152)，重新注册: %s", path)
                    self._invalidate_dfid()
                    if attempt < retries:
                        continue
                    return data
                return data
            except requests.exceptions.ConnectionError as e:
                raise RuntimeError(
                    f"无法连接酷狗音乐API服务（{self.base_url}），"
                    f"请检查服务是否运行或地址配置是否正确: {e}"
                ) from e
            except requests.RequestException as e:
                logger.warning("请求 %s 第 %d 次失败: %s", path, attempt, e)
                if attempt < retries:
                    time.sleep(1.5 * attempt)
        logger.error("请求 %s 失败，已重试 %d 次", path, retries)
        return {}

    # ------------------------------------------------------------------
    # 歌曲条目归一化（三种响应形态 → 统一结构）
    # ------------------------------------------------------------------
    @staticmethod
    def _norm_song(raw: dict) -> dict | None:
        """归一化上游歌曲条目，返回 None 表示条目无效（无 album_audio_id）

        上游四种形态（2026-09-04 实测）：
        1. 嵌套型（/album/songs → data.songs[]）：
           base.album_audio_id / base.audio_name / base.author_name，
           audio_info.hash 即 128 档 hash（hash_128 同时存在）
        2. 扁平榜单型（/rank/audio → data.songlist[]）：
           顶层 songname / author_name / album_audio_id，
           audio_info.hash 为空、128 档在 audio_info.hash_128
        3. 扁平歌手型（/artist/audios → data[] 直接数组）：
           顶层 audio_name / author_name / hash / hash_128 / timelength
        4. 歌单曲目型（/playlist/track/all → data.songs[]）：
           顶层 mixsongid / name / hash / timelen，
           歌手在 singerinfo[].name、专辑在 albuminfo.name；
           注意 mixsongid 才是可传给 /krm/audio 的 album_audio_id
           （顶层 audio_id 是 wide_audio_id，取流无效，实测验证）；
           name 实测为「歌手 - 歌名」合并串，经歌手一致性校验拆分
           （见形态 4 分支内注释）

        Returns:
            {"id"(album_audio_id), "name", "artists", "album",
             "duration_ms", "hashes": {档位: hash}, "fee"}
        """
        # 形态 4：歌单曲目型（mixsongid 顶层字段是本形态唯一标识）
        if "mixsongid" in raw:
            singer_names = [str(s.get("name") or "").strip()
                            for s in (raw.get("singerinfo") or [])]
            artists = "/".join(n for n in singer_names if n)
            name = str(raw.get("name") or "")
            # 上游 /playlist/track/all 的 name 是「歌手 - 歌名」合并串
            # （实测 '丁阳、顾焕gkuank - 听闻山上有路 (男生版)'），直接用会
            # 与 build_filename 拼出「歌手 - 歌手 - 歌名」重复文件名，且与
            # 搜索链路（纯歌名）格式不一致。仅当前段歌手与 singerinfo
            # 完全一致时才拆分，避免误伤歌名本身含 ' - ' 的歌。
            if " - " in name and singer_names:
                front, _, rest = name.partition(" - ")
                front_set = {p.strip() for p in re.split(r"[、/]", front)}
                singer_set = {n for n in singer_names if n}
                if rest and front_set == singer_set:
                    name = rest
            hashes: dict[str, str] = {}
            if raw.get("hash"):
                hashes["128"] = str(raw["hash"]).upper()
            try:
                duration_ms = int(raw.get("timelen") or 0)
            except (TypeError, ValueError):
                duration_ms = 0
            return {
                "id": str(raw["mixsongid"]),
                "name": name,
                "artists": artists,
                "album": str((raw.get("albuminfo") or {}).get("name") or ""),
                "duration_ms": duration_ms,
                "hashes": hashes,
                "fee": map_fee(raw),
            }
        base = raw.get("base") or {}
        ai = raw.get("audio_info") or {}
        aaid = base.get("album_audio_id") or raw.get("album_audio_id")
        if aaid is None:
            return None
        name = (base.get("audio_name") or raw.get("audio_name")
                or base.get("songname") or raw.get("songname") or "")
        # 嵌套型有 authors 数组；扁平型只有 author_name 字符串
        artists = (base.get("author_name") or raw.get("author_name")
                   or "/".join(a.get("author_name", "") for a in (raw.get("authors") or []))
                   or "")
        album = ((raw.get("album_info") or {}).get("album_name")
                 or raw.get("album_name") or "")
        hashes: dict[str, str] = {}
        for quality, key in _QUALITY_HASH_KEY.items():
            h = ai.get(key) or raw.get(key)
            if h:
                hashes[quality] = str(h).upper()
        if "128" not in hashes:
            # 128 档兜底：嵌套型 audio_info.hash / 扁平型顶层 hash
            h = ai.get("hash") or raw.get("hash")
            if h:
                hashes["128"] = str(h).upper()
        duration_ms = (ai.get("duration") or ai.get("duration_128")
                       or raw.get("timelength") or raw.get("timelength_128") or 0)
        try:
            duration_ms = int(duration_ms)
        except (TypeError, ValueError):
            duration_ms = 0
        return {
            "id": str(aaid),
            "name": str(name),
            "artists": str(artists),
            "album": str(album),
            "duration_ms": duration_ms,
            "hashes": hashes,
            "fee": map_fee(raw),
        }

    @staticmethod
    def _cache_songs(norms: list[dict]) -> None:
        """把归一化歌曲条目写入模块级缓存（搜索/专辑/榜单流程调用）"""
        if not norms:
            return
        now = time.time()
        with _song_cache_lock:
            for n in norms:
                _song_cache[n["id"]] = {
                    "hashes": n["hashes"],
                    "name": n["name"],
                    "artists": n["artists"],
                    "album": n["album"],
                    "duration_ms": n["duration_ms"],
                    "ts": now,
                }
            # 容量控制：超限时按插入序淘汰最旧条目
            overflow = len(_song_cache) - _SONG_CACHE_MAX
            if overflow > 0:
                for k in list(_song_cache)[:overflow]:
                    _song_cache.pop(k, None)

    def _ensure_song(self, song_id: str) -> dict | None:
        """确保歌曲条目在缓存中且含元数据与 hash（冷缓存两步回填）

        song_id = album_audio_id。缓存 miss 时：
        1) /krm/audio 取元数据（含 album_id，可定位所属专辑；本身不返回 hash）
        2) /album/songs?id={album_id} 回查所属专辑曲目，恢复各档位 hash

        Returns:
            缓存条目 dict；歌曲不存在（/krm/audio 无数据）返回 None
        """
        sid = str(song_id)
        now = time.time()
        with _song_cache_lock:
            entry = _song_cache.get(sid)
        if entry and now - entry["ts"] < _SONG_TTL \
                and entry.get("hashes") and entry.get("meta"):
            return entry

        body = self._request("/krm/audio", {"album_audio_id": sid})
        data = body.get("data") or []
        item = data[0] if data else {}
        base = item.get("base") or {}
        if not base.get("album_audio_id"):
            # krm 失败：返回已有缓存条目（可能缺 meta/hash），由调用方降级
            return entry if entry else None
        album_info = item.get("album_info") or {}
        meta = {
            "title": base.get("songname") or (entry or {}).get("name") or "",
            "artist": base.get("author_name") or (entry or {}).get("artists") or "",
            "album": album_info.get("album_name") or base.get("album_name")
                     or (entry or {}).get("album") or "",
            "year": (base.get("publish_date") or "")[:4],
            "cover_url": fix_cover_url(album_info.get("cover") or ""),
        }
        # hash 恢复：已有缓存 hash 优先，缺失时经所属专辑曲目回查
        hashes = (entry or {}).get("hashes") or {}
        album_id = base.get("album_id")
        if not hashes and album_id:
            for s in self._fetch_album_songs(int(album_id), max_songs=100):
                if s.get("id") == sid:
                    hashes = s.get("hashes") or {}
                    break
        new_entry = {
            "hashes": hashes,
            "meta": meta,
            "name": meta["title"],
            "artists": meta["artist"],
            "album": meta["album"],
            # 时长优先取曲目列表来源（krm/audio 无时长字段）
            "duration_ms": (entry or {}).get("duration_ms") or 0,
            "ts": now,
        }
        with _song_cache_lock:
            _song_cache[sid] = new_entry
        return new_entry

    # ------------------------------------------------------------------
    # 窄接口：播放/下载地址
    # ------------------------------------------------------------------
    def get_song_urls(self, song_ids: list[str], level: str = "exhigh") -> list[dict]:
        """获取歌曲下载链接（酷狗不支持批量，逐首取流）

        端点路由见 _get_one_url：匿名走 v6（128 封顶），登录态走 v5
        （真实高音质，失败降级 128）。失败时 url=None，上层按
        「无版权或需VIP」失败处理。
        ext 一律以响应实际字段为准——服务端可能降级（如匿名传高音质
        档 hash 时返回 128 mp3），按请求档位写 ext 会产生 .flac 后缀的
        mp3 脏文件。

        Returns:
            UrlInfo 列表，与 song_ids 顺序对齐：
            [{"url": str|None, "ext": str, "size": int|None, "is_trial": False}]
        """
        quality = QUALITY_LEVEL.get(level, "128")
        out = []
        for sid in song_ids:
            out.append(self._get_one_url(str(sid), quality))
        return out

    def _is_logged_in(self) -> bool:
        """是否带登录 token（cookie 含独立 token 字段；键名精确匹配 token=，
        不要求 token 必须是首个字段，也不会误匹配 vip_token=）"""
        return bool(_TOKEN_RE.search(self._cookie or ""))

    def _request_url_v5(self, hash_: str, sid: str, quality: str) -> dict:
        """请求 v5 /song/url 取流（登录态专用，返回真实高音质）

        响应为扁平结构（顶层 status/extName/bitRate/fileSize/url[]），
        归一化为 {"ok", "url", "ext", "size", "bitrate", "err"}。
        匿名（仅 dfid）时 status=2 连 128 都拒绝，勿在匿名路径调用。
        bust_cache：CDN 链接带时间戳目录，绕过服务端 2 分钟缓存。
        """
        body = self._request("/song/url",
                             {"hash": hash_, "album_audio_id": sid,
                              "quality": quality},
                             bust_cache=True)
        if not isinstance(body, dict) or not body:
            return {"ok": False, "url": None, "ext": "", "size": None,
                    "bitrate": 0, "err": "取流响应异常"}
        if body.get("status") != 1:
            return {"ok": False, "url": None, "ext": "", "size": None,
                    "bitrate": 0,
                    "err": f"取流失败(status={body.get('status')},"
                           f"error_code={body.get('error_code')})"}
        urls = body.get("url") or []
        if not urls:
            return {"ok": False, "url": None, "ext": "", "size": None,
                    "bitrate": 0, "err": "响应无url"}
        try:
            size = int(body.get("fileSize") or 0) or None
        except (TypeError, ValueError):
            size = None
        try:
            bitrate = int(body.get("bitRate") or 0)
        except (TypeError, ValueError):
            bitrate = 0
        return {"ok": True, "url": urls[0],
                "ext": body.get("extName") or "mp3", "size": size,
                "bitrate": bitrate, "err": ""}

    def _request_url_v6(self, hash_: str, sid: str) -> dict:
        """请求 v6 /song/url/new 取流（匿名兜底，固定 128 mp3）

        响应嵌套 data[0].info（_errno/extname/bitrate/filesize/tracker_url），
        归一化同 _request_url_v5。登录态下该端点对部分 VIP 账号一律
        降级 128（实测任何传参组合均无法拿到高音质），仅作兜底。
        """
        body = self._request("/song/url/new",
                             {"hash": hash_, "album_audio_id": sid},
                             bust_cache=True)
        items = body.get("data") or []
        item = items[0] if items else {}
        if item.get("_errno") != 0:
            return {"ok": False, "url": None, "ext": "", "size": None,
                    "err": f"取流失败(_errno={item.get('_errno')})"}
        info = item.get("info") or {}
        urls = info.get("tracker_url") or []
        if not urls:
            return {"ok": False, "url": None, "ext": "", "size": None,
                    "err": "响应无tracker_url"}
        try:
            size = int(info.get("filesize") or 0) or None
        except (TypeError, ValueError):
            size = None
        return {"ok": True, "url": urls[0],
                "ext": info.get("extname") or "mp3", "size": size, "err": ""}

    def _get_one_url(self, sid: str, quality: str) -> dict:
        # err 字段携带失败诊断（url=None 时非空），供上层区分
        # 「登录态失效/鉴权失败」与「真无版权」，避免笼统报"无版权或需VIP"
        def _empty(err: str = "") -> dict:
            return {"url": None, "ext": "mp3", "size": None,
                    "is_trial": False, "err": err}
        entry = self._ensure_song(sid)
        if not entry:
            return _empty("元数据或hash获取失败")
        hashes = entry.get("hashes") or {}

        # 匿名态高音质档会被服务端强制降级 128（实测任意档位 hash 匿名均返回
        # _errno=0 且 quality/extname 恒为 128/mp3），直接用 128 hash 结果相同
        # 且省一次无效请求；登录态（含 token）才按目标档位请求真实高音质。
        target = quality if self._is_logged_in() else "128"
        hash_ = hashes.get(target) or hashes.get("128")
        if not hash_:
            return _empty("无可用音质hash")

        # 双端点路由：匿名走 v6（v5 匿名 status=2 全拒绝）；
        # 登录态走 v5 拿真实高音质（v6 对部分 VIP 账号一律降级 128）
        if not self._is_logged_in():
            item = self._request_url_v6(hash_, sid)
        else:
            item = self._request_url_v5(hash_, sid, target)
            # 登录态三级降级链（目标档失败时）：
            # 1) v5 128 档重试（非 VIP 账号冲高音质 / 该档需更高会员）
            # 2) v6 128 兜底（v5 拒绝但 token 未完全失效时仍可下 128）
            if not item.get("ok"):
                hash128 = hashes.get("128")
                if hash128 and hash128 != hash_:
                    retry = self._request_url_v5(hash128, sid, "128")
                    if retry.get("ok"):
                        item = retry
                if not item.get("ok"):
                    retry6 = self._request_url_v6(
                        hashes.get("128") or hash_, sid)
                    if retry6.get("ok"):
                        item = retry6
        if not item.get("ok"):
            # v5 err 含 status/error_code，v6 err 含 _errno（6=音频不存在，
            # VIP 歌无有效登录凭证时常见，即凭证缺 vip_token/vip_type 或 token 失效）
            return _empty(item.get("err") or "取流失败")
        return {
            "url": item["url"],
            "ext": item.get("ext") or "mp3",
            "size": item.get("size"),
            "is_trial": False,
            "err": "",
        }

    # ------------------------------------------------------------------
    # 窄接口：歌曲详情
    # ------------------------------------------------------------------
    def get_song_detail(self, song_ids: list[str]) -> list[dict]:
        """获取歌曲详情——/krm/audio 元数据（走 _song_cache，与取流共享）

        artist 取上游主歌手；取不到时为空串，触发 task_manager 用任务
        记录的 artists 回退决定下载子目录。
        """
        out = []
        for sid in song_ids:
            entry = self._ensure_song(str(sid))
            if not entry or not entry.get("meta"):
                out.append({
                    "title": (entry or {}).get("name") or "",
                    "artist": (entry or {}).get("artists") or "",
                    "album": (entry or {}).get("album") or "",
                    "year": "",
                    "cover_url": "",
                    "duration_ms": (entry or {}).get("duration_ms") or 0,
                })
                continue
            meta = dict(entry["meta"])
            meta["duration_ms"] = entry.get("duration_ms") or 0
            out.append(meta)
        return out

    # ------------------------------------------------------------------
    # 窄接口：歌词
    # ------------------------------------------------------------------
    def get_lyric(self, song_id: str) -> dict:
        """获取歌词——两步链路：/search/lyric（必须带 hash）→ /lyric

        酷狗无翻译歌词，tlyric 固定空串。

        Returns:
            {"lrc": str, "tlyric": str}
        """
        empty = {"lrc": "", "tlyric": ""}
        entry = self._ensure_song(str(song_id))
        if not entry:
            return empty
        hash_ = (entry.get("hashes") or {}).get("128")
        if not hash_:
            return empty
        params = {"hash": hash_}
        duration_ms = entry.get("duration_ms") or 0
        if duration_ms:
            params["duration"] = duration_ms // 1000
        if entry.get("name"):
            params["keywords"] = entry["name"]
        body = self._request("/search/lyric", params)
        cands = body.get("candidates") or []
        if not cands:
            return empty
        c = cands[0]
        if not (c.get("id") and c.get("accesskey")):
            return empty
        body2 = self._request("/lyric", {
            "id": c["id"], "accesskey": c["accesskey"],
            "fmt": "lrc", "decode": "true",
        })
        return {"lrc": body2.get("decodeContent") or "", "tlyric": ""}

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def search_songs(self, keyword: str, limit: int = 50, offset: int = 0) -> dict:
        """搜索单曲——专辑中转 + 歌手中转（/search?type=song 已失效，接口文档 §6.2）

        方案 A（专辑中转）：/search?type=album → 逐专辑 /album/songs，
        合并去重（一次能拿到整张专辑的歌，hash 同时入缓存）；
        结果不足时方案 B（歌手中转）补充：/search?type=author → /artist/audios。
        中转搜索无自然分页，offset 参数忽略（恒返回第一页），total 用实际条数。

        Returns:
            {"items":[{"id"(album_audio_id),"name","artists","album","fee"}],
             "total": N}
            fee: 0=免费 1=VIP（pkg_price/musicpack_advance 启发式映射）
        """
        if not keyword:
            return {"items": [], "total": 0}
        candidates: list[dict] = []
        seen: set[str] = set()

        # 方案 A：专辑中转（取前 5 个专辑避免请求过多；每专辑最多取 50 首
        # 候选，先聚合再按相关性排序截断，避免首个专辑塞满结果导致
        # 目标歌曲（可能在前几个专辑之外）不出现）
        body = self._request("/search", {
            "keywords": keyword, "type": "album",
            "page": 1, "pagesize": 5,
        }, timeout=10)
        albums = (body.get("data") or {}).get("lists") or []
        for alb in albums[:5]:
            try:
                album_id = int(alb.get("albumid"))
            except (TypeError, ValueError):
                continue
            for s in self._fetch_album_songs(album_id, max_songs=50):
                if s["id"] not in seen:
                    seen.add(s["id"])
                    candidates.append(s)

        # 方案 B：歌手中转（结果不足时补充）
        if len(candidates) < limit:
            body2 = self._request("/search", {
                "keywords": keyword, "type": "author",
                "page": 1, "pagesize": 3,
            }, timeout=10)
            for au in (body2.get("data") or {}).get("lists") or []:
                try:
                    author_id = int(au.get("AuthorId") or 0)
                except (TypeError, ValueError):
                    continue
                if not author_id:
                    continue
                for s in self._fetch_artist_songs(author_id, pagesize=min(limit, 50)):
                    if s["id"] not in seen:
                        seen.add(s["id"])
                        candidates.append(s)

        # 相关性排序（稳定排序）：歌名包含关键词的优先，其余保持原序
        kw = keyword.lower()
        candidates.sort(key=lambda s: kw not in s["name"].lower())
        items = candidates[:limit]

        out = [{"id": s["id"], "name": s["name"], "artists": s["artists"],
                "album": s["album"], "fee": s["fee"]} for s in items]
        return {"items": out, "total": len(out)}

    def search_albums(self, keyword: str, limit: int = 50, offset: int = 0) -> dict:
        """搜索专辑（/search?type=album 原生可用）

        Returns:
            {"items":[{"id"(albumid),"name","artist","size","publish_time"}],
             "total": N}
        """
        if not keyword:
            return {"items": [], "total": 0}
        page = offset // limit + 1 if limit > 0 else 1
        body = self._request("/search", {
            "keywords": keyword, "type": "album",
            "page": page, "pagesize": min(limit, 50),
        }, timeout=10)
        data = body.get("data") or {}
        out = []
        for a in (data.get("lists") or []):
            try:
                size = int(a.get("songcount") or a.get("track_count") or 0)
            except (TypeError, ValueError):
                size = 0
            out.append({
                "id": a.get("albumid"),
                "name": a.get("albumname") or "",
                "artist": a.get("singer") or "",
                "size": size,
                "publish_time": a.get("publish_time") or "",
            })
        return {"items": out, "total": data.get("total") or len(out)}

    # ------------------------------------------------------------------
    # 专辑
    # ------------------------------------------------------------------
    def get_album_songs(self, album_id) -> list[dict]:
        """获取专辑内全部歌曲（注意上游参数名为 id 而非 album_id）

        Returns:
            [{"id"(album_audio_id),"name","artists","album","fee"}]
            各档位 hash 同步写入 _song_cache 供取流使用
        """
        songs = self._fetch_album_songs(int(album_id))
        return [{"id": s["id"], "name": s["name"], "artists": s["artists"],
                 "album": s["album"], "fee": s["fee"]} for s in songs]

    def _fetch_album_songs(self, album_id: int, page_size: int = 50,
                           max_songs: int = 300) -> list[dict]:
        """调 /album/songs 分页拉取专辑曲目并归一化（含缓存写入）

        pagesize 上限 50——实测 >50 返回 error_code 20010 invalid param。
        """
        out: list[dict] = []
        total = None
        page = 1
        while len(out) < max_songs:
            body = self._request("/album/songs", {
                "id": album_id, "page": page, "pagesize": page_size,
            })
            data = body.get("data") or {}
            raw_list = data.get("songs") or []
            norms = [n for n in (self._norm_song(s) for s in raw_list) if n]
            out.extend(norms)
            self._cache_songs(norms)
            if total is None:
                total = data.get("total") or 0
            if not raw_list or (total and len(out) >= total) or page >= 10:
                break
            page += 1
        return out

    def _fetch_artist_songs(self, author_id: int, pagesize: int = 50) -> list[dict]:
        """调 /artist/audios 拉取歌手热门单曲（data 直接为数组的扁平结构）"""
        body = self._request("/artist/audios", {
            "id": author_id, "page": 1, "pagesize": pagesize,
        })
        raw_list = body.get("data") or []
        if not isinstance(raw_list, list):
            return []
        norms = [n for n in (self._norm_song(s) for s in raw_list) if n]
        self._cache_songs(norms)
        return norms

    # ------------------------------------------------------------------
    # 发现接口（仅榜单；歌单曲目接口不可用，接口文档 §6.3）
    # ------------------------------------------------------------------
    def get_toplists(self) -> list[dict]:
        """获取所有排行榜列表（/rank/list，实测 57 个榜，扁平结构）

        Returns:
            [{"id"(rankid),"name","description","update_frequency",
              "cover_img_url","track_count":0}, ...]
            track_count 固定 0：上游无此字段，真实曲目数在
            get_playlist_detail 走 /rank/audio 时以 total 回填
        """
        return [
            {
                "id": r.get("rankid"),
                "name": r.get("rankname") or "",
                "description": r.get("intro") or "",
                "update_frequency": r.get("update_frequency") or "",
                "cover_img_url": fix_cover_url(r.get("banner_9") or r.get("imgurl") or ""),
                "track_count": 0,
            }
            for r in self._load_ranks()
        ]

    def _load_ranks(self) -> list[dict]:
        """加载 /rank/list 榜单清单（模块级缓存，按 base_url 分桶）

        data.info[] 为扁平榜单数组（children 恒空，兼容未来分组结构）。
        """
        key = self.base_url
        with _rank_cache_lock:
            cached = _rank_cache.get(key)
        if cached and time.time() - cached["ts"] < _RANK_TTL:
            return cached["list"]
        body = self._request("/rank/list")
        info = (body.get("data") or {}).get("info") or []
        ranks: list[dict] = []
        for g in info:
            # 分组结构兼容：children 非空时取子项（实测 57 个榜均为扁平）
            entries = g.get("children") or [g]
            for r in entries:
                if r.get("rankid") is None:
                    continue
                ranks.append(r)
        if ranks:
            with _rank_cache_lock:
                _rank_cache[key] = {"list": ranks, "ts": time.time()}
        return ranks

    def get_playlist_detail(self, playlist_id, limit: int = 200) -> dict:
        """获取榜单/歌单详情，包含歌曲列表

        按 ID 类型分流（酷狗 rankid 与 specialid 量级重叠，不能按位数分流）：
        - rankid（在 /rank/list 集合内）→ /rank/audio（官方榜单）
        - specialid（歌单）→ /playlist/track/all，但该接口只认
          global_collection_id（collection_3_{suid}_{slid}_0），上游无
          specialid→gcid 换算接口，故依赖 _gcid_cache——热门歌单浏览时
          已持久化缓存；缓存 miss（如直接粘贴未浏览过的歌单链接）返回 {}

        Returns:
            {"id","name","track_count","tracks":[{"id","name","artists","fee"}]}
            解析失败/无法解析 gcid 返回 {}
        """
        try:
            pid = int(playlist_id)
        except (TypeError, ValueError):
            return {}
        ranks = {int(r["rankid"]): r for r in self._load_ranks() if r.get("rankid") is not None}
        if pid in ranks:
            return self._rank_detail(pid, ranks[pid], limit)
        return self._playlist_tracks_detail(pid, limit)

    def _rank_detail(self, pid: int, rank: dict, limit: int) -> dict:
        """官方榜单详情（/rank/audio，扁平 songlist 结构）"""
        tracks: list[dict] = []
        seen: set[str] = set()
        total = None
        page, page_size = 1, 50    # 上限 50（实测 >50 报 20010）
        while len(tracks) < limit and page <= 10:
            body = self._request("/rank/audio", {
                "rankid": pid, "page": page, "pagesize": page_size,
            })
            data = body.get("data") or {}
            raw_list = data.get("songlist") or []
            norms = [n for n in (self._norm_song(s) for s in raw_list) if n]
            self._cache_songs(norms)
            for n in norms:
                if n["id"] in seen:
                    continue
                seen.add(n["id"])
                tracks.append({"id": n["id"], "name": n["name"],
                               "artists": n["artists"], "fee": n["fee"]})
                if len(tracks) >= limit:
                    break
            if total is None:
                total = data.get("total") or 0
            if not raw_list or (total and len(tracks) >= total):
                break
            page += 1
        return {
            "id": pid,
            "name": rank.get("rankname") or str(pid),
            "track_count": total or len(tracks),
            "tracks": tracks,
        }

    def _playlist_tracks_detail(self, pid: int, limit: int) -> dict:
        """用户歌单详情（/playlist/track/all，需 gcid；页面结构见 _norm_song 形态4）"""
        gcid = _resolve_gcid(pid)
        if not gcid:
            logger.info("酷狗歌单 %s 无 gcid 映射（需先在热门歌单浏览一次以缓存）", pid)
            return {}
        tracks: list[dict] = []
        seen: set[str] = set()
        total = None
        name = ""
        page, page_size = 1, 50
        while len(tracks) < limit and page <= 10:
            body = self._request("/playlist/track/all", {
                "id": gcid, "page": page, "pagesize": page_size,
            })
            if body.get("error_code") not in (0, None):
                logger.warning("酷狗歌单曲目获取失败 (%s): %s", gcid, body.get("errmsg"))
                break
            data = body.get("data") or {}
            raw_list = data.get("songs") or []
            norms = [n for n in (self._norm_song(s) for s in raw_list) if n]
            self._cache_songs(norms)
            for n in norms:
                if n["id"] in seen:
                    continue
                seen.add(n["id"])
                tracks.append({"id": n["id"], "name": n["name"],
                               "artists": n["artists"], "fee": n["fee"]})
                if len(tracks) >= limit:
                    break
            li = data.get("list_info") or {}
            if not name:
                name = str(li.get("name") or "")
            if total is None:
                total = data.get("count") or 0
            if not raw_list or (total and len(tracks) >= total):
                break
            page += 1
        if not tracks:
            return {}
        return {
            "id": pid,
            "name": name or str(pid),
            "track_count": total or len(tracks),
            "tracks": tracks,
        }

    def get_hot_playlists(self, cat: str = "全部", limit: int = 30,
                          order: str = "hot", offset: int = 0) -> tuple[list[dict], int]:
        """获取热门/分类歌单（/top/playlist，2026-09-04 实测匿名可用）

        上游分页特性（实测）：返回条数不受 pagesize 完全控制——推荐流
        （category_id=0）固定每页 35 条、分类页固定 30 条，故以 30 请求、
        本地切片对齐 offset，不足一页且 has_next 时继续翻页补足。
        每条歌单的 specialid→gcid 映射在本方法内顺手持久化（后续
        get_playlist_detail 取曲目依赖该映射）。

        Args:
            cat: 分类名（经 /playlist/tags 解析为 tag_id，未知名回退 0=全部）
            limit: 每页数量
            order: hot（sort=1）/ new（sort=2，按 publishtime 降序实测验证）
            offset: 偏移量

        Returns:
            (playlists, total)：total 上游不提供，按 has_next 合成
            （offset + 本页条数 + has_next 时补一页），仅支撑翻页控件
        """
        tags = self._load_playlist_tags()
        category_id = tags.get(cat, 0)
        sort = 1 if order != "new" else 2
        page_size = 30                     # 上游分类页固定 30/页（实测 20/35/50 均回 30）
        page = offset // page_size + 1
        start = offset % page_size
        collected: list[dict] = []
        total = 0
        empty_retry_done = False           # sort=1 偶发空页（实测），回退 sort=2 重试一次
        while len(collected) < limit:
            bust = bool(empty_retry_done)   # 重试请求绕过服务端 2 分钟响应缓存
            body = self._request("/top/playlist", {
                "page": page, "pagesize": page_size,
                "sort": sort, "category_id": category_id,
            }, bust_cache=bust)
            if body.get("error_code") not in (0, None):
                logger.warning("酷狗热门歌单获取失败: %s", body.get("errmsg"))
                break
            data = body.get("data") or {}
            raw_list = data.get("special_list") or []
            if not raw_list:
                if not empty_retry_done and not collected:
                    # 上游空页偶发（推荐流分页不稳定 + 服务端 2 分钟响应缓存），
                    # 带 bust_cache 重试一次，仍空则按无数据返回
                    empty_retry_done = True
                    time.sleep(0.5)
                    continue
                break
            _cache_gcid_mappings(raw_list)
            seg = raw_list[start:] if page == offset // page_size + 1 else raw_list
            start = 0                      # 仅第一页需要切片对齐
            for it in seg:
                if len(collected) >= limit:
                    break
                try:
                    pid = int(it.get("specialid"))
                except (TypeError, ValueError):
                    continue
                collected.append({
                    "id": pid,
                    "name": str(it.get("specialname") or ""),
                    "cover_img_url": fix_cover_url(
                        it.get("imgurl") or it.get("flexible_cover") or it.get("pic") or ""),
                    "play_count": it.get("play_count") or 0,
                    "track_count": 0,      # 上游列表不提供曲目数
                    "creator": str(it.get("nickname") or it.get("singername") or ""),
                    "description": str(it.get("intro") or ""),
                })
            has_next = data.get("has_next")
            if len(collected) >= limit:
                break
            if not has_next:
                break
            page += 1
        # total 合成：仅用于翻页控件（有无下一页 + 当前偏移）
        total = offset + len(collected) + (page_size if has_next else 0) if collected else 0
        return collected, total

    def _load_playlist_tags(self) -> dict[str, int]:
        """加载歌单分类名→tag_id 映射（/playlist/tags，24h 缓存）

        data[] 为父子两层树（tag_name/tag_id + son[]），拍平两层。
        """
        key = self.base_url
        with _tags_lock:
            cached = _tags_cache.get(key)
            if cached and time.time() - cached["ts"] < _TAGS_TTL:
                return cached["map"]
        body = self._request("/playlist/tags")
        mapping: dict[str, int] = {}
        for node in body.get("data") or []:
            try:
                mapping[str(node.get("tag_name"))] = int(node.get("tag_id"))
            except (TypeError, ValueError):
                continue
            for son in node.get("son") or []:
                try:
                    mapping[str(son.get("tag_name"))] = int(son.get("tag_id"))
                except (TypeError, ValueError):
                    continue
        if mapping:
            with _tags_lock:
                _tags_cache[key] = {"map": mapping, "ts": time.time()}
        return mapping

    def get_playlist_categories(self) -> list[dict]:
        """获取所有歌单分类（/playlist/tags，2026-09-04 实测匿名可用）

        Returns:
            [{"name": 分类名}, ...]；"全部"固定在首位；解析失败回退 [{"name": "全部"}]
        """
        names = list(self._load_playlist_tags().keys())
        if "全部" in names:
            names.remove("全部")
        names.insert(0, "全部")
        return [{"name": n} for n in names]

    # ------------------------------------------------------------------
    # 账号信息
    # ------------------------------------------------------------------
    def get_user_info(self) -> dict:
        """获取当前登录账号信息（/user/detail，需 token+userid 登录 Cookie）

        匿名实测返回 HTTP 502（error_code 20018）。登录态下的响应字段
        未经实测（无可用账号），解析做防御式兼容；失败时 ok=False，
        上层保留账号原有信息不覆盖（同 QQ /getUserInfo 语义）。

        Returns:
            {"ok": bool, "nickname": str, "vip_type": int,
             "vip_expire_ts": int, "msg": str}
        """
        err = {"ok": False, "nickname": "", "vip_type": 0,
               "vip_expire_ts": 0, "msg": ""}
        if not _TOKEN_RE.search(self._cookie or ""):
            err["msg"] = "Cookie 缺少 token（酷狗登录态核心字段）"
            return err
        body = self._request("/user/detail")
        if not isinstance(body, dict):
            err["msg"] = "酷狗API返回异常"
            return err
        if body.get("error_code") not in (0, None):
            err["msg"] = f"Cookie 无效或登录态失效（error_code={body.get('error_code')}）"
            return err
        d = body.get("data") or {}
        if isinstance(d, list):
            d = d[0] if d else {}
        u = d.get("user") or d
        # 字段名以 2026-09-04 实测为准：顶层 data.nickname（此前误用
        # nick_name/user_name/pnick 导致 VIP 账号昵称恒空 → 显示"未知"）
        nickname = str(u.get("nickname") or u.get("nick_name")
                       or u.get("user_name") or u.get("pnick") or "")
        is_vip = bool(u.get("is_vip") or u.get("vip_type"))
        return {
            "ok": True,
            "nickname": nickname,
            "vip_type": 1 if is_vip else 0,
            "vip_expire_ts": 0,
            "msg": "" if nickname else "昵称未获取到（Cookie 可能不完整）",
        }

    def test_download_capability(self) -> dict:
        """实测当前 cookie 的下载能力（登录态 + 高音质是否真生效）

        用一首热门歌的高音质档（优先 flac，其次 320）直接请求 v5
        /song/url，不降级，根据原始响应诊断登录态是否被酷狗服务端认可：
        - ok 且 extName=flac 或 bitRate>320000 → 高音质生效，登录态有效
        - ok 且 mp3 128                        → 登录态被降级（异常）
        - 不 ok                                 → 登录态失效（status!=1）

        Returns:
            {"ok": bool, "level": str, "msg": str}
            level: "flac"/"320"/"128"/"" —— 实际可达的最高档
        """
        try:
            r = self.search_songs("周杰伦", limit=1)
            items = r.get("items") or []
            if not items:
                return {"ok": False, "level": "", "msg": "搜索测试歌无结果"}
            sid = items[0]["id"]
            entry = self._ensure_song(sid)
            hashes = (entry or {}).get("hashes") or {}
            test_hash, test_q = "", ""
            for q in ("flac", "320"):
                if hashes.get(q):
                    test_hash, test_q = hashes[q], q
                    break
            if not test_hash:
                return {"ok": False, "level": "", "msg": "测试歌无高音质档（仅128），无法验证"}
            item = self._request_url_v5(test_hash, sid, test_q)
            if not item.get("ok"):
                return {"ok": False, "level": "",
                        "msg": f"{test_q}档不可用（{item.get('err')}），登录态可能已失效"}
            ext = item.get("ext") or ""
            # v5 顶层 bitRate 单位 bps（实测 flac=997499、320=320000）
            bitrate = int(item.get("bitrate") or 0)
            if ext == "flac" or bitrate > 320000:
                return {"ok": True, "level": test_q,
                        "msg": f"高音质生效（{ext} {bitrate // 1000}kbps），VIP 凭证完整"}
            return {"ok": True, "level": "128",
                    "msg": f"被降级 {ext} {bitrate // 1000}kbps，登录态可能未生效（128 仍可下，高音质不可用）"}
        except Exception as e:
            return {"ok": False, "level": "", "msg": f"实测异常: {e}"}

    # ------------------------------------------------------------------
    # 扫码登录（/login/qr/*，2026-09-04 实测匿名可用）
    # ------------------------------------------------------------------
    def create_qr_login(self) -> dict:
        """生成酷狗扫码登录二维码

        链路：/login/qr/key 拿 key → 强制 /login/qr/create?qrimg=true 渲染 base64 图像
        （login/qr/key 上游不返回 qrcode_img，仅返回 key，故无"直接返回图"捷径）

        Returns:
            {"ok": bool, "key": str, "qr_img": str, "msg": str}
             qr_img 统一为 "data:image/png;base64,..." 完整 data URI（前端可直接 <img src>）
        """
        # bust_cache：防止拿到缓存的旧 key（上游 2 分钟响应缓存）
        body = self._request("/login/qr/key", bust_cache=True)
        data = body.get("data") or {}
        # key 字段名：实测 KuGou 上游 /v2/qrcode 返回 data.qrcode
        key = str(data.get("qrcode") or data.get("key") or "")
        if not key:
            return {"ok": False, "key": "", "qr_img": "",
                    "msg": f"二维码 key 生成失败（error_code={body.get('error_code')}）"}
        # 强制走渲染接口，避免对不存在的 qrcode_img 字段做假设
        body2 = self._request("/login/qr/create",
                              {"key": key, "qrimg": "true"},
                              bust_cache=True)
        img = str((body2.get("data") or {}).get("base64") or "")
        if not img:
            return {"ok": False, "key": key, "qr_img": "",
                    "msg": "二维码图片渲染失败（上游未返回 base64）"}
        # 防御式补齐 data URI 前缀：
        # 新版 upstream 返回 qrcode.toDataURL() 已自带前缀；旧二进制可能漏前缀。
        if img and not img.startswith("data:image"):
            img = "data:image/png;base64," + img
        return {"ok": True, "key": key, "qr_img": img, "msg": ""}

    def check_qr_login(self, key: str) -> dict:
        """轮询扫码状态（/login/qr/check）

        上游状态码（接口文档 §2.2）：0=二维码过期 1=等待扫码
        2=已扫码待确认 4=授权登录成功（此时返回 token+userid）

        token 提取优先级：data.token → cookie 数组（模块会把
        "token=xxx"/"userid=xxx" push 进响应 cookie 列表）。

        注意必须 bust_cache：服务端对 GET 响应有 2 分钟缓存（实测同 key
        无时间戳的第二次轮询直接命中缓存不打上游），不加时间戳轮询会
        永远拿到首次的 status=1，扫码成功后页面无反应。

        Returns:
            {"ok": bool, "status": int, "cookie": str, "msg": str}
            status=4 时 cookie 为 "token=xxx;userid=xxx"（可直接
            填入账号 Cookie 字段）
        """
        body = self._request("/login/qr/check", {"key": str(key or "")},
                             bust_cache=True)
        if body.get("error_code") not in (0, None):
            return {"ok": False, "status": -1, "cookie": "",
                    "msg": f"扫码状态查询失败（error_code={body.get('error_code')}）"}
        try:
            status = int((body.get("data") or {}).get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        if status != 4:
            return {"ok": True, "status": status, "cookie": "", "msg": ""}
        d = body.get("data") or {}
        token = str(d.get("token") or "")
        userid = str(d.get("userid") or "")
        if not token:
            # 兜底：上游模块把 token/userid 追加进响应 cookie 数组
            for c in body.get("cookie") or []:
                cs = str(c).strip()
                if cs.startswith("token="):
                    token = cs[len("token="):]
                elif cs.startswith("userid="):
                    userid = cs[len("userid="):]
        if not token:
            return {"ok": False, "status": 4, "cookie": "",
                    "msg": "授权成功但响应未包含 token，请重试或改用手动填入"}
        # 补完整 VIP 凭证：二维码登录上游只回 token+userid，缺 vip_token/vip_type。
        # 凭证不完整时部分 VIP 歌取流会被拒（v6 _errno=6 / v5 status!=1）。
        # 经 /login/token（入参恰为 token+userid）换完整凭证；失败退回基础 token。
        full = self._fetch_full_credential(token, userid)
        if full:
            parts = [f"token={full.get('token') or token}"]
            if full.get("userid") or userid:
                parts.append(f"userid={full.get('userid') or userid}")
            if full.get("t1"):
                parts.append(f"t1={full['t1']}")
            if full.get("vip_type"):
                parts.append(f"vip_type={full['vip_type']}")
            if full.get("vip_token"):
                parts.append(f"vip_token={full['vip_token']}")
            cookie = ";".join(parts)
        else:
            cookie = f"token={token}"
            if userid:
                cookie += f";userid={userid}"
        return {"ok": True, "status": 4, "cookie": cookie, "msg": ""}

    def _fetch_full_credential(self, token: str, userid: str) -> dict:
        """调 /login/token 换完整登录凭证（含 vip_token/vip_type）

        上游 KuGouMusicApi 的二维码登录路径（login_qr_check.js）只返回
        token+userid，遗漏了 vip_token/vip_type；而其他登录方式（账密/手机码/
        QQ/刷新登录）都会补齐。凭证不完整会导致部分 VIP 歌取流被拒
        （v6 _errno=6 / v5 status!=1）。
        /login/token 入参恰为 token+userid，返回完整五件套，用作补救。

        Returns:
            {"t1","token","userid","vip_type","vip_token"} 子集；
            失败（路由异常/登录失效/无凭证字段）返回 {}，调用方退回基础 token。
        """
        if not token:
            return {}
        try:
            body = self._request("/login/token",
                                 {"token": token, "userid": userid or "0"},
                                 bust_cache=True)
        except (ValueError, RuntimeError):
            return {}
        if not isinstance(body, dict):
            return {}
        # 成功判定：error_code 0/None 且 status=1；失败形如
        # {"data":null,"status":0,"error_code":20017}（token 无效）
        if body.get("error_code") not in (0, None) or body.get("status") != 1:
            return {}
        d = body.get("data")
        if not isinstance(d, dict):
            return {}
        out = {}
        for k in ("t1", "token", "userid", "vip_type", "vip_token"):
            v = d.get(k)
            if v is not None and str(v) != "":
                out[k] = str(v)
        # 兜底：响应 cookie 数组（防御式，上游部分版本会把凭证 push 进 cookie）
        if len(out) < 2:
            for c in body.get("cookie") or []:
                cs = str(c).strip()
                for k in ("t1", "token", "userid", "vip_type", "vip_token"):
                    if k not in out and cs.startswith(f"{k}="):
                        out[k] = cs[len(k) + 1:]
        return out
