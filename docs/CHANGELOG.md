# 更新日志

本文件记录各版本变更内容（最新版本在最前）。

## 0.7.0（2026-09-20）

### 重大变更

- **音质档位扩展（网易云 / QQ）**：网易云新增 `jyeffect` / `dolby` / `vivid` / `jymaster` / `sky` 五档，其中 `jymaster` 进统一降级链、其余四档不进链（无对应权益时直接失败提示，不静默降档）；QQ 新增 `jymaster`（臻品母带，进链）与 `ogg640`（不进链，由客户端内部降 128 兜底）。`MusicProvider.QUALITY_ORDER` 首位插入 `jymaster`。
- **`.ogg` 元数据完整写入**：新增 `write_ogg_tags`，字段集与 FLAC 逐行对齐（标题 / 歌手 / 专辑 / 年份 / 音轨号 / 碟号 / 专辑歌手 / 歌词 / 封面），容器经 `mutagen.File()` 嗅探 Vorbis / Opus 双兜底。封面须按 Vorbis Comment 规范写 `METADATA_BLOCK_PICTURE` —— mutagen 的 Ogg 容器没有 FLAC 那套 `add_picture`。
- **下载任务控制**：新增单任务「暂停 / 继续 / 删除」与全局「暂停全部 / 继续全部」；暂停保留 `.part`、继续断点续传、删除即中止并清理临时文件。`Downloader.download()` 增设四段协作式中止检查（重试入口 / 发起连接前 / 每个数据分块 / 退避等待），响应延迟由「整首下完」降到亚秒级；新增 `paused` 状态并纳入去重与任务列表。

### 功能

- **任务控制接口**：新增 `POST /api/tasks/<pk>/pause|resume`、`DELETE /api/tasks/<pk>`、`POST /api/tasks/pause-all|resume-all`；`GET /api/tasks` 追加 `paused_all` / `has_active`，后者驱动导航栏「下载中 / 空闲」指示器。
- **启动恢复**：进程重启时重建下载队列（`pending` 入队、中断的 `downloading` 回退 `pending` 后入队）；`paused` 刻意不恢复。
- 三平台音质下拉按音质高→低排列（纯展示，不参与降级逻辑；QQ 保留 `hires` 并标注「同无损」）；设置页值域 `_VALID_LEVELS` 扩展至 10 档，新增档位保存不再被静默重置为空串。

### 修复

- **歌单同步因上游 null 字段整体崩溃**（`POST /api/sync/<pid>` 返回 500，`TypeError: sequence item 0: expected str instance, NoneType found`）：上游对已下架 / 失效曲目返回「键存在但值为 null」的结构，而 `dict.get(k, default)` 只在**键缺失**时生效。三层修复：解析层全字段归一（文本 `str(x or "")`、数值 `_to_int`）并改逐条 `try` / `continue` 取代列表推导式（单首脏数据不再中断整张歌单）；全仓 9 处 `.get(k, [])` / `.get(k, {})` 补 `or` 归一；无 `id` 的占位曲目在去重查询**之前**拦下并 `WARNING` 留痕（避免 `str(None) == "None"` 写入 `songs` 主键、把该歌永久卡成「已下载」）。
- **失败写入 `None` 卡死任务**：`_mark_failed` 直接用入参构造 `Song`，而 `Song.name` 为 NOT NULL、`song_name` 列可为 NULL，`None` 在 `merge(Song(...))` 处抛 `IntegrityError`，且异常**早于** `task.status="failed"` → 任务卡在 `downloading` 且无任何失败记录。改为函数首行入口归一，单点防御全部 6 个调用方。
- **空值透传造成的 500 与标签丢失**：`metadata.write_tags` 入口归一并新增 `_to_int` 处理 `dt` / `duration_ms`；网易云 `get_lyric` 的 null 归一为空串；`PUT /api/playlists/<pid>`（歌单名传 `null` 撞 NOT NULL）、`PUT /api/accounts/<aid>`、添加歌单 / 账号时字段传 `null` 直接 `.strip()` 等问题改为归一后校验。
- **歌单同步失败返回 HTML 报错页**：`/api/sync/<pid>` 与 `/api/sync-all` 改为捕获异常返回 `{"code":1,"msg":...}`（HTTP 200），前端可正常提示。
- **删除「小时限额等待」中的任务白占 worker**：该状态下任务为 `pending`，删除接口只清中止登记、等待循环又只认中止登记，单线程 worker 被白占满 30 分钟、后续任务集体停摆。等待循环改为回查任务行，5 秒内释放。
- **暂停被失败标记覆盖**：暂停恰好落在「本例已注定失败」与「写库」之间时，任务会被改写为 `failed` 并留下失败记录；`_mark_failed` 增加 `paused` 优先判断。
- **删除与入库竞态产生幽灵记录 / 孤儿文件**：`write_tags` 耗时可达数秒，期间删行仍会写入 `songs` 成功记录（下载历史看不到、却通过去重阻断该歌重下），本次产出也不被清理。入库前增加终检，并用 `DownloadOutcome.produced` 区分「本次真正产出」与「命中文件已存在提前返回」。
- **中止登记泄漏误伤复用 pk 的新任务**：认领失败、全局暂停转 `paused`、任务行已消失三条路径未清中止登记，SQLite 复用 rowid 后残留的删除登记会让新任务被误中止并永久卡在 `downloading`。
- 网易云 `get_song_urls` 空 `song_ids` 直接返回空列表，不再发一次注定失败、还要重试 3 次的批量请求。

### 代码审查修复（28 项）

对照《代码审查报告-2026-09-20》与《修复方案-2026-09-20》落地，语法级 0 缺陷（`py_compile` / `node --check` 全通过），全部为逻辑 / 边界 / 并发 / 类型契约类缺陷。逐条成因与改法见方案文档，此处按风险列名：

- **高危（5）**：酷狗 `year` 字段遇整数型 `publish_date` 切片抛 `TypeError`；自动同步歌单缺 `isinstance` 守卫致整单静默中断；`_mark_failed` 的 `platform=None` 致任务永久卡 `downloading`；`_req_platform()` 无白名单致脏平台值落库污染两表；三平台 `_request` 返回点未归一到 dict。
- **中危（15）**：酷狗热门歌单无页数上限可无限翻页；`ConnectionError` 直接抛致网络抖动被误判为鉴权失败而逐个换号；两处裸 `get_json(force=True)` 返回 HTML 400 页；前端 `api()` 未校验 `resp.ok`（5xx 可被误判为成功）；账号导入未防元素类型与 `enabled=null`；网易云扫码把网络瞬态误报为「MUSIC_U 无效」；gcid 磁盘缓存一次加载失败后本进程内不再重试；启动脚本三处确定性缺陷（bat 的 `&` 优先级、sh 的 `set -e` 死代码、只校验单个 API 二进制）并清理废弃 spec；Linux `preexec_fn` 在多线程父进程内 fork 不安全；QQ 专辑曲目数探测 8 线程共用 `Session`；`GET /api/songs` 的 `status` 非白名单值静默「不过滤」；各平台 `Session` 从不 `close()`；`_fit_path` 目录过长分支无日志；删除账号清空在途任务的 `account_id`；重试过滤缺平台维度。
- **低危（8）**：`_abort` 登记补生命周期约束；`event.listen` 幂等保护；platform 归一化补 `accounts` 表；QQ `quality_key` 注解更正为 `int`；`_rank_of` 链外档位语义补注释；删除死代码 `Song.to_dict()`；fee 统计改用 `_safe_int`；前端 5 处轮询在页面隐藏时不发请求。

### 其他

- **接口语义实测（决定实现方式）**：`/song/download/url/v1` 的 `level` 必填，且多 id 会返回 HTTP 200 但 body 非 JSON（客户端按解析失败白重试约 9 秒）→ 高级档补链必须逐歌请求（请求数 = 1 + 缺失曲目数）；`/song/url/v1` 对未知 `level` 不报错而是静默降级 standard → 高级档必须显式传值。QQ 匿名请求 `file_type=8` 可拿到真实 OGG 文件（据此确认 `QUALITY_EXT[8] = "ogg"`），`file_type=1` 匿名无权限、越界值返回 422。
- **补链实现约束与防御**：只针对试听接口未返回 `url` 的曲目；补链响应 `code != 200`、`data` 为空或 `url` 仍为空时保留原项；回填前比对返回 `id` 与请求 `id` 一致（防上游串号污染结果）。
- **行为变更（空名曲目）**：曲名 / 歌单名为空不再静默兜底为「未知歌手 - 未知歌曲.mp3」，改为记一条明确失败（`error_msg="上游返回值为空，下载失败"`）；判定以单曲详情曲名为准，QQ / 酷狗无单曲详情接口、以任务记录名为准；恢复需手动「重试」（自动 / 定时同步不重试同歌单的 `failed` 终态）。添加歌单同理：上游歌单名为空直接拒绝（`code=1`），不再用 ID 兜底成 `18398083374` 这类无名记录。
- **签名变更**：`Downloader.download()` 返回值 `Path | None` → `DownloadOutcome(path, produced)`。
- **内部整理**：在途状态字面量收敛为 `models.ACTIVE_TASK_STATUSES`（含 `paused`）/ `RUNNABLE_TASK_STATUSES`，替换 `task_manager` / `routes` 的 8 处硬编码；移除从未被读取的 `TaskManager._current_pk`；`sync_all` 单歌单失败不再中断整批。
- **文档与测试**：README 音质配置表按平台列出完整档位与降档链，`docs/技术文档.md` 补「分平台音质档位与取链路径」表与 `.ogg` 标签写入说明；新增 `.workbuddy/tests/test_quality_ext.py`（32 项）与接口探测脚本。

## 0.6.3（2026-09-17）

### 修复

- **特定歌曲下载永久卡「下载中」**：下载 CDN 直链时 `requests` 的 `timeout` 只覆盖 TCP 连接与读取、**不覆盖 DNS 域名解析**——特定歌曲的 CDN 域名一旦解析挂起（DNS 故障 / 污染 / 代理异常），请求会无限阻塞，任务一直「下载中」且串行队列被无限期占用（表现为某几首歌卡住、进度不动、多次运行反复出现）。改为请求阶段放入守护线程并限制等待 `timeout+10s`（覆盖 DNS 挂起，超时即放弃本次尝试），流式阶段追加双重兜底（60s 无数据 / 单首总时长超 15 分钟立即中断），超时后按原机制重试 3 次，仍失败则标记失败可手动重试，正常歌曲下载不受影响
- **飞牛/NAS 以非 root 用户启动即崩溃**：`@appcenter` 安装目录文件属主为 root，非 root 运行进程每次启动对 `ncm-api`/`qqmusic-api`/`kugou-api` 二进制强制 `chmod 0o755` 必抛 `PermissionError: Operation not permitted` 直接退出。改为兜底式权限处理：chmod 失败先忽略（执行位由打包/安装时以 root 设置），改用 `os.access(X_OK)` 校验，文件已可执行即正常启动；仅当「改不了权限且确实不可执行」才报错并提示手动 `chmod +x`
- **fnOS 日志只显示警告/报错、INFO 不可见且 webapp.log 缺失**：日志初始化在部分 import 之后执行且 `basicConfig` 非强制，root logger 一旦被抢先配置 INFO 即失效；frozen 打包时日志目录按 exe 所在目录（安装目录，只读）计算，`logs/webapp.log` 建不出来只能降级仅控制台。日志初始化移至模块最前并加 `force=True` 强制 INFO 生效，控制台流兜底 `sys.stdout or sys.stderr`；fnOS（注入 `APP_DATA_DIR`）下日志改落可写数据卷 `data/logs/webapp.log`，本地与普通打包仍落 `_ROOT/logs` 行为不变
- **API 二进制无法执行时拖垮整个 Web 服务**：`Popen` 在 exec 被系统拒绝（无执行权限 / 架构不匹配 / 缺 glibc 加载器 / Windows WinError 193 等）时抛 `OSError`，而 `main()` 与 Web 启停接口均只捕获 `RuntimeError`，异常逃逸导致整个服务启动失败。三个 bridge 的 `start()` 统一把 `OSError` 转为带原因的 `RuntimeError`，API 启动失败仅记警告日志，Web 服务正常启动，可在设置页查看状态并手动重试

### 其他

- **运行时日志落盘**：webapp 日志由仅控制台输出改为控制台 + `logs/webapp.log`（UTF-8），取流失败、下载超时/重试的具体原因（DNS 挂起 / 连接超时 / 空闲超时）均可事后查档定位；日志目录不可写时自动降级为仅控制台，不影响启动

## 0.6.2（2026-09-14）

### 修复

- **SQLite 并发读写锁冲突**：下载工作线程写进度与前端轮询并发时，读语句偶发 `database is locked`（默认 rollback journal 模式下写事务 commit 独占库文件）；连接级启用 WAL（读写并发，读不再被写锁阻塞）+ `busy_timeout=30000`（锁冲突等待 30s 而非立即报错）
- **QQ 专辑搜索曲目数恢复**：新服务端搜索响应无曲目数字段（切换前旧服务端 song_count 已随切换消失，此前恒显示 0），改为按专辑并发探测 `/album/{mid}/songs?num=1` 的 total_num 补齐（8 并发，失败降级显示 "—" 不阻断搜索）
- **QQ 专辑歌曲列表分页聚合**：单页 200 上限改为循环翻页取全量，超长合辑不再截断；分页中途失败 fail-loud 返回空列表，防静默下载半张专辑

## 0.6.1（2026-09-13）

### 修复

- **网易云扫码 Cookie 规范化**：内置 ncm-api 服务端按 `;\s+` 正则解析请求 Cookie 头，无空格分隔时整串会被当成单个 cookie，MUSIC_U 丢失导致登录态降级为匿名；`set_cookie` 与扫码 Cookie 提取统一规范化为 `k=v; k=v`（分号+空格）格式
- **网易云扫码假成功拦截**：803 授权成功后再调 `/login/status` 校验登录态，MUSIC_U 未生效时不再静默当成成功，改为提示重新扫码
- **二维码防缓存**：key/create/check 链路统一携带 timestamp 参数（create 补充 platform=web），避免 CDN/代理缓存导致二维码或登录态检查复用旧响应
- **扫码过期提示保持可见**：二维码过期时仅停止轮询、保留面板，过期提示不再被隐藏（此前隐藏面板会让校验结果写到不可见元素上）
- **网易云账号信息刷新修正**：`profile` 为空即匿名态（`/user/account` 未登录也返回 code=200），不再覆盖账号原有昵称/会员信息，改为返回「Cookie 未生效，请重新扫码获取」；昵称权威字段改取 `profile.nickname`（`account.userName` 为登录名非昵称）；会员类型沿用 `account.vipType`（经典 0/11/12 语义，与展示映射一致；`profile.vipType`、`/vip/info` 的 vipCode 均非展示编码，弃用）
- **QQ 专辑搜索高亮标记移除**：专辑搜索接口加 `highlight=false`，关闭关键词 `<em>` 高亮（歌手/专辑名携带 `<em>` 导致前端展示异常）

## 0.6.0（2026-09-13）

### 重大变更

- **QQ 音乐 API 服务端切换（FastAPI 版）**：内置 qqmusic-api 二进制更换为 FastAPI/uvicorn 架构，`client.py` 核心重写——响应解析 `{code,msg,data}`、标准 Cookie 传凭证（uin/qqmusic_key 自动映射 musicid/musickey）、取下载链接两步化（`get_song_urls` + CDN dispatch 拼接）、音质改整型枚举（hires 13 / lossless 12 / flac 7，降级 128 保留）、歌曲详情 50 首一批、榜单与歌单详情客户端按 100/页分页聚合；`bridge.py` 配套修复环境变量名（`QQMUSIC_SERVER_HOST/PORT`）与就绪探测（`/` 根端点校验）
- **三平台扫码登录全对齐**：新增 QQ/微信扫码（双按钮）与网易云扫码登录，三平台共用前端状态机与酷狗语义 status（0=过期 1=等待 2=已扫 3=拒绝 4=成功）；扫码落库 Cookie 与手工录入格式一致，添加账号校验零改动；分类歌单降级：新服务端无等价接口，热门歌单改走官方推荐歌单单页、分类固定「全部」

### 功能

- **QQ 账号页昵称 + 会员到期时间显示**：homepage 接口取昵称（失败不清空存量）；`userinfo.expire` 缺失时从 `identity.*_end` 解析（兼容带时间与纯日期两种格式，主会员档位优先）
- **微信登录 Cookie 兼容**：musicid 补 wxuin 兜底（修复微信扫码账号 422），wx 系字段别名映射供 W_X_ 凭证续期；账号 Cookie 强校验兼容 musicid
- **QQ 歌词可用**：新服务端提供 `/song/{mid}/lyric`（原服务端恒空）
- **下载元数据增强**：MP3 双 USLT 帧（原文/翻译歌词）、TRCK/TPOS/TPE2 帧、年份帧 TYER→TDRC（ID3v2.4）；FLAC 加 tracknumber/discnumber/albumartist/translation；网易云经 `/album` 补全音轨/碟号/专辑歌手（按专辑缓存），QQ/酷狗 meta 同步补全；详情/歌词空结果 1s 重试 1 次

### 修复

- 错误语义：401/422 确定性错误不重试，429 保留重试；账号页昵称仅非空时覆盖，防误清空
- **fpk 部署 QQ 扫码「API 进程异常退出」**：qqmusic-api 启动即在 cwd 下写 `web/data/` 运行时文件，fpk 安装目录只读导致进程秒退；运行时 cwd 改为数据目录 `qqmusic/` 子目录（尽力复制 config.toml 保留限流配置；源码/普通打包行为不变），秒退报错附带 exit code 与日志路径
- **网易云扫码恒「等待扫码」**：轮询请求加 `noCookie=true` + `timestamp` 防缓存参数；上游原始状态码落日志（INFO/未知码 WARNING 含响应体）便于定位；ncm-api 匿名令牌目录改为应用数据目录下持久 `tmp/`（fpk 系统临时目录可能被清理或首启刷新失败致令牌为空，导致 check 恒 801）

### 构建与打包

- `api/config.toml` 新增本机限流豁免（新服务端默认 60 次/分/IP，批量下载会触发 429）；`build.py` 打包复制 config.toml；`api/readme.txt`、`docs/技术文档.md` 同步更新
- 三平台 API 子进程 stdout/stderr 落日志文件（`APP_DATA_DIR/logs/`，目录不可写自动回退关闭日志），进程秒退的真实原因不再被吞掉

## 0.5.1（2026-09-10）

### 功能

- **歌单下载上限由 1000 首放宽到 9999 首**：添加歌单弹窗、我的歌单每行数量输入、设置页默认下载数量及后端校验（`POST/PUT /api/playlists`、`default_playlist_limit` 取值范围）全链路同步放宽
- **网易云大歌单分页拉取**：歌单曲目超过 1000 首时经 `/playlist/track/all` 接口分页补齐（每页 500 首，取满 limit 或取完即止）；分页接口失败自动回退 `/playlist/detail` 的前 1000 首
- 酷狗歌单曲目分页页数上限按 limit 动态计算（原先写死最多 10 页 = 500 首，QQ 本身无此限制）

### 界面

- 歌单页切回「我的歌单」标签时自动重新拉取列表：发现页 / 弹窗新添加的歌单立即可见，无需刷新整页
- **下载任务进度 0.5 秒刷新**：任务列表轮询间隔 2s → 0.5s，并改为增量更新 DOM（复用已有节点，仅更新进度条 / 状态徽章 / 错误信息），消除高频刷新下的闪烁卡顿；后端进度写库粒度同步收紧为进度变化 ≥1% 或每 0.5s 一次

## 0.5.0（2026-09-10）

### 重大变更

- **飞牛 fnOS 统一网关适配**：新增 `FNNAS_GATEWAY_SOCKET` / `FNNAS_GATEWAY_PREFIX` 环境变量驱动的 Unix Socket 监听模式；`PrefixMiddleware` WSGI 中间件剥离网关前缀并写入 SCRIPT_NAME，路由 / url_for / 静态资源自动携带前缀；非网关模式保持原 TCP 行为不变（本地 / Windows / Docker 不受影响）
- **应用标识与产物统一改名 `deen-music-downloader`**：PyInstaller 产物 exe 与 `dist/` 目录、fnos 应用标识（manifest appname、ui/config 键名）、Unix Socket 与统一网关前缀（`/app/deen-music-downloader`）整体更名；zip 内可执行文件为 `deen-music-downloader(.exe)`，fpk 内为 `server/deen-music-downloader`
- **fnos/ fpk 打包源入库**：manifest、`cmd/` 全套 9 个生命周期脚本（`main` 为主脚本：注入网关 Socket / 前缀 / 数据目录三个环境变量，PID 与日志管理，启动等待 socket 就绪最长 60s；其余 8 个为 fnpack 1.2.3 强制要求的 no-op 脚本）、config、桌面图标；fnpack 打包工具二进制随仓库内置（官方 1.2.3，避免外部下载源失效）
- **CI 新增 build-fnos 产物**：GitHub Actions 新增 `build-fnos` job（Debian 12 容器构建 Linux 二进制 → fnpack 打包 `.fpk`），随 Windows / Linux zip 一并产出
- **README 精简重写**：面向用户的安装 / 快速上手 / 配置 / FAQ 文档；技术栈、目录结构、核心机制、数据模型、API 概览等开发细节移入 `docs/技术文档.md`

### 功能

- 网关模式首启：默认下载目录自动固定到数据卷绝对路径，用户手动改过的路径不受影响
- Session cookie 隔离：专属名 `md_session` + 前缀路径绑定，与同域其他 fnOS 应用互不冲突

### 修复

- 前端统一网关前缀适配：`api()` 请求、401 跳转、账号导出均拼接 `APP_BASE` 前缀；模板链接统一改用 `url_for`（网关模式下不再脱前缀 404）

### 构建与发布

- **CI 产物名带版本号**：`deen-music-downloader-v<版本>-win-x64.zip` / `-linux-x64.zip` / `-v<版本>.fpk`
- manifest 版本号以 version.txt 为唯一来源：CI 组装 fpk 时 sed 覆盖，仓库内 manifest 不再手动同步
- **fpk 上传 Release 改为 fail-loud**：`gh release upload` + find 定位；softprops 的 files glob 失配只打 warning，曾导致 .fpk 在 Release 中静默缺失
- fnos 图标资源整理（ICON.PNG / ICON_256.PNG / app/ui/images）

### 文档

- 新增《初次使用教程》（docs/初次使用教程.md，含界面截图）
- README / 技术文档一致性修正：fnOS 网关前缀更名同步、音质配置默认值与 `DEFAULT_SETTINGS` 字面值对齐（默认空串，回退旧全局 `level`，其缺省 exhigh）

## 0.4.2（2026-09-07）

### 功能

- **API 服务默认随程序自启**：网易云 / QQ 音乐 / 酷狗三个 API 服务的 `auto_start` 默认值由 `false` 改为 `true`，新装环境下启动程序即自动拉起；已保存过设置的存量库不受影响，可在设置页手动开启
- **历史页失败记录批量清除**：失败重试栏新增「清除失败记录」按钮，配套 `DELETE /api/songs/failed` 接口一次性清理 download_tasks + songs 两表的全部失败记录（失败记录无本地文件、无下载中竞态），清除后歌曲可重新下载
- **失败重试栏常驻提示**：只要存在失败记录即显示重试栏（原先仅"失败"筛选下可见），任意筛选视图均可一键重试或清除
- **分页省略号**：历史页码过多时中间页码收起为省略号（首页 … 当前页 ±2 … 末页），不再平铺全部页码

### 界面

- 设置页「下载设置」布局调整：下载目录独占整行，三平台音质下拉并排同一行

### 文档

- README：三个 API 服务 `auto_start` 默认值说明同步更新

## 0.4.1（2026-09-07）

### 重大变更

- **音质按平台独立配置**：设置页「音质等级」由单一全局 `level` 拆分为三平台独立下拉（`level_netease` / `level_qq` / `level_kugou`），并新增「目标音质不可用时自动降档」开关（`enable_quality_fallback`）；未单独设置时自动回退旧全局 `level` 兼容迁移，保存旧值不会被静默覆盖
- **音质自动降档链**：`MusicProvider` 新增 `get_song_url_with_fallback` / `quality_chain`，目标档取不到流时沿音质链逐档向低档回退（standard ← exhigh ← lossless ← hires），实际生效档位回填到下载记录 `quality` 字段；QQ 档位经 `quality_key` 去重（hires/lossless 同为 flac、exhigh/higher 同为 320）避免重复请求，酷狗禁用外层降级（client 内部已有完整降级链）
- **取流失败诊断透传**：网易云 get_song_urls 接口整体失败不再吞成空列表，返回带 `err` 诊断的占位结构；`_transform` 透传 `code`/`err`/`level` 等字段，失败原因分类更精确（取流失败[err] / 无音源(-110) / 试听片段 / 无可用音源）；-110 无音源直接终态不再换号空转

### 功能

- 历史页删除弹窗：成功记录删除时二选一「仅删除记录」/「删除记录和文件」；失败/跳过记录直接确认删除
- `DELETE /api/songs/<pk>` 支持 `?delete_file=1`：先删除本地文件再级联删除该歌曲（song_id+platform）所有歌单关联记录；仅删记录时若歌曲无其他 done 任务引用则一并清理 songs 表，重新下载不再被"已下载"去重拦截
- 删除文件安全校验：只允许删除下载目录内的文件（`_delete_song_file`），路径越界拒绝删除；歌曲正在下载中（pending/downloading）时阻断删除防竞态（仅删记录/删文件两种模式均阻断）；文件删除失败整体中断、数据库不动

### 修复

- 网易云接口级失败（如登录态失效）不再被误报为"无版权或需VIP"，避免换号空转与误判
- **酷狗降级音质档回填错误**：登录态高音质失败、v5/v6 降级 128 兜底成功时，下载记录音质仍误按原目标档回填（请求无损实际落 128k 却记 "lossless"）；改为跟踪实际生效档，且目标档 hash 缺失时直接回落 128 档请求
- **网易云试听片段禁止下载**：`freeTrialInfo` 命中时试听 URL 视同取流失败——接力模式自动换更高权益账号；降档链遇试听自动跳档继续向低档找完整音源（fee=8 低音质免费歌可拿到完整 128k）；全档试听则标记失败"试听片段（会员未生效或权益不足）"，不再把 30 秒片段当完整歌曲静默下载成功
- 旧全局 `level=higher`（192k，新 UI 已不支持）迁移：设置页读取时归一到 exhigh，select 不再显示空白
- 历史页删除：仅删记录模式同样阻断"该歌曲下载中"的记录（原先仅删文件模式阻断），防与下载 worker 竞态导致状态更新落空

### 文档

- README：配置表拆分为三平台音质项 + 降档开关；`/api/songs/<pk>` 删除接口补充 `delete_file` 参数说明

## 0.4.0（2026-09-05）

### 重大变更

- **酷狗音乐平台完整接入**：新增 `core/providers/kugou/`（`KuGouProvider` + client + bridge + 链接解析），注册到 `registry.py`；内置 kugou-api 二进制进程管理（默认端口 45603），Web「设置」页可配置端口、一键启停、`auto_start` 与自定义 API URL
- **酷狗扫码登录**：账号页「添加账号」弹窗新增扫码登录（生成二维码 → 酷狗 APP 扫码 → 轮询状态自动填入 `token=xxx;userid=xxx` Cookie），配套 `/api/kugou/qr/create`、`/api/kugou/qr/check` 接口
- **酷狗账号全链路支持**：Cookie 核心字段强校验（须含 `token`）、添加后自动刷新账号信息（昵称/VIP）、登录测试附带下载能力实测、`vip_text_for` 归一为「VIP会员/非会员」
- **下载链路适配**：歌名权威化（详情接口纯歌名覆盖歌单合并串「歌手 - 歌名」）；主歌手按 `/`、`、` 分隔符统一拆分；酷狗取流失败追加底层诊断（v5 status / v6 _errno）到失败原因
- **静态资源缓存控制**：模板新增 `static_v()`（mtime 缓存戳 URL）并覆盖全部页面，HTML 响应加 `Cache-Control: no-cache`，解决改 JS 后浏览器缓存不失效问题

### 功能

- 网易云 / QQ 音乐 / 酷狗音乐三平台账号管理、调度与下载
- 酷狗匿名音质封顶 128kbps，登录态走 v5 取流接口返回真实高音质（320/FLAC/Hi-Res），高音质失败自动降级 128 重试
- 酷狗歌手拆分兼容「、」分隔符（酷狗搜索链路返回格式）
- **数据库位置可配置**：启动参数 `--data-dir <目录>` 或环境变量 `APP_DATA_DIR` 指定数据目录，数据库固定为 `<目录>/downloads.db`（目录不存在自动创建，两者同设时参数优先）；未指定时行为不变（打包后 exe 同目录 / 开发态项目根）
- **旧库自动迁移**：数据目录被指定且程序目录存在旧 `downloads.db` 时，首次启动自动搬迁（含 SQLite `-journal/-wal/-shm` 附属文件）；目标位置已有数据库时不覆盖；启动日志输出实际数据库路径
- `reset_password.py` 支持同样的 `--data-dir` / `APP_DATA_DIR` 位置规则
- **飞牛 fnOS fpk 适配**：打包工程 `cmd/main` 以 `--data-dir "$TRIM_PKGVAR"` 启动，数据库按官方规范落在 appdata（var）目录而非 appcenter 程序目录，覆盖升级后旧库自动迁入

### 已知限制

- 酷狗音乐 API `/user/detail` 未返回到期时间字段（vip_expire_ts 恒 0），账号页显示「酷狗未提供」
- 酷狗翻译歌词（tlyric）接口固定为空，仅返回纯歌词 lrc
- 酷狗 `/search?type=song` 已失效，搜索单曲走专辑中转 + 歌手中转

### 构建与运行脚本

- **API 二进制随仓库内置**：三平台预编译二进制（网易云 / QQ 音乐 / 酷狗音乐）从"不随源码分发"改为入库跟踪（.gitignore 放行 `api/*.exe`），源码 clone 即可运行，无需手动准备
- `build.py`：`post_pack` 自动将源码 `api/` 中当前平台的三个二进制复制进产物 `dist/music_downloader/api/`，缺失时生成占位提示文件（不中断打包）
- `build.py` / `run_web.bat`：API 二进制清单扩展为三平台（网易云 + QQ + 酷狗）
- `run_web.bat`：依赖安装先走官方 PyPI，失败自动回退清华镜像；启动前逐项检查三个平台二进制
- `api/readme.txt`：更新为入库说明（内置版本来源 + 升级覆盖方式），并新增酷狗音乐（KuGouMusicApi）二进制说明
- `.gitignore`：新增 `kugou_api_*`、`api/.kugou_gcid_cache.json`

### 修复

- `build_win.bat`：Python 版本解析改为单行 `for /f` + 标记跳转，规避多行 `if ( )` 块解析 bug

### 文档

- README 更新为三平台说明（账号/歌单/配置项/API 概览/FAQ/打包说明）
- README 新增「数据库位置」章节（`--data-dir` / `APP_DATA_DIR` / 旧库自动迁移 / fpk 部署说明）

## 0.3.1（2026-08-29）

缺陷修复版本：修复 22 项已知问题（含 2 项 P0 级构建崩溃 / 下载风暴隐患），无新功能。老库首次启动会自动执行数据库迁移，建议升级前备份 `downloads.db`。

### 修复

**P0（构建与下载链路致命问题）**

- **接力模式无限递归**：多账号接力下载失败后切换账号无终止条件，坏 Cookie 账号会引发请求风暴；现在同一首歌按「已尝试账号集合」各尝试一次后即失败，失败原因标注「已尝试 N 个账号」
- **失败歌每轮同步重复入队**：`failed` 状态歌曲在每日定时同步时反复重新入队，并与接力切换叠加成风暴；现在同一歌单内曾失败的歌曲自动同步不再重试（日志输出「曾失败跳过 N 首」）

**数据一致性与迁移**

- **songs 表复合主键（含迁移）**：主键由单列 `id` 改为 `(id, platform)`，网易云数字 ID 与 QQ songmid 撞号时不再互相覆盖；启动时自动执行幂等的表重建迁移（单事务失败整体回滚，二次启动自动跳过）
- **重试丢失 VIP 标记**：重试失败歌曲时新建任务未携带原 `fee`，VIP 歌会被分给非 VIP 账号；现在删除旧记录前显式读取并透传
- **失败记录覆盖成功记录**：同首歌已有成功记录时，其他任务失败不再覆盖 songs 表的成功记录
- **重复 skipped 记录**：同一歌单重复同步会产生重复的「已下载」行；查重带上 platform 并去掉 status 限定，并为 `download_tasks` 新增 `(platform, song_id)` 与 `(status)` 两条查询索引
- **搜索结果「已下载」标记对网易云恒失效**：已下载 ID 集合存为 str 而比较用原始值（int），徽章与按钮禁用从未生效；统一 str 化比较（QQ 平台原本正常）
- **QQ 榜单空歌误判**：榜单详情改为仅按歌曲列表判空，有榜单元数据但无歌曲时不再被当作有效数据
- **QQ 音质降级扩展名**：匿名降级 128k 时按实际降级集合将扩展名修正为 mp3，不再依赖 URL 后缀猜测
- **账号导入脏值**：导入 / 添加 / 编辑账号的 `quota_limit`、`vip_type`、`sort_order` 统一经 `_safe_int` 收敛，非数字字符串不再落库导致账号选择中断

**设置与输入防御**

- **保存设置端口校验误伤**：不带端口字段的裸 API 部分更新不再被「API服务运行中」误拦；JSON 数组 / 字符串体返回 400 而非 500，并为全部 13 处 JSON 请求体统一添加类型防御
- **数值设置无校验**：`max_retries`、`sync_jitter` 等六项数值设置保存时非法值回退默认、越界钳制上下限并在保存结果中提示；读取点（小时限额 / 同步抖动 / 重试次数）坏值自动回退默认，不再抛异常

**界面**

- 小时限额暂停无提示：全部账号达每小时上限时，任务卡显示「所有账号小时限额已满，等待恢复后自动继续」，恢复后自动继续并清除提示（等待改为轮询，恢复即提前结束）
- 修正运行 / 构建脚本的过时路径提示（`source/api` → `api`），Linux 构建完成提示补充 QQ API 二进制放置说明

### 安全

- 账号 Cookie 导出（含全部明文 Cookie）改为**仅管理员**可用，普通用户不显示导出按钮且账号页其余功能不受影响；其余账号操作维持登录即可（单人工具定位）
- 登录接口限速：同一 用户名+IP 连续失败 5 次锁定 10 分钟
- 修复登录 `next` 参数开放重定向（拦截 `//host` 与 `/\host` 协议相对变体）
- 酷狗音乐定位为预留平台：添加弹窗禁选、页签标注「预留」、后端显式拒绝 `platform=kugou` 的添加请求
- XSS 加固：转义歌单名 / 分类名 / 封面 URL（`src` 属性）/ 账号别名与昵称 / 任务卡片歌名等全部上游插值点

### 代码清理

- 删除成套死代码 `verify_account` / `login_status` / `transform_account_info`；前端重复定义的 `escapeHtml` / `formatSize` / `statusBadge` 收敛至全局 `app.js`；移除 `api.py` 未使用的导入；账号导出文件的版本号改走 `APP_VERSION` 配置

### 行为变更说明

1. 自动同步不再重试「同一歌单内」曾失败的歌曲；手动重试请用失败列表的「重试」或搜索页单点下载
2. 搜索下载与专辑下载共享失败记忆：搜索失败过的歌，专辑下载时也会被跳过（反之亦然）
3. 账号 Cookie 导出仅管理员可用；其余账号操作不受影响
4. 酷狗音乐为预留平台：不可添加账号，入口标注「预留」
5. 删除下载历史仍不会让歌曲重新下载（确认框已注明）

## 0.3.0（2026-08-23）

### 重大变更

- **多平台 Provider 架构**：重构 `core/providers/` 抽象层，引入 `MusicProvider` ABC、`registry.py` 工厂，并新增 `_proc.py` 子进程保护（Windows 作业对象 / Linux PDEATHSIG，「父进程死亡即关闭 API 服务」）
- **QQ 音乐平台完整接入**：新增 `core/providers/qq/`（`QqProvider` + client + bridge + 链接解析），注册到 `registry.py`；内置 qqmusic-api 二进制进程管理，Web「设置」页可配置端口、一键启停、`auto_start` 与自定义 API URL；账号校验、发现页（榜单/热门歌单/搜索/批量下载）、歌单同步均支持 QQ 平台
- **多平台歌单 Tab**：歌单管理页"发现"标签改为配置驱动的平台标签（当前"网易云 / QQ音乐"）；所有 `/api/discover/*` 与添加歌单请求按平台分流
- **歌单管理增强**：添加歌单弹窗增加平台下拉框，我的歌单列表每行显示平台徽章，各平台子标签状态（排行榜/热门歌单/搜索）独立记忆

### 功能

- 网易云 / QQ 音乐双平台账号管理、调度与下载（酷狗预留）
- 多账号调度策略：接力（fallback）/ 轮询（round_robin）、VIP 偏好过滤
- 单账号月额度 + 每自然小时下载上限管控（避免风控）
- APScheduler 多时间点 cron 定时同步，支持抖动延迟
- HTTP Range 断点续传 + 失败自动重试
- MP3（ID3v2）/ FLAC（Vorbis Comment）元数据写入，含封面、歌词、专辑信息
- Web「设置」页实时查看 API 服务状态（网易云 / QQ 音乐），支持端口配置、一键启停、`auto_start` 与自定义 API URL
- 排除关键字过滤（live/伴奏/remix 等），playlist/search 独立配置
- 发现页：官方排行榜、热门歌单、搜索、单曲/专辑批量下载
- 下载历史 + 失败重试
- 用户管理（管理员/普通用户角色）、账号 JSON 导入导出

### 已知限制

- QQ 音乐 API 服务端未提供歌词接口，下载时跳过歌词，不影响音频文件与封面写入
- QQ 品质映射：`lossless`/`hires` 均映射为 flac；`standard`/`higher`/`exhigh` 对应 mp3 128/320kbps

### 构建与运行脚本

- `build.py` / `build_win.bat` / `build_linux.sh` 重构：优先使用 `.venv` Python、清理旧产物、创建 `api/`、`downloads/` 占位目录
- `run_web.sh`：POSIX sh 兼容、pip 镜像回退、缺失时自动安装 `python3-venv`/`python3-pip`、Node 18+ 版本检测
- `run_web.bat`：CRLF 行尾 + 纯 ASCII，避免 cmd 代码页乱码与 `for /f` 解析 bug
- GitHub Actions 自动构建（Windows/Linux + Release），linux zip 用 `-C` 简化、增加 API 完整性校验

### 修复

- 第三方依赖漏包（如 Flask）根因定位与修复：打包显式使用 `.venv` 解释器
- 网易云 API 服务检测改用 `curl`，提升跨平台兼容性

### 文档

- README 更新为多平台架构说明并修正过时引用
- 新增 QQ 音乐平台接入方案、QQmusic API 调用说明、CHANGELOG（docs 目录）

### 说明

- docs 目录为本地维护，不纳入 Git 跟踪
