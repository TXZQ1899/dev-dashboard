---
name: app-process-check
description: 一键核查某应用在指定环境（默认生产 PRODUCT）是否有运行进程、端口监听和部署目录。用户询问某应用生产是否有进程、应用是否存活、或排查“有部署目录但无进程”时使用。只读，不用于修改配置、重启服务或触发采集。
---

# App Process Check

输入应用名或数字应用 ID，自动从快照定位该环境的全部实例，逐台经 JumpServer 登录执行只读诊断（监听端口、匹配进程、部署目录），最后给出存活台数汇总。比逐台手敲登录命令更省事，也避免漏台。

## 执行

在仓库根目录运行（脚本经 stdin 送入 `envscope-local` 容器，与 `jumpserver-server-access` 同一登录链路）：

```bash
docker --context lenovo exec -i envscope-local python3 - --app pos-api \
  < .trae/skills/app-process-check/scripts/check_app_processes.py
```

参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `--app` | 应用名（如 `pos-api`）或数字应用 ID（如 `1659`），必填 | — |
| `--env` | 环境名，如 `PRODUCT` / `SIMULATION` / `TEST` | `PRODUCT` |
| `--version` | 快照版本 ID，省略则读 `/data/current.json` 的当前版本 | 当前版本 |
| `--timeout` | 单台命令超时秒数 | `100` |
| `--verbose` | 额外打印每台原始诊断输出，便于深挖 | 关闭 |

## 判读

输出一张逐台汇总表，列含义：

| 列 | 含义 |
|---|---|
| `LISTEN` | 该实例声明端口当前是否有监听（`ss -lntp` 命中） |
| `PROCESS` | `ps` 中是否存在应用名相关进程 |
| `DIRS` | 是否存在部署目录（`/var/www/webapps/<app>`、`/opt/apache-tomcat-<app>`、`/opt/anchor-packages/<app>`） |
| `NOTE` | 异常标注，如 `elevation=password_error`（提权失败，ps/ss 仍可读但结果可能受限） |

末行结论给出“存活台数 / 总台数”。`LISTEN=NO 且 PROCESS=NO 且 DIRS=YES` 即典型的“有部署目录、无运行进程”的离线态。

## 约束与口径

- 全程只读：不修改配置、不重启、不触发采集。
- 实例与端口取自 `/data/versions/<ver>/devops.json` 的当前快照；若快照与实际有偏差，以本次登录结果为准。
- 某台登录失败或提权失败时会在 `NOTE` 标注并继续核查其余机器，不会中断整次巡检。
- 本技能只回答“有没有进程在跑”。如需进一步核查 Nginx/Kong 转发链路，用 `nginx-kong-route-check`；如需逐台执行自定义只读命令，用 `jumpserver-server-access`。
