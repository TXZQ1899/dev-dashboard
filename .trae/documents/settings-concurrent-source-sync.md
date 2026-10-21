# Settings 页：单数据源并发重试采集

## Context（为什么做）

全量同步（`kind='sync'`）用 3 个并发槽采集 7 个来源（ECS / CLB / NAT / JumpServer / Codeup / Local GitLab / DevOps）。当阿里云 Cookie 失效时，ECS/CLB/NAT/Codeup 很快失败，但整个任务必须等最慢的 JumpServer 采集完才结束。`Service.start()`（[service.py](file:///Users/vian/WorkSpace/devops-analysis/environment-web/runtime/service.py#L721-L748)）在 `self.job['status']=='running'` 时直接拒绝任何新采集，前端 [page.tsx](file:///Users/vian/WorkSpace/devops-analysis/environment-web/app/settings/page.tsx#L197-L204) 又用全局 `running` 把全部按钮禁用，导致用户即使已修好 Cookie、对应来源早已失败/空闲，也必须等整轮跑完才能点“单独同步 ECS/CLB/NAT/云效”重试。

**目标**：采集任务运行期间，允许对「本轮已失败」的单个数据源立即并发重试；全量“保存并一键同步”和版本切换仍锁定。用户在 \[AskUserQuestion] 中已确认「仅单个数据源并发」这一范围。

## 关键设计决策（务必遵守）

1. **并发重试仅在「主任务是全量 sync 且该来源状态为『失败』」时允许。** 理由：`parallel_collect` 任一来源失败即 `raise`，`work()` 在 `activate()` 之前抛出（[service.py](file:///Users/vian/WorkSpace/devops-analysis/environment-web/runtime/service.py#L778-L780)），所以该主任务**必定失败、永不 activate**，并发任务直接 activate 是确定安全的、不会互相覆盖版本。若放开到「完成/等待」等状态，主 sync 成功后会 activate 并覆盖并发任务的新数据 → 必须避免。
2. **同时只允许 1 个并发任务。** 两个并发任务都以同一份 current 为基，后 activate 者会丢掉先 activate 者的新数据；顺次重试天然正确（第二个在无任务时走主任务路径，基=已激活版本）。
3. **`activate_lock`** **覆盖 build+launch+swap 全程，且在** **`self.lock`** **之外获取**（只在 [service.py](file:///Users/vian/WorkSpace/devops-analysis/environment-web/runtime/service.py#L884) 的 swap 处进入 `self.lock`）。`self.lock` 不能包住 build，否则每 4 秒轮询的 `status()` 会被阻塞超时。
4. **不纳入并发**：`kind='sync'`、`kind='switch'`、`kind='jumpserver_asset'`（后者由 JumpServer 页面读 `state.job` 驱动，保持原“运行期间锁定”行为）。

## 实现步骤

### A. 后端 `environment-web/runtime/service.py`

* **`__init__`（L103-135）**：新增 `self.extra_jobs = read(DATA/'extra-jobs.json', [])` 与 `self.activate_lock = threading.Lock()`。重启恢复时，对 `extra_jobs` 中 `status=='running'` 的条目标记 `failed`（并把其 `versions/<id>/version.json` 从 `collecting` 改为 `failed`），与主任务 L127-135 的处理一致。

* **新增辅助方法**：

  * `_active_jobs()`：返回主任务 + `extra_jobs` 中所有 running 的 job 列表。

  * `_any_running()`：`bool(_active_jobs())`。

  * `_source_label(kind)`：`local_gitlab→'Local Git Lab'`… 映射为全量同步 `progress.sources` 的键（`ECS/CLB/NAT/JumpServer/Codeup/Local GitLab/DevOps`）。

  * `_source_available(kind)`：kind 不在允许集合 → False；`_any_running()` 为假 → True（普通独立同步）；否则要求「主任务 kind=='sync' 且 `progress.sources[label]=='失败'` 且无其他 running 的 extra」；`progress.json` 缺失按不可用（保守）处理。

  * `_job_view(job)`：抽取 [status() L606-649](file:///Users/vian/WorkSpace/devops-analysis/environment-web/runtime/service.py#L599-L658) 中给 job 附加 `phase/progress/logTail/slotLogs/failures/jumpServer` 的逻辑；注意 L625 的 `self.job.get('phase')` 要改为 `job`，否则并发任务会误加“已采集 N 个应用”。

  * `_set_job(job, **changes)`：主任务走 `set_job`（写 `job.json`）；并发任务更新对应 record 并整体写回 `extra-jobs.json`（均在 `self.lock` 内）。

* **`start()`（L721-748）**：

  * `kind in ('sync','switch')`：改为 `_any_running()` 为真即拒绝（语义不变）。

  * `kind in ('jumpserver_asset',)`：保持「`_any_running()` 即拒绝」。

  * `kind` 为 7 个单数据源之一：`_any_running()` 为假 → 走原主任务路径（`self.job=...`）；为真 → 调 `_source_available(kind)` 校验，失败则抛明确 ValueError，通过则生成 identifier、`mkdir` 创建版本目录（捕获 `FileExistsError` 重试）、写 `version.json`、append 到 `extra_jobs` 并持久化、起线程 `work(kind, identifier, asset_id, job=record)`。

  * identifier 目录创建建议 `folder.mkdir()` 加异常重试，避免时间戳碰撞 500。

* **`work(self, kind, identifier, asset_id=None, job=None)`（L750-828）**：把内部所有 `self.set_job(...)` 替换为 `self._set_job(job, ...)`；`job` 默认 `self.job`。其余流程（复制 current 文件、跑 `collect.py`、合并 ip-password-map、`validate`、`activate`、标记 version ready）不变。

* **`activate()`（L859-890）**：函数体整体包在 `with self.activate_lock:` 内（内部原有 `with self.lock:` 保持在内层），串行化 build/launch/swap，避免两线程抢 3101/3102 端口、重复 `stop(self.retired)`。

* **`status()`（L599-658）**：返回体新增：

  * `extraJobs: [_job_view(dict(j)) for j in self.extra_jobs]`；

  * `sourceStates: { <kind>: {'busy':bool,'retryable':bool,'reason':str} }`，服务端统一判定（7 个数据源），前端只读取。

  * 保留 `job` 键不变（[jumpserver/page.tsx L110-122](file:///Users/vian/WorkSpace/devops-analysis/environment-web/app/jumpserver/page.tsx#L110-L122) 与 application-check 依赖它）。

* **收尾**：`scheduler()`（L908）与 application-check 冲突检查（L175）、archive（L318）统一改用 `_any_running()`。新主任务 `start()` 时清理已完成的 extra 记录（保留 running），并限制列表长度。

### B. 前端 `environment-web/app/settings/page.tsx`

* `State` 类型新增 `extraJobs?: Job[]` 与 `sourceStates?: Record<string, {busy:boolean; retryable:boolean; reason?:string}>`（`Job` 复用现有 job 结构）。

* 计算 `anyRunning = state.job?.status==='running' || (state.extraJobs??[]).some(j=>j.status==='running')`。

* 按钮门控：

  * 保存设置：`disabled={busy}`（不变）。

  * 保存并一键同步 / 生成 Topology / 版本切换：`disabled={busy || anyRunning}`（原 `running` → `anyRunning`）。

  * 7 个单独同步按钮：`disabled={busy || !enabled(key)}`，`enabled(key) = state.sourceStates?.[key]?.retryable ?? !anyRunning`；`syncSource('local-gitlab')` 映射到键 `local_gitlab`。

* 新增“并发任务”展示区块：遍历运行中/刚完成的 `extraJobs`，显示 `phase`、`logTail`、`failures`；提示文案说明“本轮全量同步该来源已失败，此处重试结果会在独立版本中激活”。

* 运行中提示文案补充“可重试来源”。

* [collection-progress.tsx](file:///Users/vian/WorkSpace/devops-analysis/environment-web/components/collection-progress.tsx) 无需改（单来源 run 无 `slots`，`hasCollectors=false` 返回 null，属既有行为）。

### C. 测试

* `environment-web/runtime/test_service.py`：保留 `test_concurrent_jobs_rejected` / `test_credentials_can_be_saved_while_a_job_is_running` / `test_startup_cannot_race_activation`；新增：`test_per_source_retry_allowed_only_after_its_sync_source_failed`、`test_per_source_retry_rejected_while_source_collecting_or_waiting`、`test_per_source_retry_rejected_while_another_extra_runs`、`test_per_source_retry_rejected_on_single_source_main`、`test_extra_job_writes_extra_jobs_not_main_job`、`test_extra_job_status_view_exposes_own_progress_and_log`、`test_restart_marks_running_extra_job_failed`、`test_activation_is_serialized_alternating_ports`、`test_scheduler_waits_for_extra_job`、`test_switch_sync_blocked_while_extra_runs`、`test_source_states_label_mapping`。

* `environment-web/tests/settings-page.test.cjs`：把 `sync, switch and per-source buttons stay locked while a job runs` 拆成 `sync/topology stay locked while any task runs` 与 `per-source buttons are gated by server retryability`；更新 `version switch ... keeps its own lock`（`running`→`anyRunning`）；新增 `extra job progress is rendered`、`running-job notice names retryable sources`。

### D. 文档

* [SETTINGS.md](file:///Users/vian/WorkSpace/devops-analysis/environment-web/SETTINGS.md)：L21 补 `/data/extra-jobs.json`；L27 的「一次只允许一个同步或切换任务」补充“全量与切换独占；单个数据源仅在其本轮全量同步中已失败时可并发重试”。

## 验证

1. 单测：`cd environment-web && python3 -m unittest discover -s runtime -p 'test_*.py'` 与 `npm test`。
2. 容器内受控 E2E：临时 `ENVSCOPE_DATA`，写入 `job.json(kind=sync, status=running)` + `versions/<id>/progress.json`（ECS=失败、JumpServer=采集中）+ 假 aliyun Cookie。`POST /api/settings/sync/ecs` 应 200，`job.json` 不变且响应含 `extraJobs`、`sourceStates.ecs.retryable=true`；`sync`/`switch`/对同一来源重复重试应 400。
3. 真实凭据手工验证：Cookie 失效 → 全量同步 → ECS 显示「失败」后其「单独同步」按钮解禁 → 点击重试 → 观察并发面板与最终 current 版本（含新 ECS、其他来源沿用上一版）。

## 影响文件

* `environment-web/runtime/service.py`（主要）

* `environment-web/app/settings/page.tsx`

* `environment-web/runtime/test_service.py`、`environment-web/tests/settings-page.test.cjs`

* `environment-web/SETTINGS.md`

* 需 `bash build.sh` + `docker --context lenovo compose build && up -d` 部署生效

