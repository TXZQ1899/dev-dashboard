---
name: backend-api-traffic-analysis
description: 统计并分类某后端服务经 Kong 网关转发的真实 API 流量（日请求数、接口 TOP、业务模块、来源域名、时段分布、状态码），基于多台 Kong 的 access log 采集后离线聚合。当用户问“某服务每天多少请求/哪些接口最热/流量从哪个域名来/要不要扩容/日活调用量”时使用。只读分析，不修改配置、不重启服务、不触发压测。
---

# 后端服务 API 流量分析

## 核心原则

一个后端服务往往被 **多个域名、多台 Nginx、多台 Kong** 暴露。唯一稳定的统计点是 **Kong 的 route 前缀**（如 `/prod-merchant-api/`）。

理由（已实测验证）：

| 统计点 | 问题 |
|---|---|
| Nginx per-domain access log | 按域名切分，一个文件混入多个后端服务 + 静态资源，总数虚高（实测 merchant.access.log 36,166 行，真实服务请求 30,216） |
| Nginx 全部 server block | 同一服务被多域名/多端口暴露，重复计数 |
| **Kong route 前缀** | **唯一指向一个上游服务；无论前面多少入口，都必须过 Kong，只计一次** |

## 流程（6 步）

### Step 1 定位服务在 Kong 上的 route 前缀

```bash
grep -oE '/prod-[a-z0-9-]+/' /usr/local/kong/logs/access.log | sort | uniq -c | sort -rn | head -30
```

前缀通常等于 Kong service 名。不要用裸服务名 grep（会命中 UA、referer 里的字符串）。

### Step 2 找出所有承载该 route 的 Kong 实例

**不能只抓一台**。典型架构是多台 Kong 轮询分流（实测两台分别 15,117 / 15,099，接近 1:1）。

判定方法：对候选 IP 逐个执行 `grep -c '/prod-<service>-api/'`，非 0 即为承载实例。

Kong 实例发现线索：Nginx 配置里的 `proxy_pass http://kong_servers/...`、`upstream kong_servers` 的 server 列表，或快照中监听 8000/8443 的 nginx 进程。

### Step 3 服务器端缩窄（关键，禁止全量拉取）

单台 access.log 常达 190MB，直接传输或全量扫描必然超时。

```bash
grep '/prod-<service>-api/' /usr/local/kong/logs/access.log > /tmp/ma.log; wc -l < /tmp/ma.log
```

后续所有聚合都基于 `/tmp/ma.log`，避免反复扫描大文件。

### Step 4 采集到容器（两种模式）

**模式 A（默认，推荐）**：服务器端聚合，只回传 KB 级统计结果 —— 直接在 Step 5 的命令里跑，不传输明细。

**模式 B（需要明细/复核/留存）**：流式落盘到容器

```bash
docker --context lenovo exec -i envscope-local python3 - \
  --ip <KONG_IP> --timeout 600 \
  --cmd "cat /tmp/ma.log" \
  < .trae/skills/jumpserver-server-access/scripts/remote_exec.py \
  | grep -vE '^(  \[|# elevation)' \
  | docker --context lenovo exec -i envscope-local sh -c \
    'mkdir -p /data/analysis/traffic/<service> && cat >> /data/analysis/traffic/<service>/<kong_ip>.log'
```

`grep -vE '^(  \[|# elevation)'` 必须保留，否则会写入提权提示行污染数据。落盘位置用持久化卷 `/data/analysis/traffic/`，结束后按需清理。

### Step 5 分类聚合（6 个维度）

聚合前先探测字段位置（UA 含空格，禁止盲取 `$NF`）：

```bash
awk 'NR==1{for(i=1;i<=12;i++) print i": "$i}' /tmp/ma.log
```

Kong 标准 combined 格式（实测字段）：`$1=client_ip  $4/$5=time  $6=method  $7=uri  $9=status  $11=referer`，UA 在 `$12` 之后且内部含空格。

以下命令中的 `\$` 是经宿主机双引号 `--cmd` 传递所需，复制时保留：

```bash
# 维度 1：HTTP 方法（含 CORS 预检）
awk '{gsub(/"/,"",\$6); print \$6}' /tmp/ma.log | sort | uniq -c | sort -rn

# 维度 2：业务模块（按路径段）
awk '{print \$7}' /tmp/ma.log | cut -d'?' -f1 \
  | awk -F/ '{if(\$4=="app") print \$5; else if(\$4=="manage") print "manage/"\$5; else print \$4}' \
  | sort | uniq -c | sort -rn

# 维度 3：接口 TOP（URI 归一化，数字段换成 {id}）
awk '{print \$7}' /tmp/ma.log | cut -d'?' -f1 \
  | sed -E 's#/[0-9]{2,}#/{id}#g' | sort | uniq -c | sort -rn | head -30

# 维度 4：来源域名（referer）
awk '{print \$11}' /tmp/ma.log \
  | sed -E 's#^"https?://([^/"]+).*#\1#; s#^"-"\$#(none)#' | sort | uniq -c | sort -rn | head -15

# 维度 5：时段分布（按小时）
awk '{print substr(\$4,14,2)}' /tmp/ma.log | sort | uniq -c

# 维度 6：状态码
awk '{print \$9}' /tmp/ma.log | sort | uniq -c | sort -rn
```

多日趋势使用 logrotate 归档（Kong 默认保留 15+ 天）：

```bash
for f in /usr/local/kong/logs/access.log /usr/local/kong/logs/access.log-*.gz; do
  day=\$(echo \$f | grep -oE '2026[0-9]{6}' || echo today)
  echo \"\$day \$(zgrep -c '/prod-<service>-api/' \$f)\"
done
```

### Step 6 校验

- 两台 Kong 计数差 **< 1%** → 采集完整
- 差异大 → 漏抓 Kong 实例、或日志轮转边界截断
- 与 Nginx 侧数字对照，差异应能由“其他服务流量 + 静态资源 + 重复入口”解释

## 无效尝试（踩坑清单，不要重犯）

1. **宿主机直连 SSH** —— 被拒绝；必须 `envscope-local` + JumpServer。
2. **用 Nginx 日志总行数当服务请求数** —— 单文件混多服务，实测虚高约 20%。
3. **只抓一台 Kong** —— 双机各约 50%，结果会少一半。
4. **在 `remote_exec.py` 里跑 python heredoc** —— 解析到 0 行；`stdin` 已被脚本自身占用。
5. **`echo <base64> | base64 -d | python3` 传脚本** —— 同样因 `stdin` 冲突失败。
6. **`awk '{print $NF}'` 取上游地址** —— UA 含空格，`$NF` 落在 UA 尾部碎片。
7. **把 190MB 全量日志经 stdout 回传** —— 超时（300s）且体积不可控。
8. **宿主机命令用未转义的 `$` / `$(...)`** —— 被宿主机 shell 提前展开成空值。
9. **按服务名裸 grep** —— 命中 UA、referer、JSON 正文中的字样；必须用 route 前缀。
10. **假设“配置了就有流量”** —— 某 Nginx 上 `/api/manage/equity/` 配置齐全，实测 0 命中。
11. **对 60MB+ 文件直接 `sort | uniq`** —— 超时；必须先 grep 缩窄再聚合。
12. **忽略 `# elevation` 提示行** —— 直接落盘会污染统计结果。

## 有效方法

1. **Kong route 前缀**是最干净的统计标识。
2. 先 `grep` 到 `/tmp` 再多次聚合，避免重复全扫。
3. **聚合在服务器端完成**，只回传结果，传输量从 MB 降到 KB。
4. URI 归一化：`cut -d'?' -f1` 去 query + `sed 's#/[0-9]{2,}#/{id}#g'` 收敛路径参数。
5. **双 Kong 交叉校验**，用差异率判断采集完整性。
6. **剥离 `OPTIONS` 预检**（实测占 17.5%），否则业务量虚高。
7. **referer 还原来源域名**，可验证多域名配置里哪些真正承载流量。
8. 用 logrotate 的 `gzip` 归档做多日趋势，成本极低。
9. `awk -F/` 按路径段切业务模块，比正则更稳。

## 判读约束

- 报数必须声明口径：**时间范围 + 是否含 OPTIONS + 几台 Kong**。
- 非 2xx 单独列出，不要混在总量里。
- 只读：不修改配置、不重启、不触发压测。
- 不输出密码、Cookie、Token。
- 分析结束后清理服务器 `/tmp` 临时文件与容器内中间产物。
