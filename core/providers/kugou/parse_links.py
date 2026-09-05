"""酷狗音乐链接解析模块

仅包含 parse_kugou_playlist_id 模块级函数（平台分流在 api.py 层完成）。
酷狗歌单曲目接口不可用（接口文档 §6.3），只解析榜单 rankid；
specialid（歌单 ID）误入时由 client.get_playlist_detail 的
榜单白名单校验兜底拒绝。
"""

import re


def parse_kugou_playlist_id(text: str) -> int | None:
    """从用户输入解析酷狗榜单 ID

    支持以下格式：
        - 纯数字：8888（TOP500 的 rankid）
        - 含 rankid 参数/路径的 URL
        - 分享链接中提取的末尾数字 ID

    Returns:
        榜单 ID（rankid，纯数字），解析失败返回 None
    """
    text = text.strip()
    if not text:
        return None
    # 纯数字
    if text.isdigit():
        return int(text)
    # URL 中的 rankid= 参数或 /rankid/ 路径
    m = re.search(r"rankid[=/](\d+)", text)
    if m:
        return int(m.group(1))
    # 路径中的末尾数字（分享链接格式，3 位以上避免误匹配年份等；
    # 兼容 /8888.html、/8888/、?x=1 等后缀形态）
    m = re.search(r"/(\d{3,})(?:[/?\.\s]|$)", text)
    if m:
        return int(m.group(1))
    return None
