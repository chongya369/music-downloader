"""QQ音乐链接解析模块

仅包含 parse_qq_playlist_id 模块级函数（平台分流在 api.py 层完成）。
"""

import re


def parse_qq_playlist_id(text: str) -> int | None:
    """从用户输入解析 QQ 歌单 ID

    支持以下格式：
        - 纯数字：7707261125
        - 歌单页链接：https://y.qq.com/n/ryqq/playlist/7707261125
        - 含 disstid= 参数的 URL
        - 分享文案末尾数字

    Returns:
        歌单 ID（disstid，纯数字），解析失败返回 None
    """
    text = text.strip()
    if not text:
        return None
    # 纯数字
    if text.isdigit():
        return int(text)
    # URL 中的 disstid= 参数
    m = re.search(r"[?&]disstid=(\d+)", text)
    if m:
        return int(m.group(1))
    # 歌单页路径 /playlist/xxx
    m = re.search(r"/playlist/(\d+)", text)
    if m:
        return int(m.group(1))
    # 路径中的末尾数字（分享文案格式）
    m = re.search(r"/(\d{5,})(?:/|\s|$)", text)
    if m:
        return int(m.group(1))
    return None
