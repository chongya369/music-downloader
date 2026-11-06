"""旧数据库搬迁（一次性启动迁移）

场景：数据位置被 --data-dir / APP_DATA_DIR 指定，且老库仍在程序目录
（_ROOT/downloads.db）时，自动搬迁（含 SQLite 附属文件 -journal/-wal/-shm）。

失败语义（F1，2026-10-13）：任一步失败 → 倒序回滚已完成项 → logger.error
落盘 → SystemExit 拒绝启动。旧实现静默降级"用新空库启动"，会导致空库 +
admin/admin123 + 强制改密 + 旧库永久搁置（下次启动 DB_PATH 已存在不再重试），
即"旧版升级后被强制改密"事故的数据漂移根因之一，必须杜绝。

降级复制语义（v2，2026-10-15）：rename 因 PermissionError（源目录只读，
fnOS 安装目录 root 属主、运行用户无写权限场景）或 EXDEV（跨挂载点、
_same_drive 失判兜底）失败时，自动降级为复制模式——目标库仍原子完整
落位，源文件保留（下次启动 db_path 已存在直接跳过）；回滚按「源是否
仍在」分流（源在 = 降级模式，仅删目标副本）。数据卷不可写等真故障仍
回滚 + 拒绝启动。残留源由 .migrated 标记防「数据卷被清 → 陈旧源复活」。

原子性保证：
- 同设备：os.replace（POSIX/Windows 均原子）
- 跨设备：copy 到临时名 → fsync → 字节数校验 → os.replace 原子落位 → 删源。
  中断时目标处只可能存在临时名半截文件（由本函数清理），目标正式名要么
  不存在、要么完整。
- 回滚仅对"已完成项"执行（目标完整落位才算完成）；单步回滚失败只
  ERROR 留痕，不掩盖原始异常。

零副作用模块：不 import 应用代码，供 app.py 与探针共同使用。
"""

import errno
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

logger = logging.getLogger("webapp")

# 搬迁顺序：主库优先，附属文件随后。SQLite 附属文件与主库"同生共死"，
# 全部一起搬才能保证目标库一致。
_DB_SUFFIXES = ("", "-journal", "-wal", "-shm")

# 陈旧源标记：成功搬迁后源仍在（降级复制/删源失败）时写在源旁，
# 指纹（size+mtime_ns）命中即视为"数据卷被清后的陈旧源"，跳过搬迁
_MARKER_SUFFIX = ".migrated"


def _same_drive(a: Path, b: Path) -> bool:
    """两路径是否在同一设备/挂载点（决定能否用原子 rename）

    优先比较 st_dev：POSIX 下 os.path.splitdrive 恒返回 ('', path)，
    旧实现同盘判定恒真，跨挂载点 rename 会抛 EXDEV。以「存在一方的
    设备号」为准（源文件必存在；目标取父目录——migrate_old_db 在搬迁
    前已 mkdir 目标父目录）。stat 失败（极端：路径不可访问）回退
    splitdrive（Windows 盘符语义）。
    """
    try:
        pa = a if a.exists() else a.parent
        return os.stat(pa).st_dev == os.stat(b.parent).st_dev
    except OSError:
        return os.path.splitdrive(str(a)) == os.path.splitdrive(str(b))


def _atomic_move(src: Path, dst: Path) -> None:
    """单文件原子搬迁（成功 = 内容已完整落位到 dst）

    同设备 → os.replace 原子；跨设备 → copy 临时名 → fsync → 字节数校验
    → os.replace 落位 → 删源。rename 因 PermissionError（源目录只读，fnOS
    安装目录 root 属主场景）或 EXDEV（跨挂载点，_same_drive 失判兜底）
    失败时降级走复制路径：目标仍完整落位，源保留——下次启动 db_path
    已存在，migrate_old_db 入口直接跳过；残留源由 .migrated 标记防复活。
    其余 OSError（盘满/IO 错误等）不降级，由 migrate_old_db 回滚拒启。
    """
    degraded = False
    if _same_drive(src, dst):
        try:
            os.replace(str(src), str(dst))
            return
        except OSError as e:
            # PermissionError（EACCES/EPERM：源目录只读或目标被占用）与
            # EXDEV 降级复制；其余 OSError 维持原语义上抛
            # （真故障 → migrate_old_db 回滚 + 拒绝启动）
            if not (isinstance(e, PermissionError) or e.errno == errno.EXDEV):
                raise
            logger.warning(
                "搬迁 rename 失败（%s），降级为复制模式（源文件将保留）: %s", e, src)
            degraded = True
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
        if degraded:
            # 源目录只读：降级模式下属预期行为，INFO 说明而非误导性告警
            logger.info("源目录只读，源文件按设计保留（无需手工删除）: %s", src)
        else:
            logger.warning("搬迁完成但删除源文件失败（请手动删除）: %s", src)


def _marker_path(old_main: Path) -> Path:
    return old_main.with_name(old_main.name + _MARKER_SUFFIX)


def _write_marker(old_main: Path) -> None:
    """成功搬迁后，源仍在（降级复制/删源失败）时在源旁写 .migrated 标记

    防「数据卷被清 → 陈旧源复活」：下次启动若目标库又消失而源与标记
    仍在且指纹一致，按陈旧源跳过搬迁（宁可空库+WARN，不用旧时点数据
    冒充）。best-effort：源目录只读（fnOS）写不进去则跳过——该场景无
    任何安装侧可写位置，复活风险由 README/CHANGELOG 明示，根治靠
    manifest version 修复（F6）。
    """
    marker = _marker_path(old_main)
    try:
        st = old_main.stat()
        marker.write_text(json.dumps({
            "source": str(old_main),
            "migrated_at": time.time(),
            "source_mtime_ns": st.st_mtime_ns,
            "source_size": st.st_size,
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.info("源侧迁移标记写入失败（源目录只读？），跳过: %s", marker)


def migrate_old_db(old_root: Path, db_path: Path) -> bool:
    """把 old_root/downloads.db(+附属文件) 搬迁到 db_path

    返回 True = 已搬迁；False = 无需搬迁（源不存在或目标已存在，含
    陈旧源标记命中）。
    搬迁失败（含回滚后）直接 SystemExit(1) 拒绝启动——数据安全优先，
    绝不静默换新空库。源目录只读/跨挂载点时自动降级复制（源保留）。
    """
    old_main = old_root / "downloads.db"
    if not old_main.exists() or db_path.exists():
        return False

    # 防陈旧源复活：源旁 .migrated 标记指纹命中（size+mtime_ns 与记录
    # 一致 = 正是上次已搬走的那个文件）→ 数据卷被清后的陈旧源，跳过并
    # 告警。指纹不一致（用户新放置的待迁移库）→ 照常搬迁，不误伤。
    rec = None
    try:
        rec = json.loads(_marker_path(old_main).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = None
    if isinstance(rec, dict):
        try:
            st = old_main.stat()
            if (rec.get("source_size") == st.st_size
                    and rec.get("source_mtime_ns") == st.st_mtime_ns):
                logger.warning(
                    "检测到已迁移过的陈旧源（数据卷可能被清空），跳过搬迁并以"
                    "新库启动；如需找回旧数据请手动复制 %s 到 %s", old_main, db_path)
                return False
        except OSError:
            pass

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
        # 倒序回滚已完成项。分流：源仍在 = 降级复制模式（源从未离开
        # 原位，源即权威）→ 仅删目标副本即恢复原状；源已消失 = 正常
        # 搬迁模式 → 搬回原位。此前不分流时，只读源回滚需在源目录
        # 创建 .migrating-tmp → 必然 PermissionError 被本层吞掉 →
        # 目标残留半套库 → 下次启动 db_path.exists() 跳过搬迁 →
        # 「不完整库启动」，正是 F1 要杜绝的语义。
        for src, dst in reversed(done):
            try:
                if src.exists():
                    dst.unlink(missing_ok=True)
                else:
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
    # 源仍在（降级复制/删源失败）时写防复活标记；正常搬迁源已删、无标记
    if old_main.exists():
        _write_marker(old_main)
    return True
