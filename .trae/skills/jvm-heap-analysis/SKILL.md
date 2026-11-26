---
name: jvm-heap-analysis
description: 对指定服务器上的 Java 进程做 JVM 堆转储并在容器内离线分析（MAT），输出堆概览、泄漏嫌疑、TOP 组件和 RSS 真实构成。用户要分析 JVM 内存、排查堆泄漏、解释 RSS 远大于 -Xmx 或直接说“dump 一下堆”时使用。会触发一次短暂的 stop-the-world，属生产敏感操作。
---

# JVM Heap Analysis

经 JumpServer 登录目标机，用进程自身的 JDK 执行 `jmap` 转储堆，压缩后传回 `envscope-local` 容器，用 MAT 离线解析。整套流程与产品页面「进程分析 → JVM 分析 → JVM 堆分析」共用 `environment-web/runtime/jvm_heap.py`。

## 前置条件

| 依赖 | 位置 | 说明 |
|---|---|---|
| JumpServer Cookie / 资产密码 | `/data/secrets/` | 由 Settings 保存，容器内已有 |
| 目标机权限 | — | 需能提权到进程属主（通常 root），`jmap` 才能 attach |
| 目标机 `python3` | `PATH` | 仅用于临时 HTTP 下载服务 |
| MAT | `/data/tools/mat` 或 `/data/tools/mat/mat` | MAT 1.17，解析需 Java 21 |
| JDK 21 | `/data/tools/jdk-21.0.13.1+2` | MAT 启动用，不要用 JDK 17（会静默退出码 14） |

可用环境变量覆盖：`ENVSCOPE_MAT_HOME`、`ENVSCOPE_JDK21_HOME`。缺失时报告会返回 `MAT_NOT_INSTALLED` / `JDK21_NOT_INSTALLED`，不会静默失败。

## 首次部署（持久卷，不写入镜像）

镜像不内置 MAT/JDK，工具安装在 `/data` 持久卷，容器重建后仍然保留；新数据卷或新环境需执行一次：

```bash
docker --context lenovo exec envscope-local python3 /app/runtime/install_jvm_tools.py
```

脚本可重复执行（已安装则跳过），默认从清华 Adoptium 镜像和 Eclipse 下载，并按内置 SHA-256 校验。离线环境用 `--jdk-archive` / `--mat-archive` 指定本地压缩包，`--force` 可重装，`--dir` 可改安装根目录。

## 执行

在仓库根目录运行（脚本经 stdin 送入容器，与 `jumpserver-server-access` 同一登录链路）：

```bash
docker --context lenovo exec -i envscope-local python3 - --ip 10.179.5.150 --pid 17915 \
  < .trae/skills/jvm-heap-analysis/scripts/jvm_heap_analyze.py
```

| 参数 | 说明 | 默认 |
|---|---|---|
| `--ip` | 目标服务器 IP（须在快照中有唯一资产） | 必填 |
| `--pid` | JVM 进程 PID（须在快照进程列表中唯一） | 必填 |
| `--out` | 数据卷内的报告与归档根目录 | `/data/jvm` |
| `--version` | 快照版本，省略读 `/data/current.json` | 当前版本 |

脚本会把报告写入 `<out>/<task-id>/`：

- `report.json`：结构化结论（同一份数据供页面展示）
- `mat/System_Overview`、`mat/Leak_Suspects`、`mat/Top_Components`：MAT HTML
- `*_System_Overview.zip` 等：可下载的 MAT 报告压缩包
- `mat.log`：MAT 控制台输出，失败时先看这里

校验完成的压缩转储保存在 `<out>/dumps/<应用ID>_<应用名>/<YYYY-MM-DD>/<任务ID>.hprof.gz`。应用归属无法唯一识别时使用 `<IP>_<进程ID>`。解压后的 `heap.hprof` 和 MAT 索引在任务结束时清理。

## 流程与要点

1. **确认进程**：`readlink -f /proc/<pid>/exe` 必须是 `/bin/java`；JDK 取 `JAVA_HOME`，否则用可执行文件的上两级目录。所有 `jmap` / `jstat` / `jcmd` 都用该 JDK 自带的二进制，避免多版本混用。
2. **采集运行指标**：`jstat -gc`（容量/使用量/YGC/FGC，单位 KiB）、`jcmd VM.flags`、`ls /proc/<pid>/task`、`/proc/<pid>/status` 的 `VmRSS`。这些是 RSS 拆分的实测依据。
3. **转储**：`jmap -dump:format=b,file=...`。不加 `live` 可保留尚未回收的不可达对象。JDK 8 HotSpot 的 `live` 模式在写文件前请求 Full GC。
4. **压缩**：目标机 `gzip -1`，通常 1.7 GiB 堆可压到约 400 MiB。
5. **传输**：目标机在临时目录启动单文件 HTTP 服务（`ThreadingMixIn` + 绑 0 端口 + 随机 token），容器流式拉取并校验 SHA-256。**不要**用 `python -m http.server` 的 `SimpleHTTPRequestHandler` 做断点续传：它不支持 Range；单线程 server 会被僵尸连接卡死。不要试图用 PTY 分块 base64 传 400 MiB，会超时。
6. **离线解析**：MAT 用 JDK 21 启动，必须覆盖 MAT 自带 ini 的 `-Xmx1024m`：

```bash
java --add-exports=java.base/jdk.internal.org.objectweb.asm=ALL-UNNAMED -Xmx2500m \
  -jar /data/tools/mat/plugins/org.eclipse.equinox.launcher_*.jar \
  -configuration /data/tools/mat/configuration -data <workspace> \
  -consolelog -nosplash -application org.eclipse.mat.api.parse <heap.hprof> \
  org.eclipse.mat.api:suspects org.eclipse.mat.api:overview org.eclipse.mat.api:top_components
```

7. **解读**：从 MAT HTML 解析概览（存活对象、类、类加载器、GC 根、JVM 版本）、TOP 组件和泄漏嫌疑；页面只展示结构化摘要，完整细节用报告压缩包打开。

## RSS 真实构成

`VmRSS` 通常远大于堆内活对象，按下列口径拆分（页面标注实测/估算/差额）：

| 项 | 口径 |
|---|---|
| Java 堆（已提交） | `jstat -gc` 的 `S0C+S1C+EC+OC`，实测 |
| Metaspace（已提交） | `MC`（含压缩类空间 `CCSC`），实测 |
| 线程栈 | 线程数 × `Xss`（默认 1 MiB），估算 |
| 其他 JVM / native | 上述之外的 RSS 差额：代码缓存、GC 结构、直接内存、malloc 等 |

典型结论：`-Xms` 等于 `-Xmx` 时，RSS 的大头是“已提交但未使用”的堆；先比较 `jstat` 的 OU/EU 与 `-Xmx`，再看 MAT 的存活对象，才能判断是泄漏还是启动参数过大。不要仅凭 RSS 断言泄漏。

## 判读与边界

- 存活对象只占已提交堆的一小部分、MAT 无显著嫌疑 → 优先建议下调 `-Xms`，而不是加堆。
- MAT 的 “Problem Suspect” 是启发式结果，需要结合业务确认；不要把嫌疑直接当作泄漏结论。
- 转储会造成秒级 stop-the-world，生产执行前应向用户确认影响。
- 只读原则：除 `/tmp/envscope-jvm.*` 临时目录和容器内报告目录外不修改目标机；结束时会清理临时目录。
- 堆转储可能包含业务数据。压缩归档保存在数据卷中，由操作人员按需手动删除。
