# syntax=docker/dockerfile:1.7

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

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-websocket \
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
    fetch_ecs_inventory.py \
    ./runtime/

USER node

EXPOSE 3000

HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=30s \
    --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["python3", "runtime/service.py"]