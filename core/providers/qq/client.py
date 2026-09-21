"""QQ音乐 API 客户端 - 调用内置 qqmusic-api 服务（FastAPI/uvicorn 版）

服务二进制由 bridge 模块管理（自动拉起，监听 127.0.0.1 默认 45602 端口），
API 地址每次请求时经 base_url 属性动态解析；配置外部服务地址时经
use_custom_qq_api_url + qq_api_base_url 设置项注入 custom_base_url 覆盖。

响应统一为 {"code":0,"msg":"ok","data":{...}}；错误为 HTTP 4xx/5xx +
{"code":-1,"msg":"..."}。凭证无效/过期 → 401；限流 → 429；参数错误 → 422。

会员鉴权通过标准 Cookie（musicid + musickey）实现，由存储的网页 Cookie
映射而来（QQ 登录：uin/qqmusic_key；微信登录：wxuin/wx* 系字段），
可解锁 VIP 歌曲与无损音质；匿名可用低音质。

能力限制（QQ API 服务端未提供对应接口）：
- 无分类歌单浏览（旧 /getSongLists /getRecommend 已移除）→ 热门歌单
  降级为官方推荐歌单（单页），分类固定"全部"
- 专辑歌曲列表可经 num 参数全量获取（服务端透传分页参数）

扫码登录：create_qr_login / check_qr_login 走上游 /login/qrcode/{qq|wx}
原生路由（免鉴权、不缓存），成功时 Credential 拼标准 Cookie 串，与
手工录入的网页 Cookie 共用 set_cookie 映射链。
"""

import logging
import re
import time
import urllib.parse
import weakref
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from . import bridge

logger = logging.getLogger(__name__)

# 统一音质等级（网易云语义）-> 服务端 file_type 整型枚举
# （EnumIntMapping 按成员位置索引：13=MP3_128, 12=MP3_320, 8=OGG_640,
#  7=FLAC, 1=MASTER）
# 刻意不映射 m4a/ape 档：输出仅 mp3/flac/ogg，复用现有标签写入能力
# jymaster(1) 进入统一降级链（最高档，失败沿链向低档回退）；
# ogg640(8) 不在 QUALITY_ORDER 内，不进降级链，失败由内部降 128 兜底
QUALITY_LEVEL = {
    "standard": 13,   # 标准 128kbps mp3 (M500)
    "higher": 12,     # 较高 320kbps mp3 (M800)
    "exhigh": 12,     # 极高 320kbps mp3 (M800)
    "lossless": 7,    # 无损 flac (F000)
    "hires": 7,       # Hi-Res（映射 flac）
    "jymaster": 1,    # 臻品母带 flac (MASTER, FLAC 24bit/192kHz)，需豪华绿钻权益
    "ogg640": 8,      # SQ 无损 OGG 640k (OGG_640)，该档不进统一降级链
}

# file_type -> 文件扩展名（必须与 QUALITY_LEVEL 同步加档，
# 漏加会被 get_song_urls 的 QUALITY_EXT.get(quality, "mp3") 静默错标扩展名）
QUALITY_EXT = {
    13: "mp3",
    12: "mp3",
    7: "flac",
    1: "flac",
    8: "ogg",
}

# 实际 file_type -> 统一音质档位名（level 回填用；12 归 exhigh）
_LEVEL_BY_QUALITY = {
    13: "standard",
    12: "exhigh",
    7: "lossless",
    1: "jymaster",
    8: "ogg640",
}

# get_song_urls 结果码（UrlinfoItem.result）
_URL_RESULT_OK = 0          # 成功
# 104003=无权限 104004=VKey 获取失败 104013=播放设备受限（均视为取链失败）

# 搜索类型（SearchType IntEnum）：0=歌曲 2=专辑
_SEARCH_TYPE_SONG = 0
_SEARCH_TYPE_ALBUM = 2

# query_song 批量详情单批上限（避免单请求过大）
_DETAIL_BATCH_SIZE = 50
# 歌单/榜单详情聚合分页大小与页数上限（防止异常大歌单死循环）
_DETAIL_PAGE_SIZE = 100
_DETAIL_MAX_PAGES = 50

# 专辑曲目数探测并发数（search_albums 补齐用；/album/{mid}/songs?num=1
# 单请求实测 ~400ms，50 条/页串行需 ~20s，8 并发约 2.5s）
_SIZE_PROBE_WORKERS = 8

# 专辑歌曲分页大小（num=200 实测上游可用；>200 首合辑靠翻页补全）
_ALBUM_PAGE_SIZE = 200

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


def _album_cover_url(album_mid: str) -> str:
    """由专辑 MID 拼标准封面 URL（歌曲对象不带封面，仅专辑 MID）"""
    if not album_mid:
        return ""
    return f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{album_mid}.jpg"


def _singers_text(singer) -> str:
    """singer 数组 → 'a/b/c' 文本（容忍 name 为 null / 元素非 dict）

    上游可能返回 {"singer": [{"name": null}]}：dict.get(k, default) 只在键缺失时
    给默认值，值为 null 时仍返回 None，直接 join 会抛 TypeError。
    此处统一收敛为 str 并整条剔除空名（'' 混入会拼出 'a//b'）。
    """
    if isinstance(singer, str):
        return singer
    if not isinstance(singer, list):
        return ""
    names = []
    for s in singer:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        if name is None:
            continue
        name = str(name).strip()
        if name:
            names.append(name)
    return "/".join(names)


def _first_singer(singer) -> str:
    """singer 数组 → 首歌手名（专辑歌手用）"""
    if isinstance(singer, list) and singer and isinstance(singer[0], dict):
        return singer[0].get("name", "") or ""
    return ""


def _year_from_date(date_str) -> str:
    """'2003-07-31' → '2003'（无有效日期返回空串）"""
    m = re.match(r"(\d{4})", str(date_str or ""))
    return m.group(1) if m else ""


class QqClient:
    """QQ音乐 API 客户端

    通过内置 qqmusic-api 服务调用 QQ音乐接口（可被 custom_base_url 覆盖）。
    凭证经标准 Cookie（musicid/musickey）下发。
    """

    def __init__(self, cookie: str = "", custom_base_url: str = ""):
        """持有 bridge 引用；API 地址经 base_url 属性动态解析

        Args:
            cookie: Cookie 字符串（uin=xxx; qqmusic_key=xxx; ...）
            custom_base_url: 自定义API服务URL（设置后直接使用，不通过内置bridge）
        """
        self._bridge = bridge.get_bridge()
        self._custom_base_url = custom_base_url.rstrip("/") if custom_base_url else ""
        self._euin = ""  # 网页 Cookie 自带的加密 uin，取昵称用（set_cookie 更新）
        self.session = requests.Session()
        # 显式关闭代理环境变量读取，避免本机 API 请求走代理
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
        - 内置服务：Web 停止→重启后端口会变（45602 被占用时回退随机端口），
          缓存旧地址会导致请求失败
        - 服务未运行时经 start() 幂等拉起（与 auto_start=false 的
          "程序启动不拉起、用到再拉"语义一致）
        - _request 自带重试，拉起期间请求可自然恢复
        """
        if self._custom_base_url:
            return self._custom_base_url
        return self._bridge.start().rstrip("/")

    def set_cookie(self, cookie: str) -> None:
        """设置登录凭证

        服务端从标准 Cookie 读取 musicid/musickey（必须同时提供，musicid
        须可解析为整数）。存储的网页 Cookie 字段做映射：
            QQ 登录：  uin/qqmusic_uin       -> musicid（提取纯数字）
            微信登录： wxuin                 -> musicid（纯数字虚拟 uin，兜底）
            qqmusic_key/qm_keyst             -> musickey
        其余可识别字段（openid/refresh_token/access_token/unionid/
        refresh_key 等）按名透传，利于服务端凭证续期；微信登录 Cookie 用
        wx 前缀字段（wxopenid/wxunionid/wxrefresh_token），标准名缺失时
        映射为标准名透传——W_X_ 凭证续期需要 openid/unionid/refresh_token
        （qqmusic_api login.refresh_credential login_type=1 分支）。
        """
        self.session.cookies.clear()
        pairs = {}
        for part in (cookie or "").split(";"):
            if "=" not in part:
                continue
            k, _, v = part.strip().partition("=")
            if k and v:
                pairs[k.strip()] = v.strip()

        # musicid：QQ 登录取 uin/qqmusic_uin；微信登录（W_X_ key）Cookie 无
        # uin，只有 wxuin，作兜底来源。两者均为纯数字。
        musicid = (
            pairs.get("uin")
            or pairs.get("qqmusic_uin")
            or pairs.get("musicid")
            or pairs.get("wxuin")
            or ""
        )
        # uin 可能带前导字母/符号，提取纯数字；musicid 必须可 int() 解析
        digits = re.sub(r"\D", "", musicid)
        musickey = pairs.get("qqmusic_key") or pairs.get("qm_keyst") or pairs.get("musickey") or ""
        if digits:
            self.session.cookies.set("musicid", digits)
            self.session.cookies.set("str_musicid", digits)
        if musickey:
            self.session.cookies.set("musickey", musickey)
        wx_aliases = {"openid": "wxopenid", "unionid": "wxunionid", "refresh_token": "wxrefresh_token"}
        for name in ("openid", "refresh_token", "access_token", "unionid", "refresh_key", "expired_at"):
            value = pairs.get(name) or pairs.get(wx_aliases.get(name, ""))
            if value:
                self.session.cookies.set(name, value)
        # 加密 uin：QQ/微信登录 Cookie 均自带，经 /user/{euin}/homepage 取昵称
        self._euin = pairs.get("euin") or ""

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    def _request(self, path: str, params: dict | None = None, body: dict | None = None,
                 retries: int = 3, timeout: int = 15, session=None) -> dict:
        """调用 QQ音乐 API 接口，返回 data 部分

        - 连接失败：抛 RuntimeError（中文提示，供上层捕获展示）
        - 上游限流（HTTP 429）：等待 1.5s 重试，重试耗尽返回 {}
        - 凭证无效（401）/ 参数错误（422）：确定性错误不重试，记日志返回 {}
        - 其他失败：记日志返回 {}
        - session：可选独立 Session（默认主 Session）。并发探测
          （_probe_album_size）传入独立 Session，避免多线程改写
          self.session 的 headers/CookieJar 叠加成串号状态
        """
        sess = session or self.session
        method = sess.post if body is not None else sess.get
        kwargs = {"timeout": timeout}
        if params:
            kwargs["params"] = params
        if body is not None:
            kwargs["json"] = body
        # url 在循环内每次重新解析：停止→重启后端口漂移时，
        # 正在重试的请求也能经 base_url 属性取到新地址
        for attempt in range(1, retries + 1):
            url = f"{self.base_url}{path}"
            try:
                resp = method(url, **kwargs)
                if resp.status_code == 429:
                    logger.warning("QQ音乐API限流(429): %s 第 %d/%d 次重试", path, attempt, retries)
                    if attempt < retries:
                        time.sleep(1.5)
                        continue
                    return {}
                if resp.status_code in (401, 422):
                    # 401=凭证无效/过期；422=参数校验失败——确定性错误，重试无意义
                    try:
                        msg = (resp.json() or {}).get("msg") or ""
                    except ValueError:
                        msg = ""
                    logger.warning("QQ音乐API请求失败(%d): %s %s", resp.status_code, path, msg)
                    return {}
                resp.raise_for_status()
                data = resp.json()
                # 成功响应统一为 {"code":0,"msg","data"}；HTTP 200 但 code!=0 亦视为失败
                if not isinstance(data, dict) or data.get("code") != 0:
                    logger.warning("QQ音乐API业务失败: %s %s", path,
                                   (data or {}).get("msg") if isinstance(data, dict) else data)
                    return {}
                # 返回点单点归一为 dict：上游 data 为数组时原样返回会违反
                # -> dict 签名，调用点 result.get(...) 直接抛 AttributeError
                payload = data.get("data")
                return payload if isinstance(payload, dict) else {}
            except requests.exceptions.ConnectionError as e:
                # 与 kugou/client.py 对齐：瞬时断连先按既有退避重试，
                # 耗尽后才抛 RuntimeError，避免网络抖动被误判为鉴权失败而换号
                logger.warning("连接QQ音乐API失败 %s 第 %d/%d 次: %s",
                               path, attempt, retries, e)
                if attempt < retries:
                    time.sleep(1.5)
                    continue
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
        """获取当前登录账号信息（会员等级、会员到期时间、登录态有效性）

        调 /user/get_vip_info，凭证经标准 Cookie 下发。凭证无效
        （HTTP 401）时单独处理，不走 _request 重试逻辑。

        注意：无效凭证调 /user/get_vip_info 返回 200 全 0 数据（服务端
        不抛 401），无法与"真非会员"区分；故 vip_level==0 时再调
        /user/get_friend（登录态敏感接口）复判——401 即登录态失效。

        Returns:
            {"ok": bool, "nickname": str, "vip_type": int, "vip_expire_ts": int,
             "msg": str}
            - ok=True: 请求成功且登录态有效；nickname 尽力而为获取
              （/user/{euin}/homepage），失败返回空串（账号页保留存量昵称）
            - ok=False: msg 携带原因（未提供凭证 / cookie 无效 / 限流 /
              服务异常）
            - vip_type: 0=非会员，>0=会员（映射 identity.level 或
              svip/star/ystar 标志）
            - vip_expire_ts: 会员到期秒级时间戳，0=非会员或无到期信息
              （userinfo.expire 优先，缺失时解析 identity.*_end 兜底，
              自动兼容秒/毫秒）

        Raises:
            RuntimeError: API 服务连接失败
        """
        err = {"ok": False, "nickname": "", "vip_type": 0, "vip_expire_ts": 0, "msg": ""}
        # 限流（429）等待后重试一次；401（无凭证）为确定性错误直接返回
        resp = None
        for attempt in (1, 2):
            url = f"{self.base_url}/user/get_vip_info"
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
                logger.warning("QQ音乐API限流(429): /user/get_vip_info 重试一次")
                time.sleep(1.5)
                continue
            break

        if resp is None:
            err["msg"] = "QQ音乐API请求失败"
            return err
        if resp.status_code == 429:
            err["msg"] = "QQ音乐API限流（429），请稍后重试"
            return err
        if resp.status_code == 401:
            err["msg"] = "未提供登录凭证（Cookie 缺失或不完整）"
            return err
        if resp.status_code != 200:
            err["msg"] = f"QQ音乐API返回异常状态码 {resp.status_code}"
            return err
        try:
            data = resp.json()
        except ValueError:
            err["msg"] = "QQ音乐API返回非 JSON 数据"
            return err
        if not isinstance(data, dict) or data.get("code") != 0:
            err["msg"] = (data or {}).get("msg") if isinstance(data, dict) else "QQ音乐API业务失败"
            return err

        result = data.get("data") or {}
        # 会员等级：identity.level 优先（实际绿钻等级），缺失时按标志位映射
        identity = result.get("identity") or {}
        try:
            vip_level = int(identity.get("level") or 0)
        except (TypeError, ValueError):
            vip_level = 0
        if vip_level <= 0:
            has_flag = any(
                _safe_int(result.get(k)) > 0 for k in ("svip", "star", "ystar", "huge_vip")
            )
            vip_level = 1 if has_flag else 0
        # 到期时间：userinfo.expire（秒/毫秒自适应）优先；缺失时解析
        # identity.*_end（北京时间字符串）兜底，取各档会员最晚到期
        userinfo = result.get("userinfo") or {}
        expire_ts = _safe_int(userinfo.get("expire"))
        if expire_ts > 10**12:      # 毫秒
            expire_ts //= 1000
        elif expire_ts <= 10**9:    # 无效/过早时间戳视为无到期信息
            expire_ts = 0
        if expire_ts <= 0:
            expire_ts = _expire_from_identity(identity)
        if vip_level <= 0:
            expire_ts = 0

        if vip_level <= 0:
            # vip_info 对无效凭证也返回 200 全 0，用登录态敏感接口复判
            probe = self.session.get(f"{self.base_url}/user/get_friend",
                                     params={"page": 1, "num": 1}, timeout=10)
            if probe.status_code == 401:
                err["msg"] = "Cookie 无效（账号未登录或登录态失效）"
                return err
            if probe.status_code == 429:
                err["msg"] = "QQ音乐API限流（429），请稍后重试"
                return err

        return {
            "ok": True,
            "nickname": self._fetch_nickname(),
            "vip_type": vip_level,
            "vip_expire_ts": expire_ts,
            "msg": "",
        }

    def _fetch_nickname(self) -> str:
        """尽力而为获取账号昵称（/user/{euin}/homepage）

        euin 取自录入的网页 Cookie（QQ/微信登录均有）；euin 缺失、请求
        失败或解析失败一律返回空串，不影响登录判定——账号侧对空昵称
        保留存量值不覆盖。
        """
        if not self._euin:
            return ""
        url = f"{self.base_url}/user/{urllib.parse.quote(self._euin, safe='')}/homepage"
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code != 200:
                logger.debug("获取QQ昵称失败(euin=%s): HTTP %d", self._euin, resp.status_code)
                return ""
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.debug("获取QQ昵称失败(euin=%s): %s", self._euin, e)
            return ""
        if not isinstance(data, dict) or data.get("code") != 0:
            return ""
        base_info = (data.get("data") or {}).get("base_info") or {}
        return str(base_info.get("name") or "")

    # ------------------------------------------------------------------
    # 扫码登录（QQ / 微信）
    # ------------------------------------------------------------------
    # 支持的扫码登录类型（上游 WebQRLoginType：qq=手机QQ扫码，wx=微信扫码）
    QR_LOGIN_TYPES = ("qq", "wx")

    def create_qr_login(self, login_type: str = "qq") -> dict:
        """生成扫码登录二维码（GET /login/qrcode/{login_type}）

        上游登录路由不缓存、免鉴权（AuthPolicy.NONE），无需携带 Cookie。

        Args:
            login_type: "qq"=手机QQ扫码，"wx"=微信扫码

        Returns:
            {"ok": bool, "login_type": str, "identifier": str, "qr_img": str,
             "msg": str}
            identifier 用于轮询（check_qr_login）；qr_img 为可直接用于
            <img src> 的 DataURL（取上游 img 字段，缺失时由 data+mimetype 拼）
        """
        login_type = (login_type or "").strip().lower()
        if login_type not in self.QR_LOGIN_TYPES:
            return {"ok": False, "login_type": login_type, "identifier": "",
                    "qr_img": "",
                    "msg": f"login_type 仅支持 {'/'.join(self.QR_LOGIN_TYPES)}"}
        result = self._request(f"/login/qrcode/{login_type}", timeout=30)
        identifier = str((result or {}).get("identifier") or "")
        img = str((result or {}).get("img") or "")
        if not img:
            # 兜底：img 缺失时由 base64 data + mimetype 拼 DataURL
            data = str((result or {}).get("data") or "")
            mime = str((result or {}).get("mimetype") or "image/png")
            if data:
                img = f"data:{mime};base64,{data}"
        if not identifier or not img:
            return {"ok": False, "login_type": login_type, "identifier": identifier,
                    "qr_img": "", "msg": "二维码生成失败（上游未返回图像或标识符）"}
        return {"ok": True, "login_type": login_type, "identifier": identifier,
                "qr_img": img, "msg": ""}

    def check_qr_login(self, login_type: str, identifier: str) -> dict:
        """轮询扫码登录状态（GET /login/qrcode/{login_type}/status）

        上游 event 码：0=DONE 1=SCAN(等待扫码) 2=CONF(已扫码待确认)
        3=TIMEOUT(二维码超时) 4=REFUSE(用户拒绝) -1=其他错误。
        本方法映射为酷狗扫码语义 status（api 层与 accounts.js 复用同一套
        前端状态机）：1→1 等待 2→2 已扫 0→4 成功 3→0 过期 4→3 拒绝。

        成功时 credential 含 musicid/musickey/openid/unionid/refresh_token/
        refresh_key/access_token/expired_at 等（QQ 登录无 openid/unionid，
        微信登录 musickey 为 W_X_ 前缀），经 _credential_to_cookie 拼为
        标准 Cookie 串（set_cookie 原生可解析，落库格式与手工录入一致）。

        Returns:
            {"ok": bool, "status": int, "cookie": str, "msg": str}
            status=4 时 cookie 非空，可直接填入账号 Cookie 字段
        """
        empty = {"ok": False, "status": -1, "cookie": "", "msg": ""}
        login_type = (login_type or "").strip().lower()
        if login_type not in self.QR_LOGIN_TYPES:
            empty["msg"] = f"login_type 仅支持 {'/'.join(self.QR_LOGIN_TYPES)}"
            return empty
        identifier = str(identifier or "").strip()
        if not identifier:
            empty["msg"] = "缺少二维码 identifier"
            return empty
        result = self._request(f"/login/qrcode/{login_type}/status",
                               params={"identifier": identifier}, timeout=30)
        if not result:
            empty["msg"] = "扫码状态查询失败"
            return empty
        # 未知/缺失 event 码按 -1（其他错误）处理
        try:
            event = int(result.get("event"))
        except (TypeError, ValueError):
            event = -1
        # 上游 event -> 酷狗语义 status
        status = {0: 4, 1: 1, 2: 2, 3: 0, 4: 3}.get(event, -1)
        if status < 0:
            empty["msg"] = f"未知扫码状态码（event={event}）"
            return empty
        if status != 4:
            return {"ok": True, "status": status, "cookie": "", "msg": ""}
        credential = result.get("credential")
        if not isinstance(credential, dict) or not credential:
            return {"ok": False, "status": 4, "cookie": "",
                    "msg": "授权成功但响应未包含凭证，请重试或改用手动填入"}
        cookie = _credential_to_cookie(credential)
        if not cookie:
            return {"ok": False, "status": 4, "cookie": "",
                    "msg": "授权成功但凭证缺少 musicid/musickey，请重试"}
        return {"ok": True, "status": 4, "cookie": cookie, "msg": ""}

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def search_songs(self, keyword: str, limit: int = 50, offset: int = 0) -> dict:
        """搜索单曲（search_type=0）

        Returns:
            {"items":[{"id"(songmid),"name","artists","album","fee"}], "total": N}
            fee: 0=免费 1=VIP（由上游 pay.pay_play 映射）
        """
        if not keyword:
            return {"items": [], "total": 0}
        page = offset // limit + 1 if limit > 0 else 1
        result = self._request("/search/search_by_type", params={
            "keyword": keyword, "search_type": _SEARCH_TYPE_SONG,
            "num": min(limit, 100), "page": page,
        }, timeout=10)
        songs = result.get("song") or []
        total = result.get("total_num") or result.get("estimate_sum") or len(songs)
        out = []
        for s in songs:
            if not isinstance(s, dict):
                continue
            out.append({
                "id": s.get("mid"),
                # title 含 <em> 高亮标记，优先取 name
                "name": s.get("name") or s.get("title") or "",
                "artists": _singers_text(s.get("singer")),
                "album": ((s.get("album") or {}).get("name") or "")
                if isinstance(s.get("album"), dict) else "",
                "fee": 1 if ((s.get("pay") or {}).get("pay_play")) == 1 else 0,
            })
        return {"items": out, "total": total}

    def search_albums(self, keyword: str, limit: int = 50, offset: int = 0) -> dict:
        """搜索专辑（search_type=2）

        Returns:
            {"items":[{"id"(albumMID),"name","artist","size","publish_time"}], "total": N}
        """
        if not keyword:
            return {"items": [], "total": 0}
        page = offset // limit + 1 if limit > 0 else 1
        # highlight=False 关闭关键词高亮，避免歌手/专辑名携带 <em> 标记
        result = self._request("/search/search_by_type", params={
            "keyword": keyword, "search_type": _SEARCH_TYPE_ALBUM,
            "num": min(limit, 100), "page": page, "highlight": False,
        }, timeout=10)
        albums = result.get("album") or []
        total = result.get("total_num") or result.get("estimate_sum") or len(albums)
        out = []
        for a in albums:
            if not isinstance(a, dict):
                continue
            artist = _singers_text(a.get("singer")) or _singers_text(a.get("singer_list"))
            out.append({
                "id": a.get("mid"),
                "name": a.get("name") or a.get("title") or "",
                "artist": artist or "",
                "size": 0,
                "publish_time": a.get("time_public") or "",
            })
        # 补齐曲目数：搜索响应不含曲目数（旧服务端 song_count 字段已随服务端
        # 切换消失），按专辑并发探测 /album/{mid}/songs 的 total_num；失败
        # 保持 0（前端显示 "—"），不阻断搜索
        mids = [a.get("mid") for a in albums if isinstance(a, dict) and a.get("mid")]
        if mids:
            with ThreadPoolExecutor(max_workers=_SIZE_PROBE_WORKERS) as pool:
                sizes = dict(zip(mids, pool.map(self._probe_album_size, mids)))
            for item in out:
                item["size"] = sizes.get(item.get("id"), 0)
        return {"items": out, "total": total}

    def _probe_album_size(self, albummid: str) -> int:
        """探测专辑真实曲目数（/album/{mid}/songs?num=1，读 total_num）

        最小请求（只取 1 首即可读到总数）；尽力而为语义：请求失败、
        限流（429 重试耗尽）或字段缺失返回 0，不影响搜索结果返回。
        用独立 Session 隔离并发：probe 不需要 cookie，不复用主 Session，
        避免 8 线程并发改写 self.session 的 headers/CookieJar 状态。
        """
        with requests.Session() as s:
            # 与主 Session 对齐：固定 UA；显式关闭代理环境变量读取
            # （目标为 127.0.0.1 的本机 API，走代理会直接失败）
            s.trust_env = False
            s.headers.update({"User-Agent": _UA})
            result = self._request(f"/album/{albummid}/songs",
                                   params={"num": 1, "page": 1},
                                   timeout=10, session=s)
        return _safe_int(result.get("total_num")) if result else 0

    def get_album_songs(self, albummid: str) -> list[dict]:
        """获取专辑内全部歌曲（按 _ALBUM_PAGE_SIZE/页分页聚合取全量）

        合辑可能超过单页上限，循环翻页至取满 total_num 或页空
        （page 参数经服务端透传，实测 page=2 生效；_DETAIL_MAX_PAGES
        封顶 1 万首防死循环）。分页中途失败导致取不全时整体视为
        失败返回 []（fail-loud，防止静默下载半张专辑），上层
        task_manager 对空列表返回 enqueued=0。

        Returns:
            [{"id"(songmid),"name","artists","fee"}]（fee 由 pay.pay_play 映射）
        """
        tracks = []
        total = 0
        for page in range(1, _DETAIL_MAX_PAGES + 1):
            result = self._request(f"/album/{albummid}/songs",
                                   params={"num": _ALBUM_PAGE_SIZE, "page": page})
            if not result:
                break
            total = _safe_int(result.get("total_num")) or total
            songs = result.get("song_list") or []
            if not songs:
                break
            for s in songs:
                tracks.append({
                    "id": s.get("mid"),
                    "name": s.get("name") or s.get("title") or "",
                    "artists": _singers_text(s.get("singer")),
                    "fee": 1 if ((s.get("pay") or {}).get("pay_play")) == 1 else 0,
                })
            if total and len(tracks) >= total:
                break
        if total and len(tracks) < total:
            return []
        return tracks

    # ------------------------------------------------------------------
    # 播放/下载地址
    # ------------------------------------------------------------------
    def get_song_urls(self, songmids: list[str], level: str = "exhigh") -> list[dict]:
        """批量获取歌曲下载链接（POST /song/get_song_urls，多首一批）

        返回的 purl 为相对路径，需经 /song/get_cdn_dispatch 取 CDN 域名拼接。
        匿名场景 QQ 仅能获取 128kbps 及以下音质（320/flac 需登录 cookie），
        故目标音质拿不到 url 时自动降级 128 重试一次（VIP 歌曲两档都拿不到，
        仍返回 None 交给上层走失败/切号逻辑）。

        Returns:
            UrlInfo 列表，与 songmids 顺序对齐：
            [{"url": str|None, "ext": str, "size": None, "is_trial": False,
              "level": str}]   # level 为实际生效档（内部降级 128 时为 standard）
        """
        quality = QUALITY_LEVEL.get(level, 12)
        play_url = self._fetch_play_url(songmids, quality)

        # 目标音质拿不到 url 的歌，降级 128 再试（免费歌匿名可拿 128）
        # downgraded 记录实际靠 128 降级拿到 url 的歌，用于输出循环修正扩展名
        downgraded: set[str] = set()
        if quality != 13:
            missing = [m for m in songmids if not (play_url.get(str(m)) or {}).get("url")]
            if missing:
                fallback = self._fetch_play_url(missing, 13)
                downgraded = {m for m in missing if (fallback.get(str(m)) or {}).get("url")}
                for mid in missing:
                    if (fallback.get(str(mid)) or {}).get("url"):
                        play_url[str(mid)] = fallback[str(mid)]

        ext = QUALITY_EXT.get(quality, "mp3")
        # 实际生效档回填统一档位名（12 由 higher/exhigh 两档共用，归 exhigh）
        actual_level = "standard" if quality == 13 else _LEVEL_BY_QUALITY.get(quality, level)
        out = []
        for mid in songmids:
            item = play_url.get(str(mid)) or {}
            url = item.get("url") or None
            # 降级拿到 128 时扩展名同步降为 mp3
            item_ext = "mp3" if str(mid) in downgraded else ext
            item_level = "standard" if str(mid) in downgraded else actual_level
            out.append({
                "url": url,
                "ext": item_ext,
                "size": None,
                "is_trial": False,
                "level": item_level,
            })
        return out

    def _get_cdn_base(self) -> str:
        """取 CDN 域名（/song/get_cdn_dispatch，公开接口服务端缓存 60s）

        固定取 sip[0]，避免同任务内随机选域导致 URL 域名不一致。
        """
        result = self._request("/song/get_cdn_dispatch", timeout=10)
        sip = result.get("sip") or []
        return sip[0] if sip else "http://aqqmusic.tc.qq.com/"

    def _fetch_play_url(self, songmids: list[str], quality: int) -> dict:
        """批量取播放链接，返回 {mid: {url, error}}

        result=0 且 purl 非空视为成功；其余（104003 无权限/104004 vkey
        失败/104013 设备受限/purl 空）视为取链失败，url=None。
        """
        if not songmids:
            return {}
        result = self._request("/song/get_song_urls", body={
            "file_info": [{"mid": str(m)} for m in songmids],
            "file_type": quality,
        })
        items = result.get("data") or []
        if not items:
            return {}
        cdn = self._get_cdn_base()
        out = {}
        for it in items:
            mid = str(it.get("mid") or "")
            purl = it.get("purl") or ""
            ok = _safe_int(it.get("result")) == _URL_RESULT_OK and purl
            out[mid] = {"url": (cdn + purl) if ok else None}
        return out

    def get_song_detail(self, songmids: list[str]) -> list[dict]:
        """获取歌曲详情——调 POST /song/query_song 分批取元数据

        artist 取上游主歌手（singer[0].name）；封面由专辑 MID 拼标准 URL；
        音轨号/碟号取 index_album/index_cd，专辑歌手取首歌手；
        取不到时为空串/0，触发 task_manager 用任务记录的 artists 回退。
        """
        if not songmids:
            return []
        mapping: dict[str, dict] = {}
        for i in range(0, len(songmids), _DETAIL_BATCH_SIZE):
            batch = [str(m) for m in songmids[i:i + _DETAIL_BATCH_SIZE]]
            result = self._request("/song/query_song", body={
                "query_info": [{"mid": m} for m in batch],
            }, timeout=10)
            for t in (result.get("tracks") or []):
                if not isinstance(t, dict):
                    continue
                mid = str(t.get("mid") or "")
                if mid:
                    mapping[mid] = t
        out = []
        for mid in songmids:
            item = mapping.get(str(mid)) or {}
            album = item.get("album") or {}
            out.append({
                "title": item.get("name") or item.get("title") or "",
                "artist": _singers_text(item.get("singer")),
                "album": (album.get("name") or "") if isinstance(album, dict) else "",
                "year": _year_from_date(item.get("time_public")),
                "cover_url": _album_cover_url(album.get("mid") or "") if isinstance(album, dict) else "",
                "duration_ms": (_safe_int(item.get("interval")) or 0) * 1000,
                "track_no": _safe_int(item.get("index_album")),
                "disc_no": _safe_int(item.get("index_cd")),
                "albumartist": _first_singer(item.get("singer")),
            })
        return out

    def get_lyric(self, song_id: str) -> dict:
        """获取歌词——调 /song/{mid}/lyric（value 兼容 mid/数字 ID）

        返回 {"lrc": 原文, "tlyric": 翻译}；失败返回空（不阻断下载）。
        """
        result = self._request(f"/song/{song_id}/lyric", params={"trans": "true"}, timeout=10)
        if not result:
            return {"lrc": "", "tlyric": ""}
        return {
            "lrc": result.get("lyric") or "",
            "tlyric": result.get("trans") or "",
        }

    # ------------------------------------------------------------------
    # 发现接口（排行榜 / 推荐歌单）
    # ------------------------------------------------------------------
    def get_toplists(self) -> list[dict]:
        """获取所有排行榜列表

        Returns:
            [{"id"(topId),"name","description","update_frequency",
              "cover_img_url","track_count":0}, ...]
            track_count 固定 0：上游 songList 仅为前 3 首预览，不能作曲目数；
            真实曲目数在 get_playlist_detail 走 /top/{id}/detail 时以
            total_num 回填
        """
        result = self._request("/top/get_category")
        groups = result.get("group") or []
        out = []
        for g in groups:
            for t in (g.get("toplist") or []):
                out.append({
                    "id": t.get("id"),
                    "name": t.get("name") or t.get("title_detail") or "",
                    "description": t.get("intro") or "",
                    "update_frequency": t.get("period") or t.get("update_time") or "",
                    "cover_img_url": _fix_img_url(t.get("head_pic_url") or t.get("front_pic_url") or ""),
                    "track_count": 0,
                })
        return out

    def get_hot_playlists(self, cat: str = "全部", limit: int = 30,
                          order: str = "hot", offset: int = 0) -> tuple[list[dict], int]:
        """获取热门歌单（降级：官方推荐歌单，无分类/分页能力）

        新服务端移除了分类歌单浏览接口（旧 /getSongLists），此方法降级为
        /recommend/get_recommend_songlist 单页结果；cat/order 参数保留
        对齐签名但被忽略。翻页（offset>0）返回空页。

        Returns:
            (playlists, total)：歌单列表与总数（单页结果条数）
        """
        if offset > 0:
            return [], 0
        result = self._request("/recommend/get_recommend_songlist")
        lst = result.get("songlists") or []
        playlists = []
        for item in lst[:limit]:
            try:
                pid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            playlists.append({
                # id 转 int：与 netease 数字 id 行为一致（前端数字比较逻辑兼容）
                "id": pid,
                "name": item.get("title") or "",
                "cover_img_url": _fix_img_url(item.get("picurl") or ""),
                "play_count": item.get("listennum") or 0,
                "track_count": item.get("songnum") or 0,
                "creator": item.get("creator_nick") or "",
                "description": item.get("desc") or "",
            })
        return playlists, len(playlists)

    def get_playlist_categories(self) -> list[dict]:
        """获取歌单分类（降级：新服务端无分类接口，固定返回"全部"）"""
        return [{"name": "全部"}]

    # ------------------------------------------------------------------
    # 歌单/榜单详情（分流）
    # ------------------------------------------------------------------
    def get_playlist_detail(self, playlist_id: int, limit: int = 200) -> dict:
        """获取歌单/榜单详情，包含歌曲列表

        QQ 榜单（topId，量级小）与歌单（disstid，10 位数字）ID 量级差异明显，
        据此分流：pid < 10000 走 /top/{id}/detail（失败回退歌单接口），否则走
        /songlist/{id}/detail。两者均按 num=100 分页在客户端聚合。

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
        """榜单详情（/top/{id}/detail，按 num=100 分页聚合）"""
        tracks = []
        total = 0
        name = ""
        for page in range(1, _DETAIL_MAX_PAGES + 1):
            result = self._request(f"/top/{top_id}/detail", params={
                "num": _DETAIL_PAGE_SIZE, "page": page,
            })
            if not result:
                break
            info = result.get("info") or {}
            if info:
                name = info.get("name") or name
            total = _safe_int(info.get("total_num")) or total
            songs = result.get("songs") or []
            if not songs:
                break
            for s in songs:
                if len(tracks) >= limit:
                    break
                tracks.append({
                    "id": s.get("mid"),
                    "name": s.get("name") or s.get("title") or "",
                    "artists": _singers_text(s.get("singer")),
                    "fee": 1 if ((s.get("pay") or {}).get("pay_play")) == 1 else 0,
                })
            if len(tracks) >= limit or len(songs) < _DETAIL_PAGE_SIZE:
                break
        if not tracks:
            return {}
        return {
            "id": top_id,
            "name": name or str(top_id),
            "track_count": total or len(tracks),
            "tracks": tracks,
        }

    def _songlist_detail(self, disstid: int, limit: int) -> dict:
        """歌单详情（/songlist/{id}/detail，按 num=100 分页聚合）"""
        tracks = []
        name = ""
        total = 0
        for page in range(1, _DETAIL_MAX_PAGES + 1):
            result = self._request(f"/songlist/{disstid}/detail", params={
                "num": _DETAIL_PAGE_SIZE, "page": page,
            })
            if not result:
                break
            info = result.get("info") or {}
            if info:
                name = info.get("title") or name
            total = _safe_int(result.get("total")) or total
            songs = result.get("songs") or []
            if not songs:
                break
            for s in songs:
                if len(tracks) >= limit:
                    break
                tracks.append({
                    "id": s.get("mid"),
                    "name": s.get("name") or s.get("title") or "",
                    "artists": _singers_text(s.get("singer")),
                    "fee": 1 if ((s.get("pay") or {}).get("pay_play")) == 1 else 0,
                })
            if len(tracks) >= limit or not result.get("hasmore"):
                break
        if not tracks:
            return {}
        return {
            "id": disstid,
            "name": name or str(disstid),
            "track_count": total or len(tracks),
            "tracks": tracks,
        }


def _safe_int(value) -> int:
    """宽容整型转换（None/脏数据 → 0）"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _credential_to_cookie(credential: dict) -> str:
    """扫码登录 Credential → 标准 Cookie 串（set_cookie 原生可解析）

    Credential 字段参考上游 qqmusic_api.models.request.Credential（FastAPI
    序列化默认 by_alias=True，故 snake_case/alias 双命名兼容）：
        musicid / str_musicid  -> musicid（set_cookie 提取纯数字）
        musickey               -> musickey（QQ 登录 Q_H_L_ 前缀 / 微信 W_X_ 前缀）
        openid / unionid / refresh_token / refresh_key / access_token /
        expired_at             -> 同名透传（set_cookie 白名单字段，微信
                                  W_X_ 凭证续期必需）
        encryptUin / encrypt_uin -> euin（/user/{euin}/homepage 取昵称用）
    零值/空值字段跳过；musicid 与 musickey 缺一返回空串（登录态不完整）。
    """
    if not isinstance(credential, dict):
        return ""

    def pick(*names: str) -> str:
        for n in names:
            v = credential.get(n)
            if v:
                return str(v)
        return ""

    musicid = pick("musicid", "str_musicid")
    musickey = pick("musickey")
    if not musicid or not musickey:
        return ""
    parts = [f"musicid={musicid}", f"musickey={musickey}"]
    for name in ("openid", "unionid", "refresh_token", "refresh_key",
                 "access_token", "expired_at"):
        value = pick(name)
        if value:
            parts.append(f"{name}={value}")
    euin = pick("encryptUin", "encrypt_uin")
    if euin:
        parts.append(f"euin={euin}")
    return ";".join(parts)


# identity 块中 各档会员标志位 -> 到期时间字段（get_vip_info 返回结构）
_IDENTITY_END_FIELDS = (
    ("huge_vip", "huge_vip_end"),
    ("star", "star_end"),
    ("twelve", "twelve_end"),
    ("group_vip_flag", "group_vip_end"),
    ("cp_lover_flag", "cp_lover_end"),
    ("eight", "eight_end"),
)


def _expire_from_identity(identity: dict) -> int:
    """从 vip_info 的 identity 块解析会员到期时间戳（秒）

    userinfo.expire 缺失（部分登录类型不下发，实测返回 0）时的兜底：
    按 _IDENTITY_END_FIELDS 优先级扫描各档会员标志位，取第一个
    flag>0 且到期字符串可解析的档位（对应用户主会员档位的到期）。
    到期字符串为北京时间，实测存在两种格式："YYYY-MM-DD HH:MM:SS"
    （如 huge_vip_end）与纯日期 "YYYY-MM-DD"（如 eight_end，按当天
    零点计，界面仅展示日期不受影响）；无有效项返回 0。
    """
    for flag_key, end_key in _IDENTITY_END_FIELDS:
        if _safe_int(identity.get(flag_key)) <= 0:
            continue
        raw = str(identity.get(end_key) or "").strip()
        if not raw:
            continue
        ts = _parse_vip_end(raw)
        if ts > 0:
            return ts
    return 0


def _parse_vip_end(raw: str) -> int:
    """解析会员到期字符串（北京时间，本地时区），兼容两种格式；失败返回 0"""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(raw, fmt).timestamp())
        except ValueError:
            continue
    return 0
