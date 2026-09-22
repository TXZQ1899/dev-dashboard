#!/usr/bin/env bash
# 构建 envscope:local 镜像。collectors 上下文指向仓库根目录（Dockerfile 中的采集器）。
set -e
cd "$(dirname "$0")"

NODE_IMAGE="${NODE_IMAGE:-public.ecr.aws/docker/library/node:22-bookworm-slim}"

docker build \
  --build-context collectors=.. \
  --build-arg NODE_IMAGE="$NODE_IMAGE" \
  -t envscope:local \
  .
