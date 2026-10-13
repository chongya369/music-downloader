"""旧数据库搬迁（一次性启动迁移）

场景：数据位置被 --data-dir / APP_DATA_DIR 指定，且老库仍在程序目录
（_ROOT/downloads.db）时，自动搬迁（含 SQLite 附属文件 -journal/-wal/-shm）。

失败语义（F1，2026-10-13）：任一步失败 → 倒序回滚已完成项 → logger.error
落盘 → SystemExit 拒绝启动。旧实现静默降级"用新空库启动"，会导致空库 +
admin/admin123 + 强制改密 + 旧库永久搁置（下次启动 DB_PATH 已存在不再重试），
即"旧版升级后被强制改密"事故的数据漂移根因之一，必须杜绝。

原子性保证：
- 同盘：os.replace（POSIX/Windows 均原子）
- 跨盘：copy 到临时名 → fsync → 字节数校验 → os.replace 原子落位 → 删源。
  中断时目标处只可能存在临时名半截文件（由本函数清理），目标正式名要么
  不存在、要么完整。
- 回滚仅对"已完成项"执行（os.replace 落位成功才算完成）；单步回滚失败只
  ERROR 留痕，不掩盖原始异常。

零副作用模块：不 import 应用代码，供 app.py 与探针共同使用。
"""

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger("webapp")

# 搬迁顺序：主库优先，附属文件随后。SQLite 附属文件与主库"同生共死"，
# 全部一起搬才能保证目标库一致。
_DB_SUFFIXES = ("", "-journal", "-wal", "-shm")


def _same_drive(a: Path, b: Path) -> bool:
    """两路径是否在同一盘符/挂载点（决定能否用原子 rename）"""
    return os.path.splitdrive(str(a)) == os.path.splitdrive(str(b))


def _atomic_move(src: Path, dst: Path) -> None:
    """单文件原子搬迁（成功 = 内容已完整落位到 dst）

    同盘 os.replace 原子；跨盘 copy 到临时名 → fsync → 字节数校验 →
    os.replace 落位 → 删源。任何失败都会清理自己的临时文件后重抛异常，
    保证 dst 正式名要么不存在、要么完整。删源失败仅告警（内容已完整
    落位，迁移实际已成功）。
    """
    if _same_drive(src, dst):
        os.replace(str(src), str(dst))
        return
    tmp = dst.with_name(dst.name + ".migrating-tmp")
    try:
        shutil.copyfile(str(src), str(tmp))
        with open(str(tmp), "r+b") as f:
            os.fsync(f.fileno())
        if tmp.stat().st_size != src.stat().st_size:
            raise OSError(f"copy 字节数不一致: {src} -> {tmp}")
        os.replace(str(tmp), str(dst))
    except BaseException:
        # 清理半截临时文件（目标正式名未触碰，源文件完好）
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        src.unlink()
    except OSError:
        # 内容已完整落位：迁移成功，残留源文件只提醒手动清理
        logger.warning("搬迁完成但删除源文件失败（请手动删除）: %s", src)


def migrate_old_db(old_root: Path, db_path: Path) -> bool:
    """把 old_root/downloads.db(+附属文件) 搬迁到 db_path

    返回 True = 已搬迁；False = 无需搬迁（源不存在或目标已存在）。
    搬迁失败（含回滚后）直接 SystemExit(1) 拒绝启动——数据安全优先，
    绝不静默换新空库。
    """
    old_main = old_root / "downloads.db"
    if not old_main.exists() or db_path.exists():
        return False

    done: list[tuple[Path, Path]] = []       # (src, dst) 已完整落位项
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        for suf in _DB_SUFFIXES:
            src = old_root / f"downloads.db{suf}"
            if not src.exists():
                continue
            dst = db_path.with_name(db_path.name + suf)
            _atomic_move(src, dst)
            done.append((src, dst))
    except OSError as e:
        # 倒序回滚已完成项，恢复搬迁前状态（半截项已被 _atomic_move 自清理）
        for src, dst in reversed(done):
            try:
                _atomic_move(dst, src)
            except OSError as re_:
                logger.error(
                    "回滚单项失败（保留现状，请手动核对文件）: %s -> %s: %s",
                    dst, src, re_,
                )
        # 先落盘再退出：frozen 双击 exe 控制台瞬关，webapp.log 是唯一痕迹
        logger.error(
            "旧数据库搬迁失败，已回滚已完成项并拒绝启动（防止用空库启动"
            "造成数据丢失假象）。源: %s -> 目标: %s；原因: %s。"
            "请关闭占用旧库的进程（旧实例/杀毒软件等）或检查目标目录权限后重启。",
            old_main, db_path, e,
        )
        raise SystemExit(1)
    logger.info("已迁移旧数据库: %s -> %s", old_main, db_path)
    return True
