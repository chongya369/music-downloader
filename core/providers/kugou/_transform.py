"""酷狗音乐字段 → 统一结构转换层

酷狗的字段映射大部分已内联在 KuGouClient 各方法中（歌曲条目归一化
_norm_song、UrlInfo 组装、榜单 tracks 统一等），本模块保留歌曲维度的
VIP 判定、fee 映射与封面 URL 修复等纯函数。
"""


def map_fee(song: dict) -> int:
    """酷狗原始歌曲条目 → 统一 fee（0=免费 1=VIP）

    酷狗没有网易云语义的直接 fee 字段，采用启发式组合判定
    （接口文档 §9，未做大样本验证，误判仅影响"优先非 VIP 账号"
    的排序精度，不阻断下载，可上线后调优）：
    - deprecated.pkg_price > 0          → 需单独购买（付费单曲）
    - trans_param.musicpack_advance==1  → 音乐包/VIP 专属

    兼容三种响应形态（嵌套型/扁平榜单型/扁平歌手型）：
    deprecated 与 trans_param 在三种形态中均位于歌曲条目顶层。
    """
    dep = song.get("deprecated") or {}
    tp = song.get("trans_param") or {}
    try:
        pkg_price = int(dep.get("pkg_price") or 0)
    except (TypeError, ValueError):
        pkg_price = 0
    if pkg_price > 0 or tp.get("musicpack_advance") == 1:
        return 1
    return 0


def is_vip_song(fee) -> bool:
    """判断歌曲是否为 VIP 歌曲

    统一 fee 语义（网易云语义）：
        0=免费 1=VIP 4=购买专辑 8=低音质免费
    酷狗侧 fee 由 map_fee 映射（0/1），其余场景固定 0。
    """
    try:
        fee_int = int(fee)
    except (TypeError, ValueError):
        return False
    return fee_int == 1


def fix_cover_url(url: str, size: str = "480") -> str:
    """修复酷狗封面 URL 的 {size} 占位符

    上游封面形如 http://imge.kugou.com/stdmusic/{size}/20240122/xxx.jpg，
    必须替换后才能访问（实测 480 档返回 HTTP 200 / image/jpeg）。
    空值防御：返回空串。
    """
    if not url:
        return ""
    return url.replace("{size}", size)
