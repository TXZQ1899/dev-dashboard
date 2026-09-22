ARG NODE_IMAGE=node:22-bookworm-slim
FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV CI=true \
    WRANGLER_SEND_METRICS=false \
    WRANGLER_WRITE_LOGS=false
COPY package.json package-lock.json ./
RUN npm ci --include=dev --no-audit --no-fund
COPY . .
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-websocket ca-certificates tzdata && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data && chown node:node /data
ENV NODE_ENV=production \
    WRANGLER_SEND_METRICS=false \
    WRANGLER_WRITE_LOGS=false \
    MINIFLARE_REGISTRY_PATH=/tmp/miniflare \
    TZ=Asia/Shanghai
COPY --from=build --chown=node:node /app ./
COPY --from=collectors --chown=node:node daily_collection.py export_devops.py export_devops_details.py export_codeup_groups.py check_codeup_access.py ./runtime/
COPY --from=collectors --chown=node:node export_slb.py export_nat.py fetch_ecs_inventory.py ./runtime/
RUN chown node:node /app
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["python3", "runtime/service.py"]
