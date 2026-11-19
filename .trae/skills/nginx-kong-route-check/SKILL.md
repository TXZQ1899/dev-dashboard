---
name: nginx-kong-route-check
description: 核查指定主机 IP 与端口的 Nginx 与 Kong 转发关系，基于 envscope-local 快照、原始 Nginx 配置和 Kong 配置导出。当用户询问某台机器端口是否被 Nginx/Kong 转发、梳理网关路由，或核实进程监听与转发链路时使用。不用于修改配置或执行采集。
---

# Nginx / Kong 路由核查

只读核查。不要修改 `/data`、不要重启容器、不要触发采集。

## 输入

- 目标主机 IP，例如 `10.58.6.2`
- 目标端口列表；用户未给出时，用脚本自动取该机全部监听端口
- 如需指定历史快照，使用 `--version <versionId>`

## 执行

在仓库根目录运行。脚本通过 stdin 进入 `envscope-local`，只输出 JSON：

```bash
docker --context lenovo exec -i envscope-local python3 - 10.58.6.2 \
  --ports 3000,3001,3333,3400,3600,3601,3800,46009 \
  < .trae/skills/nginx-kong-route-check/scripts/check_routes.py > /tmp/route-check.json
```

不带 `--ports` 时自动使用当前快照中的监听端口。需要限定历史版本时追加 `--version 20261116-200515-938752`。

## 判读

先看 `coverage`，每个端口四个布尔字段：

| 字段 | 含义 |
|---|---|
| `host_listening` | 当前快照中该端口有监听记录 |
| `nginx_normalized` | 规范化 `nginxRoutes` 中存在 `IP:端口` 后端，属静态解析证据 |
| `nginx_raw` | 原始 Nginx 配置中出现了字面量 `IP:端口` |
| `kong` | Kong 配置导出中存在该 `IP:端口` target |

再按需展开：

- `hosts[].appPorts` 取监听地址、PID、应用名；`nodeRelatedProcesses` 取 Node 进程启动时间与已运行天数。
- `nginx.normalized` 取域名、URI、listen、upstream、后端；这是回答“哪些域名转发到该端口”的主证据。
- `nginx.raw[].upstreams` 取 upstream 名称与完整 server 列表；用于判断该机是单后端还是双活后端之一。
- `kong[].matches` 取 target、upstream、service、route；用于回答“Kong 是否转发到该端口”。
- `secondpass.host` 与 `secondpass.processes` 是最新一轮进程核查（可能比快照新）；时间不一致时分别标注。

## 报告要求

1. 先给结论：端口是否被 Nginx 转发、是否被 Kong 转发，分别列出数量。
2. 进程与端口表：PID、服务、端口、绑定地址、启动时间、已运行天数。
3. Nginx 表：域名/路径、目标端口、upstream、同组其他后端。
4. Kong 表：集群、target、upstream、service、route、配置导出时间。
5. 明确证据口径与限制，至少包括：
   - 原始 Nginx 只按字面 IP 匹配，未解析主机名型 upstream。
   - Kong 结论来自配置导出快照，不是实时 Admin API。
   - 配置中有转发不等于当前健康；端口无监听仍可能有失效 upstream。
   - 仅绑定 `127.0.0.1` 的端口不能从外部直接访问。
   - 不得用“连接采样中没有该端口”单独证明 Kong 没有配置路由。

## 约束

- 先读 `references/data-layout.md` 再写新的查询逻辑；优先复用脚本。
- 不整份读取 `jumpserver-snapshot.json`、`topology.json` 或原始配置，按 IP、assetId、upstream 定向查询。
- 不把 `INFERRED` / `AMBIGUOUS` 写成确定关系；静态 Nginx 后端为 `EXACT`，进程归属推断需单独注明。
- 不修改采集器、快照 schema 或项目规则。
