#!/usr/bin/env bash
# 构建 envscope:local 镜像
# collectors 上下文指向仓库根目录
set -euo pipefail

cd "$(dirname "$0")"

NODE_IMAGE="${NODE_IMAGE:-public.ecr.aws/docker/library/node:22-bookworm-slim}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"

# 每次构建传入唯一 CACHEBUST 值，确保 COPY . . 和 npm run build 一定执行。
# npm ci 层不受影响（依赖 package-lock.json），仍可复用 Docker Layer Cache。
# 如需完全复用缓存（如 CI 中重复构建同一 commit），可 CACHEBUST=0 bash build.sh。
CACHEBUST="${CACHEBUST:-$(date +%s)}"

echo "Using NODE_IMAGE:   $NODE_IMAGE"
echo "Using NPM_REGISTRY: $NPM_REGISTRY"
echo "Using CACHEBUST:    $CACHEBUST"
echo "Docker context:     $(docker context show 2>/dev/null || echo 'default')"

docker build \
  --progress=plain \
  --build-context collectors=.. \
  --build-arg NODE_IMAGE="$NODE_IMAGE" \
  --build-arg NPM_REGISTRY="$NPM_REGISTRY" \
  --build-arg CACHEBUST="$CACHEBUST" \
  -t envscope:local \
  .