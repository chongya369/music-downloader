# 更新日志

## [0.2.0] - 2026-08-15

### 新增
- 内置 NeteaseCloudMusicApi-enhanced 官方预编译二进制，跨平台（Windows / Linux x64）自动选择对应版本
- Web 设置页新增"API 服务"卡片：实时状态、启动/停止按钮、端口配置（`ncm_api_port`）与 `auto_start` 开关
- 支持自定义 API 服务 URL（`use_custom_api_url` / `custom_api_url`，勾选时内置服务禁用）
- 新增 3 个后台接口：`GET /api/ncm/status`、`POST /api/ncm/start`、`POST /api/ncm/stop`
- 新增 GitHub Actions 自动构建（`.github/workflows/build.yml`）：推送 `v*` 标签时 Windows / Linux 双平台 onefile 打包并发布 Release
- Web 界面更名为 Deen音乐下载器

### 变更
- 移除 `api_url` 外部服务配置，删除相关代码与配置项，不再依赖外部启动的 Node 服务
- `NeteaseClient` 改为从内置服务动态解析 API 地址，未运行时幂等懒启动（`auto_start` 与"用到再拉"语义一致）
- `ncm_api_auto_start` 默认值改为 `false`：程序启动不主动拉起，首次业务请求经 `base_url` 自动拉起；退出时通过 `finally` + `atexit` 自动关闭，避免子进程残留
- Web 服务监听地址改为 `*:45600`（支持 `host:port` 格式，`*` 表示监听所有网卡；默认端口由 56700 调整为 45600）
- 打包脚本重构：`build_exe.py` / `build_exe.bat` 替换为跨平台 `build.py` + `build_win.bat` / `build_linux.sh`，产物名由 `NeteaseMusicDownloader` 改为 `music_downloader`，输出目录为 `dist/music_downloader/`
- 启动脚本（`run_web.bat` / `run_web.sh`）移除手动启动 Node / 检测 3000 端口逻辑，不再要求安装 Node.js
- README 同步更新技术栈、目录结构、前置条件、快速启动、配置说明及 FAQ 中与 Node / 端口 / API 配置相关的内容

### 修复
- 服务就绪探测改为请求根路由 `/`，判定标准与状态码解耦（任意 HTTP 响应即视为就绪），规避未认证请求返回 301 导致的误判
- 内置服务仅监听 `127.0.0.1` 并清空代理环境变量，避免局域网暴露与代理干扰

### 说明
- 内置服务二进制需从官方 Release 下载，重命名为 `ncm-api-win-x64.exe` / `ncm-api-linux-x64` 后放入 `source/api/`（该目录不入库）
