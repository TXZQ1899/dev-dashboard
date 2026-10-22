# 不再声明 `# syntax=docker/dockerfile:1.7`：远端 daemon 只配了 daocloud 镜像，
# 它不代理 docker/dockerfile 前端镜像（401），会让构建在第一步就失败。
# 引擎 29.x 的内置 frontend 已支持本文件用到的 cache mount 与具名构建上下文。

ARG NODE_IMAGE=node:22-bookworm-slim

# =========================
# Build stage
# =========================
FROM ${NODE_IMAGE} AS build

WORKDIR /app

ENV CI=true \
    WRANGLER_SEND_METRICS=false \
    WRANGLER_WRITE_LOGS=false

ARG NPM_REGISTRY=https://registry.npmmirror.com

# 先复制依赖描述文件
# 这样 package.json / package-lock.json 不变时，可以直接复用 Docker Layer Cache
COPY package.json package-lock.json ./

# 使用 BuildKit cache 保存 npm 下载缓存
# 即使 package-lock.json 变化，npm package tarball 也可以继续复用
RUN --mount=type=cache,target=/root/.npm \
    npm config set registry "${NPM_REGISTRY}" \
    && npm ci \
       --include=dev \
       --no-audit \
       --no-fund \
       --prefer-offline

# CACHEBUST: build.sh 传入时间戳，确保源码变更后 COPY . . 和 npm run build
# 不会被 BuildKit 内容缓存跳过（npm ci 层不受影响，仍按 package-lock.json 缓存）。
ARG CACHEBUST=1

# 再复制业务代码，避免源码变化导致 npm ci 缓存失效
COPY . .

RUN npm run build


# =========================
# Runtime stage
# =========================
FROM ${NODE_IMAGE} AS runtime

WORKDIR /app

USER root

# Debian bookworm 默认走 deb.debian.org，实测只有 26 KB/s，apt 一步能拖到 13 分钟。
# 这里在安装前把 deb822 源换成国内镜像（同时覆盖 debian 与 debian-security），
# 并给下载加超时与重试：慢连接快速失败重试，而不是静默挂住。
# 用 HTTP 入口：构建容器里访问 https 镜像会因证书链不完整而校验失败；
# apt 仍会用 debian-archive-keyring 校验索引与包的 GPG 签名，完整性不受影响。
ARG DEBIAN_MIRROR=http://mirrors.tuna.tsinghua.edu.cn
ARG APT_TIMEOUT=20

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}/debian|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::http::Timeout=${APT_TIMEOUT} \
               -o Acquire::https::Timeout=${APT_TIMEOUT} \
               -o Acquire::Retries=1 update \
    && apt-get -o Acquire::http::Timeout=${APT_TIMEOUT} \
               -o Acquire::https::Timeout=${APT_TIMEOUT} \
               -o Acquire::Retries=1 install -y --no-install-recommends \
        python3 \
        python3-websocket \
        python3-pymysql \
        python3-cryptography \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data \
    && chown node:node /data

ENV NODE_ENV=production \
    WRANGLER_SEND_METRICS=false \
    WRANGLER_WRITE_LOGS=false \
    MINIFLARE_REGISTRY_PATH=/tmp/miniflare \
    TZ=Asia/Shanghai

# 接受 build.sh 传入的 CACHEBUST；写一个 marker 文件使该层失效，
# 从而强制后续 COPY --from=collectors 重新拉取最新 collectors 源码，
# 避免 BuildKit Layer Cache 跳过 collectors 上下文的文件更新。
ARG CACHEBUST=1
RUN echo "$CACHEBUST" > /tmp/collectors-cachebust.marker

# 拷贝构建产物
COPY --from=build --chown=node:node /app ./

# 拷贝 collectors 脚本
COPY --from=collectors --chown=node:node \
    daily_collection.py \
    export_devops.py \
    export_devops_details.py \
    export_codeup_groups.py \
    check_codeup_access.py \
    ./runtime/

COPY --from=collectors --chown=node:node \
    export_slb.py \
    export_nat.py \
    export_dms.py \
    fetch_ecs_inventory.py \
    aliyun_openapi.py \
    ./runtime/

# Fresh volumes launch the prebuilt app directly and create runtime.log in /app.
RUN chown node:node /app

USER node

EXPOSE 3000

HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=30s \
    --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["python3", "runtime/service.py"]
