"""内置 qqmusic-api 二进制进程管理（跨平台单例）

负责拉起/停止预编译二进制（qqmusic-api-win-x64.exe / qqmusic-api-linux-x64），
对外提供幂等的 start/stop/status。单个进程仅监听 127.0.0.1 端口（默认 45602）。

与 netease/bridge.py 结构对齐，差异点：
- 二进制为 PyInstaller onefile 打包的 FastAPI/uvicorn 应用，监听地址经环境变量
  QQMUSIC_SERVER_HOST / QQMUSIC_SERVER_PORT 控制（pydantic-settings，Env 优先级
  高于 exe 同目录 config.toml）
- 就绪探测走 / 根端点（服务自身端点，返回 {"code":0,...}；本服务无 /health）
- onefile 首次启动需自解压，就绪等待 timeout 默认 60s

此模块为基础设施层，不经过 Provider 抽象。
"""

import logging
import os
import shutil
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
        # 子进程日志（open_api_log 打开的文件句柄及路径，重启/停止时关闭）
        self._log_fh = None
        self._log_path: str | None = None

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
            # 服务默认监听 127.0.0.1（config.toml），环境变量显式覆盖以保证
            # 任意默认配置下都不暴露局域网（Env 优先级高于 config.toml）
            env = {**os.environ,
                   "QQMUSIC_SERVER_HOST": "127.0.0.1",
                   "QQMUSIC_SERVER_PORT": str(self.port)}
            for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                env.pop(k, None)
            # 运行时目录（cwd）：qqmusic-api 启动即在 cwd 下创建 web/data/
            # （device.json / credentials.sqlite3 / logs）。fpk 部署时 bin_dir
            # 位于 APPDEST（安装目录，只读或应用专用用户无写权限），写入失败
            # 会导致进程秒退（表现为"QQ音乐API进程异常退出"）。APP_DATA_DIR
            # 由 fnos 生命周期脚本注入为可写持久目录；未设置（源码运行/普通
            # 打包）保持 bin_dir 原行为。监听地址已由环境变量覆盖，config.toml
            # 缺失不影响启动，尽力复制一份以保留限流等配置。
            runtime_dir = self.bin_dir
            app_data = os.environ.get("APP_DATA_DIR")
            if app_data:
                candidate = Path(app_data).expanduser() / "qqmusic"
                try:
                    candidate.mkdir(parents=True, exist_ok=True)
                except OSError:
                    logger.warning("QQ音乐API运行时目录创建失败，回退 bin_dir: %s", candidate)
                else:
                    runtime_dir = candidate
                    cfg = self.bin_dir / "config.toml"
                    dst = runtime_dir / "config.toml"
                    if cfg.exists() and not dst.exists():
                        try:
                            shutil.copy2(cfg, dst)
                        except OSError:
                            pass  # 复制失败仅丢失自定义限流配置，不阻断启动
            # 子进程日志：写文件而非 DEVNULL——此前 stdout/stderr 全丢弃，
            # 进程秒退时真实死因（如只读目录写失败、glibc 不兼容）被吞掉
            self._close_log()
            log_fh, log_path = _proc.open_api_log("qqmusic-api")
            self._log_fh = log_fh
            self._log_path = log_path
            # spawn_protected 启用"父进程死亡即杀"（Win 作业对象 / Linux PDEATHSIG），
            # 下载器无论正常还是被强制退出，其启动的 API 进程都会被系统关闭
            self.proc = _proc.spawn_protected(
                [str(self.bin_path)], cwd=str(runtime_dir), env=env,
                stdout=log_fh if log_fh else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if log_fh else subprocess.DEVNULL,
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
            self._close_log()

    def _close_log(self) -> None:
        """关闭当前子进程日志句柄（重启/停止时复用，幂等）"""
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except OSError:
                pass
            self._log_fh = None

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
        # 探测 / 根端点（服务自身端点，返回 {"code":0,...}，不碰上游）；
        # 本服务无 /health。判定标准：收到 200 且响应体 code==0 才视为就绪，
        # 连接拒绝/超时才视为未就绪继续轮询。requests.get 对 4xx/5xx 不抛
        # 异常，需检查状态码。清空代理 env 只作用于子进程，此处需显式
        # proxies 强制直连 127.0.0.1。
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                tail = (f"（exit code={self.proc.returncode}，"
                        f"输出见 {self._log_path}）") if self._log_path else ""
                raise RuntimeError(f"QQ音乐API进程异常退出{tail}，请检查二进制完整性")
            try:
                resp = requests.get(
                    f"{self.base_url}/", timeout=2,
                    proxies={"http": None, "https": None},
                )
                if resp.status_code == 200 and (resp.json() or {}).get("code") == 0:
                    return  # 根端点返回标准成功响应即就绪
            except (requests.exceptions.RequestException, ValueError):
                pass  # 未就绪/非 JSON 响应，继续轮询
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
