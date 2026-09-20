# Codeup 代码组导出 Excel

运行：

```bash
python3 export_codeup_groups.py
```

默认读取脚本同目录下的 `codeup-cookie.txt`，访问 `https://codeup.aliyun.com/groups?navKey=all`。先从页面识别当前组织，再对 `/api/v4/groups/all` 分页，直到返回空页。仅发送 GET，请求不跟随重定向，Cookie 只发往固定的 Codeup HTTPS 域名。

输出到新的 `outputs/任务ID/codeup-groups-时间戳/`：

- `Codeup代码组清单.xlsx`：主表包含代码组、英文名称、中文名称、更新日期、代码库数量；附字段说明和来源 URL。
- `codeup_groups.json`：保留用于重建 Excel 的数据，不含 Cookie。
- 两张预览 PNG：用于核对表格布局。

字段口径与当前页面一致：代码组=`name`（显示名称），英文名称=`path`（英文路径），中文名称=`description`（描述原文）。页面没有独立的中文名字段，因此不自动翻译，未填写的留空。更新日期优先取 `last_activity_at`，否则 `updated_at`，使用北京时间、Excel 可排序日期类型。代码库数量取 `project_count`，与组内每个库是否有访问权限无关。

导出该“全部代码组”页面的所有分页记录，不递归展开子组。分页重复 ID、网络失败或数量字段缺失时停止，不将部分数据标成完整结果。

可选参数：

```bash
python3 export_codeup_groups.py --cookie-file codeup-cookie.txt --page-size 10
python3 export_codeup_groups.py --output outputs/my-codeup-groups
python3 export_codeup_groups.py --json-only
```

Excel 由 `environment-web/scripts/build_codeup_groups.mjs` 通过 `@oai/artifact-tool` 生成。当前电脑已配置 Codex 提供的 Node 和 `environment-web/scripts/node_modules` 依赖链接，无需额外安装。迁移到其他电脑时需要配置可用的 Node 和该库；通过 `--node /path/to/node` 或 `CODEUP_NODE` 指定运行时。`--json-only` 仅需 Python 标准库。

离线重建（使用已经配置好的 Node）：

```bash
/Users/vian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node environment-web/scripts/build_codeup_groups.mjs /path/to/codeup_groups.json /path/to/output-directory
```

登录过期会提示更新 Cookie；不需要在聊天中发送 Cookie。Excel 生成失败时 JSON 仍保留，可离线重建。
