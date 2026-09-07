"""统一 Provider 抽象基类与数据结构

窄接口（方案 A）：仅覆盖下载管线必要能力，
专辑/榜单/发现/账号详情等为平台专有旁路。
"""

from abc import ABC, abstractmethod


def _rank_of(order: list[str], level: str) -> int:
    """档位在有序表中的秩（越高越大）；未知档位返回 -1"""
    try:
        return order.index(level)
    except ValueError:
        return -1


class MusicProvider(ABC):
    """音乐 Provider 抽象基类

    子类必须实现 5 个窄接口方法 + set_cookie/set_custom_base_url。
    """

    platform: str = ""

    # 音质从高到低的完整顺序（子类可覆写为去重后的子集）
    QUALITY_ORDER: list[str] = ["hires", "lossless", "exhigh", "higher", "standard"]

    @abstractmethod
    def get_song_urls(self, song_ids: list[str], level: str) -> list[dict]:
        """批量获取歌曲下载链接

        Returns:
            UrlInfo 列表（见下方统一数据结构）
        """
        ...

    @abstractmethod
    def get_song_detail(self, song_ids: list[str]) -> list[dict]:
        """获取歌曲详情

        Returns:
            SongMeta 列表（见下方统一数据结构）
        """
        ...

    @abstractmethod
    def get_lyric(self, song_id: str) -> dict:
        """获取歌词

        Returns:
            {"lrc": str, "tlyric": str}
        """
        ...

    @abstractmethod
    def is_vip_song(self, fee) -> bool:
        """判断歌曲是否 VIP

        歌曲维度而非账号维度，由 fee 字段决定。
        """
        ...

    def set_cookie(self, cred: str) -> None:
        """注入凭证（需在业务调用前完成）"""
        raise NotImplementedError

    def set_custom_base_url(self, url: str) -> None:
        """注入自定义 API 地址"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 音质降级链（具体方法，子类按需覆写）
    # ------------------------------------------------------------------
    def quality_key(self, level: str) -> str:
        """归一化到平台实际请求参数值，用于链内去重

        默认原样返回；QQ 的 hires/lossless 同为 flac、exhigh/higher
        同为 320，覆写本方法映射后可去掉重复档位。
        """
        return level

    def quality_chain(self, level: str) -> list[str]:
        """从 level 起（含自身）向下递减的候选档位列表，按 quality_key 去重

        level 不在 QUALITY_ORDER 中时返回 [level]（保守不降级）。
        """
        try:
            start = self.QUALITY_ORDER.index(level)
        except ValueError:
            return [level]
        chain: list[str] = []
        seen: set[str] = set()
        for lv in self.QUALITY_ORDER[start:]:
            k = self.quality_key(lv)
            if k in seen:
                continue
            seen.add(k)
            chain.append(lv)
        return chain

    def get_song_url_with_fallback(self, song_id: str, level: str) -> tuple[dict, str]:
        """逐档尝试取流，返回 (url_info, 实际生效档位)

        - 首档精确命中即返回（命中时零额外请求）
        - client 内部降级（url_info["level"] 与请求档不一致，如 QQ/酷狗
          目标档失败自动落 128）时：保留该结果为兜底，继续沿链试中间档，
          音质优先；若内部已降到链尾档则直接返回
        - code == -110（网易云无音源语义）立即终止：降档也无音源
        - 全链失败返回末档结果与 level
        """
        chain = self.quality_chain(level)
        order = self.QUALITY_ORDER
        last: dict = {}
        best: dict = {}
        best_level = level
        for i, cand in enumerate(chain):
            lst = self.get_song_urls([song_id], level=cand)
            info = lst[0] if lst else {}
            last = info
            # 接口/鉴权级失败：降档无意义，终止降档（上层据此换账号）
            if info.get("err"):
                break
            # 无音源：与档位无关，终止降级
            if info.get("code") == -110 and not (info.get("err") or ""):
                break
            # 试听片段视为未命中（会员权益不足），继续沿链降档：
            # fee=8（低音质免费）歌曲可降档拿到免费完整 128k；
            # 全档试听（fee=1 非 VIP）链耗尽后由上层 is_trial 拦截拒绝下载
            if not info.get("url") or info.get("is_trial"):
                continue
            act = str(info.get("level") or cand)
            if act == cand:
                info["level"] = act
                return info, act
            if i + 1 < len(chain) and _rank_of(order, act) > _rank_of(order, chain[i + 1]):
                # 内部降级跳过了中间档（如 flac 失败直接落 128），先记兜底继续
                best, best_level = info, act
                continue
            # 已降到链尾 / 无更优中间档可试
            info["level"] = act
            return info, act
        if best:
            best["level"] = best_level
            return best, best_level
        last["level"] = level
        return last, level


# ------------------------------------------------------------------
# 统一数据结构（类型注解用）
# ------------------------------------------------------------------

# Song: 歌曲基础信息
# {
#     "platform": str,          # 平台标识（如 "netease"）
#     "song_id": str,           # 歌曲 ID（统一 str）
#     "name": str,              # 歌曲名
#     "artists": str,           # 歌手名（多歌手用 "/" 连接）
#     "album": str,             # 专辑名
#     "duration_ms": int,       # 时长（毫秒）
#     "is_vip_song": bool,      # 是否 VIP 歌曲
# }

# UrlInfo: 下载链接信息
# {
#     "url": str | None,        # 下载 URL
#     "ext": str,               # 文件扩展名（如 "mp3", "flac"）
#     "size": int | None,       # 文件大小（字节）
#     "is_trial": bool,         # 是否为试听片段
#     "level": str,             # 实际生效音质档（内部降级时与请求档不同；可选）
#     "code": int,              # 平台原始状态码（网易云 data[].code；可选）
#     "err": str,               # 取流失败诊断（url=None 时非空；可选）
# }

# SongMeta: 歌曲元数据
# {
#     "title": str,             # 歌曲名
#     "artist": str,            # 主歌手
#     "album": str,             # 专辑名
#     "year": str,              # 发行年份
#     "cover_url": str,         # 封面 URL
#     "duration_ms": int,       # 时长（毫秒）
# }

# AccountInfo: 账号信息
# {
#     "ok": bool,               # 凭证是否有效
#     "nickname": str,          # 昵称
#     "vip_type": int,          # VIP 类型（0=非会员, 11=黑胶VIP, 12=SVIP）
#     "vip_expire_at": int | None,  # VIP 到期时间戳（毫秒）
#     "vip_text": str,          # VIP 文本描述
# }
