# G23 RECON — 架构与事务边界地图（调查员报告，主Agent核实关键行号）

BASE: origin/master b8f2a6ecc（worktree /home/kevin/projects/webgis/webgis-wt-g23-durable-ops-20261111T151520Z）
范围：data_import（G17）、field_compute（G19）、jobs/analysis_tasks（ADR-0052）、workflow_runtime、取消与进度。

## A. data_import 现状

### A.1 入口
- HTTP API `app/api/routes/data_import.py`：POST batches(:93)/list(:139)/detail(:151)/plan(:184)/preview(:212)/commit(:231，**background=True 硬编码** :247)/cancel(:254)/receipts(:270)/diagnostics(:286)/DELETE(:322)。挂载 main.py:1139。
- Pi 工具 `app/tools/data_import_tools.py`：inspect/plan/preview/**commit(:255-283 走 background=False 同步阻塞)**/receipt。注册 tools/__init__.py:92。
- UI `frontend/components/data-import/import-workbench.tsx`（useBoundedPoll 3000ms）+ `frontend/lib/api/data-import.ts`。

### A.2 进程内运行时状态（service.py:68-72）
- `_locks: Dict[str, asyncio.Lock]`（:69）进程内互斥；save_plan:220 / preview:289,327 / start_commit:355 / _run_commit:428,498,511 / delete:201。
- `_cancel_events`（:70）：start_commit 创建:394，cancel `event.set()`:563，finally 弹出:537；commit_member `_check_cancel`（commit.py:115-119，每 1000 行）。
- `_commit_tasks`（:71）：asyncio.create_task:404-406；delete/cancel 据此判在飞:196-198/:564。
- `_committing_ids`（:72）：**本实例**提交中集合；:391 加入，:538 移除。
- 单例 docstring :66 自述"锁与取消注册表进程内"——多进程假设不存在。

### A.3 恢复逻辑与多进程误判路径（代码证实）
`_load_owned()`（service.py:134-185）：
- 触发 :150-153：`COMMITTING and batch_id not in self._committing_ids`（且 :154-155 无活 task）→ 复位成员并推导批次终态、整文件覆写 batch.json（:184）。
- **误判路径（生产 API replicas≥2 真实存在）**：副本 A 提交中（`_committing_ids` 仅 A 进程内存）；副本 B 任意 GET/轮询 → B 复位 A 的活任务成员为 PLANNED、批次改 CONFIGURED。`save_state` 整文件 read-modify-write（staging.py:202-210），`rev`(:208) 只递增不校验 → A/B 互相覆盖。
- 次生路径：B 的 delete_batch 判无在飞（:196-198）→ rmtree staging（staging.py:291-299），A 中途源文件消失；B 的 cancel 走无在飞分支（:544-557）落 CANCELLED 但 A 的 event 未燃 → A 继续完整摄入、副作用已发生而 UI 显示已取消（终由 :513 读 cancel_requested 收敛）。

### A.4 staging 与 commit 步骤、事务边界
- 目录 `DATA_DIR/import_batches/<batch_id>/`（staging.py:3-8,72-81）：owner.json / batch.json（唯一事实源，原子写 :49-69）/ sources/（不可变）/ sources/extract/ / outputs/ / diag/。
- TTL 48h（budgets.py:35）**惰性清理**（staging.py:142-146,270-272），无后台 sweeper；会话批次上限 20。
- commit 步骤（commit.py:95-348，串行并发 1）：幂等复用(（member_key,plan_hash）+输出 ref 可读 :121-134) → 解释（与 preview 同引擎）→ plan_hash 复核（service.py:450-466）→ 空间 `IngestPipeline.ingest`→`ref:upload-*`+outputs 副本 / 纯表 `session_data_manager.store`→`ref:table-*`+artifact 登记 → 收据落账（service.py:497-509）。
- **无 DB 事务**：跨文件系统/SessionStore/registry 非原子，靠收据披露（commit.py:5-8 诚实声明）。

### A.5 budgets.py
文件 20/原始 200MB/解压 200MB/容器成员 64/zip 条目 4096/嵌套 1；预览 200 默认 2000 帽；提交行 500_000、取消间隔 1000 行、单成员输出 150MB；TTL 48h、批次 20；xlsx sheet 采样 200/上限 64。

### A.6 数据模型
pydantic+文件系统（models.py：ImportBatchState :210-235 含 rev:221/cancel_requested:235；CommitReceipt :185-207）。**无 SQLAlchemy/alembic**。

### A.7 输入/产物/MapSpec adoption
输入=已验证 session 的上传字节；产物=ref:upload-*/ref:table-*；adoption 发生在后续图层意图（mapspec_source.py:96-120 ref→source），G23 不改 MapSpec。

## B. field_compute 现状（G19）

- `app/services/data_preparation/field_compute/`（12 文件）。service.py 919 行；receipt.py 文件系统台账（BASE_STORAGE_DIR 下，stable_key=fingerprint+input_ref+content_hash）。
- preview/execute 共用 `_prepare`+`_eval_config`+`interpreter.evaluate_rows`（同解释器同快照，anchor 双重核对 :296-299/:539-541）。
- **无任何 job 机制**：execute 在 HTTP 请求内同步 await（routes/field_compute.py:156-174）；进程内 `_EXEC_LOCKS/_ACTIVE_EXEC/_CANCEL_REQUESTS`（service.py:60-69，TTL 300s）。
- 取消 `request_cancel`（:113-120）仅本进程生效。
- 发布闸门：草稿 published=False 先行 → store(ref:fc-*) → additive 登记 → 项目清单 → 已发布收据 upsert；失败 `_rollback_unpublished`（:630-661）。

## C. durable jobs 基础设施（ADR-0052）

- 表 `analysis_tasks`（db_model.py:97-183）：归属三元组、status CHECK 8 值、progress CHECK、result_summary/result_ref、heartbeat/cancel_requested、idempotency_key unique、attempt/worker_id（fencing）、dispatch_spec。迁移 0013。`job_kind` String(20) **无 CHECK** → 新 kind 纯加性。
- 状态机 lifecycle.py:33-193：cancelling 无→completed（late success 收敛 cancelled）；cancelled/completed 不可变；failed/stale 仅显式新 attempt。
- store.py：原子条件迁移 `_transition_stmt`(:391-419)；**fencing（#1398）** `require_worker_id/require_attempt`（:409-414）；幂等复用/释放（:113-134,195-251）；归属谓词三链（:88-110）；stale sweep 300s / orphan 3600s（:1016-1053/:955-984）；**周期驱动=main.py `_periodic_stale_job_sweep`（main.py:424,633-667，60s tick，多副本安全）**。
- submit.py：`build_execution_key`(:32-40)；pending→queued 原子认领防双投（:150-157）。
- worker.py（Celery 同步体）：认领即条件迁移（:362-396）；`_CancelWatchdog` 每 2.5s **DB 轮询**推 token + 30s 心跳（:217-304）；`checkpoint()` 内存便宜 / `ensure_not_cancelled()` 强制 DB（:111-139，用于不可逆副作用前）；`job.artifact()` 原子产物。
- views.py：JobView(:32-69)；atlas `page_status` ≤21 条投影先例（:92-123）。
- celery_origin.py：**与 Celery 无关**——Redis 投递池+origin 头。Celery app=task_queue.py:11-25（USE_REDIS=false → memory broker+eager :75-76）；include 列表无 data_import/field_compute。
- API：`/api/v1/tasks/jobs`（routes/jobs.py:54 挂载）list(:86)/get(:148)/DELETE cancel(:169-215：request_cancel 落 DB + 本进程 registry.cancel)/retry(:218)。
- **G18 范例**（data_delivery/service.py）：submit:232-304（幂等键 :98-106；**不走 Celery、不落 dispatch_spec、max_retries=0**，重试=同规格重提）；runner:309-400（registry token → mark_running 认领 → 阶段执行 → fenced 终态）；幂等恢复 :288-295（pending 且本进程无 runner → 补启动）；`_ACTIVE_RUNNERS` dict:70、`_WRITER_SEMAPHORE(1)`:67。**已知弱点：runner 不轮询 DB 取消（跨副本取消只在完成边界收敛）**。

## D. workflow_runtime 与分层结论

- workflow_runtime 是**平行 durable 运行时**（自有 5 表+两级租约 120s+事件 journal；main.py `_periodic_workflow_recovery_sweep`）。geocompute_runs 亦有 lease_epoch CAS。
- **结论：G23 接 jobs 层（G18 模式）**，不接 workflow_runtime（导入/字段计算是单作用域操作，无需 DAG）。
- 辅助件：distributed_lock.py=Redis per-session 锁（owner_token 轮换等）；singleflight.py=缓存防 stampede；task_queue.py 的 TaskQueueService._task_owners=旧兼容面。

## E. 取消与进度现状

- durable job 取消：DB `cancel_requested_at` 持久事实 + 同进程 token 即时 + **跨进程=worker watchdog 2.5s DB 轮询**；不用 Redis。
- data_import 取消：进程内 asyncio.Event——跨副本无效（A.3）。
- field_compute 取消：进程内 dict——跨副本无效。
- 进度：ProgressThrottle 1%/500ms；UI 3s 轮询 /tasks/jobs。
