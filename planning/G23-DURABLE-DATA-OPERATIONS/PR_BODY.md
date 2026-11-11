# G23 — 导入与数据操作的持久任务运行基础

BASE `b8f2a6ecc` → HEAD `e4bcb4dbb`（5 commits，+4722/−169，45 文件）

## 功能增量

把 data_import（G17）与 field_compute（G19）的**进程内执行**接入既有 durable jobs 事实源（analysis_tasks，ADR-0052），复用 G18 数据交付确立的「DB 事实源 + 进程内 runner」范式——不新建任务平台、不加表、无迁移：

1. **共享 runner**（`app/services/data_operations/runner.py`）：asyncio 执行体的原子认领（pending|queued→running）、看门狗（2.5s DB 取消轮询**修 G18 已知弱点** + 30s 心跳，独立短事务）、fenced 终态（worker_id+attempt，#1398）、取消两段迁移（running→cancelling→cancelled）。
2. **导入接线**（`import_jobs.py`）：幂等键=owner scope+批次+计划版本+**收据态指纹**（部分失败重提/伪造 ref 重跑不被旧 completed job 的 TTL 复用挡住）；收据短路（不建 job、duplicate 标注=BASE 语义）；`_load_owned` 恢复判据 durable 化——**消除多副本误复位**（生产 k8s API replicas≥2 真实风险）；cancel 跨副本桥接（完成后取消=NOT_COMMITTING 409/job 终态 no-op 双明确语义）；delete 活跃 job 门禁。
3. **字段计算接线**（`compute_jobs.py`）：execute() 内联 durable 化（签名不变）；execute_background()；同键活跃 job 排队收敛（cancelled 继承取消/failed 重提/completed 取收据——跨副本版 fingerprint 锁）；request_cancel durable 桥。
4. **任务中心/Pi/前端**：JobKind +`data_import`/`field_compute`（无 CHECK 纯加性）；JobView +`member_status` 有界投影（仿 atlas pages）；`import_batch_commit`/`field_compute_apply` 改 durable submit+有界等待（不再无限期占用工具调用）；新增 `import_commit_status`/`field_compute_status`（完成且产物可读才返回 ref）；前端类型+成员清单渲染+i18n（tsc 通过）。
5. **有界失败**：ENOSPC→typed `STAGING_WRITE_FAILED`(507)+job failed；收据落盘失败→成员失败披露；行预算超限 typed 拒绝（既有保持）。

## 关键证据（详见 planning/G23-DURABLE-DATA-OPERATIONS/）

- **双真实进程复现**：BASE 上副本 B 把 A 正在提交的批次从 committing 误复位为 configured（350k 行 CSV 窗口，evidence-p0-base-*.log）→ 修复后不复位（evidence-p0-fixed-*.log）。
- **双 uvicorn 集成 5/5**（真实进程+共享 SQLite WAL+HTTP）：跨副本可见/不重置、B 取消 A 执行双面 cancelled、B 重提完成、kill -9 A→诚实状态→注入 stale 清扫→恢复→重启一致、import→compute 全链 B 执行/A 查询+A 取消。
- **测试**：tests/data_operations 24（含 300k 行 RSS<1.2GB+行数完整、500,001 行 typed 拒绝、ENOSPC、A01-A16 矩阵）；回归 data_import 41/41、jobs 288、delivery 57（G18 无回归）、field_compute 123/124（1 失败为 BASE 预存 OpenAPI schema 名问题，干净 BASE 同失败）。
- **资源**：双进程 RSS 峰 ~740-760MB（含应用基线 ~487MB）、DATA_DIR 峰 ~40MB（resource_curve.csv）。

## 措辞声明（review 重点核对项）

- **执行语义**：at-least-once 执行（认领互斥防重复执行体）+ effectively-once 提交（收据幂等键+ref 可读复核+fenced 终态）。不宣称跨文件系统/SessionStore 分布式 ACID——收据与披露如实承载边界。
- 取消前已发布产物不可回滚：`retained_refs` 明示保留；源文件任何路径不被修改（A16 测试）。

## 排除项 / 非目标

- 不新建任务表/DAG/工作流（workflow_runtime/geocompute/Celery include 不动）；不改 G18 writer 本体；不动 Pi vendor（G22）；无 Alembic 迁移（复用 analysis_tasks 既有列）。
- session ref 跨副本可见性依赖生产 Redis（本地降级拓扑为进程内存，集成脚本如实分进程安排）；Postgres 方言经 SQLite 全量+SQLAlchemy 核心表达式覆盖，real_services lane（CI）承载真实 PG。

## 兼容 / 回退

- 旧格式批次（无 active_job_id）与无 job 历史走原路径（G17 全量测试通过）；API 形状加性（新字段全默认值）。
- 回退：revert 本 PR 即回到 G17/G19 进程内语义；durable 数据（analysis_tasks 行）对旧代码只读无影响。

## 外部依赖与未测项

- Pi 真实子进程端到端未跑：vendor/pi 未构建（master 既有；G22 方向所有权）。工具层经真实 ToolRegistry dispatch 验证。
- 浏览器 E2E 未跑（改动为类型/渲染块/i18n；tsc 通过）。
- 本机无 Redis/Postgres（本地降级拓扑验证，与 G18 测试范式一致）。
