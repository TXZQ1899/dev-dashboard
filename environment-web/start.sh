#!/usr/bin/env bash
# 以 compose 启动 envscope（镜像由 build.sh 构建或 compose 自动构建）。
# 自定义端口：ENVSCOPE_PORT=8080 ./start.sh
# 允许局域网访问：放开 Host 白名单（默认仅本机）。
set -e
cd "$(dirname "$0")"

export ENVSCOPE_ALLOWED_HOSTS='*'
docker rm -f envscope-local 2>/dev/null || true
docker compose up -d
docker compose ps
