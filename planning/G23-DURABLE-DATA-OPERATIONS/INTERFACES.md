# G23 INTERFACES — 接线清单

## 新增文件（无碰撞命名空间）

- `app/services/data_operations/__init__.py|runner.py|import_jobs.py|compute_jobs.py`
- `tests/data_operations/conftest.py|test_runner.py|test_import_jobs.py|test_compute_jobs.py|test_scale.py`
- `planning/G23-DURABLE-DATA-OPERATIONS/**`（含 bin/ 复现与集成脚本、证据）

## 修改的既有文件（全部加性）

| 文件 | 改动 | 兼容约定 |
|---|---|---|
| app/services/jobs/lifecycle.py | JobKind + data_import/field_compute | String(20) 无 CHECK，纯加性 |
| app/services/jobs/views.py | JobView.member_status + _import_member_status | 仿 atlas page_status；缺省 None |
| app/services/data_import/service.py | start_commit→submit_import_commit；_load_owned 恢复判据 durable 化；cancel_batch durable 桥+完成后语义；delete_batch durable 在飞检查；_run_commit→_run_commit_members(+handle) | 对外方法签名不变（start_commit 新增可选 actor）；无 job 旧批次走原路径 |
| app/services/data_import/models.py | ImportBatchState.active_job_id: Optional[int] | 默认 None；旧 JSON 反序列化兼容 |
| app/services/data_preparation/field_compute/service.py | execute() 内联 durable 化（签名不变+可选 actor）；新增 execute_background()；_execute_locked +progress 钩子；request_cancel durable 桥 | 收据短路/取消优先语义保持 |
| app/services/data_preparation/field_compute/contracts.py | EXEC_ERROR_CODES + JOB_INCOMPLETE | 加性 |
| app/schemas/data_import_schema.py | Commit/CancelResponse + job_id 等可选字段 | 加性默认值 |
| app/schemas/field_compute_schema.py | ExecuteRequest.background: bool=False | 加性 |
| app/api/routes/data_import.py | commit 路由传 JobActor | 行为不变 |
| app/api/routes/field_compute.py | execute 路由 background 分支 | 默认 False=BASE |
| app/tools/data_import_tools.py | import_batch_commit durable submit+有界等待(+wait_max_s)；新增 import_commit_status | 小 fixture 立即收据（BASE 形） |
| app/tools/field_compute_tools.py | field_compute_apply execute_background+有界等待(+wait_max_s)；新增 field_compute_status | 同上 |
| frontend/lib/api/jobs.ts / data-import.ts | ImportMemberStatusView/member_status/job_id 类型 | 可选字段 |
| frontend/components/sidebar/tasks-tab.tsx | data_import 成员清单渲染块 | 仿 atlas pages；无 member_status 不渲染 |
| frontend/messages/{zh-CN,en-US}/sidebar.json | tasks.importMembers/importRejected | 两条新键 |

## 消费的既有稳定接口（未改签名）

DurableJobStore 全套（create_sync/transition_sync/mark_*/request_cancel(_sync)/heartbeat_sync/update_progress_sync/is_cancel_requested_sync/sweep_*）；JobStatus/JobKind/lifecycle 谓词；cancellation registry/CancellationToken/use_token；ProgressThrottle/JobProgress；default_worker_id；staging.*；commit_member；compute_plan_hash；store.find_receipt（fc）；session_data_manager.get/store/overwrite；ToolRegistry @tool；verify_session_owner/_require_session；JobView/job_from_record。

## 与兄弟方向分区

不改：workflow_runtime/geocompute/Celery include 列表、data_delivery 本体、Pi vendor/bridge（G12/G22）、资格政策（G13）、MapSpec schema、上传协议、Lakehouse/GC（#2070 有 owner）、#2022 refresh 域、#2084-86 已合并修复面。共享 DB 无新表无迁移（analysis_tasks 既有列复用）。

## 已知边界（评审 NOTE 登记）

1. **幂等键 owner scope 的入口分歧**：HTTP 路由传 JobActor(user/owner_token)，Pi 工具路径不传 actor（scope="anon"）——同批次经 UI 与经 Pi 提交产生不同幂等键；不双跑由「COMMITTING 门禁 + 收据短路」兜住，A01 的严格收敛只在同类入口内成立。staging 归属按 session 判定不变。
2. **delete_batch 的 check-then-act 残窗**（查活跃 job→rmtree，跨进程建 job 未落 COMMITTING 的间隙）：执行侧会得到 typed 失败可恢复；如需彻底闭合可用 B1 同款 rev/CAS 手法（后续项）。
3. **data_operations 同步短事务在事件循环上执行**（与 G18 data_delivery 范式一致）：生产 Postgres 时每次心跳/探测短暂阻塞循环，后续可统一 to_thread 化。
4. **field_compute_status 可读性探针整载 payload**：大产物时为判可读而全量加载；后续可换轻量存在性检查。
