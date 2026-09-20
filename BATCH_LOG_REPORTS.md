# 批量部署日志报告

在项目目录运行（Python 3.9 或更新版本，无需安装第三方依赖）：

```bash
python3 batch_analyze_deployment_logs.py --input exports --output outputs
```

脚本依赖同目录的 `analyze_deployment_logs.py` 中的参数解析函数。只读取本地已导出的文件，无需 Cookie，不访问平台，不执行日志中的脚本或命令。

## 输出

每个应用生成两个文件，直接放在指定输出目录：

- `应用名.md`：按环境、服务器和部署快照组织的完整报告，包含代码与构建、应用运行与分发配置、IP/端口/名称表、操作时间和平台状态、数据缺失及需复核的问题。
- `应用名.json`：上述内容的精简结构化数据，没有 `evidence` 节点，也不包含原始日志或完整全局参数表。
- `_analysis_index.md` / `_analysis_index.json`：本次生成的应用清单、文件名、记录覆盖情况和解析错误。

例如：`outputs/fsop-productcenter.md`、`outputs/fsop-productcenter.json`。

## 使用选项

仅处理一个应用：

```bash
python3 batch_analyze_deployment_logs.py --input exports --output outputs --app fsop-productcenter
```

仅分析一个导出批次：

```bash
python3 batch_analyze_deployment_logs.py \
  --input exports/devops-details-时间戳 \
  --output outputs
```

`--input exports` 会扫描下面所有 `devops-details-*` 批次，以 `summary.json` 的应用名分组，遍历各环境 `logs/*/index.json` 指向的步骤日志。相同部署、环境、服务器及内容的重复快照会合并，内容不同则分别保留。文件名中不适合文件系统的字符会替换为下划线。

重复运行会更新本次涉及的应用报告及索引，不删除其他应用的旧文件。因此，本次处理范围以 `_analysis_index.json` 为准；单应用运行也会更新索引为单应用清单。

## 数据口径

- 这是导出快照分析，不是从平台补拉完整部署历史。
- 以步骤中的 Deploy 开始时间汇总“日志中最新 Deploy 开始时间”；缺失时保留空值，不拿文件修改时间或构建版本字符串替代。
- 基本信息取自应用摘要；各环境的构建参数、分支、运行配置取自各自日志，不把摘要仓库当作已确认的构建来源。
- 一个快照的多个步骤若存在不同配置值，用数组保留。多个导出批次的应用摘要不同，也保留不同值。
- 输出为空、文件缺失、运行详情导出失败、缺少索引等情况会在报告中说明。解析失败项另列在索引的 `errors` 中。
- `network_endpoints` 包含 IP、域名、明确端口、名称、配置名及范围。未明确的端口为 `null`；名称可以是配置名称、应用映射名、脚本 VM 名或从配置名识别的服务类别，不作反向 DNS 查询。
- 全局配置、其他环境配置、脚本模板、注释与实际输出的地址分别标记。记录了 SSL 端口不代表 SSL 已启用。
- 平台 SUCCESS 不能替代各子命令成功及应用健康检查；复核项使用规则发现错误关键词，需人工结合实际情况确认。
- 保留 JDK 原始标签及 JAVA_HOME 模板候选，不推断实际运行的 Java 版本。
- 按敏感参数和认证参数等规则脱敏。自动规则不保证覆盖所有未知格式的敏感内容，原始文件不会修改。

## 验证

```bash
python3 -m unittest discover -s tests -p test_batch_log_analysis.py
```

覆盖重复快照合并、变化快照保留、空/缺失日志、包含空值和空格的构建参数、明确端口及注释范围、成功状态下的错误输出、凭据脱敏和路径越界检查。
