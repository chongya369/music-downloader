"""内置 NeteaseCloudMusicApi-enhanced 进程管理（跨平台单例）

仅支持预编译二进制模式：api/ncm-api-win-x64.exe（Windows）/
api/ncm-api-linux-x64（Linux）。二进制内部已打包全部依赖，
开箱即用，无需额外安装任何东西。

对外提供幂等的 start/stop/status。单个进程仅监听 127.0.0.1 随机空闲端口。

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

logger = logging.getLogger(__name__)

# ---------------- 运行模式常量 ----------------
# 预编译二进制文件名（内部已打包全部依赖）
_BINARY_WIN = "ncm-api-win-x64.exe"
_BINARY_LINUX = "ncm-api-linux-x64"


class NodeBridge:
    def __init__(self, api_dir: Path, auto_start: bool = True, timeout: float = 60.0, port: int = 0):
        # api_dir 为 API 二进制所在目录（打包产物/开发期的 api/）
        self.api_dir = Path(api_dir).resolve()
        self.auto_start = auto_start
        self.timeout = timeout
        self._preferred_port = port
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.base_url: str | None = None
        self._lock = threading.Lock()
        # 恒定二进制模式
        self._mode = "binary"

    # ---------------- 运行模式辅助 ----------------

    def _binary_name(self) -> str:
        return _BINARY_WIN if sys.platform == "win32" else _BINARY_LINUX

    def _binary_path(self) -> Path | None:
        """查找预编译二进制，按优先级顺序探测：
        1) api_dir 本身（打包产物 api/ 目录、开发期 source/api/ 目录）
        2) frozen 模式下可执行文件同级 api/ 目录
        3) 项目根目录下 "依赖api二进制文件/"（开发期手工放二进制的常见位置）
        """
        root_candidates = []
        if getattr(sys, "frozen", False):
            root_candidates.append(Path(sys.executable).resolve().parent)
        else:
            # 开发环境：bridge.py 在 source/core/providers/netease/，上溯 3 级到 source
            dev_source_root = Path(__file__).resolve().parents[3]
            root_candidates.extend([
                dev_source_root,                        # source/
                dev_source_root.parent,                 # 项目根（music downloader/）
            ])

        candidates = [
            self.api_dir / self._binary_name(),                    # api/（主放置位置）
        ]
        for r in root_candidates:
            candidates.append(r / "api" / self._binary_name())
            candidates.append(r / "依赖api二进制文件" / self._binary_name())

        seen = set()
        for p in candidates:
            key = str(p.resolve()) if p.exists() else str(p)
            if key in seen:
                continue
            seen.add(key)
            if p.exists() and p.is_file():
                return p
        return None

    # ---------------- 状态查询 ----------------

    def status(self) -> dict:
        # 不加 self._lock——首次 start() 持锁最长 60s，共用锁会让设置页
        # 3s 轮询 /api/ncm/status 全部挂起。仅读原子引用，瞬时不一致可接受。
        p = self.proc

        bin_path = self._binary_path()
        bin_exists = bin_path is not None and bin_path.exists()
        exe_path = str(bin_path) if bin_path else ""

        return {
            "running": self._is_alive(),
            "port": self.port,
            "preferred_port": self._preferred_port,
            "base_url": self.base_url,
            "pid": p.pid if p else None,
            # 向后兼容：保留旧字段名
            "exe": exe_path,
            "bin_exists": bin_exists,
            "api_dir": str(self.api_dir),
            "platform": sys.platform,
            "auto_start": self.auto_start,
            "mode": self._mode,                  # 恒定 "binary"
            "auto_installable": False,
        }

    # ---------------- 启动 ----------------

    def start(self) -> str:
        """启动（幂等），返回 base_url；失败抛 RuntimeError（中文原因）"""
        with self._lock:
            if self._is_alive():
                return self.base_url
            self.proc = None      # 清理上次退出/失败的残留引用
            self.port = None
            self.base_url = None

            return self._start_binary()

    def _start_binary(self) -> str:
        """预编译二进制模式：直接启动 exe，无需额外依赖"""
        bin_path = self._binary_path()
        if bin_path is None:
            raise RuntimeError(
                "未找到 API 预编译二进制文件，请检查 api/ 目录下是否存在:\n"
                f"  {self._binary_name()}"
            )
        # 查找空闲端口
        self.port = self._find_free_port(self._preferred_port)
        # 上游机制以 PORT/HOST 环境变量控制监听地址；--port/--host 为兼容写法，
        # 若目标二进制不认参数，仍以 PORT/HOST 环境变量生效。
        cmd = [
            str(bin_path),
            "--port", str(self.port),
            "--host", "127.0.0.1",
        ]
        # 构造 env：清代理，设置 PORT/HOST 兜底
        env = {**os.environ, "PORT": str(self.port), "HOST": "127.0.0.1"}
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            env.pop(k, None)

        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        # cwd = exe 所在目录（api/），保证相对路径行为一致

        # Linux 下二进制可能缺少可执行权限：提前给出友好提示，避免裸崩。
        # Windows 下文件损坏/被占用同样会抛 OSError，一并兜底。
        if sys.platform != "win32" and not os.access(bin_path, os.X_OK):
            raise RuntimeError(
                f"API 二进制文件缺少执行权限，请先执行:\n"
                f"  chmod +x {bin_path}"
            )
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(bin_path.parent), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
            )
        except OSError as e:
            raise RuntimeError(
                f"无法启动 API 二进制文件（{bin_path}）：{e}\n"
                f"请确认文件完整且具有执行权限。"
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
        logger.info("API 服务（二进制模式）已启动: %s", self.base_url)
        return self.base_url

    # ---------------- 停止 ----------------

    def stop(self) -> None:
        """停止（幂等）"""
        with self._lock:
            self._kill_proc()
            self.proc = None
            self.port = None
            self.base_url = None

    # ---------------- 进程控制 ----------------

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
                raise RuntimeError("API 进程异常退出，请检查 API 二进制文件与运行环境")
            try:
                requests.get(
                    f"{self.base_url}/", timeout=2,
                    proxies={"http": None, "https": None},
                )
                return  # 收到任意 HTTP 响应即就绪
            except requests.exceptions.RequestException:
                pass  # 未就绪，继续轮询
            time.sleep(0.5)
        raise RuntimeError("API 服务启动超时")


# ---------------- 模块级单例 ----------------
_bridge: NodeBridge | None = None
_bridge_lock = threading.Lock()


def get_bridge(auto_start: bool | None = None, port: int | None = None) -> NodeBridge:
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
                    # netease(0) → providers(1) → core(2) → source(3)
                    root = Path(__file__).resolve().parents[3]
                _bridge = NodeBridge(
                    api_dir=root / "api",
                    auto_start=(True if auto_start is None else auto_start),
                    port=(0 if port is None else port),
                )
    return _bridge
