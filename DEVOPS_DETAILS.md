# 应用汇总与运行详情日志导出

脚本：`export_devops_details.py`。Python 3.9+，仅使用标准库；与 `export_devops.py` 放在同一目录。默认读取 DevOps 的 `cookie.txt`，不是 Codeup Cookie。

## 运行

按应用名精确匹配，可多次指定：

```bash
python3 export_devops_details.py --app-name fosun-aggregation-openapi
python3 export_devops_details.py --app-name 应用A --app-name 应用B
```

按 ID 验证，或全量导出当前账号能读取的应用：

```bash
python3 export_devops_details.py --app-id 1911
python3 export_devops_details.py
```

其他参数：`--cookie-file cookie.txt`、`--output 新目录`、`--timeout 30`、`--delay 0.15`。输出目录必须不存在，防止混入旧结果。不输出 Cookie 或日志正文到终端。

## 目录和文件

```text
exports/devops-details-时间戳/
  export_report.json
  fosun-aggregation-openapi/
    summary.json
    测试环境/
      logs/
        10.58.10.91_deploy-19117/
          index.json
          build-1391889_resource-38259_action-11/
            Deploy_2026-08-18 18-34-22_SUCCESS_log.txt
            source_response.txt
            step.json
    仿真环境/
      logs/...
    线上环境/
      logs/...
```

- **summary.json**：详情 URL、应用 ID、应用名称、代码仓库类型、代码库地址、JDK 版本；三个环境的服务器 IP、部署 ID、scene ID，以及日志导出状态和错误。先写汇总，再下载日志。
- **操作名_开始时间_状态_log.txt**：步骤详情日志。时间使用接口原始时区值，不推断或转换时区；文件名中的冒号替换为 `-`，没有开始时间用“未开始”。操作名动态读取，支持 Deploy、PullOut、Stop、Start、PushIn 等，不限定操作列表。
- **source_response.txt**：日志接口原始响应，保留原始格式以便核对；日志正文文件会把 `<br/>` 转换为换行、去掉 `<b>` 展示标签，再解码 HTML 实体，保留日志中的 XML 和 Shell 符号。
- **step.json**：操作名、IP、构建/资源/操作 ID、原始时间和状态、日志路径、导出前后执行状态及是否仍在变化。
- **index.json**：一个部署实例所有步骤的索引和失败记录。
- **export_report.json**：应用目录索引和总体完成情况。

服务器、部署实例、构建/资源/操作分别分目录，确保同名步骤不会覆盖。应用名称遇到非法路径字符会替换，过长名称会截短并加摘要；同名或大小写冲突的应用目录加应用 ID，真实名称保留在 summary.json。

## 导出范围和状态

导出的是**各部署实例点击“运行详情”后当前页面返回的全部步骤日志**，不是所有历史发布记录。接口随新部署变化，因此不同时间运行可能对应不同 Build ID。

无环境配置、无实例、没有启用“运行详情”按钮分别记录。按钮存在但解析不到步骤会报错，避免将登录页或未知页面当作无日志。网络及部分服务异常重试两次；单个步骤失败后继续其他步骤，错误记入索引及汇总。登录/权限错误停止，已导出文件保留。

正在执行的步骤导出当前日志快照，不等待部署结束。下载前后再次核对状态；若变化，`changed_during_export=true`，需稳定后重跑。日志接口返回空内容时保留空文件，`log_bytes=0`，不伪造日志。`complete` 表示所有查询/保存成功，不代表每个部署成功或每个运行步骤已经结束。

退出码：0=导出完成；2=部分日志失败；1=登录、连接或输入错误；130=手动中断。

## 只读接口依据

根据平台前端实际实现和应用 1911 的返回结构：

1. `/theone-web/ops/app/list` 获取应用详情字段。
2. `/theone-web/ops/app/list/app` 获取三个环境的部署实例。
3. `/theone-web/ops/app/deploy/searchPlandetail?sceneId=...&objectId=...&objectType=deploy_type` 获取运行详情表格。
4. `/theone-web/build/{buildId}/{sceneResourceId}/{actionTypeId}/actionRun` 获取时间、执行状态。
5. `/theone-web/build/{buildId}/{sceneResourceId}/{actionTypeId}/getLogContentFromFile` 获取日志正文。

全部使用 GET。页面中同时包含“重新执行”“继续”等会触发运行的接口；脚本不执行页面 JavaScript，并对运行详情请求路径使用只读白名单，避免误调用这些接口。
