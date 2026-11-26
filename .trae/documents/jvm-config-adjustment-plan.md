# JVM 配置展示与参数调整留档 实施计划

## 仓库调研结论

### 当前数据链路
1. **进程 command 里已经有完整 JVM 参数**：`catalog_processes.py` 的 `attach_processes()` 从 JumpServer inspection 进程快照中匹配到了 Java 进程，但只把 pid/startedAt/elapsedSeconds/ports/portStatus 放进 `processObservation`，**没有把 command 字段传出来**。而进程 command（ps 输出）里包含完整的 `-Xmx4G -Xms4G` 等启动参数。
2. **前端已有 `jvmMemory()` 提取器**：`server-processes.ts` 里的 `jvmMemory(process)` 能从 command 正则提取 `-Xmx` / `-Xms` / `-Xmn` / `-Xss` 等 JVM 选项，并计算 RSS / Xmx 占比。但这个函数还没有被用到部署单元列表页。
3. **MySQL 已在用**（不是设计草案）：`runtime/migrations/001/002` 和 `deployment_catalog.py` 里已经有完整的 MySQL 迁移、连接、事务、缓存机制。新数据的权威存储用 MySQL。
4. **持久化选择**：JVM 参数调整历史是结构化操作日志（实例 → 参数对 → 操作人 → 时间 → 备注），**适合用 MySQL 新表存**，可以按实例查询历史、按时间倒序展示、未来接 DevOps 时有明确的数据源。容器卷继续存原始采集快照和大文件证据。

### 需要改的层次
- **后端数据传递**：`catalog_processes.py` → `processObservation` 增加 `command` 字段 → 前端收到后用 `jvmMemory()` 提取 JVM 配置
- **前端展示**：`deployment-units.tsx` 的 `InstanceDetails` 表格里增加 JVM 配置列 / 或在标题区展示
- **调整 UI**：点击 JVM 配置旁的"调整"按钮 → Modal 输入 xms / xmx → POST 到后端
- **后端 API + 存储**：新增 `/api/catalog/jvm-adjustments` POST/GET，新 MySQL 表存历史

## 文件与模块变更

| 文件 | 变更内容 |
|---|---|
| `environment-web/runtime/catalog_processes.py` | `attach_processes()` 匹配到进程后，增加 `command=process.get('command')` 到 processObservation |
| `environment-web/lib/deployment-catalog.ts` | `CatalogInstance.processObservation` 类型增加 `command?: string` |
| `environment-web/runtime/migrations/003_jvm_adjustment.sql` | 新建 `jvm_adjustment` 表 |
| `environment-web/runtime/deployment_catalog.py` 或 `runtime/service.py` | 新增 jvm_adjustments CRUD（POST 提交 / GET 查历史） |
| `environment-web/runtime/service.py` | 新增 `/api/catalog/jvm-adjustments` 路由 |
| `environment-web/app/applications/deployment-units.tsx` | InstanceDetails 区域：展示 JVM 配置、调整按钮、提交表单、历史查看 |
| `environment-web/lib/server-processes.ts` | `jvmMemory()` 函数已存在，可直接复用；也可加一个更简洁的 `extractJvmFlags(command: string)` |
| （可选）`environment-web/app/applications/deployment-units.css` | 新增 jvm-adjustment 相关样式 |

## 实施步骤

### Step 1: 后端 —— 把 command 带到 processObservation
- 修改 `catalog_processes.py` L73-76，MATCHED 分支增加 `command=process.get('command')`
- 类型定义 `CatalogInstance.processObservation` 加 `command?: string`
- 跑 `test_catalog_processes.py` 验证不破坏现有匹配逻辑

### Step 2: 后端 —— 新增 JVM 参数调整历史表 + API
- 新建迁移文件 `migrations/003_jvm_adjustment.sql`：
  ```sql
  CREATE TABLE jvm_adjustment (
    id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
    deployment_instance_id CHAR(36) CHARACTER SET ascii,
    instance_ip VARCHAR(45),
    unit_id CHAR(36) CHARACTER SET ascii,
    environment VARCHAR(20),
    current_jvm_config VARCHAR(512),    -- 调整时读到的实时 JVM 配置
    xms_new VARCHAR(32),                -- 用户填写的新 Xms（空表示不改）
    xmx_new VARCHAR(32),                -- 用户填写的新 Xmx（空表示不改）
    operator VARCHAR(191),              -- 操作人（当前简化为系统用户，后续接 SSO）
    note TEXT,                          -- 备注
    source VARCHAR(30) DEFAULT 'MANUAL', -- MANUAL / DEVOPS_API / ...
    created_at DATETIME(6) NOT NULL,
    INDEX idx_jvm_adj_instance (deployment_instance_id, created_at),
    INDEX idx_jvm_adj_ip_env (instance_ip, environment, created_at)
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
  ```
- 修改 `deployment_catalog.py` 的 `migrate()` 方法追加 version 3
- 在 `service.py` 注册 `/api/catalog/jvm-adjustments` 路由：
  - `GET ?instanceId=xxx` 查某实例历史
  - `POST { instanceId, ip, unitId, environment, currentJvmConfig, xmsNew, xmxNew, note }` 插入一条

### Step 3: 前端 —— 展示 JVM 配置
- 在 InstanceDetails 组件中，当行是 APPLICATION 类型且 `processObservation.command` 存在时，用 `jvmMemory()` 提取 JVM flags
- 在实例明细表的"部署 ID / 进程 ID"一列旁边，追加一行显示 `JVM 配置：-Xmx4G -Xms4G`
- 或者更整洁地：在展开行的标题区域（"一键巡检"按钮右侧）集中展示 JVM 配置概览

### Step 4: 前端 —— 调整 Modal + 提交
- 在 JVM 配置展示旁放一个"调整参数"按钮
- 点击弹出 Modal，显示当前配置、Xms / Xmx 输入框（支持 512M / 4G 格式）、备注
- 提交后：
  1. 乐观更新本地历史列表
  2. POST 到后端存 MySQL
  3. Toast 提示"调整记录已保存，实际 JVM 参数需人工或 DevOps 平台生效"
- 历史查看：Modal 或侧边栏显示该实例所有调整记录，时间倒序

### Step 5: 运行时验证
- 构建容器 `docker build -t envscope:local environment-web/`
- 在 `envscope-local` 容器内跑 Python 后端测试
- 页面访问 `http://localhost:3000/deployment-units`，展开某个应用环境，验证：
  - JVM 配置正确显示
  - 调整 Modal 提交成功、历史可查
  - MySQL `jvm_adjustment` 表里有记录

## 依赖与考虑

- **存储选型理由**：项目 MySQL 已成熟，调整历史是结构化日志（可查询、可审计、可关联实例），MySQL 明显优于 JSON 文件（需要自己管理锁、并发、历史查询）。用户提到"写在容器卷还是 MySQL"，选 MySQL。
- **当前 JVM 配置只展示、不修改实际启动参数**：用户明确说"这只是一个留档"，后端不触发任何进程重启或配置变更。后续对接 DevOps 时再增加 `source=DEVOPS_API` 和状态轮询。
- **进程 command 可能为空**：processObservation 的 command 在进程未匹配或 inspection 失败时会是空，前端需优雅降级（显示"未采集"而非报错）。
- **迁移追加而非修改 001/002**：遵循现有 migration 模式，新增 003。

## 验证

- `python -m pytest environment-web/runtime/test_catalog_processes.py -v`
- 容器内 `python -c "from runtime.service import ..."` 验证新路由可导入
- 页面手动验证 + `curl` 调用 API
- MySQL 查表确认写入

## 风险

- **前端类型变更**：`CatalogInstance.processObservation` 加可选字段是向后兼容的，旧缓存/旧快照不会坏
- **迁移幂等**：`migrations/003` 文件用 `IF NOT EXISTS`，多次执行安全
- **JVM 参数解析边界**：`jvmMemory()` 目前只识别 `-Xmx/Xms/Xmn/Xss`，如果后续有 `XX:+UseG1GC` 这种非 Xm 参数需求再扩展；当前用户场景只关心内存
