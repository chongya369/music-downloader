# 网易云音乐下载器 Web 版

基于 Flask 的网易云音乐下载器，支持多账号管理、定时同步歌单、断点续传下载、元数据写入、Web 界面管理。

## 功能特性

- **多账号管理**：支持添加多个网易云账号（Cookie 鉴权），按使用顺序调度
- **多账号调度策略**：
  - 接力模式（fallback）：当前账号达额度或失败时自动切换下一个
  - 轮询模式（round_robin）：按计数器轮流分配下载任务
  - VIP 偏好：VIP 歌曲用 VIP 账号，非 VIP 歌曲优先用非 VIP 账号
- **额度管控**：单账号月额度限制 + 每自然小时下载上限（避免风控）
- **定时同步**：APScheduler 多时间点 cron 触发，支持抖动延迟（避免固定时刻请求）
- **断点续传**：HTTP Range 协议，下载失败自动重试，支持临时文件续传
- **元数据写入**：MP3（ID3v2）/ FLAC（Vorbis Comment）标签，含封面、歌词、专辑信息
- **语言检测**：基于歌词字符集自动识别中/英/日/韩语（日文优先判假名，避免汉字误判）
- **排除过滤**：按关键字过滤歌曲（如 live、伴奏、remix），playlist 和 search 两个场景独立配置
- **发现页**：官方排行榜、热门分类歌单、搜索歌曲/专辑、单曲/专辑批量下载
- **下载历史**：完整记录下载任务（成功/失败/跳过），支持失败重试
- **用户管理**：Web 登录系统，管理员/普通用户两种角色
- **路径保护**：自动清洗非法文件名字符，超长路径自动截断（兼容 Windows MAX_PATH）
- **账号导入导出**：JSON 格式批量导入导出账号配置

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | Flask 3.0+ |
| ORM | Flask-SQLAlchemy 3.1+ |
| 数据库 | SQLite |
| 定时调度 | APScheduler 3.10+ |
| HTTP 客户端 | requests 2.31+ |
| 音频元数据 | mutagen 1.47+ |
| 前端 | Bootstrap 5.3.2 + Bootstrap Icons 1.11.3 |
| 网易云 API | [NeteaseCloudMusicApi](https://github.com/Binaryify/NeteaseCloudMusicApi)（Node.js 本地服务） |

## 目录结构

```
source/
├── core/                          # 核心业务层
│   ├── netease_client.py          # 网易云 API 客户端（调用本地 Node 服务）
│   ├── downloader.py              # 文件下载器（断点续传 + 重试 + 路径保护）
│   ├── metadata.py                # MP3/FLAC 元数据写入
│   └── language_detector.py       # 基于歌词的语言检测
├── webapp/
│   ├── app.py                     # Flask 应用入口（默认端口 56700）
│   ├── models.py                  # 数据库模型（6 张表）+ 默认配置
│   ├── auth.py                    # 登录态校验装饰器
│   ├── task_manager.py            # 下载任务调度器（核心）
│   ├── version.py                 # 版本号读取
│   ├── reset_password.py          # 命令行重置密码脚本
│   ├── routes/
│   │   ├── views.py               # 页面路由
│   │   └── api.py                 # JSON API 路由
│   ├── templates/                 # Jinja2 HTML 模板
│   └── static/                    # 静态资源（CSS/JS/vendor）
├── downloads/                     # 下载输出目录（按歌手名分子目录）
├── downloads.db                   # SQLite 数据库文件
├── requirements.txt               # Python 依赖
├── VERSION                        # 版本号
├── CHANGELOG.md                   # 版本变更记录
└── run_web.bat                    # Windows 一键启动脚本
```

## 环境依赖

- **Python** 3.10+
- **Node.js**（用于运行 NeteaseCloudMusicApi 服务）

## 快速开始

### 1. 启动网易云 API 服务

先部署并启动 [NeteaseCloudMusicApi](https://github.com/Binaryify/NeteaseCloudMusicApi) Node 服务，默认监听 `http://localhost:3000`。

### 2. 启动 Web 服务

Windows 用户直接双击 `run_web.bat`，脚本会自动：
- 检测 Python 环境
- 检测 NeteaseCloudMusicApi 服务是否可达
- 创建虚拟环境（首次运行）
- 安装 Python 依赖（首次运行）
- 启动 Flask 服务

手动启动方式：

```bash
# 创建虚拟环境
python -m venv .venv

# 安装依赖
.venv\Scripts\pip install -r requirements.txt

# 启动服务
.venv\Scripts\python webapp\app.py
```

### 3. 访问 Web 界面

浏览器打开 `http://localhost:56700`，使用默认管理员账号登录：

- 用户名：`admin`
- 密码：`admin123`

**首次登录后请立即在「用户管理」页修改密码。**

### 4. 添加网易云账号

在「账号管理」页添加账号：
- 填写账号别名（如"主账号"）
- 粘贴 Cookie（**必须包含 `MUSIC_U` 字段**，从浏览器登录网易云后从 F12 → Network → Request Headers 复制）
- 可选设置月额度上限（0 表示不限制）

### 5. 添加歌单

在「歌单管理」页添加要同步的歌单，支持：
- 纯数字歌单 ID：`3778678`
- 网易云分享链接：`https://music.163.com/playlist?id=3778678`
- 短链接：`https://y.music.163.com/m/playlist?id=3778678`
- 官方榜单：从「发现」页选择

## 配置说明

所有配置项存储在 SQLite 数据库 `settings` 表，可通过 Web「设置」页修改。默认配置如下（定义在 [models.py](webapp/models.py) `DEFAULT_SETTINGS`）：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `api_url` | `http://localhost:3000` | NeteaseCloudMusicApi 服务地址 |
| `web_port` | `56700` | Web 服务端口（修改后需重启服务） |
| `output_dir` | `downloads` | 下载输出目录（相对路径基于项目根目录） |
| `level` | `exhigh` | 音质等级：standard / higher / exhigh / lossless / hires |
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

## 核心机制

### 多账号调度

账号选择器（[task_manager.py](webapp/task_manager.py) `AccountSelector`）负责按策略分配下载账号：

- **接力模式**：维护当前账号指针，账号达额度或下载失败时切换到下一个可用账号
- **轮询模式**：维护全局计数器，每个新任务按顺序分配账号，单账号失败不切换直接标记失败
- **VIP 偏好过滤**：
  - VIP 歌曲（fee=1）：只用 VIP 账号（无 VIP 账号回退到全部）
  - 非 VIP 歌曲：优先非 VIP 账号（无非 VIP 账号回退到全部）
- **额度管控**：
  - 月额度：`quota_limit > 0` 且本月成功数已达上限 → 跳过该账号
  - 小时限额：`hourly_limit_per_account > 0` 且本自然小时成功数已达上限 → 跳过该账号
  - 所有账号均因小时限额满 → 暂停 30 分钟后重新入队

### 定时同步

- 解析 `sync_times` 配置（如 `03:00,09:00,21:00`）为多个 cron 任务
- 每个时间点触发时先随机延迟 `0~sync_jitter` 秒（避免固定时刻请求触发风控）
- 延迟期间若服务停止会被打断（通过 `_stop_event` 分段 sleep 检测）
- 同步逻辑：扫描所有已启用歌单 → 拉取歌曲列表 → 过滤已下载/进行中 → 应用排除关键字 → 入队

### 排除关键字过滤

- 配置 `exclude_keywords` 为英文逗号分隔的关键字列表（如 `live,伴奏,remix`）
- 匹配规则：大小写不敏感子串匹配，同时检查歌名和歌手名
- 应用范围由 `exclude_scope` 控制：
  - `playlist`：定时同步歌单时应用
  - `search`：搜索下载、专辑下载时应用
  - 单首下载（用户主动选择）**不应用**排除过滤

### 下载去重与失败重试

- **去重依据**：`songs` 表中 `status="success"` 的记录
- **进行中检测**：`download_tasks` 表中 `status in ("pending","downloading")` 的记录
- **失败重试**：失败任务记录到 `songs` 表（`status="failed"`），可在「下载历史」页单首或全部重试
- **跳过记录**：已下载歌曲在同步时记录为 `skipped` 状态（不重复下载，但保留任务记录）

### 下载文件命名

- 文件名格式：`{全部歌手} - {歌名}.{ext}`（ext 由 API 返回，默认 mp3）
- 子目录：取主歌手名（多歌手取第一个，空则用"群星"）
- 非法字符清洗：`\/:*?"<>|\r\n\t` 替换为 `_`
- Windows 保留名保护：CON/PRN/AUX/NUL/COM*/LPT* 等加 `_` 前缀
- 路径长度保护：超过 240 字符自动截断文件名（保留扩展名）

## 数据模型

数据库共 6 张表（定义在 [models.py](webapp/models.py)）：

### Account（网易云账号）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | 账号 ID |
| name | String | 别名（如"主账号"） |
| cookie | Text | Cookie 字符串（含 MUSIC_U） |
| nickname | String | 网易云昵称 |
| vip_type | Integer | 0=非会员 / 11=黑胶VIP / 12=SVIP |
| vip_expire_at | DateTime | 会员到期时间 |
| quota_limit | Integer | 月额度上限（0=不限制） |
| sort_order | Integer | 使用顺序（升序） |
| enabled | Boolean | 是否启用 |
| last_check_at | DateTime | 上次登录校验时间 |
| created_at | DateTime | 创建时间 |

### Playlist（关注的歌单/榜单）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | 网易云歌单/榜单 ID |
| name | String | 歌单名 |
| type | String | official（官方榜单）/ user（用户歌单） |
| enabled | Boolean | 是否启用自动同步 |
| limit_count | Integer | 取前 N 首 |
| last_synced_at | DateTime | 上次同步时间 |
| track_count | Integer | 歌曲总数 |
| created_at | DateTime | 创建时间 |

### Song（已下载歌曲记录）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | 网易云歌曲 ID |
| name | String | 歌名 |
| artists | String | 歌手 |
| album | String | 专辑 |
| duration_ms | Integer | 时长（毫秒） |
| quality | String | 下载音质 |
| file_path | String | 文件路径 |
| file_size | Integer | 文件大小（字节） |
| playlist_id | Integer FK | 来源歌单 ID |
| downloaded_at | DateTime | 下载时间 |
| status | String | success / failed / skipped |
| error_msg | String | 失败原因 |
| source_name | String | 来源歌单名（失败记录兜底） |
| account_id | Integer | 下载账号 ID |

### DownloadTask（下载任务）
| 字段 | 类型 | 说明 |
|------|------|------|
| pk | Integer PK | 自增主键 |
| song_id | Integer | 网易云歌曲 ID |
| song_name | String | 歌名 |
| artists | String | 歌手 |
| playlist_id | Integer | 来源歌单 ID |
| playlist_name | String | 来源歌单名 |
| status | String | pending / downloading / done / failed / skipped |
| progress | Integer | 进度 0-100 |
| error_msg | String | 失败原因 |
| account_id | Integer | 下载账号 ID |
| fee | Integer | 歌曲费用类型（0=免费 1=VIP 4=购买专辑 8=低音质免费） |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

### Setting（配置项）
| 字段 | 类型 | 说明 |
|------|------|------|
| key | String PK | 配置键 |
| value | Text | 配置值 |

### User（系统用户）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | 用户 ID |
| username | String | 用户名（唯一） |
| password_hash | String | 密码哈希（werkzeug） |
| is_admin | Boolean | 是否管理员 |
| enabled | Boolean | 是否启用 |
| created_at | DateTime | 创建时间 |
| last_login_at | DateTime | 上次登录时间 |

## API 接口概览

所有 `/api/*` 接口需登录（通过 session cookie 鉴权），管理员接口需额外校验。

### 歌单管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/playlists` | 获取已关注歌单列表 |
| GET | `/api/toplists` | 获取网易云官方榜单 |
| POST | `/api/playlists` | 添加歌单（支持链接解析） |
| PUT | `/api/playlists/<id>` | 更新歌单设置 |
| DELETE | `/api/playlists/<id>` | 取消关注 |
| POST | `/api/sync/<id>` | 立即同步某歌单 |
| POST | `/api/sync-all` | 同步所有已启用歌单 |

### 下载历史与任务
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/songs` | 分页查询下载历史 |
| DELETE | `/api/songs/<pk>` | 删除记录 |
| POST | `/api/retry` | 重试失败歌曲 |
| GET | `/api/tasks` | 获取活跃任务进度 |
| GET | `/api/stats` | 总览页统计数据 |

### 账号管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/accounts` | 账号列表 |
| POST | `/api/accounts` | 添加账号 |
| PUT | `/api/accounts/<id>` | 更新账号 |
| DELETE | `/api/accounts/<id>` | 删除账号 |
| POST | `/api/accounts/<id>/move` | 调整顺序 |
| POST | `/api/accounts/import` | 批量导入 |
| GET | `/api/accounts/export` | 导出 JSON |
| POST | `/api/accounts/<id>/test` | 测试登录 |
| GET | `/api/accounts/stats` | 本月下载统计 |

### 发现页
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/discover/toplists` | 官方排行榜 |
| GET | `/api/discover/playlists` | 热门分类歌单（分页） |
| GET | `/api/discover/categories` | 歌单分类列表 |
| POST | `/api/discover/search` | 搜索歌曲/专辑 |
| POST | `/api/discover/search-download` | 搜索并批量下载 |
| POST | `/api/discover/album-download` | 下载整张专辑 |
| POST | `/api/discover/download-song` | 下载单首歌曲 |

### 设置
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings` | 获取配置 |
| PUT | `/api/settings` | 保存配置 |

### 用户管理（仅管理员）
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/users` | 用户列表 |
| POST | `/api/users` | 创建用户 |
| PUT | `/api/users/<id>` | 更新用户 |
| DELETE | `/api/users/<id>` | 删除用户 |

## 默认账号与密码重置

首次启动时自动创建管理员账号：`admin / admin123`。

忘记密码时可在服务器命令行执行重置脚本（[reset_password.py](webapp/reset_password.py)）：

```bash
# 重置 admin 密码为默认值 admin123
python webapp/reset_password.py

# 重置指定用户密码为默认值
python webapp/reset_password.py 张三

# 重置指定用户密码为指定值
python webapp/reset_password.py 张三 newpass456
```

## 常见问题

### Q: 添加账号时提示"Cookie 必须包含 MUSIC_U"？
A: 从浏览器登录网易云后，打开 F12 → Network → 任意请求 → Request Headers → 复制完整 Cookie 字符串，其中必须包含 `MUSIC_U=xxx` 字段。

### Q: 下载失败提示"无版权或需 VIP"？
A: 该歌曲需要会员权限，请确保有可用的 VIP 账号（vip_type=11 或 12）。接力模式下会自动切换到 VIP 账号重试。

### Q: 下载失败提示"试听片段"？
A: API 返回了 freeTrialInfo，表示当前账号无该歌曲完整版权，只能下载试听片段（已自动跳过）。

### Q: 所有账号都提示"小时限额已满"？
A: 所有启用账号在当前自然小时内的成功下载数已达 `hourly_limit_per_account` 上限，系统会自动暂停 30 分钟后继续，无需手动干预。

### Q: NeteaseCloudMusicApi 服务未启动会怎样？
A: 「发现页」相关功能会失败；榜单列表会回退到本地常驻列表（[OFFICIAL_TOPLISTS](core/netease_client.py)）；下载功能因需要获取歌曲链接会失败。建议先确保 Node 服务正常运行。

### Q: 修改 Web 端口后无法访问？
A: `web_port` 修改后需重启 Flask 服务才生效（通过 `run_web.bat` 重启或手动重启 `python webapp/app.py`）。

### Q: 下载的文件名包含特殊字符导致问题？
A: 下载器已自动清洗 `\/:*?"<>|\r\n\t` 等非法字符为 `_`，并处理 Windows 保留名（CON/PRN 等）。如仍有问题请检查输出目录路径长度是否超过 Windows MAX_PATH（260 字符），系统会自动截断过长的文件名。

### Q: 路径过长导致下载失败？
A: 系统对路径长度有保护机制：超过 240 字符自动截断文件名（保留扩展名）。若仍失败（如目录部分本身过长），请缩短 `output_dir` 路径或调整歌单/歌手名。

## 开发说明

### 运行模式

- Flask 使用 `threaded=True` 多线程模式（非 reloader）
- 下载工作线程为独立 daemon 线程，串行处理任务队列
- APScheduler 使用 BackgroundScheduler，在 Flask 进程内运行
- SQLAlchemy session 在每个子线程内通过 `app.app_context()` 创建/销毁

### 日志

日志输出到 stdout，格式：`%(asctime)s [%(levelname)s] %(name)s - %(message)s`，级别 INFO。

### 数据库迁移

首次启动自动建表 + 写入默认配置 + 创建管理员账号。兼容迁移逻辑（如新增 `account_id` 列）在 [models.py](webapp/models.py) `init_db` 中通过 `ALTER TABLE` 实现。

## 版本

当前版本：**0.1.0**（见 [VERSION](VERSION)）

版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。
