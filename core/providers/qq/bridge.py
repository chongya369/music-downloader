"""内置 qqmusic-api 二进制进程管理（跨平台单例）

负责拉起/停止预编译二进制（qqmusic-api-win-x64.exe / qqmusic-api-linux-x64），
对外提供幂等的 start/stop/status。单个进程仅监听 127.0.0.1 端口（默认 45602）。

与 netease/bridge.py 结构对齐，差异点：
- 二进制为 PyInstaller onefile 打包的 Flask 应用，监听地址经环境变量
  QQMUSIC_API_HOST / QQMUSIC_API_PORT 控制（非 PORT/HOST）
- 就绪探测走 /health 端点（服务自身端点，不碰上游）
- onefile 首次启动需自解压，就绪等待 timeout 默认 60s

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

# 平台 -> 二进制文件名
_BINARIES = {
    "win32": "qqmusic-api-win-x64.exe",
    "linux": "qqmusic-api-linux-x64",
}


class QqApiBridge:
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
        # 3s 轮询 /api/qq/status 全部挂起。仅读原子引用，瞬时不一致可接受。
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
                    f"未找到QQ音乐API二进制: {self.bin_path}\n"
                    "请将 qqmusic-api 对应平台版本放到 api/ 目录"
                )
            if sys.platform == "linux":
                self.bin_path.chmod(0o755)
            self.port = self._find_free_port(self._preferred_port)
            # 服务默认监听 0.0.0.0，内置 API 无鉴权，显式绑定 127.0.0.1
            # 避免暴露局域网（经 QQMUSIC_API_HOST/PORT 环境变量传入，
            # 该二进制不识别 PORT/HOST）
            env = {**os.environ,
                   "QQMUSIC_API_HOST": "127.0.0.1",
                   "QQMUSIC_API_PORT": str(self.port)}
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
        # 探测 /health（服务自身端点，不碰上游）；判定标准与状态码解耦：
        # 收到任意 HTTP 响应即视为服务已监听就绪，连接拒绝/超时才视为
        # 未就绪继续轮询。requests.get 对 4xx/5xx 不抛异常，仅连接层
        # 错误抛 RequestException。清空代理 env 只作用于子进程，此处需
        # 显式 proxies 强制直连 127.0.0.1。
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("QQ音乐API进程异常退出，请检查二进制完整性")
            try:
                requests.get(
                    f"{self.base_url}/health", timeout=2,
                    proxies={"http": None, "https": None},
                )
                return  # 收到任意 HTTP 响应即就绪
            except requests.exceptions.RequestException:
                pass  # 未就绪，继续轮询
            time.sleep(0.5)
        raise RuntimeError("QQ音乐API服务启动超时")


# ---------------- 模块级单例 ----------------
_bridge: QqApiBridge | None = None
_bridge_lock = threading.Lock()


def get_bridge(auto_start: bool | None = None, port: int | None = None) -> QqApiBridge:
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
                    # qq(0) → providers(1) → core(2) → source(3)
                    root = Path(__file__).resolve().parents[3]
                _bridge = QqApiBridge(
                    bin_dir=root / "api",
                    auto_start=(True if auto_start is None else auto_start),
                    port=(0 if port is None else port),
                )
    return _bridge
