"""dir_layout=artist_album 存量文件迁移：/歌手/歌曲 → /歌手/专辑/歌曲

用户在设置页手动触发（移动用户文件必须显式确认，不做启动自动迁移）。
只搬位置不改文件名（旧式「歌手 - 歌名」与新式「音轨号 - 歌名」
均保持原样，需要统一命名可用历史页「重下」刷新），Song.file_path
随迁更新。以 songs 表 success 记录为清单：文件不存在 / 目标已存在 /
单条失败 → 跳过并记日志，不中断整体。songs 表不存年份，迁移目录不带
(年份) 后缀；该专辑后续新下载会生成相邻的带年份目录，属可接受的共存。
迁移按目标位置判断幂等：已在专辑目录内的文件自动跳过，可重复执行。
"""

import logging
import os
import re
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def run(app) -> tuple[int, int, int, int] | None:
    """执行迁移（返回 (移动, 跳过, 失败, 总数)；dir_layout 非 artist_album
    时返回 None 表示前置条件不满足。异常由调用方兜底。）"""
    with app.app_context():
        from models import Setting, Song, db
        from core.downloader import sanitize_filename

        if Setting.get("dir_layout", "artist_album") != "artist_album":
            return None

        output_dir = Path(Setting.get("output_dir", "downloads"))
        if not output_dir.is_absolute():
            if getattr(sys, "frozen", False):
                root = Path(sys.executable).resolve().parent
            else:
                # migrate_layout.py 位于 webapp/，项目根为上级目录
                root = Path(__file__).resolve().parent.parent
            output_dir = root / output_dir

        songs = Song.query.filter(Song.status == "success", Song.file_path != "").all()
        moved = skipped = failed = 0
        for s in songs:
            try:
                old = Path(s.file_path)
                if not old.exists():
                    skipped += 1
                    continue
                # 目录计算与 task_manager 下载路径同款：主歌手 + 专辑名
                # （缺失时用歌名，即单曲/EP 惯例）
                artists = (s.artists or "").strip()
                primary = re.split(r"[/、]", artists)[0].strip() if artists else ""
                primary = sanitize_filename(primary) if primary else "群星"
                album_dir = (s.album or "").strip() or (s.name or "").strip()
                if not album_dir:
                    skipped += 1
                    continue
                dest_dir = output_dir / primary / sanitize_filename(album_dir)
                if os.path.normpath(str(old.parent)) == os.path.normpath(str(dest_dir)):
                    skipped += 1
                    continue
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / old.name
                if dest.exists():
                    skipped += 1
                    continue
                shutil.move(str(old), str(dest))
                s.file_path = str(dest)
                moved += 1
            except Exception as e:  # 单条失败不中断其余记录
                failed += 1
                logger.warning("dir_layout 迁移跳过 %s - %s: %s", s.artists, s.name, e)
        db.session.commit()
        logger.info("dir_layout 迁移完成: 移动 %d / 跳过 %d / 失败 %d（共 %d 条）",
                    moved, skipped, failed, len(songs))
        return moved, skipped, failed, len(songs)
