#!/usr/bin/env bash
# 构建 envscope:local 镜像
# collectors 上下文指向仓库根目录
set -euo pipefail

cd "$(dirname "$0")"

# 默认走 1ms.run 镜像源（lenovo 远端主机无法访问 public.ecr.aws，daocloud 又有白名单限制）。
# 网络恢复后可用 NODE_IMAGE=node:22-bookworm-slim 覆盖。
NODE_IMAGE="${NODE_IMAGE:-docker.1ms.run/library/node:22-bookworm-slim}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"

# 每次构建传入唯一 CACHEBUST 值，确保 COPY . . 和 npm run build 一定执行。
# npm ci 层不受影响（依赖 package-lock.json），仍可复用 Docker Layer Cache。
# 如需完全复用缓存（如 CI 中重复构建同一 commit），可 CACHEBUST=0 bash build.sh。
CACHEBUST="${CACHEBUST:-$(date +%s)}"

echo "Using NODE_IMAGE:   $NODE_IMAGE"
echo "Using NPM_REGISTRY: $NPM_REGISTRY"
echo "Using CACHEBUST:    $CACHEBUST"
echo "Docker context:     $(docker context show 2>/dev/null || echo 'default')"

# --- 同步 collectors 脚本到 environment-web/runtime/ ---
# collectors 的真实源在父目录（仓库根），Dockerfile 通过 --build-context collectors=..
# 把父目录版本拷贝进容器。environment-web/runtime/ 下保留同名副本仅用于本地开发、
# IDE 跳转与单元测试（aliyun.py / collect.py / test_parallel.py 等会 import 它们）。
# 为避免「改了副本但容器仍用父目录旧版」的误判，build 前从父目录强制同步最新版。
# 修改 collectors 时请只编辑父目录下的文件，不要编辑 environment-web/runtime/ 下副本。
COLLECTORS=(
  daily_collection.py
  export_devops.py
  export_devops_details.py
  export_codeup_groups.py
  check_codeup_access.py
  export_slb.py
  export_nat.py
  fetch_ecs_inventory.py
)
ROOT_DIR="$(cd .. && pwd)"
echo "Syncing collectors from $ROOT_DIR -> runtime/"
for f in "${COLLECTORS[@]}"; do
  if [ -f "$ROOT_DIR/$f" ]; then
    cp -p "$ROOT_DIR/$f" "runtime/$f"
  else
    echo "WARNING: $ROOT_DIR/$f not found, runtime/$f not synced" >&2
  fi
done

docker build \
  --progress=plain \
  --build-context collectors=.. \
  --build-arg NODE_IMAGE="$NODE_IMAGE" \
  --build-arg NPM_REGISTRY="$NPM_REGISTRY" \
  --build-arg CACHEBUST="$CACHEBUST" \
  -t envscope:local \
  .