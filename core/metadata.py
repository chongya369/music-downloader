"""音频元数据写入模块

支持：
- MP3 (ID3v2)：标题、艺术家、专辑、年份、封面、歌词
- FLAC (Vorbis Comment)：同上
"""

import logging
from pathlib import Path

import requests
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, TALB, TIT2, TPE1, TYER, USLT
from mutagen.mp3 import MP3

logger = logging.getLogger(__name__)


def _download_cover(url: str, timeout: int = 10) -> bytes | None:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        logger.warning("下载封面失败 %s: %s", url, e)
        return None


def _guess_cover_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return "image/jpeg"


def write_mp3_tags(
    file_path: Path,
    title: str,
    artist: str,
    album: str,
    year: str = "",
    cover_url: str = "",
    lyric: str = "",
) -> bool:
    """写入 MP3 ID3 标签"""
    try:
        audio = MP3(file_path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        for key in ("TIT2", "TPE1", "TALB", "TYER", "APIC", "USLT"):
            tags.delall(key)

        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        tags.add(TALB(encoding=3, text=album))
        if year:
            tags.add(TYER(encoding=3, text=year))

        cover = _download_cover(cover_url)
        if cover:
            mime = _guess_cover_mime(cover)
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))

        if lyric:
            tags.add(USLT(encoding=3, lang="chi", desc="Lyrics", text=lyric))

        audio.save()
        return True
    except Exception as e:
        logger.error("写入 MP3 标签失败 %s: %s", file_path.name, e)
        return False


def write_flac_tags(
    file_path: Path,
    title: str,
    artist: str,
    album: str,
    year: str = "",
    cover_url: str = "",
    lyric: str = "",
) -> bool:
    """写入 FLAC Vorbis Comment 标签"""
    try:
        audio = FLAC(file_path)
        audio["title"] = title
        audio["artist"] = artist
        audio["album"] = album
        if year:
            audio["date"] = year
        if lyric:
            audio["lyrics"] = lyric

        audio.clear_pictures()
        cover = _download_cover(cover_url)
        if cover:
            pic = Picture()
            pic.type = 3
            pic.mime = _guess_cover_mime(cover)
            pic.desc = "Cover"
            pic.data = cover
            audio.add_picture(pic)

        audio.save()
        return True
    except Exception as e:
        logger.error("写入 FLAC 标签失败 %s: %s", file_path.name, e)
        return False


def write_tags(file_path: Path, meta: dict) -> bool:
    """根据扩展名自动选择写入器"""
    ext = file_path.suffix.lower()
    common = dict(
        title=meta.get("title", ""),
        artist=meta.get("artist", ""),
        album=meta.get("album", ""),
        year=meta.get("year", ""),
        cover_url=meta.get("cover_url", ""),
        lyric=meta.get("lyric", ""),
    )
    if ext == ".mp3":
        return write_mp3_tags(file_path, **common)
    if ext == ".flac":
        return write_flac_tags(file_path, **common)
    logger.warning("不支持的格式，跳过元数据写入: %s", ext)
    return False
