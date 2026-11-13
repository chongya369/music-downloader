# syntax=docker/dockerfile:1.7
# Deen 音乐下载器 — 单阶段构建。
# 基础镜像已含 Python 3.11、libsqlite3、libstdc++、useradd（passwd 包），
# 只需装 tini。不用 alpine：三个 API 二进制动态链接 glibc。
# 换源：--build-arg APT_MIRROR=... --build-arg PIP_INDEX_URL=...

ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="deen-music-downloader" \
      org.opencontainers.image.description="Flask-based multi-platform music downloader (Netease / QQ / Kugou)" \
      org.opencontainers.image.source="https://github.com/chongya369/music-downloader"

ARG APT_MIRROR
ARG PIP_INDEX_URL

# 换源、装 tini、清 apt 缓存及 doc/man/locale（装包与清理同层，否则清理无效）。
RUN set -eux; \
    sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends tini; \
    rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/* \
           /usr/share/info/* /usr/share/locale/*

WORKDIR /app

# requirements 单独 COPY：pip install 层慢，只随依赖变化失效。
COPY requirements.txt ./
RUN PIP_INDEX_URL=${PIP_INDEX_URL} PIP_DISABLE_PIP_VERSION_CHECK=1 \
    pip install --no-cache-dir -r requirements.txt

# PYTHONDONTWRITEBYTECODE 阻止运行时重编译 .pyc，让 pip 装好的字节码一直复用。
# 放在 pip install 之后：置在前面会让 pip 自己那 368 个 .pyc 缺失。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_DATA_DIR=/data

# 建用户并预建可写目录。uid/gid 固定 1000 对齐宿主 bind mount。
# 必须在 COPY 之前：事后 chown -R /app 会触发 copy-up，把 140MB 二进制复制到新层。
RUN groupadd --system --gid 1000 deen && \
    useradd --system --uid 1000 --gid deen --home-dir /data \
            --shell /usr/sbin/nologin deen && \
    mkdir -p /data /app/downloads /app/logs && \
    chown -R deen:deen /data /app/downloads /app/logs && \
    chown deen:deen /app

# COPY 顺序 = 缓存代价从低到高：层缓存逐层链式，前层变了后面全部失效。
# api/ 二进制几乎不变，放最前，改 core/webapp 时这层仍命中缓存。
# --chmod 在建层时设执行位，避免事后 chmod 再 copy-up 一次 140MB。
# Windows .exe 由 .dockerignore 排除。
COPY --chown=deen:deen --chmod=0755 api/ /app/api/
COPY --chown=deen:deen core/ /app/core/
COPY --chown=deen:deen webapp/ /app/webapp/
# version.txt 单独一层：只在发版时变，不影响上面三层缓存。
COPY --chown=deen:deen version.txt /app/version.txt

USER deen

EXPOSE 45600

# 探 /login：未登录也返回 200。探 / 会 302 跳 /login，urllib 跟随后才断言，绕。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
r=urllib.request.urlopen('http://127.0.0.1:45600/login', timeout=2); \
sys.exit(0 if r.status==200 else 1)"

# tini 作 PID 1：转发信号给 Flask，回收 API 子进程。
ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["sh", "-c", "exec python webapp/app.py --data-dir ${APP_DATA_DIR}"]
