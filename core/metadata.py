"""音频元数据写入模块

支持：
- MP3 (ID3v2)：标题、艺术家、专辑、年份、音轨号、碟号、专辑歌手、封面、原文/翻译歌词
- FLAC (Vorbis Comment)：同上
- OGG (Vorbis / Opus，同为 Vorbis Comment)：字段集与 FLAC 对齐，
  封面按规范写 METADATA_BLOCK_PICTURE（base64 的 FLAC Picture 块）
"""

import base64
import logging
from pathlib import Path

import requests
from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, USLT
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

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
    tlyric: str = "",
    track_no: int = 0,
    disc_no: int = 0,
    albumartist: str = "",
) -> bool:
    """写入 MP3 ID3 标签（ID3v2.4：年份用 TDRC，音轨/碟号用 TRCK/TPOS）"""
    try:
        audio = MP3(file_path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        for key in ("TIT2", "TPE1", "TALB", "TDRC", "TYER", "APIC", "USLT",
                    "TRCK", "TPOS", "TPE2"):
            tags.delall(key)

        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        tags.add(TALB(encoding=3, text=album))
        if year:
            tags.add(TDRC(encoding=3, text=year))
        if track_no > 0:
            tags.add(TRCK(encoding=3, text=str(track_no)))
        if disc_no > 0:
            tags.add(TPOS(encoding=3, text=str(disc_no)))
        if albumartist:
            tags.add(TPE2(encoding=3, text=albumartist))

        cover = _download_cover(cover_url)
        if cover:
            mime = _guess_cover_mime(cover)
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))

        if lyric:
            tags.add(USLT(encoding=3, lang="chi", desc="Lyrics", text=lyric))
        if tlyric:
            tags.add(USLT(encoding=3, lang="chi", desc="Lyrics-Translation", text=tlyric))

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
    tlyric: str = "",
    track_no: int = 0,
    disc_no: int = 0,
    albumartist: str = "",
) -> bool:
    """写入 FLAC Vorbis Comment 标签"""
    try:
        audio = FLAC(file_path)
        audio["title"] = title
        audio["artist"] = artist
        audio["album"] = album
        if year:
            audio["date"] = year
        else:
            audio.pop("date", None)
        if track_no > 0:
            audio["tracknumber"] = str(track_no)
        else:
            audio.pop("tracknumber", None)
        if disc_no > 0:
            audio["discnumber"] = str(disc_no)
        else:
            audio.pop("discnumber", None)
        if albumartist:
            audio["albumartist"] = albumartist
        else:
            audio.pop("albumartist", None)
        if lyric:
            audio["lyrics"] = lyric
        if tlyric:
            audio["translation"] = tlyric
        elif "translation" in audio:
            audio.pop("translation", None)

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


def write_ogg_tags(
    file_path: Path,
    title: str,
    artist: str,
    album: str,
    year: str = "",
    cover_url: str = "",
    lyric: str = "",
    tlyric: str = "",
    track_no: int = 0,
    disc_no: int = 0,
    albumartist: str = "",
) -> bool:
    """写入 OGG（Vorbis / Opus）Vorbis Comment 标签

    字段集与 write_flac_tags 逐行对齐（音轨号/碟号 str() 化）。容器类型经
    mutagen.File 嗅探：QQ 的 OGG 640k 理论为 Vorbis，Opus 做兜底。

    封面：mutagen 的 OggVorbis/OggOpus 均无 FLAC 那套 add_picture/
    clear_pictures，按 Vorbis Comment 规范写 METADATA_BLOCK_PICTURE =
    base64(FLAC Picture 块)——与 foobar2000 / 各播放器读法一致。
    """
    try:
        audio = MutagenFile(str(file_path))
        if not isinstance(audio, (OggVorbis, OggOpus)):
            logger.error("写入 OGG 标签失败 %s: 不支持的容器类型 %s",
                         file_path.name, type(audio).__name__)
            return False
        audio["title"] = title
        audio["artist"] = artist
        audio["album"] = album
        if year:
            audio["date"] = year
        else:
            audio.pop("date", None)
        if track_no > 0:
            audio["tracknumber"] = str(track_no)
        else:
            audio.pop("tracknumber", None)
        if disc_no > 0:
            audio["discnumber"] = str(disc_no)
        else:
            audio.pop("discnumber", None)
        if albumartist:
            audio["albumartist"] = albumartist
        else:
            audio.pop("albumartist", None)
        if lyric:
            audio["lyrics"] = lyric
        if tlyric:
            audio["translation"] = tlyric
        elif "translation" in audio:
            audio.pop("translation", None)

        # 清封面（等价于 FLAC 的 clear_pictures）
        audio.pop("metadata_block_picture", None)
        cover = _download_cover(cover_url)
        if cover:
            pic = Picture()
            pic.type = 3
            pic.mime = _guess_cover_mime(cover)
            pic.desc = "Cover"
            pic.data = cover
            audio["metadata_block_picture"] = [
                base64.b64encode(pic.write()).decode("ascii")
            ]

        audio.save()
        return True
    except Exception as e:
        logger.error("写入 OGG 标签失败 %s: %s", file_path.name, e)
        return False


def write_tags(file_path: Path, meta: dict) -> bool:
    """根据扩展名自动选择写入器

    入口归一：meta 字段可能为 None（上游「键存在值为 null」，dict.get 默认值
    兜不住），而 mutagen 的 `audio["title"] = None` / `TIT2(text=None)` 会直接
    抛异常导致整首歌标签写不进去。此处单点收敛为 ""，各写入器无需再防御。
    """
    ext = file_path.suffix.lower()
    common = dict(
        title=str(meta.get("title") or ""),
        artist=str(meta.get("artist") or ""),
        album=str(meta.get("album") or ""),
        year=str(meta.get("year") or ""),
        cover_url=str(meta.get("cover_url") or ""),
        lyric=str(meta.get("lyric") or ""),
        tlyric=str(meta.get("tlyric") or ""),
        track_no=_as_int(meta.get("track_no")),
        disc_no=_as_int(meta.get("disc_no")),
        albumartist=str(meta.get("albumartist") or ""),
    )
    if ext == ".mp3":
        return write_mp3_tags(file_path, **common)
    if ext == ".flac":
        return write_flac_tags(file_path, **common)
    if ext == ".ogg":
        return write_ogg_tags(file_path, **common)
    logger.warning("不支持的格式，跳过元数据写入: %s", ext)
    return False


def _as_int(value) -> int:
    """宽容整型转换（None/脏数据 → 0，0=不写入该字段）"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
