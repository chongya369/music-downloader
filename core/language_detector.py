"""歌曲语言检测模块

依据歌词字符集判断歌曲语言，用于下载目录分类。
网易云 API 不返回语言字段，需自行判断。

优先级（关键）：日文歌词含大量汉字，必须先检查假名再检查汉字，
否则会把日文误判为中文。
"""

import logging

logger = logging.getLogger(__name__)


def detect_language(lyric: str = "", name: str = "", artists: str = "") -> str:
    """检测歌曲语言，返回目录名

    Args:
        lyric: 歌词文本（首选判断依据，准确度最高）
        name: 歌曲名（歌词为空时降级使用）
        artists: 歌手名（歌词为空时降级使用）

    Returns:
        目录名：中文 / 英文 / 日文 / 韩文 / 语言未知
    """
    # 优先用歌词判断（准确度最高）
    text = lyric.strip() if lyric else ""
    if not text:
        # 降级：用歌名 + 歌手名
        text = (name or "") + " " + (artists or "")
    if not text.strip():
        return "语言未知"

    # 统计各语种字符数
    kana = sum(1 for c in text if '\u3040' <= c <= '\u30ff')      # 平假名+片假名
    hangul = sum(1 for c in text if '\uac00' <= c <= '\ud7af')    # 韩文音节
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')       # 中日韩统一汉字
    # 拉丁字母（排除空格和标点）
    latin = sum(1 for c in text if c.isalpha() and ord(c) < 128)

    # 按优先级判断（顺序很重要）
    # 1. 含假名 → 日文（日文歌词常含大量汉字，必须先判假名）
    if kana > 0:
        return "日文"
    # 2. 含韩文音节 → 韩文
    if hangul > 0:
        return "韩文"
    # 3. 含汉字（且无假名/韩文）→ 中文
    if cjk > 0:
        return "中文"
    # 4. 拉丁字母占比 >= 30% → 英文
    total_alpha = kana + hangul + cjk + latin
    if latin > 0 and total_alpha > 0 and latin >= total_alpha * 0.3:
        return "英文"
    # 5. 无法判断
    return "语言未知"
