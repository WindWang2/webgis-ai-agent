# G23 PLAN — 导入与数据操作的持久任务运行基础

BASE_SHA: `b8f2a6ecc777d0ae633202034b609a48a8d140d9`（origin/master，2026-11-11）
Worktree: `/home/kevin/projects/webgis/webgis-wt-g23-durable-ops-20261111T151520Z`
Branch: `zcode/g23-durable-data-operations-20261111T151520Z`

## 核心判断（基于亲自读码）

1. **durable jobs 基础设施已完备且无需迁移**：`app/services/jobs/`（ADR-0052）
   - 事实源 = `analysis_tasks` 表；原子条件迁移（`store.py:391-448`）
   - fencing = `worker_id` + `attempt`（#1398，`store.py:409-414`）——这就是"递增 generation/fencing token"，`start_retry` 递增 attempt
   - 心跳/stale 清扫（300s）/孤儿清扫（3600s）（`store.py:897-1053`）
   - 取消两段式：DB `cancel_requested_at` 持久事实 + 进程内 token（`routes/jobs.py:169-215`）
   - 幂等键唯一约束 + 复用/释放语义（`store.py:113-134, 195-251`）
2. **G18 已确立接入范式**（`data_delivery/service.py:232-304`）：`DurableJobStore.create`(幂等键) → 复用判断 → 进程内 asyncio runner（`mark_running` 原子认领 + fenced 终态 + `cancellation_registry`）。**G23 完全沿用该范式**，不自建新层。
3. **两个改造对象都是进程内状态**：
   - `data_import/service.py:69-72`：`_locks/_cancel_events/_commit_tasks/_committing_ids`；恢复逻辑 `service.py:148-184` 以"本实例无在飞任务"复位 COMMITTING——多 API 进程下会误复位别的进程活任务（**待双进程实验证实**）
   - `field_compute/service.py:60-69`：`_EXEC_LOCKS/_ACTIVE_EXEC/_CANCEL_REQUESTS`（进程内取消登记）
   - 两者都已有**文件级幂等收据**（import: staging receipts+plan_hashes；compute: fingerprint 收据 `receipt.py`）——G23 不重造，只把"执行活性/取消/进度"搬进 durable job
4. **生产拓扑**（docker-compose.prod.yml）：api 容器 + celery-worker 容器（`celery -A app.services.task_queue worker --concurrency=2`）共享 DATA_DIR 卷；生产 Postgres+Redis。本地无 Redis/Postgres → 双进程集成用两个真实 uvicorn + 共享 SQLite 文件库。
5. **任务中心**（`/api/v1/jobs` list/get/cancel/retry + `JobView`）已通用；atlas_export 的 `page_status`（views.py:92-123）是成员级投影先例——照此加 data_import 的 member 投影。

## 设计

### 新增共享模块 `app/services/data_operations/`（新命名空间，无碰撞）

- `runner.py` — **async durable runner**（G18 手法的提取复用，不新建状态机）：
  - `async_durable_job(job_id)` async context manager：原子认领 pending|queued→running（worker_id=`api:{hostname}:{pid}`）→ 启动心跳 asyncio 任务（30s）+ 取消轮询（2.5s，读 DB durable 事实 + 点燃 registry token）→ 正常退出 fenced `mark_succeeded`、`OperationCancelled`→`confirm_cancelled`、异常→`mark_failed`（均带 worker_id+attempt）
  - `AsyncJobHandle`：`ensure_not_cancelled()`（不可逆副作用前强制读 DB）、`progress()`（节流）、成员级进度写入 `result_summary`（有界）
  - 短事务纪律：每次心跳/取消查询/进度写各自独立 session，绝不跨线程共享 Session
- `import_jobs.py` — 导入提交接线（task_type=`data_import_commit`，kind=`data_import`）
- `compute_jobs.py` — 字段计算接线（task_type=`field_compute_apply`，kind=`field_compute`）

### 幂等键（P1）

- import：`data_import_commit:{user_fingerprint}:{batch_id}:v{plan_version}:{sha256(选中成员 plan_hashes 排序)}` —— 覆盖 owner scope、operation、plan version、source revision（源 sha256 已在 plan_hash 内）、参数指纹
- compute：`field_compute:{session_id}:{plan_id}:{compute_fingerprint}`（fingerprint 已含 IR/函数集/列/范围/单位；执行前 anchor 复核内容版本，改数据→typed 失败不静默）
- 重复提交：活跃/近期成功 → 复用既有 job；cancelled/failed → 释放旧键建新行（store 既有语义）
- 签名 URL/带 secret 的引用一律不进幂等键

### data_import 改造（保持对外 API 形状）

- `start_commit`：既有校验后 → 查/建 durable job（幂等键）→ `state.active_job_id=job_id`（staging state 新增字段，默认 None，加性）→ 置 COMMITTING → 启动 runner（进程内 asyncio task，登记 `_ACTIVE_RUNNERS`）→ 返回 `{job_id, batch_id, status}`；`background=False` 保留（同步等完成，供小 fixture 兼容路径），但内部同样走 job
- runner 循环 = 现有 `_run_commit` 骨架：逐成员 `ensure_not_cancelled()`（发布前）→ `commit_member`（不动）→ 收据仍写 staging state（receipt 事实源不变）→ 每成员后 job progress（已处理/总量/已提交）
- `_load_owned` 恢复逻辑改判据：`state.status==COMMITTING` 时查 durable job（`active_job_id` → DB）；**活跃 job 在（含另一进程的）→ 不复位**；无活跃 job → 按现规则归一化（收据优先）→ **消除 A03 误复位**
- `cancel_batch`：有活跃 job → `DurableJobStore.request_cancel`（跨进程持久事实）+ 本进程 token/事件即时联动；job 已终态 → 明确"完成后取消"语义（completed 不变，如实返回）；无 job（旧数据）→ 保留现有文件路径行为（兼容）
- 终态后 job `result_summary` 写有界成员清单（≤50：key/status/error_code 单行/行数），`result_ref` 指向批次收据端点

### field_compute 改造

- `execute()`：先走幂等收据短路（既有 `find_receipt`）→ 否则查/建 durable job → runner 执行 `_execute_locked`：
  - `_check_cancel` 扩展：除进程内 `_CANCEL_REQUESTS` 外，接受 runner 注入的 durable 取消探针（轮询缓存的 job 取消事实）
  - 批间进度：`computed_rows/total_rows` 写 job（不知总量→indeterminate，不伪造百分比）
  - 发布前 `ensure_not_cancelled()`；fenced 终态写 receipt 摘要
- `request_cancel`（service 级）：无本进程活跃执行 → 转为 durable job `request_cancel`（跨进程生效，A07）；有 → 两者都做
- 预览与执行仍同解释器同快照（不动 interpreter/snapshot）

### JobKind / 视图（P6）

- `JobKind` 增 `data_import`、`field_compute`（String(20) 列无 CHECK，纯加性，无迁移）
- `JobView` 加 `member_status: Optional[list[dict]]`（仿 `page_status`，仅 data_import 填充，≤50，脱敏单行 error）
- 前端导入面板：批次详情透出 `job_id` 与任务中心链接（最小 additive）

### Pi 工具（P6）

- `import_batch_commit`：改为 submit + 返回 `{job_id, 紧凑摘要}`；新增 `import_commit_status`（查 job+收据，终态才给 produced_refs；继续制图仅在 completed 且产物可读时成立）
- `field_compute_apply`：同型改造（submit + handle）；新增/复用 status 查询工具
- 均不做长阻塞等待；错误脱敏；权限不足 → 404 语义（JobNotFound 既有约定）

### 取消/补偿语义（P4，映射到既有不变式）

- commit 边界前取消：成员复位 planned，无副作用（staging 收据即证据）
- 提交中取消：成员级 `ensure_not_cancelled` → 当前成员收 cancelled，已完成成员保留（partial 披露）；**源文件不改**（staging 只读）
- 完成后取消：completed 不可迁（IMMUTABLE），返回明确"已完成，取消无效"（A09）
- 迟到完成：cancelling→cancelled 收敛（store 既有，A08）；
- 已发布产物保留并在 result_summary 披露 retained（不可回滚的明示）；临时文件清理仅限本 job 的 `.part`/staging 临时物

### 调查补充的两个关键设计点（RECON_RISKS R5/R6/R9-7）

1. **runner 必须轮询 DB 取消**（修 G18 范式已知弱点）：生产 API ≥2 副本（k8s replicas:2 + HPA min2），跨副本取消若只靠完成边界收敛，长导入（500k 行）长时间不响应。runner 心跳循环同时读 `cancel_requested_at`，点燃本地 token + `_cancel_events`。
2. **活跃 job 持有批次写权**（R9-7）：存在活跃 job（pending/queued/running/cancelling）时，save_plan/preview/delete 等非 job 写路径一律 409（既有 COMMIT_IN_PROGRESS 语义的 durable 化）；cancel 走 durable 取消。替代对 batch.json 的跨进程 CAS 需求（`rev` 已在但本期不引入乐观锁，靠写权门禁+job 串行化）。

### 不做（Non-goals）

- 不新建第二任务表/DAG/工作流引擎；不动 workflow_runtime
- 不重写解释器/ingest 管线/格式支持范围；不扩流式声明
- 不做通用 lease 服务器；不宣称同主机文件锁为分布式锁
- 不动 G18 writer、Lakehouse GC（#2070 有 owner）、governor 重构
- Celery 化这两个操作（保持 G18 同款进程内 runner + DB 事实源；retry 语义 = 重新提交幂等键，max_retries=0，不落 dispatch_spec）

## 工作包顺序

| WP | 内容 | 产出 |
|---|---|---|
| P0 | 生命周期地图（本文件+RECON_ARCHITECTURE.md）；**双进程误复位复现脚本**（BASE 行为基线） | 复现证据（失败先例） |
| P1 | `data_operations/runner.py` + 幂等键构造 + import/compute 提交接线 | 代码+单测 |
| P2 | `_load_owned` durable 判据改造、cancel 桥接、fenced 终态 | 代码+单测（A03/A05/A07/A08/A09） |
| P3 | 成员级 checkpoint/进度投影、收据对账补齐（commit后receipt前 crash → 幂等键查询核实补齐，不盲目重做） | 代码+单测（A11/A13） |
| P4 | 取消语义矩阵落实 + retained 披露 | 代码+单测 |
| P5 | 内存/背压审计：commit_member/evaluate_rows 已有界性核实，补 job 级统计（行数/字节）| 审计记录+必要修正（A17/A18/A19） |
| P6 | JobKind/JobView/Pi 工具/前端链接 | 代码+契约测试 |
| P7 | 双进程+SQLite 集成验证（真实 uvicorn×2）、故障注入矩阵、10万行规模档 | TEST_EVIDENCE.md |
| RV | 独立 review → fix → review 循环 | REVIEW.md |

## 验收矩阵映射（A01–A26）

全部映射到测试名/集成脚本，见 ACCEPTANCE.md（实现期间维护）。

## 风险

- `analysis_tasks.parameters` 的 JSON 体积：成员清单入 result_summary 有界裁剪
- SQLite 双进程写锁竞争：WAL + busy_timeout；集成脚本控制并发度；生产 Postgres 行锁语义在单测层面以方言无关 SQLAlchemy 语句保证
- 旧批次（无 active_job_id 字段）：默认 None → 恢复逻辑兼容旧文件（JSON 反序列化缺字段）
