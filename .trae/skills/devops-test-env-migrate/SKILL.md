---
name: devops-test-env-migrate
description: Run DevOps test environment instance IP migrations via CDP. Use when the user asks to migrate a test-env app, move a batch of apps per the serial plan workbook, or retry a failed Deploy. Do not use for prod or sim environments.
---

# DevOps 测试环境 IP 迁移

用仓库 `scripts/devops_migrate/run_migrate.py` 通过 Chrome DevTools Protocol 在 DevOps 上修改测试环境实例 IP 并等待部署完成。

## 启动顺序

1. **确认 Chrome 已启动且 CDP 端口可访问**（9222）：
   ```bash
   curl -s http://127.0.0.1:9222/json/list | python3 -c "import sys,json;[print(t['url'][:80]) for t in json.load(sys.stdin)]"
   ```
   没有任何 tab 时，用持久化 profile 启动 Chrome：
   ```bash
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
       --remote-debugging-port=9222 \
       --user-data-dir=/private/tmp/mig/chrome-profile \
       --no-first-run --no-default-browser-check about:blank
   ```
   首次启动后在浏览器中访问 `http://devops.folidaymall.com/` 完成 SSO；后续复用 profile。
2. **准备 venv 并安装依赖**：
   ```bash
   cd scripts/devops_migrate && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
   ```
   依赖仅 `websocket-client`，不影响仓库其它脚本。
3. **目标 IP 来源**：
   - 单应用：用户给或从"应用迁移明细"D 列（应用 ID）+ H 列（当前服务器）+ I 列（目标服务器）读。
   - 批量：从串行迁移计划"Sheet 的 本步终点 列（G 列 = 7）或 S 列（实际目标IP = 19）读。
   - **当前已在最终目标的应用直接 SKIP**，不做任何操作。

## 单应用迁移

```bash
cd scripts/devops_migrate && . .venv/bin/activate
python run_migrate.py migrate <应用ID> <目标IP>
# 示例: python run_migrate.py migrate 1618 10.58.9.60
```

脚本自动完成：停实例 → 悬停卡片 → 编辑 → 选 IP → 移除旧 IP tag → 保存 → 二次核对持久化 → 回到详情卡点部署 → 监控"运行详情"9 步骤 → 若 Deploy FAILED 自动"继续..."重试一次 → FINAL: SUCCESS / FAILED / SKIP / ERROR。

**部署时间**：WAR（war8）约 3–5 分钟，APP 类型约 8–10 分钟。脚本给两次 9 分钟等待窗口，足够覆盖。不要自己再等待/轮询，让脚本跑完。

## 批量从工作簿执行

```bash
cd scripts/devops_migrate && . .venv/bin/activate
python run_migrate.py batch \
    <工作簿路径> <Sheet> <起始Excel行号> <结束行号> [目标IP列号]
# 示例: python run_migrate.py batch \
#     outputs/01a1a600/测试环境六大资源池-三页串行迁移计划.xlsx \
#     串行迁移计划 8 20 19
```

- 列号约定（1-based）：应用 ID=D(4)、应用名称=E(5)、状态列=P(16)、备注列=T(20)。
- 第 6 个参数（目标IP列号）默认 7（G 列计划终点）；传 19 则从 S 列实际目标读。
- 每行执行完立即 `wb.save()`，中途 Ctrl+C 也不会丢。
- **必须先按用户给的起始/结束区间**，不要默认全表跑完。

## 手动恢复

失败的 Deploy 大多是 Jenkins 构建（`tcc_app_test_git_0_jdk18`）自身问题，不是 IP 脚本问题。在原 Plan tab 点"继续..."即可让脚本从失败步骤往下走，不要重新点部署：

```bash
python run_migrate.py resume <planTabUrl>      # 点 FAILED 行 "继续..." 后等待
python run_migrate.py wait <planTabUrl> [秒]   # 只轮询，不操作
```

plan tab URL 形如 `http://devops.folidaymall.com/theone-web/ops/app/deploy/searchPlandetail?sceneId=283&objectId=19416&objectType=deploy_type`，脚本日志里会打出来。

## 关键约束（多次实测印证）

- **保存 IP ≠ 迁移完成**：保存后卡片仍显示旧值；必须点部署并进入"安装状态：运行中"，卡片 IP 才刷新。`run_migrate.py migrate` 覆盖这两步，单独 save 后务必继续 migrate。
- **编辑图标只在鼠标悬停后渲染**：CDP 侧用 `Input.dispatchMouseEvent(type='mouseMoved')` 真实悬停，再 `DOM el.click()` 最稳定；直接 `click_js` 会被悬浮层拦截。
- **完成判据是 9 步骤全部 SUCCESS**（Install → InstallFlume → Update Configuration → Configure Anchor → Deploy → PullOut → Stop → Start → PushIn）。PullOut/Stop/Start/PushIn 未执行 = Deploy 失败，服务没切换。
- **多 tab 不干扰**：migrate() 只关闭新开的 plan tab，旧 application/info tab 会留在浏览器里但下次运行自动找到最新匹配项。清理 Chrome 即可。
- **端口、Nginx、实时内存** 由计划阶段负责，脚本不做这些检查。若运行时因端口冲突或资源不足失败，是目标机自身问题，记录后跳过。

## 手工兜底

脚本依赖 CDP 和持久化登录的 Chrome。若遇到 CDP 连接断开或脚本在中途崩溃，切到手工操作——详见 [测试环境调整操作指引.md](../../测试环境调整操作指引.md) §2–§10，关键要点：

1. 打开 DevOps 应用详情 `http://devops.folidaymall.com/#/project/application/info?id=<应用ID>`
2. 选中 **测试环境** 标签，核对卡片 IP、实例名
3. 点"停止"并等待停止成功
4. 鼠标悬停卡片 → 点蓝色铅笔编辑图标
5. IP 行 → 选择IP → "所有主机"搜索框输目标 IP → 回车 → 勾复选框 → 点向右箭头 → 确定
6. 移除旧 IP tag → 保存 → 重新进入编辑页核对持久化 IP
7. 回到详情卡点"部署"，查看安装状态变成"运行中"、卡片 IP 刷新为新值
8. 打开"运行详情"监控 9 步骤，全部 SUCCESS 才算完成
9. 若 Deploy FAILED，打开 "日志"/"Jenkins" 排查，或在原 Plan tab 点"继续..."

## 输出与记录

迁移过程日志会打印每一步时间戳和步骤状态；成功/失败/跳过三类，直接 `FINAL:` 前缀。批量模式每完成一行写回工作簿的 **状态列 P(16)** 和 **备注列 T(20)**，格式为 `<起始时间>-<结束时间> <结果>`。结束时总结：成功 N、跳过 M、失败 K、未开始 L。
