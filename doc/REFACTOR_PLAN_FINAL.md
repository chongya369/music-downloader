# 改造计划：内置 NeteaseCloudMusicApi-enhanced（最终版）

> 状态：最终方案（待你手动测试）
> 目标：程序内置 NeteaseCloudMusicApi-enhanced（官方预编译二进制），跨平台（Windows/Linux x64）自动选择二进制，Web 界面控制启动/停止，主程序退出自动关闭。
> 原则：**NeteaseCloudMusicApi-enhanced 源码零改动**，全部改动在 Python 侧 / Web 侧。

---

## 1. 已确认决策

| # | 决策 | 内容 |
|---|------|------|
| 1 | 二进制存放位置 | `source/api/`（放 `ncm-api-win-x64.exe` 与 `ncm-api-linux-x64`） |
| 2 | 启停模式 | 默认 `auto_start=true`（主程序启动自动拉起）；Web 可手动停止/重启；可在设置页改 `auto_start=false`（纯手动） |
| 3 | 移除 api_url 兼容 | **不保留**"填 api_url 走外部服务"逻辑，删除相关代码与配置 |
| 4 | 平台自动检测 | `sys.platform` 自动选择 win/linux 对应二进制 |
| 5 | 测试 | 不做自动化测试环境，由你后续手动验证 |
| 6 | 二进制说明 | 官方 Release 资产名与目标名不同，需重命名；单个约 40~80MB，`source/api/` 加入 `.gitignore` 不入库 |

---

## 2. 改造后架构

```
主程序（app.py main()）
  ├─ node_bridge.get_bridge() 单例
  │     win32 → source/api/ncm-api-win-x64.exe
  │     linux → source/api/ncm-api-linux-x64
  ├─ auto_start=true → 启动即自动拉起（幂等）
  │
  ├─ Web 控制（设置页"内置 API 服务"卡片）
  │     GET  /api/ncm/status   → 状态/端口/pid/二进制
  │     POST /api/ncm/start    → 启动
  │     POST /api/ncm/stop     → 停止
  │
  ├─ NeteaseClient → base_url 改为动态属性，每次请求从 bridge 解析（未运行则幂等拉起）
  │
  └─ 退出（finally + atexit）→ bridge.stop() 自动关闭
```

---

## 3. 配置文件与依赖

### 3.1 新增文件

| 文件 | 职责 |
|------|------|
| `source/core/node_bridge.py` | 跨平台二进制子进程管理（单例：start/stop/status） |

### 3.2 修改文件

| 文件 | 改动点 |
|------|--------|
| `source/core/netease_client.py` | `__init__` 去掉 `base_url` 参数，改为从 bridge 取地址；`session.trust_env=False` 代理隔离；`_request` 的 url 移入重试循环；删除旧默认 `http://localhost:3000`；更新模块 docstring |
| `source/webapp/routes/api.py` | 删除 3 处 `Setting.get("api_url", ...)`（L82/L113/L684）；新增 3 个 `/api/ncm/*` 路由；`_get_client` 改为 `NeteaseClient(cookie=...)` |
| `source/webapp/task_manager.py` | 删除 2 处 `Setting.get("api_url", ...)`（L390/L399），改为 `NeteaseClient(cookie=...)`；`_get_client_default` 保留 `app_context` 包裹 |
| `source/webapp/models.py` | `DEFAULT_SETTINGS` 删除 `"api_url"` 项；新增 `"ncm_api_auto_start": "true"` |
| `source/webapp/templates/settings.html` | 删除 `api_url` 输入框；新增"内置 API 服务"状态+启停按钮+auto_start 开关 |
| `source/webapp/static/js/settings.js` | 删除 `api_url` 读写；新增状态轮询与启停按钮逻辑；启停按钮走长超时/异步+轮询"启动中" |
| `source/webapp/app.py` | `main()` 初始化 bridge（app context 内读 auto_start 后传参）+ finally/atexit 清理；**atexit 注册写在 main() 内** |
| `source/run_web.bat` / `run_web.sh` | 删除"手动启动 Node/检测 3000 端口"逻辑；更新头部注释（不再要求 Node.js） |
| `source/README.md` | 删除 `api_url` 配置说明，改为内置二进制说明；同步更新技术栈/结构/前置条件/快速启动/FAQ 中所有 Node 相关内容 |
| `source/requirements.txt` | 更新第 2 行关于 Node 服务的注释 |

### 3.3 新建文件（发版）

| 文件 | 说明 |
|------|------|
| `source/CHANGELOG.md` | 发版时**新建**，仅保留当前版本记录 |

### 3.4 不改动

- api-enhanced 全部源码 / 二进制（0 改动）
- `core/downloader.py` / `metadata.py` / `language_detector.py`
- Python 依赖 `requirements.txt`（仅注释可改，不新增依赖）

---

## 4. 核心实现：core/node_bridge.py

```python
"""内置 NeteaseCloudMusicApi-enhanced 二进制进程管理（跨平台单例）"""

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

# 平台 -> 二进制文件名
_BINARIES = {
    "win32": "ncm-api-win-x64.exe",
    "linux": "ncm-api-linux-x64",
}


class NodeBridge:
    def __init__(self, bin_dir: Path, auto_start: bool = True, timeout: float = 60.0):
        self.bin_dir = Path(bin_dir).resolve()
        self.bin_path = self.bin_dir / _BINARIES.get(sys.platform, "")
        self.auto_start = auto_start
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.base_url: str | None = None
        self._lock = threading.Lock()

    def status(self) -> dict:
        # 不加 self._lock——首次 start() 持锁最长 60s，共用锁会让设置页
        # 3s 轮询 /api/ncm/status 全部挂起。仅读原子引用即可。
        # 局部快照 p = self.proc，避免与 stop() 并发时两次读 self.proc
        # 中间被置 None 而抛 AttributeError。
        p = self.proc
        return {
            "running": self._is_alive(),
            "port": self.port,
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
                    f"未找到 API 二进制: {self.bin_path}\n"
                    "请从官方 Release 下载对应平台版本放到 source/api/ 目录"
                )
            if sys.platform == "linux":
                self.bin_path.chmod(0o755)
            self.port = self._find_free_port()
            # 显式 HOST=127.0.0.1——server.js 中 HOST 缺省为空字符串，
            # 等效监听所有网卡；内置 API 无鉴权，暴露局域网有安全风险
            env = {**os.environ, "PORT": str(self.port), "HOST": "127.0.0.1"}
            for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                env.pop(k, None)
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self.proc = subprocess.Popen(
                [str(self.bin_path)], cwd=str(self.bin_dir), env=env,
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

    def _find_free_port(self) -> int:
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
                raise RuntimeError("API 进程异常退出，请检查二进制完整性")
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


def get_bridge(auto_start: bool | None = None) -> NodeBridge:
    """获取全局唯一 bridge（不访问数据库，无需 app context）"""
    global _bridge
    if _bridge is None:
        with _bridge_lock:
            if _bridge is None:
                root = Path(__file__).resolve().parent.parent
                _bridge = NodeBridge(
                    bin_dir=root / "api",
                    auto_start=(True if auto_start is None else auto_start),
                )
    return _bridge
```

> 说明：`auto_start` 由 `main()` 在 app context 内读出后显式传入；`main()` 总是首个调用者并传值，后续调用返回既有单例，参数被忽略。

---

## 5. NeteaseClient 改造

```python
class NeteaseClient:
    def __init__(self, cookie: str = ""):
        """持有 bridge 引用；API 地址每次请求时动态解析（见 base_url 属性）"""
        from core import node_bridge
        self._bridge = node_bridge.get_bridge()
        self.session = requests.Session()
        # session 默认 trust_env=True 会读主进程 http_proxy/https_proxy，
        # 目标 http://127.0.0.1:port 在内网直连，显式关闭避免业务请求全走代理。
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": _UA})
        if cookie:
            self.set_cookie(cookie)
        ...

    @property
    def base_url(self) -> str:
        """动态解析而非构造时缓存。

        - Web 停止→重启后端口会变（随机空闲端口），缓存旧地址会导致
          在途/后续请求全部失败
        - 服务未运行时经 start() 幂等拉起（与 auto_start=false 的
          "程序启动不拉起、用到再拉"语义一致）
        - _request 自带 3 次重试，拉起期间请求可自然恢复
        """
        return self._bridge.start().rstrip("/")

    def _request(self, path, method="GET", params=None, data=None,
                 retries=3, timeout=15) -> dict:
        """调用 NeteaseCloudMusicApi 接口"""
        for attempt in range(1, retries + 1):
            url = f"{self.base_url}{path}"   # 每次重试重新解析，重启端口漂移也能恢复
            try:
                if method.upper() == "GET":
                    resp = self.session.get(url, params=params, timeout=timeout)
                else:
                    resp = self.session.post(url, params=params, data=data, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as e:
                logger.warning("请求 %s 第 %d 次失败: %s", path, attempt, e)
                if attempt < retries:
                    time.sleep(1.5 * attempt)
        logger.error("请求 %s 失败，已重试 %d 次", path, retries)
        return {"code": -1, "msg": "request failed"}
```

**调用点统一**（api.py / task_manager.py）：

```python
# api.py（在请求上下文内调用，无需显式 app_context）
def _get_client() -> NeteaseClient:
    acc = Account.query.filter_by(enabled=True).order_by(Account.id).first()
    cookie = acc.cookie if acc else ""
    return NeteaseClient(cookie=cookie or "")

# task_manager.py
def _get_client_for_account(self, account) -> NeteaseClient:
    # 仅用传入的 account.cookie，不查库，无需 app_context
    return NeteaseClient(cookie=account.cookie or "")

def _get_client_default(self) -> NeteaseClient:
    # 此函数被调度线程调用（_sync_all_playlists 等），后台线程无 request context，
    # Account.query 必须包 app_context，否则抛 Working outside of application context
    with self.app.app_context():
        acc = Account.query.filter_by(enabled=True).order_by(Account.id).first()
        cookie = acc.cookie if acc else ""
    return NeteaseClient(cookie=cookie or "")
```

> 注：`NeteaseClient` 构造不再触发 `start()`，改为首次请求经 `base_url` 属性延迟触发；bridge 幂等，已运行时仅一次锁获取 + `poll()` 判断，开销可忽略。

---

## 6. Web 控制界面

### 6.1 路由（api.py 新增）

```python
@api_bp.route("/ncm/status", methods=["GET"])
def ncm_status():
    return jsonify({"code": 0, "data": node_bridge.get_bridge().status()})

@api_bp.route("/ncm/start", methods=["POST"])
def ncm_start():
    try:
        url = node_bridge.get_bridge().start()
        return jsonify({"code": 0, "msg": "API 服务已启动", "data": {"base_url": url}})
    except RuntimeError as e:
        return jsonify({"code": 1, "msg": str(e)}), 500

@api_bp.route("/ncm/stop", methods=["POST"])
def ncm_stop():
    node_bridge.get_bridge().stop()
    return jsonify({"code": 0, "msg": "API 服务已停止"})
```

### 6.2 设置页（settings.html "API 服务"卡片替换原 api_url 输入框）

```html
<div class="col-md-8">
  <label class="form-label">内置 API 服务状态</label>
  <div>
    <span id="ncm-status-badge" class="badge bg-secondary">未知</span>
    <span id="ncm-status-detail" class="text-muted small ms-2"></span>
  </div>
  <div class="form-check mt-2">
    <input type="checkbox" class="form-check-input" name="ncm_api_auto_start" id="ncm-auto-start" value="true">
    <label class="form-check-label" for="ncm-auto-start">随程序启动自动启动 API 服务</label>
    <div class="form-text">改动保存后需重启程序生效（同 web_port）</div>
  </div>
  <button type="button" id="btn-ncm-start" class="btn btn-sm btn-success mt-2">
    <i class="bi bi-play"></i> 启动 API 服务
  </button>
  <button type="button" id="btn-ncm-stop" class="btn btn-sm btn-danger mt-2">
    <i class="bi bi-stop"></i> 停止 API 服务
  </button>
</div>
```

### 6.3 settings.js 调整

- 删除 `form.api_url` 两处读写
- `loadSettings()` 增加 `form.ncm_api_auto_start.checked = s.ncm_api_auto_start !== "false"`
- 保存 payload 增加 `ncm_api_auto_start`
- 新增 `refreshNcmStatus()`（3s 轮询 `/api/ncm/status`；status 不加锁，启动期间轮询不会被阻塞）+ 两个按钮 click 事件

### 6.4 前端 15s 超时 vs 启动最长 60s

app.js 的 `api()` 封装 `setTimeout(..., 15000)` abort，而 `start()` 首次启动最长可达 60s。两条路径都要处理：
- **`/api/ncm/start` 手动启动**：该请求走单独的长超时（如 60s）或改为异步返回 + 前端轮询 `status` 显示"启动中"，避免 15s 就 abort。
- **懒启动**：auto_start=false 且手动停止后，首个**业务请求**触发 `start()` 也会被 15s 封装 abort（后台仍在启动）。此路径无法用"单独长超时"覆盖，接受首请求可能瞬时报错（前端可重试/用户再点一次）。

---

## 7. app.py 生命周期

```python
import atexit
from core import node_bridge

def main() -> None:
    port = _read_web_port()
    # Setting.get 需在 app context 内调用；读出后显式传入，
    # get_bridge 自身不碰数据库
    with app.app_context():
        auto_start = Setting.get("ncm_api_auto_start", "true") == "true"
    bridge = node_bridge.get_bridge(auto_start=auto_start)
    # atexit 注册必须写在 main() 内（此时单例已用真实 auto_start 创建）；
    # 若放模块顶层会在 import 时以默认 auto_start=True 先建单例，忽略用户配置
    atexit.register(bridge.stop)
    if bridge.auto_start:
        try:
            bridge.start()
            logger.info("内置 API 服务就绪: %s", bridge.base_url)
        except RuntimeError as e:
            logger.warning("API 服务启动失败: %s", e)
    task_manager.start()
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
    finally:
        task_manager.stop()
        bridge.stop()          # 主程序退出 -> 自动关闭 API 服务
```

---

## 8. 实施步骤（按序）

| 步骤 | 内容 | 验证（你手动） |
|------|------|----------------|
| 0 | 下载 win+linux 二进制放 `source/api/`（重命名为 `ncm-api-win-x64.exe` / `ncm-api-linux-x64`；`source/api/` 加入 `.gitignore` 不入库） | 文件存在且命名正确 |
| 1 | 编写 `core/node_bridge.py` | start → 探测 `/` 就绪 → stop 端口释放 |
| 2 | 改 `netease_client.py`（去掉 base_url；`session.trust_env=False`；`_request` 的 url 移入重试循环） | `NeteaseClient(cookie="")` 调 login_status 成功 |
| 3 | 改 `api.py` / `task_manager.py`（删 api_url，改调用；`_get_client_default` 保留 app_context） | Web 登录账号、发现页、同步、下载正常 |
| 4 | 改 `models.py` DEFAULT_SETTINGS | 设置页加载正常 |
| 5 | 新增 3 个 `/api/ncm/*` 路由 | curl 验证 |
| 6 | 改 `settings.html` / `settings.js`（启停按钮走长超时或异步+轮询"启动中"） | 页面显示状态、可启停、auto_start 开关 |
| 7 | 改 `app.py`（预热 + finally/atexit，atexit 注册写在 main() 内） | 启动自动拉起；退出无残留进程 |
| 8 | 改 `run_web.bat` / `run_web.sh` / `README.md` / `requirements.txt` 注释（run_web.sh 保持 POSIX sh 兼容 + LF 行尾）。README 除 L154 的 api_url 外，L35 技术栈表、L42 结构说明、L69 Node 前置、L73-75 快速启动第 1 步、L395 FAQ 均需删 Node 相关内容；run_web 头部注释同步更新 | 一键启动不再提示装 Node；`sh -n run_web.sh` 语法通过 |
| 9 | 全量回归 12 接口 + 停止/重启 API 场景（含：停止后立即调接口验证自动拉起与端口漂移后请求恢复；启动期间设置页轮询不卡顿；实测含 npm info 检查的首启耗时，据此校准 60s timeout） | 正常 |
| 10 | 发版收尾：更新 `VERSION`；**新建** `CHANGELOG.md` 并仅保留当前版本记录（项目硬约束，目前 source 无该文件） | 版本号正确 |

---

## 9. 风险与注意

| 项 | 说明 | 应对 |
|----|------|------|
| 二进制缺失 | 未下载则启动失败 | bridge 检查 + 下载指引；Web 状态显示"未找到" |
| Linux 权限 | pkg 产物默认无 x 位 | `chmod 0o755` |
| 首次启动慢 | 联网注册匿名 token | 就绪超时 60s；auto_start 预热 |
| 子进程残留 | 异常退出 | finally + atexit，stop 幂等；start 失败路径也 kill + 复位 |
| 代理环境变量 | 干扰请求 | spawn 时清空 http(s)_proxy；`_wait_ready` 探活显式 `proxies` 直连；`NeteaseClient.session.trust_env=False` 业务请求直连 |
| status 微竞态 | `self.proc.pid if self.proc else None` 与 stop() 并发可能抛 AttributeError | 局部快照 `p = self.proc` 单次读取 |
| 杀软误报 | pkg exe 可能被拦截 | 提示加白名单 |
| npm info 首启延迟 | app.js `checkVersion: true`，server.js `Promise.all` 后才 listen；`exec('npm info ...')` 联网访问 registry.npmjs.org，大陆网络可能慢（不 reject，只延迟，不硬阻断） | 步骤 9 实测含此检查的首启耗时，据此校准 60s timeout |
| 前端 15s 超时 | `/api/ncm/start` 或懒启动首业务请求可能被 app.js 15s abort | 启停按钮走长超时/异步+轮询"启动中"；懒启动首请求接受瞬时报错 |
| apicache 多账号缓存 | server.js `cache('2 minutes')` 缓存；但 key 含 `JSON.stringify(req.cookies)`（由 Cookie 头解析），本客户端走 Cookie 头 → 不同账号 key 不同，串缓存低危（上游，零改动不动） | 风险表记一笔；测试时留意账号测试回填是否被串 |
| Web 停止时任务中断 | 手动停止有进行中下载 | 提示确认；下载器已有重试；base_url 动态解析 + `_request` 3 次重试，重启后新请求自动恢复 |
| 局域网暴露 | server.js HOST 缺省监听所有网卡且无鉴权 | spawn env 强制 `HOST=127.0.0.1` |
| app context 崩溃 | `Setting.get` 在无 Flask context 时抛异常 | `get_bridge()` 不读库；main() 在 app context 内读后传参 |
| status 轮询阻塞 | start 持锁最长 60s | `status()` 不加锁，仅读原子引用 |
| 停止→重启端口漂移 | 已缓存 base_url 的客户端请求失败 | `base_url` 动态属性 + `_request` 重试（url 移入循环，重启中在途重试也能恢复） |
| auto_start 改动 | 运行中修改不生效 | UI 标注"重启后生效"（同 web_port） |