"""QQ音乐字段 → 统一结构转换层

QQ 的字段转换大部分已内联在 QqClient 各方法中（搜索 pay→fee 映射、
UrlInfo 组装、榜单/歌单 tracks 统一等），本模块仅保留歌曲维度的
VIP 判定等纯函数。
"""

def is_vip_song(fee) -> bool:
    """判断歌曲是否为 VIP 歌曲

    统一 fee 语义（网易云语义）：
        0=免费 1=VIP 4=购买专辑 8=低音质免费
    QQ 侧 fee 由搜索结果 pay.pay_play 映射（=1 → 1），其余场景固定 0。
    """
    try:
        fee_int = int(fee)
    except (TypeError, ValueError):
        return False
    return fee_int == 1
