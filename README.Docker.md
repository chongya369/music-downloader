# Docker 部署

本目录提供 `Dockerfile` 和 `docker-compose.yml`，一键打包运行 Deen 音乐下载器。

> 相关文档：[README](README.md) · [初次使用教程](docs/初次使用教程.md) · [常见问题](docs/常见问题.md) · [技术文档](docs/技术文档.md)

## 国内镜像源（默认）

为加速国内构建，`Dockerfile` 默认把 apt 和 pip 源替换为：

| 类型  | 默认源                                      | 说明                      |
| --- | ---------------------------------------- | ----------------------- |
| apt | `mirrors.aliyun.com`                     | Debian 完整镜像，安全/CVE 覆盖一致 |
| pip | `https://mirrors.aliyun.com/pypi/simple` | 阿里云 PyPI 镜像             |

海外用户构建时如需恢复官方源：

```bash
docker build \
  --build-arg APT_MIRROR=deb.debian.org \
  --build-arg PIP_INDEX_URL=https://pypi.org/simple \
  -t deen-music-downloader:latest .

# compose：编辑 docker-compose.yml 中的 build.args 段（已提供注释示例）
```

## 快速开始

```bash
# 仅构建
docker compose build | tee docker-build.log

# 构建并后台启动
docker compose up -d --build

# 查看日志
docker compose logs -f

# 浏览器访问 http://<宿主机IP>:45600
# 默认账号/密码：admin / admin123（首次登录后请立即修改）
```

## 数据持久化

| 挂载点              | 用途                                     |
| ---------------- | -------------------------------------- |
| `/data`          | SQLite（账号/歌单/任务/设置）+ 日志 + ncm-api 临时缓存 |
| `/app/downloads` | 下载的音乐文件                                |
| `/app/logs`      | 日志webapp.log                           |

如需把下载目录落到宿主机可见位置，编辑 `docker-compose.yml`：

```yaml
volumes:
  - /mnt/nas/deen-data:/data
  - /mnt/nas/music:/app/downloads   # 改成宿主机绝对路径
```

## 常用命令

```bash
docker compose ps              # 查看容器状态
docker compose restart         # 重启
docker compose pull && docker compose up -d   # 升级镜像
docker compose down            # 停止并删除容器（保留数据卷）
```

## 修改配置

大部分配置（端口、定时同步、音质、下载路径等）通过 Web UI 修改即可，无需重启容器。
**例外**：`web_port`（监听地址）修改后需要重启：

```bash
docker compose restart
```

## 直接使用 docker（不通过 compose）

```bash
# 构建
docker build -t chongya369/deen-music-downloader:latest .

# 运行
docker run -d --name deen \
  -p 45600:45600 \
  -v ./deen-data:/data \
  -v ./deen-downloads:/app/downloads \
  -e TZ=Asia/Shanghai \
  --restart unless-stopped \
  chongya369/deen-music-downloader:latest
```

默认以 `deen`（UID/GID 1000）非 root 用户运行。
