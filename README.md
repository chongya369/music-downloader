# Deen音乐下载器

> 相关文档：[部署说明](README.Docker.md) · [初次使用教程](docs/初次使用教程.md) · [常见问题](docs/常见问题.md) · [技术文档](docs/技术文档.md)

基于 Flask 的多平台音乐下载器，内置网易云音乐、QQ 音乐与酷狗音乐完整支持。多账号管理、定时同步歌单、断点续传下载、元数据写入（封面/歌词/专辑）、Web 界面管理，支持飞牛 fnOS 一键安装与 Docker 部署。

## 功能特性

- **三平台支持**：网易云 / QQ 音乐 / 酷狗音乐账号管理、歌单同步与下载，三平台均支持扫码登录
- **多账号调度**：接力 / 轮询两种模式，VIP 偏好过滤，单账号月额度 + 每小时限额管控
- **音质控制**：三平台独立音质设置，覆盖普通档与会员高阶档，目标音质取不到流时按平台降档链回退（机制细节见 [技术文档](docs/技术文档.md#音质降档链)）
- **定时同步**：多时间点触发 + 随机抖动延迟，歌单更新自动下载新歌
- **下载增强**：断点续传、失败重试、MP3/FLAC/OGG 元数据与封面歌词写入、文件名附加专辑防同名冲突、路径长度保护
- **任务控制**：任务逐个或一键暂停 / 继续 / 删除，暂停保留断点、继续断点续传
- **发现页**：官方排行榜、热门歌单、搜索歌曲/专辑、单曲/专辑批量下载
- **API 服务内置**：三平台 API 二进制随仓库内置，启动即自动拉起，无需外部搭建
- **多用户 Web 管理**：管理员/普通用户分级、首登强制改密、登录失败锁定、下载历史、失败重试、账号 JSON 导入导出
- **部署灵活**：Windows / Linux 源码运行或单文件 exe，飞牛 fnOS 应用中心一键安装，Docker / docker-compose 部署

## 安装与运行

### 方式一：飞牛 fnOS（推荐 NAS 用户）

1. 从 [Releases](https://github.com/chongya369/music-downloader/releases) 下载 `.fpk` 安装包
2. fnOS「应用中心 → 手动安装」上传 fpk 文件
3. 安装完成后从桌面或应用列表打开，经统一网关（`/app/deen-music-downloader`）访问

数据库与下载目录自动落在 fnOS 持久化数据卷，升级覆盖安装数据不丢失。

### 方式二：Release 可执行文件

1. 下载对应平台 zip（`deen-music-downloader-<版本号>-win-x64.zip` / `deen-music-downloader-<版本号>-linux-x64.zip`）并解压
2. Windows 双击 `deen-music-downloader.exe`；Linux 执行 `./deen-music-downloader`
3. 浏览器访问 `http://localhost:45600`

产物已内置当前平台 API 二进制，无需任何手动配置。

### 方式三：Docker

提供 `Dockerfile` 与 `docker-compose.yml`，构建、启动、数据持久化与常用命令见部署说明：[README.Docker.md](README.Docker.md)。

### 方式四：源码运行

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

1. **登录**：浏览器打开 `http://localhost:45600`（fnOS 经统一网关打开；Docker 为映射端口），默认账号 `admin / admin123`，**首次登录会强制要求修改密码**后方可使用
2. **添加账号**：「账号管理」页添加音乐平台账号，粘贴浏览器 Cookie（网易云须含 `MUSIC_U`，QQ 须含 `uin` 或 `musicid`，酷狗须含 `token`），或直接使用扫码登录（三平台均已支持，QQ 同时支持微信扫码）
3. **添加歌单**：「歌单管理」页选择平台后粘贴歌单链接或 ID，按设置的同步时间自动同步
4. **下载**：等待定时同步，或在「发现」页搜索/榜单即时下载，进度见「下载」页

图文详细步骤见 [初次使用教程](docs/初次使用教程.md)（含各平台 Cookie 获取方法）；遇到问题先查 [常见问题](docs/常见问题.md)。

## 数据库位置

数据库为 SQLite 文件（固定文件名 `downloads.db`），位置按以下优先级确定：

| 优先级 | 方式                     | 数据库位置                        |
| --- | ---------------------- | ---------------------------- |
| 1   | 启动参数 `--data-dir <目录>` | `<目录>/downloads.db`          |
| 2   | 环境变量 `APP_DATA_DIR`    | `$APP_DATA_DIR/downloads.db` |
| 3   | 缺省                     | 打包运行：exe 同目录；源码运行：项目根目录      |

- 目录不存在会自动创建（支持多级）；相对路径按当前工作目录解析，支持 `~`
- `--data-dir` 与 `APP_DATA_DIR` 同时设置时，启动参数优先
- **旧库自动迁移**：指定的数据位置生效、且程序目录存在旧 `downloads.db` 时，首次启动自动搬迁（含 SQLite `-journal/-wal/-shm` 附属文件）；搬迁失败会回滚并拒绝启动（避免静默用空库顶替导致"数据丢失"假象）；目标位置已有数据库时不会覆盖
- 启动日志会输出实际数据库路径（`数据库文件: ...`）

示例：

```bash
# 打包产物：指定数据目录
./deen-music-downloader --data-dir /data/deen-music     # Linux
deen-music-downloader.exe --data-dir D:\deen-data       # Windows

# 环境变量方式（适合 systemd / Docker 等不便改命令行的场景）
APP_DATA_DIR=/data/deen-music ./deen-music-downloader
```

## 默认账号与密码重置

首次启动时自动创建管理员账号：`admin / admin123`，首次登录强制修改密码。

忘记密码时可在服务器命令行执行重置脚本（[reset_password.py](webapp/reset_password.py)）：

```bash
# 重置 admin 密码为默认值 admin123
python webapp/reset_password.py

# 重置指定用户密码为指定值（数据库不在默认位置时加 --data-dir，也支持 APP_DATA_DIR 环境变量）
python webapp/reset_password.py 张三 newpass456
python webapp/reset_password.py --data-dir /data/deen-music 张三 newpass456
```

## 文档导航

- Docker 部署说明：[README.Docker.md](README.Docker.md)
- 图文使用教程：[docs/初次使用教程.md](docs/初次使用教程.md)
- 常见问题：[docs/常见问题.md](docs/常见问题.md)
- 技术文档（架构、目录结构、配置说明、数据模型、API 接口）：[docs/技术文档.md](docs/技术文档.md)
- 更新日志：[docs/CHANGELOG.md](docs/CHANGELOG.md)

## 打包与开发

跨平台一键打包为单文件可执行程序（onefile 模式，依赖全部内置）：

```bash
# Windows
build_win.bat

# Linux（POSIX sh）
chmod +x build_linux.sh && ./build_linux.sh
```

- 产物位于 `dist/deen-music-downloader/`，自动复制 `api/` 当前平台二进制并创建 `downloads/` 占位目录
- PyInstaller 不支持交叉编译，各平台产物须在对应平台构建
- 推送 `v*` 标签时 GitHub Actions 自动构建 Windows / Linux zip 与 fnOS `.fpk` 三份产物并发布 Release

更多开发细节（技术栈、目录结构、核心机制、配置说明、数据模型、API 接口、运行模式）见 [docs/技术文档.md](docs/技术文档.md)。

## 版本

当前版本：**0.7.8**（见 [version.txt](version.txt)，更新日志见 [docs/CHANGELOG.md](docs/CHANGELOG.md)）
