"""网易云字段 → 统一结构转换层

仅被窄接口方法调用（get_song_urls / get_song_detail / verify_account）。
上层永不 import/调用此模块。
"""

from datetime import datetime


def transform_song_urls(raw_list: list[dict], song_ids: list[str]) -> list[dict]:
    """将网易云 /song/url/v1 返回转换为 UrlInfo 列表

    Args:
        raw_list: NeteaseClient.get_song_urls() 返回的原始 data 列表
        song_ids: 传入的歌曲 ID 列表（用于顺序对齐）

    Returns:
        UrlInfo 列表，与 song_ids 顺序对齐
    """
    # 以 str(id) 为键建映射
    mapping: dict[str, dict] = {}
    for item in raw_list:
        sid = str(item.get("id", ""))
        mapping[sid] = {
            "url": item.get("url"),
            "ext": (item.get("type") or "mp3").lower(),
            "size": item.get("size"),
            "is_trial": bool(item.get("freeTrialInfo")),
        }

    # 按 song_ids 顺序重排，缺项填空结构
    result = []
    for sid in song_ids:
        sid_str = str(sid)
        result.append(mapping.get(sid_str, {
            "url": None,
            "ext": "mp3",
            "size": None,
            "is_trial": False,
        }))
    return result


def transform_song_detail(raw_list: list[dict], song_ids: list[str]) -> list[dict]:
    """将网易云 /song/detail 返回转换为 SongMeta 列表

    Args:
        raw_list: NeteaseClient.get_song_detail() 返回的原始 songs 列表
        song_ids: 传入的歌曲 ID 列表（用于顺序对齐）

    Returns:
        SongMeta 列表，与 song_ids 顺序对齐
    """
    # 以 str(id) 为键建映射
    mapping: dict[str, dict] = {}
    for item in raw_list:
        sid = str(item.get("id", ""))
        album_info = item.get("al") or {}
        artists_list = item.get("ar") or []

        # 主歌手
        if artists_list:
            primary_artist = artists_list[0].get("name", "") or ""
            if not primary_artist.strip():
                primary_artist = "群星"
        else:
            primary_artist = "群星"

        # 发行年份
        year = ""
        pub = item.get("publishTime")
        if pub and pub > 0:
            try:
                year = datetime.fromtimestamp(pub / 1000).strftime("%Y")
            except (OSError, ValueError):
                year = ""

        mapping[sid] = {
            "title": item.get("name", ""),
            "artist": primary_artist,
            "album": album_info.get("name", ""),
            "year": year,
            "cover_url": album_info.get("picUrl", ""),
            "duration_ms": item.get("dt", 0),
        }

    # 按 song_ids 顺序重排，缺项填空结构
    result = []
    for sid in song_ids:
        sid_str = str(sid)
        result.append(mapping.get(sid_str, {
            "title": "",
            "artist": "群星",
            "album": "",
            "year": "",
            "cover_url": "",
            "duration_ms": 0,
        }))
    return result


def transform_account_info(account_data: dict, vip_info: dict) -> dict:
    """将网易云账号+会员信息转换为 AccountInfo

    Args:
        account_data: get_account_info() 返回的 account 部分
        vip_info: get_vip_info() 返回的 vip 信息

    Returns:
        AccountInfo 结构
    """
    vip_type = vip_info.get("vip_type", 0)
    expire_time = vip_info.get("expire_time")

    # VIP 类型文本映射
    vip_text_map = {0: "非会员", 11: "黑胶VIP", 12: "SVIP"}
    vip_text = vip_text_map.get(vip_type, f"vipType={vip_type}")

    return {
        "ok": True,
        "nickname": account_data.get("userName") or account_data.get("nickname") or "",
        "vip_type": vip_type,
        "vip_expire_at": expire_time,
        "vip_text": vip_text,
    }


def is_vip_song(fee) -> bool:
    """判断歌曲是否为 VIP 歌曲

    网易云 fee 语义：
        0=免费
        1=VIP
        4=购买专辑
        8=低音质免费
    """
    try:
        fee_int = int(fee)
    except (TypeError, ValueError):
        return False
    return fee_int == 1
