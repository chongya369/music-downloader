"""QQ音乐 API 客户端 - 调用内置 qqmusic-api 服务

服务二进制由 bridge 模块管理（自动拉起，监听 127.0.0.1 默认 45602 端口），
API 地址每次请求时经 base_url 属性动态解析；配置外部服务地址时经
use_custom_qq_api_url + qq_api_base_url 设置项注入 custom_base_url 覆盖。

会员鉴权通过 Cookie（uin/qqmusic_key 等）实现，经 X-QQMusic-Cookie 请求头
透传，可解锁 VIP 歌曲与无损音质；匿名可用低音质。

已知能力限制（QQ API 服务端未提供对应接口）：
- 无歌词接口 → get_lyric 返回空
"""

import logging
import time

import requests

from . import bridge

logger = logging.getLogger(__name__)

# 统一音质等级（网易云语义）-> QQ quality 参数值
# 刻意不映射 m4a/ape 档：输出仅 mp3/flac，复用现有 MP3/FLAC 标签写入能力
QUALITY_LEVEL = {
    "standard": "128",   # 标准 128kbps mp3 (M500)
    "higher": "320",     # 较高 320kbps mp3 (M800)
    "exhigh": "320",     # 极高 320kbps mp3 (M800)
    "lossless": "flac",  # 无损 flac (F000)
    "hires": "flac",     # Hi-Res（QQ 无对应档，映射 flac）
}

# quality -> 文件扩展名
QUALITY_EXT = {
    "128": "mp3",
    "320": "mp3",
    "flac": "flac",
}

# 歌单分类"全部"的 categoryId
_ALL_CATEGORY_ID = 10000000

# 模块级分类缓存：{base_url: {分类名: categoryId}}
# 缓存的是公开数据（与账号/cookie 无关），不是 provider 实例，
# 不违反"工厂语义、每次调用返回新实例"约束——前端"查分类"与
# "按分类拉歌单"是两次独立请求，实例级缓存跨请求必然失效
_CATEGORY_CACHE: dict[str, dict[str, int]] = {}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fix_img_url(url: str) -> str:
    """补全 QQ 图片协议前缀（上游常返回 //imgcache... 开头）"""
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return url


class QqClient:
    """QQ音乐 API 客户端

    通过内置 qqmusic-api 服务调用 QQ音乐接口（可被 custom_base_url 覆盖）。
    Cookie 用于会员鉴权（uin + qqmusic_key）。
    """

    def __init__(self, cookie: str = "", custom_base_url: str = ""):
        """持有 bridge 引用；API 地址经 base_url 属性动态解析

        Args:
            cookie: Cookie 字符串（uin=xxx; qqmusic_key=xxx; ...）
            custom_base_url: 自定义API服务URL（设置后直接使用，不通过内置bridge）
        """
        self._bridge = bridge.get_bridge()
        self._custom_base_url = custom_base_url.rstrip("/") if custom_base_url else ""
        self.session = requests.Session()
        # 显式关闭代理环境变量读取，避免本机 API 请求走代理
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": _UA})
        if cookie:
            self.set_cookie(cookie)

    @property
    def base_url(self) -> str:
        """API 服务地址（动态解析，每次请求重新取值）

        - 自定义URL优先：设置后直接返回自定义地址
        - 内置服务：Web 停止→重启后端口会变（45602 被占用时回退随机端口），
          缓存旧地址会导致请求失败
        - 服务未运行时经 start() 幂等拉起（与 auto_start=false 的
          "程序启动不拉起、用到再拉"语义一致）
        - _request 自带 3 次重试，拉起期间请求可自然恢复
        """
        if self._custom_base_url:
            return self._custom_base_url
        return self._bridge.start().rstrip("/")

    def set_cookie(self, cookie: str) -> None:
        """设置 Cookie（经 X-QQMusic-Cookie 请求头透传）"""
        self.session.headers["X-QQMusic-Cookie"] = cookie

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    def _request(self, path: str, params: dict | None = None,
                 retries: int = 3, timeout: int = 15) -> dict:
        """调用 QQ音乐 API 接口，返回 response 部分

        - 连接失败：抛 RuntimeError（中文提示，供上层捕获展示）
        - 上游限流（HTTP 429）：等待 1.5s 重试，重试耗尽返回 {}
        - 其他失败：记日志返回 {}
        """
        # url 在循环内每次重新解析：停止→重启后端口漂移时，
        # 正在重试的请求也能经 base_url 属性取到新地址
        for attempt in range(1, retries + 1):
            url = f"{self.base_url}{path}"
            try:
                resp = self.session.get(url, params=params, timeout=timeout)
                if resp.status_code == 429:
                    logger.warning("QQ音乐API限流(429): %s 第 %d/%d 次重试", path, attempt, retries)
                    if attempt < retries:
                        time.sleep(1.5)
                        continue
                    return {}
                resp.raise_for_status()
                data = resp.json()
                # 成功响应统一为 {"response": ...}；错误响应（400/500 结构）无该键
                return data.get("response") or {}
            except requests.exceptions.ConnectionError as e:
                raise RuntimeError(
                    f"无法连接QQ音乐API服务（{self.base_url}），"
                    f"请检查服务是否运行或地址配置是否正确: {e}"
                ) from e
            except (requests.RequestException, ValueError) as e:
                logger.warning("请求 %s 第 %d 次失败: %s", path, attempt, e)
                if attempt < retries:
                    time.sleep(1.5 * attempt)
        logger.error("请求 %s 失败，已重试 %d 次", path, retries)
        return {}

    # ------------------------------------------------------------------
    # 账号信息
    # ------------------------------------------------------------------
    def get_user_info(self) -> dict:
        """获取当前登录账号信息（昵称、会员等级、会员到期时间）

        调 /getUserInfo，登录 Cookie 经 X-QQMusic-Cookie 请求头透传。
        错误响应（400/429/500）结构与业务响应不同，不走 _request 的
        重试逻辑（400 是确定性错误，重试无意义），单独处理。

        Returns:
            {"ok": bool, "nickname": str, "vip_type": int, "vip_expire_ts": int,
             "msg": str}
            - ok=True: 请求成功且 cookie 能识别 uin；nickname 可能为空串
              （cookie 缺 eas_sid 等完整登录字段，仅昵称接口退化）
            - ok=False: msg 携带原因（cookie 无效 / 限流 / 服务异常）
            - vip_type: 0=非会员，1-8=绿钻等级
            - vip_expire_ts: 会员到期秒级时间戳，0=非会员或无到期信息

        Raises:
            RuntimeError: API 服务连接失败
        """
        err = {"ok": False, "nickname": "", "vip_type": 0, "vip_expire_ts": 0, "msg": ""}
        # 限流（429）等待后重试一次；400（cookie 无效）为确定性错误直接返回
        resp = None
        for attempt in (1, 2):
            url = f"{self.base_url}/getUserInfo"
            try:
                resp = self.session.get(url, timeout=10)
            except requests.exceptions.ConnectionError as e:
                raise RuntimeError(
                    f"无法连接QQ音乐API服务（{self.base_url}），"
                    f"请检查服务是否运行或地址配置是否正确: {e}"
                ) from e
            except requests.RequestException as e:
                raise RuntimeError(f"请求QQ音乐API服务失败: {e}") from e
            if resp.status_code == 429 and attempt == 1:
                logger.warning("QQ音乐API限流(429): /getUserInfo 重试一次")
                time.sleep(1.5)
                continue
            break

        if resp is None:
            err["msg"] = "QQ音乐API请求失败"
            return err
        if resp.status_code == 429:
            err["msg"] = "QQ音乐API限流（429），请稍后重试"
            return err
        if resp.status_code == 400:
            # cookie 缺失或无法识别 uin
            msg = ""
            try:
                msg = ((resp.json().get("data") or {}).get("message")) or ""
            except ValueError:
                pass
            err["msg"] = msg or "Cookie 无效（账号未登录或登录态失效）"
            return err
        if resp.status_code != 200:
            err["msg"] = f"QQ音乐API返回异常状态码 {resp.status_code}"
            return err
        try:
            data = resp.json()
        except ValueError:
            err["msg"] = "QQ音乐API返回非 JSON 数据"
            return err

        result = data.get("response") or {}
        nickname = str(result.get("nickname") or "")
        is_vip = bool(result.get("isVip"))
        try:
            vip_level = int(result.get("vipLevel") or 0)
        except (TypeError, ValueError):
            vip_level = 0
        # 非会员强制等级 0（防御 isVip=False 但 vipLevel>0 的脏数据）
        if not is_vip:
            vip_level = 0
        try:
            expire_ts = int(result.get("vipExpireTime") or 0)
        except (TypeError, ValueError):
            expire_ts = 0
        # 非会员/到期时间戳为 0 表示无到期信息
        if vip_level <= 0 or expire_ts <= 0:
            expire_ts = 0

        info = {
            "ok": True,
            "nickname": nickname,
            "vip_type": vip_level,
            "vip_expire_ts": expire_ts,
            "msg": "",
        }
        if not nickname:
            # 昵称接口依赖完整网页登录态（eas_sid），缺字段时退化返回空串
            info["msg"] = "昵称未获取到（Cookie 可能不完整），会员信息已更新"
        return info

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def search_songs(self, keyword: str, limit: int = 50, offset: int = 0) -> dict:
        """搜索单曲（type=song）

        Returns:
            {"items":[{"id"(songmid),"name","artists","album","fee"}], "total": N}
            fee: 0=免费 1=VIP（由上游 pay.pay_play 映射）
        """
        if not keyword:
            return {"items": [], "total": 0}
        page = offset // limit + 1 if limit > 0 else 1
        result = self._request("/getSearchByKey", params={
            "key": keyword, "type": "song",
            "limit": min(limit, 100), "page": page,
        }, timeout=10)
        songs = ((result.get("body") or {}).get("song") or {}).get("list") or []
        total = (result.get("meta") or {}).get("sum") or len(songs)
        out = []
        for s in songs:
            out.append({
                "id": s.get("mid"),
                # title 含高亮标记，优先取 name
                "name": s.get("name") or s.get("title") or "",
                "artists": "/".join(ar.get("name", "") for ar in (s.get("singer") or [])),
                "album": (s.get("album") or {}).get("name", ""),
                "fee": 1 if (s.get("pay") or {}).get("pay_play") == 1 else 0,
            })
        return {"items": out, "total": total}

    def search_albums(self, keyword: str, limit: int = 50, offset: int = 0) -> dict:
        """搜索专辑（type=album）

        Returns:
            {"items":[{"id"(albumMID),"name","artist","size","publish_time"}], "total": N}
        """
        if not keyword:
            return {"items": [], "total": 0}
        page = offset // limit + 1 if limit > 0 else 1
        result = self._request("/getSearchByKey", params={
            "key": keyword, "type": "album",
            "limit": min(limit, 100), "page": page,
        }, timeout=10)
        albums = ((result.get("body") or {}).get("album") or {}).get("list") or []
        total = (result.get("meta") or {}).get("sum") or len(albums)
        out = []
        for a in albums:
            # 上游字段：albumMID/albumName/singerName/publicTime/song_count
            # （实测 2026-08-23，另有 singer_list 数组可作回退）
            artist = a.get("singerName") or "/".join(
                ar.get("name", "") for ar in (a.get("singer_list") or [])
            )
            try:
                size = int(a.get("song_count") or a.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            out.append({
                "id": a.get("albumMID") or a.get("mid"),
                "name": a.get("albumName") or a.get("name") or "",
                "artist": artist or "",
                "size": size,
                "publish_time": a.get("publicTime") or a.get("publishDate") or "",
            })
        return {"items": out, "total": total}

    def get_album_songs(self, albummid: str) -> list[dict]:
        """获取专辑内全部歌曲

        Returns:
            [{"id"(songmid),"name","artists","fee"}]（专辑详情无付费信息，fee=0）
        """
        result = self._request("/getAlbumInfo", params={"albummid": albummid})
        songs = (result.get("data") or {}).get("list") or []
        out = []
        for s in songs:
            out.append({
                "id": s.get("songmid"),
                "name": s.get("songname") or s.get("name") or "",
                "artists": "/".join(ar.get("name", "") for ar in (s.get("singer") or [])),
                "fee": 0,
            })
        return out

    # ------------------------------------------------------------------
    # 播放/下载地址
    # ------------------------------------------------------------------
    def get_song_urls(self, songmids: list[str], level: str = "exhigh") -> list[dict]:
        """批量获取歌曲下载链接（多首时 songmid 逗号分隔批量请求）

        匿名场景 QQ 仅能获取 128kbps 及以下音质（320/flac 需登录 cookie），
        故目标音质拿不到 url 时自动降级 128 重试一次（VIP 歌曲两档都拿不到，
        仍返回 None 交给上层走失败/切号逻辑）。

        Returns:
            UrlInfo 列表，与 songmids 顺序对齐：
            [{"url": str|None, "ext": str, "size": None, "is_trial": False}]
        """
        quality = QUALITY_LEVEL.get(level, "320")
        play_url = self._fetch_play_url(songmids, quality)

        # 目标音质拿不到 url 的歌，降级 128 再试（免费歌匿名可拿 128）
        # downgraded 记录实际靠 128 降级拿到 url 的歌，用于输出循环修正扩展名
        downgraded: set[str] = set()
        if quality != "128":
            missing = [m for m in songmids if not (play_url.get(str(m)) or {}).get("url")]
            if missing:
                fallback = self._fetch_play_url(missing, "128")
                downgraded = {m for m in missing if (fallback.get(str(m)) or {}).get("url")}
                for mid in missing:
                    if (fallback.get(str(mid)) or {}).get("url"):
                        play_url[str(mid)] = fallback[str(mid)]

        ext = QUALITY_EXT.get(quality, "mp3")
        out = []
        for mid in songmids:
            item = play_url.get(str(mid)) or {}
            url = item.get("url") or None
            # 降级拿到 128 时扩展名同步降为 mp3
            item_ext = "mp3" if str(mid) in downgraded else ext
            out.append({
                "url": url,
                "ext": item_ext,
                "size": None,
                "is_trial": False,
            })
        return out

    def _fetch_play_url(self, songmids: list[str], quality: str) -> dict:
        """调 /getMusicPlay 批量取播放链接，返回 {mid: {url, error}}"""
        if not songmids:
            return {}
        ids_str = ",".join(str(m) for m in songmids)
        result = self._request(f"/getMusicPlay/{ids_str}", params={"quality": quality})
        return result.get("playUrl") or {}

    def get_song_detail(self, songmids: list[str]) -> list[dict]:
        """获取歌曲详情——调 /getSongInfo 批量取元数据，映射为 SongMeta 列表

        artist 取上游主歌手（track_info.singer[0].name）；取不到时为空串，
        触发 task_manager 用任务记录的 artists 回退决定下载子目录。
        """
        if not songmids:
            return []
        ids_str = ",".join(str(m) for m in songmids)
        result = self._request("/getSongInfo", params={"songmid": ids_str}, timeout=10)
        mapping = result.get("songinfo") or {}
        out = []
        for mid in songmids:
            item = mapping.get(str(mid)) or {}
            out.append({
                "title": item.get("title") or "",
                "artist": item.get("artist") or "",
                "album": item.get("album") or "",
                "year": item.get("year") or "",
                "cover_url": _fix_img_url(item.get("cover_url") or ""),
                "duration_ms": item.get("duration_ms") or 0,
            })
        return out

    def get_lyric(self, song_id: str) -> dict:
        """获取歌词——QQ API 无歌词接口，返回空"""
        return {"lrc": "", "tlyric": ""}

    # ------------------------------------------------------------------
    # 发现接口（排行榜 / 热门歌单 / 分类）
    # ------------------------------------------------------------------
    def get_toplists(self) -> list[dict]:
        """获取所有排行榜列表

        Returns:
            [{"id"(topId),"name","description","","update_frequency","",
              "cover_img_url","track_count":0}, ...]
            track_count 固定 0：上游 songList 仅为前 3 首预览，不能作曲目数；
            真实曲目数在 get_playlist_detail 走 /getRanks 时以 totalNum 回填
        """
        result = self._request("/getTopLists")
        top_list = (result.get("data") or {}).get("topList") or []
        return [
            {
                "id": t.get("id"),
                "name": t.get("topTitle") or t.get("title") or "",
                "description": "",
                "update_frequency": "",
                "cover_img_url": _fix_img_url(t.get("picUrl") or ""),
                "track_count": 0,
            }
            for t in top_list
        ]

    def get_hot_playlists(self, cat: str = "全部", limit: int = 30,
                          order: str = "hot", offset: int = 0) -> tuple[list[dict], int]:
        """获取热门/分类歌单

        Args:
            cat: 分类名（经模块级分类缓存解析为 categoryId，解析失败用"全部"）
            limit: 每页数量
            order: 排序（QQ 服务端固定 sortId=5 热门，参数保留对齐签名）
            offset: 偏移量 = page * limit（QQ page 从 0 开始）

        Returns:
            (playlists, total)：歌单列表与该分类歌单总数（上游 sum 字段）
        """
        page = offset // limit if limit > 0 else 0
        category_id = self._resolve_category_id(cat)
        result = self._request("/getSongLists", params={
            "page": page, "limit": limit, "categoryId": category_id, "sortId": 5,
        })
        data = result.get("data") or {}
        lst = data.get("list") or []
        # 上游 sum 为该分类歌单总数（实测全部分类 11617），缺失时用当页数量
        total = data.get("sum") or len(lst)
        playlists = []
        for item in lst:
            try:
                pid = int(item.get("disstid"))
            except (TypeError, ValueError):
                continue
            creator = item.get("creator") or {}
            playlists.append({
                # id 转 int：与 netease 数字 id 行为一致（前端数字比较逻辑兼容）
                "id": pid,
                "name": item.get("disstname") or item.get("title") or "",
                "cover_img_url": _fix_img_url(item.get("imgurl") or item.get("cover") or ""),
                "play_count": item.get("listen_num") or item.get("listennum") or 0,
                "track_count": 0,
                "creator": creator.get("name") or creator.get("nick") or item.get("username") or "",
                "description": item.get("introduction") or "",
            })
        return playlists, total

    def get_playlist_categories(self) -> list[dict]:
        """获取所有歌单分类

        Returns:
            [{"name": 分类名}, ...]；"全部"固定在首位；解析失败回退 [{"name": "全部"}]
        """
        mapping = self._load_categories()
        names = list(mapping.keys()) if mapping else []
        if "全部" in names:
            names.remove("全部")
        names.insert(0, "全部")
        return [{"name": n} for n in names]

    # ------------------------------------------------------------------
    # 歌单/榜单详情（分流）
    # ------------------------------------------------------------------
    def get_playlist_detail(self, playlist_id: int, limit: int = 200) -> dict:
        """获取歌单/榜单详情，包含歌曲列表

        QQ 榜单（topId，量级小）与歌单（disstid，10 位数字）ID 量级差异明显，
        据此分流：pid < 10000 走 /getRanks（失败回退歌单接口），否则走
        /getSongListDetail。

        Returns:
            {"name","track_count","tracks":[{"id"(songmid),"name","artists","fee"}]}
        """
        pid = int(playlist_id)
        if pid < 10000:
            detail = self._toplist_detail(pid, limit)
            if detail:
                return detail
        return self._songlist_detail(pid, limit)

    def _toplist_detail(self, top_id: int, limit: int) -> dict:
        """榜单详情（/getRanks，最新一期）"""
        result = self._request("/getRanks", params={"topId": top_id, "limit": 100, "page": 0})
        data = result.get("data") or {}
        songs = result.get("songInfoList") or []
        if not songs:
            return {}
        tracks = []
        for s in songs[:limit]:
            tracks.append({
                "id": s.get("mid"),
                "name": s.get("name") or "",
                "artists": "/".join(ar.get("name", "") for ar in (s.get("singer") or [])),
                "fee": 0,
            })
        # 榜单元信息：title + totalNum（真实曲目数）
        return {
            "id": top_id,
            "name": data.get("title") or str(top_id),
            "track_count": data.get("totalNum") or len(tracks),
            "tracks": tracks,
        }

    def _songlist_detail(self, disstid: int, limit: int) -> dict:
        """歌单详情（/getSongListDetail，服务端自动分页聚合全部歌曲）"""
        result = self._request("/getSongListDetail", params={"disstid": disstid})
        if not result:
            return {}
        songs = result.get("songs") or []
        tracks = []
        for s in songs[:limit]:
            tracks.append({
                "id": s.get("songmid"),
                "name": s.get("songname") or s.get("name") or "",
                "artists": s.get("singers") or s.get("singer") or "",
                "fee": 0,
            })
        return {
            "id": disstid,
            "name": result.get("disstname") or str(disstid),
            "track_count": result.get("total") or len(tracks),
            "tracks": tracks,
        }

    # ------------------------------------------------------------------
    # 分类映射（模块级缓存）
    # ------------------------------------------------------------------
    def _load_categories(self) -> dict[str, int]:
        """加载"分类名 → categoryId"映射（模块级缓存，按 base_url 分桶）

        调 /getRecommend 的 category 模块解析。上游为两级分组结构（实测
        2026-08-23）：category.category[] 为分组（group_name），每组 items[]
        为具体分类（item_name/item_id，item_id 即 getSongLists 的 categoryId）。
        """
        cache_key = self.base_url
        cached = _CATEGORY_CACHE.get(cache_key)
        if cached is not None:
            return cached
        result = self._request("/getRecommend", params={"limit": 1})
        groups = ((result.get("category") or {}).get("category")) or []
        mapping: dict[str, int] = {}
        for g in groups:
            for item in (g.get("items") or []):
                name = item.get("item_name") or item.get("name") or ""
                cid = item.get("item_id") or item.get("id")
                if not name or cid is None:
                    continue
                try:
                    mapping[name] = int(cid)
                except (TypeError, ValueError):
                    continue
        if mapping:
            _CATEGORY_CACHE[cache_key] = mapping
        return mapping

    def _resolve_category_id(self, cat: str) -> int:
        """分类名 → categoryId；空/"全部"或未命中回退全部分类"""
        if not cat or cat == "全部":
            return _ALL_CATEGORY_ID
        mapping = self._load_categories()
        return mapping.get(cat, _ALL_CATEGORY_ID)
