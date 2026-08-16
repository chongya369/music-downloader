# 更新日志

## [0.2.0] - 2026-08-15

### 新增
- 内置 NeteaseCloudMusicApi-enhanced 官方预编译二进制，跨平台（Windows / Linux x64）自动选择对应版本
- Web 设置页新增"内置 API 服务"卡片：实时状态、启动/停止按钮、`auto_start`（随程序启动自动拉起）开关
- 新增 3 个后台接口：`GET /api/ncm/status`、`POST /api/ncm/start`、`POST /api/ncm/stop`

### 变更
- 移除 `api_url` 外部服务配置，删除相关代码与配置项，不再依赖外部启动的 Node 服务
- `NeteaseClient` 改为从内置服务动态解析 API 地址，未运行时幂等懒启动（`auto_start` 与"用到再拉"语义一致）
- 主程序启动时自动拉起内置服务，退出时通过 `finally` + `atexit` 自动关闭，避免子进程残留
- 启动脚本（`run_web.bat` / `run_web.sh`）移除手动启动 Node / 检测 3000 端口逻辑，不再要求安装 Node.js
- README 同步更新技术栈、结构、前置条件、快速启动及 FAQ 中与 Node 相关的内容

### 修复
- 服务就绪探测改为请求根路由 `/`，判定标准与状态码解耦（任意 HTTP 响应即视为就绪），规避未认证请求返回 301 导致的误判
- 内置服务仅监听 `127.0.0.1` 并清空代理环境变量，避免局域网暴露与代理干扰

### 说明
- 内置服务二进制需从官方 Release 下载，重命名为 `ncm-api-win-x64.exe` / `ncm-api-linux-x64` 后放入 `source/api/`（该目录不入库）