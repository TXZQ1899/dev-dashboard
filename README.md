# DevOps 应用和服务器导出

**当前采集入口**：EnvScope 看板容器的 `http://localhost:3001/settings`（Cookie 配置、一键同步、北京时间每日定时、同步历史与版本切换），数据保存在 Docker 命名卷 `envscope-data`，详见 [environment-web/SETTINGS.md](environment-web/SETTINGS.md)。

本目录保留的是单项只读导出工具。宿主机每日调度（旧 `run_daily_collection.py --update-web`）与 `DAILY_COLLECTION.md` 已随调度迁移移除，仅保留以下脚本供容器内采集复用或按需手工运行。

Codeup 代码组导出见 [CODEUP_GROUPS.md](CODEUP_GROUPS.md)：`python3 export_codeup_groups.py`，读取 Codeup Cookie 并输出代码组 Excel。

详细运行日志导出见 [DEVOPS_DETAILS.md](DEVOPS_DETAILS.md)：`python3 export_devops_details.py --app-name fosun-aggregation-openapi`，按应用和环境保存汇总及各步骤日志。

## Codeup 仓库去重与权限检查

```bash
python3 check_codeup_access.py --input path/to/applications.csv
```

默认读取当前目录的 `codeup-cookie.txt`（单行 Cookie 请求头值，可带 `Cookie:` 前缀）。省略 `--input` 时选择最近修改的 `exports/*/applications.csv`；脚本自己在输出时创建 `exports/`，宿主机历史导出目录已随旧流程移除，按需自行指定输入。不回显、不写入 Cookie。无需安装依赖。

结果在新的 `exports/codeup-access-时间戳/` 目录：

- `repository_access.csv`：git仓库名、git地址、是否能访问、说明、HTTP状态、错误代码、关联应用数。
- `summary.json`：输入、去重数量、各状态数量和检查范围。

以主机名和完整仓库路径去重，统一 HTTPS/HTTP/SSH、`.git` 和末尾斜杠，保留大小写敏感的仓库路径；不同组织或代码组下的同名仓库不会合并。空地址和异常地址保留一条“未检查”记录方便修正。CSV 地址统一为 HTTPS Git 地址，名称取路径末段。

`是` 表示 Codeup 网页仓库查询成功；`否` 表示接口明确拒绝或仓库不存在/不可见；网络错误、未知响应标为 `无法确认`。登录异常时保留已有结果，剩余仓库标为 `未检查`，退出码为 2。Cookie 只发送到固定的 `https://codeup.aliyun.com`，不跟随重定向。旧版 `code.aliyun.com` 和其他 GitLab 域名不会使用该 Cookie，标为“未检查”，需各自平台登录态才能进一步验证。

使用前端实际调用的只读 `GET /portal/path_info?path=/组织/组/仓库`，依据 `success` 和 `result.pathResource` 判断，不能仅凭网页 HTTP 200 判断。检查的是网页读取权限，不是 SSH 密钥或 Git HTTPS clone 凭据。阿里云公开 OpenAPI 使用个人访问令牌而非此 Cookie，参考[官方 GetRepository 文档](https://help.aliyun.com/zh/yunxiao/developer-reference/getrepository-query-the-code-base)。网页内部接口以后可能变化，未知响应不会误判为有权限。

其他选项：`--cookie-file 文件`、`--output 新目录`、`--timeout 25`、`--delay 0.2`；`--dedupe-only` 只去重，不联网或读取 Cookie。

Python 3.9+，无需安装第三方依赖。在可访问公司内网的机器上运行。

## 使用

1. 用自己的账号登录 DevOps，打开应用列表。
2. 浏览器开发者工具 → Network（网络），刷新列表，选择 `/theone-web/ops/app/list` 请求。
3. 从 Request Headers（请求头）复制 `Cookie` 的完整值。不要复制响应中的 `Set-Cookie`，也不需要把 Cookie 发给任何人。
4. 执行脚本，在终端提示时粘贴 Cookie（不回显、不写入输出文件）：

```bash
python3 export_devops.py
```

首次可先验证示例应用：

```bash
python3 export_devops.py --app-id 1913
```

也支持 `DEVOPS_COOKIE` 环境变量，或 `--cookie-file /path/to/cookie.txt` 读取本地单行 Cookie 文件。Cookie 失效时重新登录并复制。脚本不会读取浏览器配置或保存密码。

## 输出

默认在 `exports/时间戳/` 生成：

- `applications.csv`：应用 ID、名称、类型、代码库类型、Git/代码库地址、分支、HTTP 名、端口、详情链接。
- `environments.csv`：每个部署实例一行，包含应用、环境、服务器 IP、部署 ID、配置 ID、读取状态；无配置、无实例和缺少 IP 会分别标注，不会静默丢弃。
- `errors.json`：失败的应用 ID、环境和原因。
- `summary.json`：仅在所有请求成功且列表条数核对通过时生成，记录完成状态和数量。

CSV 使用 UTF-8 BOM，可以用 Excel 打开。缺少 IP 的实例会标为“实例未返回IP”，请求成功不代表每个实例一定有 IP。返回原始 IP 字段，不从日志、域名或其他字段猜测服务器地址。重复 IP 的不同部署实例保留。

## 环境和范围

| 页面名称 | 接口环境代码 | 导出名称 |
| --- | --- | --- |
| 测试环境 | TEST | 测试 |
| 仿真环境 | SIMULATION | 仿真 |
| 线上环境 | PRODUCT | 生产 |

页面实际使用“仿真”，是否就是公司的 UAT 请以内部定义为准，导出不强行改名。

默认取消应用类型过滤，导出当前账号可读取的全部类型。页面默认只选 `war,war8,app,node,node_backend,h5,go`，如需和默认页面一致：

```bash
python3 export_devops.py --app-types war,war8,app,node,node_backend,h5,go
```

可用 `--output 新目录`、`--page-size 100`、`--timeout 30`、`--delay 0.15` 调整输出和请求参数。禁止覆盖已有目录，以免混入旧结果。

## 接口依据与完整性

接口、分页和字段来自平台当前前端 JS（2026-09-02 检查）：

- `GET /theone-web/ops/app/list`：`page.pn` 从 1 开始，`page.size`，`sort.createTime=desc`，`search.appType_in` 为空表示不筛选类型；响应 `obj.content` 和 `obj.totalElements`。单应用使用 `search.id_eq`。
- `GET /theone-web/ops/app/list/app?appId=...&envtype=...`：响应 `obj.deployList`，读取 `ip`、`deployId`、`configId`。
- 代码库地址是 `defaultContrVersionUrlMaster`，兼容回退到 `defaultContrVersionUrl`。

脚本不会调用发布、扩缩容、删除或修改接口。请求顺序执行，网络/429/部分 5xx 自动重试两次；禁止跟随重定向，避免将 Cookie 带到登录跳转目的地。登录/权限失效立即停止；普通环境错误继续收集其他环境并以非零状态退出。应用列表失败时不生成不完整的应用清单；环境阶段失败保留已写入的清单和明细。分页总数改变或出现重复 ID 时停止，建议列表稳定后重新完整运行。

尚未取得登录态：已确认未登录接口返回 302 登录跳转，未完成真实账号的端到端导出验证。请先对 1913 运行并核对详情页，再全量运行。导出范围受账号权限限制。

## 本地验证

```bash
python3 -m unittest discover -s tests -v
```
