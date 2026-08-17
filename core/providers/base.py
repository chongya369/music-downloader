"""统一 Provider 抽象基类与数据结构

窄接口（方案 A）：仅覆盖下载管线必要能力，
专辑/榜单/发现/账号详情等为平台专有旁路。
"""

from abc import ABC, abstractmethod


class MusicProvider(ABC):
    """音乐 Provider 抽象基类

    子类必须实现 5 个窄接口方法 + set_cookie/set_custom_base_url。
    """

    platform: str = ""

    @abstractmethod
    def verify_account(self, cred: str) -> dict:
        """验证凭证并返回账号信息

        Returns:
            AccountInfo 结构（见下方统一数据结构）
        """
        ...

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
