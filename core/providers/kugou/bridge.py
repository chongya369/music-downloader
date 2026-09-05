"""内置 kugou-api 二进制进程管理（跨平台单例）

负责拉起/停止预编译二进制（kugou_api_win.exe / kugou_api_linux），
对外提供幂等的 start/stop/status。单个进程仅监听 127.0.0.1 端口（默认 45603）。

与 netease/bridge.py 结构对齐，差异点：
- 二进制为 KuGouMusicApi（Node/Express）的 pkg 单文件打包产物，
  监听地址经 PORT/HOST 环境变量控制（与 ncm-api 相同）
- 就绪探测走 GET /（public/index.html 静态页，不碰上游）
- 注意：二进制文件名为用户实际放置的下划线风格
  （kugou_api_win.exe / kugou_api_linux），与 ncm-api-* 连字符风格不同，
  改名需同步修改 _BINARIES

2026-09-04 实测（PORT=45699 HOST=127.0.0.1）：
- server.js 正确读取 PORT/HOST，启动日志 "server running @ http://127.0.0.1:45699"
- GET / 返回 HTTP 200；/register/dev、/search、/song/url/new 等主链路全部可用
- /song/url/auth/merge 的 P0 崩溃缺陷在该打包版中已修复（bogus hash 触发
  "本次请求需要验证" 后服务仍存活），client 白名单仍禁调该接口作为二道防线

此模块为基础设施层，不经过 Provider 抽象。
"""

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from .. import _proc

logger = logging.getLogger(__name__)

# 平台 -> 二进制文件名（与 api/ 目录实际放置的文件名一致）
_BINARIES = {
    "win32": "kugou_api_win.exe",
    "linux": "kugou_api_linux",
}


class KuGouApiBridge:
    def __init__(self, bin_dir: Path, auto_start: bool = True, timeout: float = 60.0, port: int = 0):
        self.bin_dir = Path(bin_dir).resolve()
        self.bin_path = self.bin_dir / _BINARIES.get(sys.platform, "")
        self.auto_start = auto_start
        self.timeout = timeout
        self._preferred_port = port
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.base_url: str | None = None
        self._lock = threading.Lock()

    def status(self) -> dict:
        # 不加 self._lock——首次 start() 持锁最长 60s，共用锁会让设置页
        # 3s 轮询 /api/kugou/status 全部挂起。仅读原子引用，瞬时不一致可接受。
        # 局部快照 p = self.proc，避免与 stop() 并发时两次读 self.proc
        # 中间被置 None 而抛 AttributeError。
        p = self.proc
        return {
            "running": self._is_alive(),
            "port": self.port,
            "preferred_port": self._preferred_port,
            "base_url": self.base_url,
            "pid": p.pid if p else None,
            "exe": str(self.bin_path),
            "platform": sys.platform,
            "bin_exists": self.bin_path.exists(),
            "auto_start": self.auto_start,
        }

    def start(self) -> str:
        """启动（幂等），返回 base_url；失败抛 RuntimeError（中文原因）"""
        with self._lock:
            if self._is_alive():
                return self.base_url
            self.proc = None      # 清理上次退出/失败的残留引用
            self.port = None
            self.base_url = None
            if not self.bin_path.exists():
                raise RuntimeError(
                    f"未找到酷狗音乐API二进制: {self.bin_path}\n"
                    "请将 kugou-api 对应平台版本放到 api/ 目录"
                )
            if sys.platform == "linux":
                self.bin_path.chmod(0o755)
            self.port = self._find_free_port(self._preferred_port)
            # 显式 HOST=127.0.0.1——server.js 中 HOST 缺省为空字符串，
            # 等效监听所有网卡；内置 API 无鉴权，暴露局域网有安全风险
            env = {**os.environ, "PORT": str(self.port), "HOST": "127.0.0.1"}
            for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                env.pop(k, None)
            # spawn_protected 启用"父进程死亡即杀"（Win 作业对象 / Linux PDEATHSIG），
            # 下载器无论正常还是被强制退出，其启动的 API 进程都会被系统关闭
            self.proc = _proc.spawn_protected(
                [str(self.bin_path)], cwd=str(self.bin_dir), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.base_url = f"http://127.0.0.1:{self.port}"
            try:
                self._wait_ready(self.timeout)
            except Exception:
                self._kill_proc()
                self.proc = None
                self.port = None
                self.base_url = None
                raise
            return self.base_url

    def stop(self) -> None:
        """停止（幂等）"""
        with self._lock:
            self._kill_proc()
            self.proc = None
            self.port = None
            self.base_url = None

    def _kill_proc(self) -> None:
        """terminate → 等待 → kill（stop 与 start 失败路径复用）"""
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)

    def _is_alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _find_free_port(self, preferred: int = 0) -> int:
        """查找空闲端口；preferred 非 0 时优先尝试绑定指定端口，失败则回退随机端口"""
        if preferred > 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", preferred))
                    return preferred
                except OSError:
                    pass
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _wait_ready(self, timeout: float) -> None:
        # 探测根路由（纯本地静态，最快、不碰上游）；判定标准与状态码解耦：
        # 收到任意 HTTP 响应即视为服务已监听就绪（index.html 将来被移除返回
        # 404 同样证明服务活着），连接拒绝/超时才视为未就绪继续轮询。
        # requests.get 对 4xx/5xx 不抛异常，仅连接层错误抛 RequestException。
        # 同时清空代理 env 只作用于子进程，此处需显式 proxies 强制直连 127.0.0.1。
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("酷狗音乐API进程异常退出，请检查二进制完整性")
            try:
                requests.get(
                    f"{self.base_url}/", timeout=2,
                    proxies={"http": None, "https": None},
                )
                return  # 收到任意 HTTP 响应即就绪
            except requests.exceptions.RequestException:
                pass  # 未就绪，继续轮询
            time.sleep(0.5)
        raise RuntimeError("酷狗音乐API服务启动超时")


# ---------------- 模块级单例 ----------------
_bridge: KuGouApiBridge | None = None
_bridge_lock = threading.Lock()


def get_bridge(auto_start: bool | None = None, port: int | None = None) -> KuGouApiBridge:
    """获取全局唯一 bridge（不访问数据库，无需 app context）

    auto_start / port 由 main() 在 app context 内读出后显式传入；main() 总是
    首个调用者并传值，后续调用返回既有单例，参数被忽略。
    """
    global _bridge
    if _bridge is None:
        with _bridge_lock:
            if _bridge is None:
                # frozen: PyInstaller 打包后用 exe 同级目录作为根目录
                if getattr(sys, "frozen", False):
                    root = Path(sys.executable).resolve().parent
                else:
                    # kugou(0) → providers(1) → core(2) → source(3)
                    root = Path(__file__).resolve().parents[3]
                _bridge = KuGouApiBridge(
                    bin_dir=root / "api",
                    auto_start=(True if auto_start is None else auto_start),
                    port=(0 if port is None else port),
                )
    return _bridge
