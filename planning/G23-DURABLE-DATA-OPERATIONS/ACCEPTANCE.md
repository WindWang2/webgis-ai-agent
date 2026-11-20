# G23 ACCEPTANCE — 验收矩阵映射（A01–A26）

标注：✅=已验证（测试/集成证据）｜🟡=部分（依赖声明/既有覆盖）｜➖=非本方向（登记依赖）

| # | 验收项 | 状态 | 证据（测试/脚本/产物） |
|---|---|---|---|
| A01 | 相同提交并发只产生一个逻辑 job | ✅ | tests/data_operations/test_runner.py::test_submit_idempotent_same_key_single_job；test_import_jobs.py::test_a01（第二提交 409 冲突/短路 duplicate） |
| A02 | 不同计划版本不误复用 | ✅ | test_import_jobs.py::test_a02_plan_version_change_new_job（2 job 不同键、plan_hash/产物不同） |
| A03 | 另一 API 进程读取不重置活任务 | ✅ | 双真实进程复现 BASE 误复位（evidence-p0-base-*.log）→ 修复后不复位（evidence-p0-fixed-*.log）；集成 S1（evidence-integration-report.json） |
| A04 | worker 失联可回收租约 | ✅ | test_runner.py::test_orphan_pending_never_claimed + test_late_finish_fenced_out_after_retry（stale→同键重提新行）；集成 S4（kill -9 → 注入 stale sweep → 归一化恢复） |
| A05 | 旧 generation 迟到提交被拒 | ✅ | test_runner.py::test_late_finish_fenced_out_after_retry（zombie attempt=1 mark_succeeded 被拒，stale 终态不变） |
| A06 | 心跳临时失败不造成双写 | ✅ | test_runner.py::test_progress_writes_fenced_to_owner（fenced 进度写 + 终态后不可写）；认领互斥 test_claim_loses_to_concurrent_winner |
| A07 | 跨进程 cancel 有效 | ✅ | 集成 S2（B 取消 A 执行 → 双面 cancelled）+ S5（A 取消 B 执行的 compute）；test_compute_jobs.py::test_cancel_via_durable_bridge_from_other_context |
| A08 | 取消后迟到结果不能变 success | ✅ | 集成 S2 job_final=cancelled；store §14 late-success 收敛（实测「成员完成后取消」retained 披露 + job cancelled）；test_import_jobs.py::test_a07_a08 |
| A09 | 完成后的 cancel 有明确语义 | ✅ | test_import_jobs.py::test_a09（批次面 NOT_COMMITTING 409；job 面 completed 不可迁 no-op） |
| A10 | source revision 排队中改变不静默执行新数据 | ✅ | test_compute_jobs.py::test_a10（overwrite → STALE_PLAN/SNAPSHOT_CHANGED typed，无收据） |
| A11 | commit 后 receipt 前 crash 可对账 | ✅ | test_import_jobs.py::test_a11（状态文件回退 COMMITTING + 收据在 → 归一化 COMPLETED，不重做、不建新 job） |
| A12 | 不把外部副作用重复执行当重试安全 | ✅ | 收据层幂等：completed+hash 匹配+ref 可读才复用（伪造 ref 重跑 test_fault_forged_ref_not_reused 既有保持）；job 幂等键含收据态指纹 |
| A13 | 成员 partial 有完整清单 | ✅ | test_import_jobs.py::test_a13（job result_summary.members 全成员清单 + retained_refs 披露）；JobView.member_status 投影 |
| A14 | fieldcompute preview 与执行同解释器同快照 | ✅ | test_compute_jobs.py::test_a14（预览指纹=执行收据指纹）；G19 既有结构未动 |
| A15 | artifact 注册失败补偿或残留披露正确 | ✅ | 表登记失败→note 披露（G17 既有）；收据落盘失败→STAGING_WRITE_FAILED 成员失败披露（test_scale.py::test_disk_exhaustion） |
| A16 | 源数据始终未改 | ✅ | test_import_jobs.py::test_source_files_never_modified；test_scale.py（预算超限/ENOSPC 后源完好） |
| A17 | 大 CSV/Parquet 内存受控且统计完整 | ✅（CSV 档） | test_scale.py::test_scale_300k（RSS<1.2GB、行数=30万完整）；Parquet 非导入支持格式（SUPPORTED_EXTS）——不强称 |
| A18 | ZIP 展开限额保持 | ✅ | G17 budgets（MAX_BATCH_EXPANDED_BYTES 200MB，create_batch 整批拒绝）既有测试保持（tests/data_import 41/41） |
| A19 | 临时盘耗尽 typed 失败 | ✅ | test_scale.py::test_disk_exhaustion（ENOSPC→STAGING_WRITE_FAILED 507+job failed，不静默截断） |
| A20 | 两用户 job/文件/进度隔离 | ✅ | 归属谓词三链（既有 test_job_api owner-scoped）；跨 owner 批次 NOT_FOUND（test_cross_owner_same_bytes 既有）；幂等键含 owner scope |
| A21 | 刷新页面恢复相同任务 | ✅ | 批次状态文件 + job 行均为持久事实；UI 轮询批次端点跨进程一致（集成 S1/S3 B 视图）；导入工作台轮询既有 |
| A22 | 真实 Pi 提交→查询→引用产物 | ✅（工具层） | import_batch_commit(wait_max_s)+import_commit_status / field_compute_apply(wait_max_s)+field_compute_status（完成且 ref 可读才给 produced_refs；test_field_compute_tools 6/6）；Pi 子进程端到端见 TEST_EVIDENCE §限制 |
| A23 | 老 batch/job 兼容 | ✅ | active_job_id=None 旧格式走同一归一化（test_a11 模拟）；无 job 行批次行为不变（G17 41/41 通过） |
| A24 | G18 原有数据交付回归 | ✅ | tests/delivery 57/57（含模拟重启恢复） |
| A25 | 服务重启与 DB 短暂不可用不伪造状态 | ✅ | 集成 S4（kill -9 后 B 诚实 committing；stale 注入后归一化；A 重启视图一致）；DB 探测失败保守按在飞处理（_active_commit_job except 分支） |
| A26 | 取消/重试/恢复全链审计可追溯 | ✅ | job 行 cancel_requested_at/attempt/worker_id/error_trace + 批次收据/notes + 双侧服务器日志（evidence-integration-uvicorn-*.log） |

## 明确的边界与不做声明

- **at-least-once 执行 + effectively-once 提交**：重复投递由认领互斥（pending→running 原子迁移）与 fenced 终态收敛；成员级产物由收据幂等键（member_key, plan_hash）+ ref 可读复核保证不重复发布。**不宣称**跨文件系统/SessionStore 的分布式 ACID。
- session-data 跨副本可见性依赖生产 Redis（本地降级拓扑为进程内存——集成 S5 的执行/查询分进程安排如实记录该边界）。
- Celery worker 未参与这两个域（G18 同款进程内 runner；Celery include 列表不变）；生产多副本正确性不依赖粘性路由。
