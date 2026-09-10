# Deen音乐下载器

基于 Flask 的多平台音乐下载器，内置网易云音乐、QQ 音乐与酷狗音乐完整支持。多账号管理、定时同步歌单、断点续传下载、元数据写入（封面/歌词/专辑）、Web 界面管理，并支持飞牛 fnOS 一键安装。

> 技术架构、目录结构、数据模型、API 接口等开发细节见 [docs/技术文档.md](docs/技术文档.md)。

## 功能特性

- **三平台支持**：网易云 / QQ 音乐 / 酷狗音乐账号管理、歌单同步与下载（酷狗支持扫码登录）
- **多账号调度**：接力 / 轮询两种模式，VIP 偏好过滤，单账号月额度 + 每小时限额管控
- **音质控制**：三平台独立音质设置，目标音质不可用时自动降档（hires → lossless → exhigh → standard）
- **定时同步**：多时间点 cron 触发 + 随机抖动延迟，歌单更新自动下载新歌
- **下载增强**：断点续传、失败重试、MP3/FLAC 元数据与封面歌词写入、路径长度保护
- **发现页**：官方排行榜、热门歌单、搜索歌曲/专辑、单曲/专辑批量下载
- **API 服务内置**：三平台 API 二进制随仓库内置，启动即自动拉起，无需外部搭建
- **部署灵活**：Windows / Linux 源码运行或单文件 exe，飞牛 fnOS 应用中心一键安装（统一网关接入）
- **Web 管理**：登录系统（管理员/普通用户）、下载历史、失败重试、账号 JSON 导入导出

## 安装与运行

### 方式一：飞牛 fnOS（推荐 NAS 用户）

1. 从 [Releases](https://github.com/chongya369/music-downloader/releases) 下载 `.fpk` 安装包
2. fnOS「应用中心 → 手动安装」上传 fpk 文件
3. 安装完成后从桌面或应用列表打开，经统一网关（`/app/music-downloader`）访问

数据库与下载目录自动落在 fnOS 持久化数据卷，升级覆盖安装数据不丢失。

### 方式二：Release 可执行文件

1. 下载对应平台 zip（`deen-music-downloader-v<版本号>-win-x64.zip` / `deen-music-downloader-v<版本号>-linux-x64.zip`）并解压
2. Windows 双击 `music_downloader.exe`；Linux 执行 `./music_downloader`
3. 浏览器访问 `http://localhost:45600`

产物已内置当前平台 API 二进制，无需任何手动配置。

### 方式三：源码运行

**Windows：** 双击 `run_web.bat`（自动创建虚拟环境、安装依赖、启动服务）

**Linux：**

```bash
chmod +x run_web.sh
./run_web.sh
```

手动方式（两平台通用）：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt    # Windows 为 .venv\Scripts\python.exe
.venv/bin/python webapp/app.py
```

> **macOS 说明**：内置 API 二进制仅提供 Windows / Linux x64 版本，macOS 需自行部署各平台 API 服务并在「设置」页通过「自定义 API 服务 URL」接入。

## 快速上手

1. **登录**：浏览器打开 `http://localhost:45600`（fnOS 经统一网关打开），默认账号 `admin / admin123`，**首次登录后立即修改密码**
2. **添加账号**：「账号管理」页添加音乐平台账号，粘贴浏览器 Cookie（网易云须含 `MUSIC_U`，QQ 须含 `uin`，酷狗须含 `token`；酷狗可直接扫码登录）
3. **添加歌单**：「歌单管理」页选择平台后粘贴歌单链接或 ID，按设置的同步时间自动同步
4. **下载**：等待定时同步，或在「发现」页搜索/榜单即时下载，进度见「下载」页

图文详细步骤见 [初次使用教程](docs/初次使用教程.md)（含各平台 Cookie 获取方法）。

## 配置说明

所有配置项存储在 SQLite 数据库 `settings` 表，通过 Web「设置」页修改。默认值定义在 [models.py](webapp/models.py) `DEFAULT_SETTINGS`：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `ncm_api_auto_start` | `true` | 是否随程序启动自动拉起 NeteaseCloudMusicApi 服务（`false` 为按需懒启动，重启生效） |
| `ncm_api_port` | `45601` | NeteaseCloudMusicApi 服务端口（0 表示随机空闲端口，运行中不可修改） |
| `use_custom_api_url` | `false` | 是否使用自定义 API 服务 URL（勾选时内置服务禁用） |
| `custom_api_url` | （空） | 自定义 API 服务 URL，`use_custom_api_url` 为 `true` 时生效 |
| `qq_api_auto_start` | `true` | 是否随程序启动自动拉起 QQ 音乐 API 服务（`false` 为按需懒启动，重启生效） |
| `qq_api_port` | `45602` | QQ 音乐 API 服务端口（0 表示随机空闲端口） |
| `use_custom_qq_api_url` | `false` | 是否使用自定义 QQ 音乐 API 服务 URL（勾选时内置服务禁用） |
| `qq_api_base_url` | `http://127.0.0.1:45602` | 自定义 QQ 音乐 API 服务 URL，`use_custom_qq_api_url` 为 `true` 时生效 |
| `kugou_api_auto_start` | `true` | 是否随程序启动自动拉起酷狗音乐 API 服务（`false` 为按需懒启动，重启生效） |
| `kugou_api_port` | `45603` | 酷狗音乐 API 服务端口（0 表示随机空闲端口） |
| `use_custom_kugou_api_url` | `false` | 是否使用自定义酷狗音乐 API 服务 URL（勾选时内置服务禁用） |
| `kugou_api_base_url` | `http://127.0.0.1:45603` | 自定义酷狗音乐 API 服务 URL，`use_custom_kugou_api_url` 为 `true` 时生效 |
| `web_port` | `*:45600` | Web 服务监听地址（`host:port` 格式，如 `*:45600` 或 `127.0.0.1:45600`，`*` 表示所有网卡，修改后需重启服务） |
| `output_dir` | `downloads` | 下载输出目录（相对路径基于项目根目录；fnOS 网关模式首启自动固定到数据卷绝对路径） |
| `level_netease` | `exhigh` | 网易云音质：standard / exhigh / lossless / hires（未单独设置时回退旧全局 `level`） |
| `level_qq` | `exhigh` | QQ 音乐音质：standard / exhigh / lossless / hires（未单独设置时回退旧全局 `level`） |
| `level_kugou` | `exhigh` | 酷狗音乐音质：standard / exhigh / lossless / hires（未单独设置时回退旧全局 `level`） |
| `enable_quality_fallback` | `true` | 目标音质取不到流时是否自动向低音质档回退 |
| `write_metadata` | `true` | 是否写入元数据（标题/艺术家/专辑/封面/歌词） |
| `write_lyric` | `true` | 是否下载并写入歌词 |
| `auto_sync_enabled` | `true` | 是否启用定时同步 |
| `sync_times` | `03:00,09:00,21:00` | 定时同步时间点（HH:MM 逗号分隔） |
| `sync_jitter` | `600` | 同步抖动延迟（秒，0~N 随机延迟，避免固定时刻请求） |
| `max_retries` | `3` | 下载失败最大重试次数 |
| `default_playlist_limit` | `50` | 添加歌单时的默认下载数量 |
| `download_mode` | `fallback` | 多账号下载模式：fallback（接力）/ round_robin（轮询） |
| `prefer_non_vip` | `false` | 是否优先使用非 VIP 账号下载（仅 VIP 歌曲才用 VIP 账号） |
| `hourly_limit_per_account` | `50` | 单账号每自然小时下载成功上限（0=不限制） |
| `exclude_keywords` | （空） | 排除歌曲关键字（英文逗号分隔，如 `live,伴奏,remix`） |
| `exclude_scope` | `playlist,search` | 排除过滤应用范围：playlist / search / 两者 |

## 数据库位置

数据库为 SQLite 文件（固定文件名 `downloads.db`），位置按以下优先级确定：

| 优先级 | 方式 | 数据库位置 |
|--------|------|-----------|
| 1 | 启动参数 `--data-dir <目录>` | `<目录>/downloads.db` |
| 2 | 环境变量 `APP_DATA_DIR` | `$APP_DATA_DIR/downloads.db` |
| 3 | 缺省 | 打包运行：exe 同目录；源码运行：项目根目录 |

- 目录不存在会自动创建（支持多级）；相对路径按当前工作目录解析，支持 `~`
- `--data-dir` 与 `APP_DATA_DIR` 同时设置时，启动参数优先
- **旧库自动迁移**：指定的数据位置生效、且程序目录存在旧 `downloads.db` 时，首次启动自动搬迁（含 SQLite `-journal/-wal/-shm` 附属文件）；目标位置已有数据库时不会覆盖
- 启动日志会输出实际数据库路径（`数据库文件: ...`）

示例：

```bash
# 打包产物：指定数据目录
./music_downloader --data-dir /data/deen-music          # Linux
music_downloader.exe --data-dir D:\deen-data            # Windows

# 环境变量方式（适合 systemd / Docker 等不便改命令行的场景）
APP_DATA_DIR=/data/deen-music ./music_downloader
```

## 默认账号与密码重置

首次启动时自动创建管理员账号：`admin / admin123`。

忘记密码时可在服务器命令行执行重置脚本（[reset_password.py](webapp/reset_password.py)）：

```bash
# 重置 admin 密码为默认值 admin123
python webapp/reset_password.py

# 重置指定用户密码为指定值（数据库不在默认位置时加 --data-dir，也支持 APP_DATA_DIR 环境变量）
python webapp/reset_password.py 张三 newpass456
python webapp/reset_password.py --data-dir /data/deen-music 张三 newpass456
```

## 常见问题

### Q: 添加账号时提示"Cookie 必须包含 MUSIC_U"？
A: 从浏览器登录网易云后，打开 F12 → Network → 任意请求 → Request Headers → 复制完整 Cookie 字符串，其中必须包含 `MUSIC_U=xxx` 字段。各平台 Cookie 获取图文见 [初次使用教程](docs/初次使用教程.md)。

### Q: 下载失败提示"无版权或需 VIP"？
A: 该歌曲需要会员权限，请确保有可用的 VIP 账号。接力模式下会自动切换到 VIP 账号重试。

### Q: 下载失败提示"试听片段"？
A: 当前账号无该歌曲完整版权，只能拿到试听片段（已自动跳过不下载残缺文件）。接力模式会自动换更高权益账号；均不可用则标记失败。

### Q: 所有账号都提示"小时限额已满"？
A: 所有启用账号在当前自然小时内的成功下载数已达 `hourly_limit_per_account` 上限，系统会自动暂停 30 分钟后继续，无需手动干预。

### Q: QQ 音乐歌词无法下载？
A: QQ 音乐 API 服务端未提供歌词接口，属已知能力限制，下载时会跳过歌词，不影响音频文件与封面的写入。

### Q: 酷狗匿名下载只能 128kbps？
A: 酷狗音乐 API 对匿名（无登录 Cookie）请求强制封顶 128kbps MP3。如需 320/FLAC/Hi-Res 音质，请在「账号管理」页添加酷狗账号（支持扫码登录）。

### Q: 酷狗账号显示"酷狗未提供"到期时间 / 歌词无翻译？
A: 均为酷狗 API 上游限制——`/user/detail` 未返回到期时间字段，翻译歌词（tlyric）接口固定为空，不影响下载功能。

### Q: 修改 Web 监听地址后无法访问？
A: `web_port` 修改后需重启服务才生效（fnOS 在应用中心重启本应用；其余通过启动脚本或手动重启 `python webapp/app.py`）。

### Q: 路径过长导致下载失败？
A: 系统对路径长度有保护机制：非法字符自动清洗为 `_`、超过 240 字符自动截断文件名。若仍失败（如目录部分本身过长），请缩短 `output_dir` 路径。

## 打包与开发

跨平台一键打包为单文件可执行程序（onefile 模式，依赖全部内置）：

```bash
# Windows
build_win.bat

# Linux（POSIX sh）
chmod +x build_linux.sh && ./build_linux.sh
```

- 产物位于 `dist/music_downloader/`，自动复制 `api/` 三平台二进制并创建 `downloads/` 占位目录
- PyInstaller 不支持交叉编译，各平台产物须在对应平台构建
- 推送 `v*` 标签时 GitHub Actions 自动构建 Windows / Linux zip 与 fnOS `.fpk` 三份产物并发布 Release

更多开发细节（技术栈、目录结构、核心机制、数据模型、API 接口、运行模式）见 [docs/技术文档.md](docs/技术文档.md)。

## 版本

当前版本：**0.5.0**（见 [version.txt](version.txt)，更新日志见 [docs/CHANGELOG.md](docs/CHANGELOG.md)）
