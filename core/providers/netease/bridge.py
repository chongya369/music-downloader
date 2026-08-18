"""内置 NeteaseCloudMusicApi-enhanced 进程管理（跨平台单例）

支持两种运行模式（自动探测，二进制优先）：
  A. 预编译二进制模式：api/ncm-api-win-x64.exe（Windows）/ api/ncm-api-linux-x64（Linux）
     —— 内部已打包 Node.js + 全部依赖，开箱即用，无需额外安装任何东西。
  B. Node.js 模式：node api/ncm/run.js
     —— esbuild 构建产物，运行前需安装 4 个外部包（jsdom / pac-proxy-agent / tunnel /
     unblockmusic-utils）。安装用 staging 目录隔离（npm 的 cwd 永远指向只含精简
     清单的 staging，绝不读 ncm/package.json 的 14 个依赖），任何场景只装 4 包。

对外提供幂等的 start/stop/status。单个进程仅监听 127.0.0.1 随机空闲端口。

此模块为基础设施层，不经过 Provider 抽象。
"""

import glob
import json
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

logger = logging.getLogger(__name__)

# ---------------- 运行模式常量 ----------------
# esbuild 构建产物入口文件（Node.js 模式）
ENTRY_POINT = "run.js"
# 预编译二进制文件名（pkg 打包产物，优先使用）
_BINARY_WIN = "ncm-api-win-x64.exe"
_BINARY_LINUX = "ncm-api-linux-x64"

# 依赖安装完成标志
_DEPS_MARKER = "node_modules"
# 精简运行时依赖清单文件名（build.py 的 post_pack 会生成；缺失时按 _RUNTIME_DEPS 现生成）
_RUNTIME_PKG_JSON = "package.runtime.json"
# 运行时真正外部依赖（app.js 中未内联的裸 require 的包）
# 版本范围与 ncm/package.json 严格对齐（unblockmusic-utils ^0.4.0 等）
_RUNTIME_DEPS = [
    "@neteasecloudmusicapienhanced/unblockmusic-utils",  # 付费/解锁歌曲（fee∈{1,4}）
    "jsdom",
    "pac-proxy-agent",
    "tunnel",
]


class NodeBridge:
    def __init__(self, ncm_dir: Path, auto_start: bool = True, timeout: float = 60.0, port: int = 0):
        self.ncm_dir = Path(ncm_dir).resolve()
        self.entry_path = self.ncm_dir / ENTRY_POINT
        # 回退依赖缓存目录；非空表示 npm 装在用户缓存区，start() 需注入 NODE_PATH
        self._deps_dir: Path | None = None
        self.auto_start = auto_start
        self.timeout = timeout
        self._preferred_port = port
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.base_url: str | None = None
        self._lock = threading.Lock()
        # 探测运行模式：预编译二进制优先，否则回退 Node.js
        self._mode = self._detect_mode()

    # ---------------- 运行模式辅助 ----------------

    def _binary_name(self) -> str:
        return _BINARY_WIN if sys.platform == "win32" else _BINARY_LINUX

    def _binary_path(self) -> Path | None:
        """查找预编译二进制，按优先级顺序探测：
        1) ncm_dir.parent（打包产物 api/ 目录、开发期 source/api/ 目录）
        2) ncm_dir 本身
        3) frozen 模式下可执行文件同级 api/ 目录（与第 1 条本质相同，显式列出更直观）
        4) 项目根目录下 "依赖api二进制文件/"（开发期手工放二进制的常见位置）
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
            self.ncm_dir.parent / self._binary_name(),                  # api/
            self.ncm_dir / self._binary_name(),                         # api/ncm/
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

    def _detect_mode(self) -> str:
        """返回 "binary"（优先，开箱即用）或 "nodejs"（回退，需 Node.js + npm 依赖）"""
        return "binary" if self._binary_path() is not None else "nodejs"

    # ---------------- 状态查询 ----------------

    def _find_node_path(self) -> str | None:
        """查找 node 可执行文件路径：先搜 PATH，再搜常见安装路径

        NAS 系统（fnOS/群晖/QNAP 等）常将 Node.js 装在非标准路径，
        非交互式进程的 PATH 不包含该路径，导致 shutil.which("node") 失败。
        此方法在 PATH 搜索失败后，用 glob 模式扫描各 NAS 的典型安装路径。

        找到非标准路径的 node 时，自动将其 bin 目录加入 PATH，
        使后续 shutil.which("npm") 等调用也能正常工作。
        """
        # 1. PATH 搜索（标准路径无需额外处理）
        node = shutil.which("node")
        if node:
            return node

        # 2. 常见安装路径搜索
        candidates = []
        if sys.platform == "win32":
            candidates.extend([
                r"C:\Program Files\nodejs\node.exe",
                r"C:\Program Files (x86)\nodejs\node.exe",
            ])
        else:
            candidates.extend([
                "/usr/bin/node",
                "/usr/local/bin/node",
                "/usr/local/node/bin/node",
                "/opt/nodejs/bin/node",
                "/opt/node/bin/node",
                "/opt/homebrew/bin/node",
            ])
            # fnOS: /vol*/@appcenter/nodejs*/bin/node
            candidates.extend(glob.glob("/vol*/@appcenter/nodejs*/bin/node"))
            # 群晖 Synology: /volume*/@appstore/Node*/usr/local/bin/node
            candidates.extend(glob.glob("/volume*/@appstore/Node*/usr/local/bin/node"))
            # 群晖（备选）: /var/packages/Node*/target/usr/local/bin/node
            candidates.extend(glob.glob("/var/packages/Node*/target/usr/local/bin/node"))
            # QNAP: /opt/QNAP/nodejs*/bin/node
            candidates.extend(glob.glob("/opt/QNAP/nodejs*/bin/node"))
            # nvm: ~/.nvm/versions/node/*/bin/node
            candidates.extend(glob.glob(str(Path.home() / ".nvm/versions/node/*/bin/node")))
            # n 版本管理器: /usr/local/n/versions/node/*/bin/node
            candidates.extend(glob.glob("/usr/local/n/versions/node/*/bin/node"))

        for p in candidates:
            path = Path(p)
            if path.exists() and path.is_file():
                # 将 bin 目录加入 PATH，使 npm 等工具也能被发现
                bin_dir = str(path.parent)
                current_path = os.environ.get("PATH", "")
                if bin_dir not in current_path.split(os.pathsep):
                    os.environ["PATH"] = bin_dir + os.pathsep + current_path
                    logger.info("已将 Node.js bin 目录加入 PATH: %s", bin_dir)
                return str(path)

        return None

    def status(self) -> dict:
        # 不加 self._lock——首次 start() 持锁最长 60s，共用锁会让设置页
        # 3s 轮询 /api/ncm/status 全部挂起。仅读原子引用，瞬时不一致可接受。
        p = self.proc

        if self._mode == "binary":
            bin_path = self._binary_path()
            deps_ready = True  # 预编译二进制内部已打包所有依赖
            bin_exists = bin_path is not None and bin_path.exists()
            exe_path = str(bin_path) if bin_path else ""
            entry_ok = bin_exists
        else:
            # Node.js 模式：ncm 目录有 node_modules，或回退目录有 node_modules
            deps_ready = (self.ncm_dir / _DEPS_MARKER).exists()
            if self._deps_dir is not None:
                deps_ready = deps_ready or (self._deps_dir / _DEPS_MARKER).exists()
            bin_exists = self.entry_path.exists()
            exe_path = str(self.entry_path)
            entry_ok = self.entry_path.exists()

        node_found = self._find_node_path()
        node_available = node_found is not None
        npm_available = shutil.which("npm.cmd" if sys.platform == "win32" else "npm") is not None
        auto_installable = (
            self._mode == "nodejs"
            and node_available
            and npm_available
            and entry_ok
            and not deps_ready
        )

        return {
            "running": self._is_alive(),
            "port": self.port,
            "preferred_port": self._preferred_port,
            "base_url": self.base_url,
            "pid": p.pid if p else None,
            # 向后兼容：保留旧字段名
            "exe": exe_path,
            "bin_exists": bin_exists,
            # 运行环境
            "node_available": node_available,
            "node_path": node_found,
            "npm_available": npm_available,
            "deps_ready": deps_ready,
            "deps_dir": str(self._deps_dir) if self._deps_dir else None,
            "entry_exists": entry_ok,
            "ncm_dir": str(self.ncm_dir),
            "platform": sys.platform,
            "auto_start": self.auto_start,
            "mode": self._mode,                  # "binary" / "nodejs"
            "auto_installable": auto_installable,  # 是否可通过点击启动自动安装依赖
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

            if self._mode == "binary":
                return self._start_binary()
            else:
                return self._start_nodejs()

    def _start_binary(self) -> str:
        """预编译二进制模式：直接启动 exe，无需 Node.js / 依赖"""
        bin_path = self._binary_path()
        if bin_path is None:
            raise RuntimeError(
                "未找到 API 预编译二进制文件，请检查 api/ 目录下是否存在:\n"
                f"  {self._binary_name()}"
            )
        # 查找空闲端口
        self.port = self._find_free_port(self._preferred_port)
        # 构造启动命令（二进制接收与 run.js 相同的 --port/--host 参数）
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
        # cwd = exe 所在目录（api/），保证相对路径行为与 Node.js 模式一致
        self.proc = subprocess.Popen(
            cmd, cwd=str(bin_path.parent), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
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

    def _start_nodejs(self) -> str:
        """Node.js 模式：node run.js，必要时自动安装 4 个外部依赖"""
        # 1. 检查 Node.js 是否可用
        node_path = self._check_node()

        # 2. 检查 ncm 目录及入口文件
        if not self.entry_path.exists():
            raise RuntimeError(
                f"未找到 API 入口文件: {self.entry_path}\n"
                "请确保 api/ncm/ 目录完整"
            )

        # 3. 检查并安装 npm 依赖（含只读目录回退）
        #    副作用：self._deps_dir 非空表示依赖装在用户缓存区（回退）
        self._ensure_deps()

        # 4. 查找空闲端口
        self.port = self._find_free_port(self._preferred_port)

        # 5. 构造启动命令
        cmd = [
            node_path,
            str(self.entry_path),
            "--port", str(self.port),
            "--host", "127.0.0.1",
        ]

        # 6. 构造 env
        env = {**os.environ, "PORT": str(self.port), "HOST": "127.0.0.1"}
        # 清代理（避免直连 127.0.0.1 走代理）
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            env.pop(k, None)
        # 回退时注入 NODE_PATH：依赖实际在 deps_dir/node_modules 下，
        # 而 Node.js 的 require 会在 NODE_PATH 每个条目直接找模块（不自动追加
        # node_modules 后缀），故必须指到 node_modules 一级。
        if self._deps_dir is not None:
            nm_path = self._deps_dir / _DEPS_MARKER
            existing = env.get("NODE_PATH", "")
            if existing:
                env["NODE_PATH"] = f"{existing}{os.pathsep}{nm_path}"
            else:
                env["NODE_PATH"] = str(nm_path)
            logger.info("启用回退依赖缓存: NODE_PATH=%s", env["NODE_PATH"])

        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            cmd, cwd=str(self.ncm_dir), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
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
        logger.info("API 服务（Node.js 模式）已启动: %s", self.base_url)
        return self.base_url

    # ---------------- 停止 ----------------

    def stop(self) -> None:
        """停止（幂等）"""
        with self._lock:
            self._kill_proc()
            self.proc = None
            self.port = None
            self.base_url = None

    # ---------------- 依赖安装（仅 Node.js 模式） ----------------

    def _ensure_deps(self) -> None:
        """确保 npm 依赖已安装（仅 4 个外部包，绝不装 14 个）

        1) ncm_dir/node_modules 已存在 → 直接使用（含 dev 期手工安装）
        2) 决定依赖落点 deps_dir：ncm_dir 可写 → ncm_dir；否则 → cache_dir
        3) 若 deps_dir/node_modules 缺失 → 用 staging 隔离安装（只装 4 包）
        """
        assert self._mode == "nodejs", "_ensure_deps 仅用于 Node.js 模式"

        # 1) 已装好（dev 期手工 npm install 的情况）
        if (self.ncm_dir / _DEPS_MARKER).exists():
            self._deps_dir = None
            return

        # 2) 决定 deps_dir 与是否需要 NODE_PATH
        if self._is_writable(self.ncm_dir):
            deps_dir = self.ncm_dir
            self._deps_dir = None          # node 在 ncm_dir 启动，能自然解析，无需 NODE_PATH
        else:
            deps_dir = self._get_deps_cache_dir()
            deps_dir.mkdir(parents=True, exist_ok=True)
            self._deps_dir = deps_dir      # 需 start() 注入 NODE_PATH=<deps_dir>/node_modules
            logger.warning("ncm 目录不可写，依赖将安装到缓存目录: %s", deps_dir)

        # 3) 未装 → staging 隔离安装（关键：npm cwd 是 staging，绝不读 ncm/package.json）
        if not (deps_dir / _DEPS_MARKER).exists():
            self._install_runtime_deps(deps_dir)

    def _install_runtime_deps(self, deps_dir: Path) -> None:
        """在 staging 目录执行 npm，把 node_modules 落到 deps_dir

        - cwd = staging（只有精简 4 依赖清单）→ npm 读它，只装 4 包
        - --prefix deps_dir → node_modules 生成到 deps_dir 下
        - 这样 deps_dir 自身的 package.json（ncm_dir 的 14 依赖版）永远不被读取
        """
        stage = self._ensure_stage_manifest()
        cmd = (
            self._npm_base_cmd()
            + ["install", "--production", "--ignore-scripts", "--prefix", str(deps_dir)]
        )
        logger.warning("API 依赖未安装，正在安装 4 个运行时外部包到 %s: %s",
                       deps_dir, ", ".join(_RUNTIME_DEPS))
        self._exec_npm(cmd, cwd=stage)

    def _ensure_stage_manifest(self) -> Path:
        """确保 staging 目录存在且只有精简 4 依赖清单，返回其路径"""
        stage = self._npm_stage_dir()
        stage.mkdir(parents=True, exist_ok=True)
        manifest = stage / "package.json"
        manifest.write_text(
            json.dumps({
                "name": "ncm-runtime",
                "private": True,
                "dependencies": {p: "*" for p in _RUNTIME_DEPS},
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        return stage

    def _npm_stage_dir(self) -> Path:
        """staging 目录：固定在用户可写缓存区下，与 deps_dir 解耦"""
        return self._get_deps_cache_dir() / "stage"

    def _get_deps_cache_dir(self) -> Path:
        """用户目录下的依赖缓存路径（回退落点 + staging 宿主）"""
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return base / "music_downloader" / "ncm_modules"

    def _is_writable(self, path: Path) -> bool:
        """检查目录是否可写（touch 测试文件）"""
        try:
            test_file = path / ".write_test"
            test_file.touch()
            test_file.unlink()
            return True
        except (OSError, PermissionError):
            return False

    def _npm_base_cmd(self) -> list:
        """npm 命令公共部分：Windows 用 npm.cmd"""
        return ["npm.cmd" if sys.platform == "win32" else "npm"]

    def _clean_proxy_env(self) -> dict:
        """清代理 env（install/启动子进程都不能继承代理）"""
        clean_env = {**os.environ}
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            clean_env.pop(k, None)
        return clean_env

    def _exec_npm(self, cmd: list, cwd: Path) -> None:
        """执行 npm 命令（公共错误处理）

        注意：无论平台一律 shell=False。
        - Windows 下 npm.cmd 可通过 PATHEXT 机制由 subprocess 直接定位，无需经
          cmd.exe /c 嵌套，避免路径含空格时 cmd.exe 二次解析吞掉引号导致截断。
        - list 参数在 shell=False 时由 Python 的 list2cmdline + CreateProcess 正
          确处理空格/引号，稳定性更好。
        """
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                env=self._clean_proxy_env(),
                capture_output=True,
                text=True,
                timeout=300,
                shell=False,
            )
            if result.returncode != 0:
                logger.error("npm install 失败:\n%s", result.stderr)
                raise RuntimeError(
                    "API 依赖自动安装失败，请重试启动程序（自动安装只装 4 个外部包）。\n"
                    "极少数无法自动安装时可手动兜底（注意此命令在 api/ncm 下会连带安装\n"
                    "完整 package.json 依赖）:\n"
                    "  cd api/ncm && npm install --ignore-scripts --omit=dev "
                    "@neteasecloudmusicapienhanced/unblockmusic-utils jsdom pac-proxy-agent tunnel\n"
                    f"错误详情: {result.stderr}"
                )
            logger.info("npm install 完成")
        except subprocess.TimeoutExpired:
            raise RuntimeError("npm install 超时（超过 5 分钟），请检查网络后重试")
        except FileNotFoundError:
            raise RuntimeError("未检测到 npm 命令，请确保 Node.js 完整安装")

    def _check_node(self) -> str:
        """检查 Node.js 是否可用，返回 node 可执行文件路径"""
        node = self._find_node_path()
        if not node:
            raise RuntimeError(
                "未检测到 Node.js 运行时，请先安装 Node.js 18+\n"
                "下载地址：https://nodejs.org/"
            )
        logger.info("检测到 Node.js: %s", node)
        return node

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
                raise RuntimeError("API 进程异常退出，请检查入口文件与运行环境")
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
                    ncm_dir=root / "api" / "ncm",
                    auto_start=(True if auto_start is None else auto_start),
                    port=(0 if port is None else port),
                )
    return _bridge
