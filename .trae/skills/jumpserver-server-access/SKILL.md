---
name: jumpserver-server-access
description: 通过 Docker context lenovo 的 envscope-local 容器、经 JumpServer 登录目标服务器并执行只读命令（查日志、进程、端口、配置）。当用户要求登录某台服务器排查应用报错、查看日志或进程时使用。禁止从宿主机直接 SSH。不用于修改配置、重启服务或执行采集。
---

# JumpServer 服务器登录与只读命令

所有服务器访问必须走：

```text
宿主机 → docker --context lenovo exec envscope-local → JumpServer koko → 目标服务器
```

不要用 `ssh` 直连，也不要用仓库或本地文件里的密码自行拼登录命令。宿主机直连会被拒绝或缺少授权，且绕过统一提权逻辑。

## 执行方式

在仓库根目录运行，脚本从 stdin 进入容器：

```bash
docker --context lenovo exec -i envscope-local python3 - \
  --ip 10.58.9.86 \
  --cmd "hostname; id -u" \
  < .trae/skills/jumpserver-server-access/scripts/remote_exec.py
```

参数：

| 参数 | 说明 |
|---|---|
| `--ip` | 目标资产 IP，必须能在快照中唯一匹配 |
| `--cmd` | **单行**命令，多条用 `;` 或 `&&`；需要多行脚本时脚本会自动包成 `bash -c` |
| `--timeout` | 命令超时秒数，默认 90 |
| `--version` | 指定历史快照版本；默认读 `/data/current.json` 的当前版本 |

输出首行为 `# elevation: <method>; rc: <code>`，其后是命令输出。

## 登录链路

1. 从 `jumpserver-snapshot.json` 按 IP 找到唯一资产。
2. `application_check.open_terminal()` 读取 `/data/secrets/cookies.json`：
   - 用 `jumpserver` Cookie 调 JumpServer API 获取该资产授权账号；
   - 通过 koko WebSocket 登录，优先 `folidev`；
   - 按 `/data/secrets/ip-password-map.json` 记录的 `sudo -i` / `su` 方式提权。
3. 终端就绪后执行命令，结束关闭连接。

## 常用排查命令

```bash
# 进程与端口
ps -eo pid,user,etimes,args | grep -i <关键字> | grep -v grep
ss -lntp | grep <端口>

# 应用日志目录
ls -lat <catalina.base>/logs | head -20

# 最近错误（定向读取，不整份拉取）
grep -n 'ERROR' <logfile> | tail -20
grep -n 'Exception' <logfile> | tail -20

# 依赖连通性
timeout 3 bash -c '</dev/tcp/<ip>/<port>' && echo open || echo refused

# 配置来源（jar 内资源必须用 unzip -p，直接 grep 扫不到）
unzip -p <jar> <resource.properties>
```

## 判读与约束

- 只读：不改配置、不重启、不删文件；需要变更时先征求用户确认。
- 大日志只做定向检索和有限行尾读取，避免一次拉取整份日志。
- 快照里的 `loginStatus=cannot_login` 只代表上次采集失败，不代表当前不能登录；以本次登录结果为准。
- 报“无法通过 JumpServer 登录资产”通常是 Cookie 失效或账号未授权，应更新 JumpServer Cookie，不要改用宿主机 SSH。
- 不要输出或回显密码、Cookie、Token。
- 不用“某个端口连不上”直接推断应用故障，需结合监听、日志和下游依赖分别给证据。
