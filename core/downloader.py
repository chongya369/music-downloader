"""歌曲下载器模块

负责：
- 文件名清洗与生成
- 断点续传下载（HTTP Range）
- 失败重试
- 已存在文件跳过

注意：下载去重改由数据库（models.Song）处理，本模块只负责文件下载本身。
"""

import logging
import re
import threading
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')
_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")

# Windows 保留设备名（大小写不敏感）
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """清洗文件名：去除非法字符、首尾空格、限制长度"""
    name = _INVALID_CHARS.sub("_", name)
    name = _CONTROL_CHARS.sub("", name)
    name = name.strip().strip(".")
    # 检查 Windows 保留名
    if name.upper() in _WINDOWS_RESERVED:
        name = "_" + name
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "未知"


def _fit_path(path: Path, max_path_len: int = 240) -> Path:
    """如果路径超过 Windows MAX_PATH 限制（260），自动截断文件名

    Args:
        path: 目标路径
        max_path_len: 最大允许路径长度（默认 240，留 20 字符安全余量）

    Returns:
        调整后的路径（如无超长则原样返回）
    """
    full = str(path)
    if len(full) <= max_path_len:
        return path

    parent = path.parent
    name = path.name
    stem = path.stem
    suffix = path.suffix

    parent_len = len(str(parent)) + 1
    available = max_path_len - parent_len - len(suffix)

    if available <= 10:
        logger.warning("路径目录部分过长，无法截断文件名: %s", full)
        return path

    if len(stem) > available:
        original = stem
        stem = stem[:available].rstrip()
        truncated = parent / (stem + suffix)
        logger.info("路径过长，截断文件名: %s -> %s", name, truncated.name)
        return truncated

    return path


def build_filename(artist: str, title: str, ext: str) -> str:
    """生成文件名：歌手 - 歌名.ext"""
    artist = sanitize_filename(artist) if artist else "未知歌手"
    title = sanitize_filename(title) if title else "未知歌曲"
    return f"{artist} - {title}.{(ext or 'mp3').lower()}"


class Downloader:
    """文件下载器，支持断点续传与重试"""

    def __init__(
        self,
        output_dir: str | Path,
        chunk_size: int = 64 * 1024,
        max_retries: int = 3,
        timeout: int = 30,
        overwrite: bool = False,
        max_total_seconds: int = 900,
        idle_timeout: int = 60,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_size = chunk_size
        self.max_retries = max_retries
        self.timeout = timeout
        self.overwrite = overwrite
        # 兜底保护：单首下载总时长上限 + 无数据空闲上限，防 CDN 慢速/静默拖死任务
        self.max_total_seconds = max_total_seconds
        self.idle_timeout = idle_timeout

    def _safe_get(self, url: str, headers: dict) -> requests.Response:
        """带 DNS 挂起保护的 GET 请求

        requests 的 timeout 只覆盖 TCP 连接与读取，不覆盖 DNS 解析
        （socket.getaddrinfo 会无限阻塞）。这里把请求放入 daemon 线程，
        外层等待 timeout + 10s，超时视为 DNS/连接挂起并抛 requests.Timeout，
        避免单首歌"永久下载中"拖死整个下载队列。
        """
        box: dict = {}
        done = threading.Event()

        def run() -> None:
            try:
                box["resp"] = requests.get(url, headers=headers, stream=True, timeout=self.timeout)
            except Exception as e:  # 任何异常都回传主流程处理
                box["err"] = e
            finally:
                done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        # 等待 timeout + 10s：请求本身耗时已含 TCP timeout，多出的 10s 用于 DNS 解析
        if not done.wait(self.timeout + 10):
            raise requests.Timeout(f"请求超时（>{self.timeout + 10}s，可能 DNS 解析挂起）: {url}")
        if "err" in box:
            raise box["err"]
        return box["resp"]

    def target_path(self, sub_dir: str | None, filename: str) -> Path:
        base = self.output_dir
        if sub_dir:
            base = base / sanitize_filename(sub_dir)
            base.mkdir(parents=True, exist_ok=True)
        path = base / filename
        # 路径长度保护：自动截断过长的文件名
        path = _fit_path(path)
        return path

    def download(
        self,
        url: str,
        sub_dir: str | None,
        filename: str,
        expected_size: int | None = None,
        progress_callback=None,
    ) -> Path | None:
        """下载文件到指定子目录，支持断点续传

        Args:
            url: 音频文件直链
            sub_dir: 子目录（歌单名），None 表示根目录
            filename: 目标文件名
            expected_size: 预期文件大小（字节），用于校验
            progress_callback: 可选的进度回调 callback(downloaded_bytes, total_bytes)

        Returns:
            下载完成的文件路径，失败返回 None
        """
        try:
            target = self.target_path(sub_dir, filename)

            if target.exists() and not self.overwrite:
                if expected_size and abs(target.stat().st_size - expected_size) > 1024:
                    logger.warning("文件已存在但大小不符，重新下载: %s", target.name)
                else:
                    logger.info("跳过已存在: %s", target.relative_to(self.output_dir))
                    return target

            # 检查路径长度，防止临时文件路径溢出
            tmp = target.with_suffix(target.suffix + ".part")
            if len(str(tmp)) > 260:
                logger.warning("临时文件路径过长(%d字符)，尝试截断: %s", len(str(tmp)), tmp)
                tmp = _fit_path(tmp, max_path_len=250)

            resume_pos = tmp.stat().st_size if tmp.exists() else 0
        except OSError as e:
            logger.error(
                "下载 %s 预处理阶段失败 [Errno %d]: %s (路径: %s)",
                filename, e.errno or 0, e, filename,
            )
            return None

        for attempt in range(1, self.max_retries + 1):
            try:
                headers = {}
                if resume_pos > 0:
                    headers["Range"] = f"bytes={resume_pos}-"

                # 416 表示 Range 越界（文件已完成或范围无效），需先关闭原连接，
                # 再无 Range 重试，避免在 with 块内重新赋值 resp 导致连接泄漏
                resp = self._safe_get(url, headers)
                if resp.status_code == 416:
                    resp.close()
                    resume_pos = 0
                    headers.pop("Range", None)
                    resp = self._safe_get(url, headers)

                try:
                    resp.raise_for_status()

                    total = int(resp.headers.get("Content-Length", 0))
                    if resume_pos > 0 and resp.status_code == 206:
                        total += resume_pos
                    mode = "ab" if resume_pos > 0 and resp.status_code == 206 else "wb"
                    if mode == "wb":
                        resume_pos = 0

                    downloaded = resume_pos
                    start_ts = time.monotonic()
                    last_activity = start_ts
                    with open(str(tmp), mode) as f:
                        for chunk in resp.iter_content(self.chunk_size):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if progress_callback:
                                    progress_callback(downloaded, total or None)
                            now = time.monotonic()
                            if chunk:
                                last_activity = now
                            # 兜底保护：无进度空闲 / 总时长超限立即中断本次尝试（走重试）
                            elif now - last_activity > self.idle_timeout:
                                raise requests.Timeout(
                                    f"下载空闲超过 {self.idle_timeout}s，中断本次尝试"
                                )
                            if now - start_ts > self.max_total_seconds:
                                raise requests.Timeout(
                                    f"下载总时长超过 {self.max_total_seconds}s，中断本次尝试"
                                )
                finally:
                    resp.close()

                actual = tmp.stat().st_size
                if expected_size and actual < expected_size - 1024:
                    raise IOError(f"文件大小不匹配: 期望 {expected_size}, 实际 {actual}")

                tmp.replace(target)
                logger.info("下载完成: %s", target.relative_to(self.output_dir))
                return target

            except OSError as e:
                logger.error(
                    "下载 %s 第 %d/%d 次失败 [Errno %d]: %s (路径: %s)",
                    filename, attempt, self.max_retries, e.errno or 0, e, target,
                )
            except (requests.RequestException, IOError) as e:
                logger.warning(
                    "下载 %s 第 %d/%d 次失败: %s",
                    filename, attempt, self.max_retries, e,
                )
            except Exception as e:
                logger.error(
                    "下载 %s 第 %d/%d 次失败 [未知异常]: %s (路径: %s)",
                    filename, attempt, self.max_retries, e, target,
                )
            if tmp.exists():
                resume_pos = tmp.stat().st_size
            if attempt < self.max_retries:
                time.sleep(1.5 * attempt)

        logger.error("下载失败，已达最大重试次数: %s", filename)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return None
