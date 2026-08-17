"""网易云链接解析模块

仅包含 parse_playlist_id 模块级函数。
"""

import re


def parse_playlist_id(text: str) -> int | None:
    """从用户输入解析歌单 ID

    支持以下格式：
        - 纯数字：3778678
        - 网易云分享链接：https://music.163.com/playlist?id=3778678
        - 短链接：https://y.music.163.com/m/playlist?id=3778678
        - 分享文案：「...」https://y.music.163.com/.../3778678/...

    Returns:
        歌单 ID，解析失败返回 None
    """
    text = text.strip()
    if not text:
        return None
    # 纯数字
    if text.isdigit():
        return int(text)
    # URL 中的 id= 参数
    m = re.search(r"[?&]id=(\d+)", text)
    if m:
        return int(m.group(1))
    # 路径中的数字（短链接格式 /playlist/xxx 或末尾数字）
    m = re.search(r"/(\d{5,})(?:/|\s|$)", text)
    if m:
        return int(m.group(1))
    return None
