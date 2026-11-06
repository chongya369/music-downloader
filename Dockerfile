# syntax=docker/dockerfile:1.7
# ============================================================================
# Deen 音乐下载器 — single-stage build
# ----------------------------------------------------------------------------
# Single FROM (python:3.11-slim-bookworm). This base image already ships the
# Python 3.11 interpreter, libsqlite3.so.0, libstdc++6, libgcc-s1, and
# ca-certificates, so we just install tini (PID 1) + passwd (useradd), then
# pip-install our deps and copy the app + API binaries.
#
# Why Debian (not alpine): the three API binaries (ncm / qqmusic / kugou) are
# dynamically linked against glibc + libstdc++ + libpthread. Alpine uses musl
# libc and would force us to rebuild all three — out of scope.
#
# Why no build-essential: every entry in requirements.txt ships as a cp311
# manylinux wheel on PyPI, so we don't need a C compiler.
# ============================================================================

# Override the mirrors at build time with `--build-arg`, e.g.:
#   docker build --build-arg APT_MIRROR=deb.debian.org --build-arg PIP_INDEX_URL=https://pypi.org/simple .
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="deen-music-downloader" \
      org.opencontainers.image.description="Flask-based multi-platform music downloader (Netease / QQ / Kugou)" \
      org.opencontainers.image.source="https://github.com/chongya369/music-downloader" \
      org.opencontainers.image.licenses="MIT"

# Re-declare the global ARGs inside the build for visibility to RUN.
ARG APT_MIRROR
ARG PIP_INDEX_URL

# Point Debian's apt sources at the Aliyun mirror. The python:*-slim-bookworm
# base already ships sources.list pointing at deb.debian.org; we rewrite it
# once. The Aliyun mirror is a full mirror of deb.debian.org so security/CVE
# coverage is identical.
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
            /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
            /etc/apt/sources.list; \
    fi

# Runtime-only apt packages:
#   tini          — PID 1, signal forwarding, zombie reaping.
#   passwd        — provides `useradd` / `groupadd` for the non-root account.
#                   (The `shadow` source package name is NOT an apt binary
#                   package on Debian; the equivalent runtime is `passwd`.)
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends tini passwd; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /install

# Copy only the requirements manifest first so the slow `pip install` layer
# caches independently of app source changes.
COPY requirements.txt ./

# Install pip deps into the system Python. We do NOT `pip install --upgrade
# pip`: pip 24.0 that ships with the base image is sufficient, and skipping
# the upgrade avoids transient 403s on some Chinese PyPI mirrors when
# fetching the latest pip wheel.
RUN PIP_INDEX_URL=${PIP_INDEX_URL} pip install --no-cache-dir -r requirements.txt

# Trim stdlib modules that python:3.11-slim-bookworm ships but we never
# import (verified for Flask, Flask-SQLAlchemy, APScheduler, requests,
# mutagen). None are needed at runtime.
#
# - test/        : Python test suite
# - idlelib/     : IDLE editor
# - tkinter/     : Tk GUI bindings
# - turtledemo/  : turtle demo scripts
# - lib2to3/     : Python 2→3 converter
# - pydoc_data/  : pre-built doc index (we don't generate docs at runtime)
RUN rm -rf \
    /usr/local/lib/python3.11/test \
    /usr/local/lib/python3.11/idlelib \
    /usr/local/lib/python3.11/tkinter \
    /usr/local/lib/python3.11/turtledemo \
    /usr/local/lib/python3.11/lib2to3 \
    /usr/local/lib/python3.11/pydoc_data \
    /usr/local/include/python3.11 \
    /usr/local/share/man/man1/python3.11.1 \
    /usr/local/share/man/man1/python3.1 \
    /usr/local/lib/libpython3.11.a \
    /usr/local/share/doc/python3.11

# Strip doc/man/info/locale installed by apt in this layer. (Layers are
# additive — files created here must be cleaned here too.)
RUN rm -rf /usr/share/doc/* \
           /usr/share/man/* \
           /usr/share/info/* \
           /usr/share/locale/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Non-root user. --system avoids UID collisions with host bind mounts and is
# the idiomatic choice for container service accounts.
RUN set -eux; \
    groupadd --system --gid 1000 deen; \
    useradd  --system --uid 1000 --gid deen --home-dir /data --shell /usr/sbin/nologin deen

# Workdir and persistent data layout.
WORKDIR /app
# /data   — persistent app state (SQLite DB, logs, ncm tmp)
# /app/downloads — large media files (kept on a separate mount by convention)
ENV APP_DATA_DIR=/data \
    DATA_DIR=/data \
    DOWNLOADS_DIR=/app/downloads
RUN mkdir -p /data /app/downloads && chown -R deen:deen /data /app/downloads

# Copy API binaries + app source last (changes most often → top of layer cache).
COPY --chown=deen:deen api/ /app/api/
COPY --chown=deen:deen core/ /app/core/
COPY --chown=deen:deen webapp/ /app/webapp/
# version.txt 放在最后：版本号只在发版时变，置于三份大 COPY 之后可让
# 改版本号时 api/ core/ webapp/ 三层缓存仍然命中。webapp/version.py
# 按项目根（/app）解析该文件，缺失则版本号回落 "0.0.0"。
COPY --chown=deen:deen version.txt /app/version.txt

# Make the three API binaries executable. They live in /app/api/ — the Python
# code looks them up via the project root (see core/providers/_proc.py), so no
# PATH symlink is needed.
RUN chmod +x /app/api/ncm-api-linux-x64 \
              /app/api/qqmusic-api-linux-x64 \
              /app/api/kugou_api_linux

# Grant the runtime `deen` user write access to /app. The app writes:
#   - /app/logs/webapp.log             (Flask logger via webapp/app.py)
#   - /app/logs/{ncm,qq,kugou}-api.log (subprocess API bridges via _proc.py)
#   - /app/api/web/...                 (ncm-api state files, cwd=/app/api/)
# Pre-create these dirs so the app's mkdir(parents=True) is a no-op.
RUN set -eux; \
    mkdir -p /app/logs /app/api/web; \
    chown -R deen:deen /app

USER deen

# Default web port. The web_port setting in the DB can override on first launch;
# mount a pre-seeded DB to bake in an override.
EXPOSE 45600

# Health check: probe /login (returns 200 even unauthenticated; / would just
# 302-redirect there, which urllib follows and then asserts against).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
r=urllib.request.urlopen('http://127.0.0.1:45600/login', timeout=2); \
sys.exit(0 if r.status==200 else 1)" || exit 1

# tini as PID 1 → Flask receives SIGTERM cleanly, runs its atexit hook, and the
# child API bridges (ncm/qq/kugou) get shut down via the PDEATHSIG prctl set
# during spawn.
ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["sh", "-c", "exec python webapp/app.py --data-dir ${APP_DATA_DIR}"]