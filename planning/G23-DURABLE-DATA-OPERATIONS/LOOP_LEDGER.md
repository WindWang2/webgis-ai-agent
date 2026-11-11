# G23 LOOP_LEDGER

## 状态总览（HEAD=e4bcb4dbb，BASE=b8f2a6ecc）

| 工作包 | 状态 | 证据锚点 |
|---|---|---|
| P0 生命周期地图+双进程复现 | verified | RECON_ARCHITECTURE.md；evidence-p0-base/fixed-*.log |
| P1 同一任务事实源（幂等/复用） | verified | runner.submit_async_job + import/compute 键；test_runner/test_import/test_compute |
| P2 跨进程 lease/fencing | verified | worker_id+attempt fencing（#1398 机制复用）；两段取消迁移；集成 S1-S5 |
| P3 成员执行/可恢复收据 | verified | 收据事实源不变+job 投影；A11 对账 test；A13 清单 |
| P4 取消与补偿 | verified | 取消矩阵（边界前/提交中/完成后）+ retained 披露（test_a07_a08/a09/a13） |
| P5 有界内存/背压 | verified | test_scale（300k/超限/ENOSPC）；RSS 曲线 |
| P6 任务中心与 Pi 可操作性 | verified | JobKind/JobView.member_status/import_commit_status/field_compute_status/前端（tsc 通过） |
| P7 兼容/恢复/真实拓扑 | verified | 双 uvicorn 集成 5/5；旧格式批次兼容 test_a11/G17 回归 |
| RV 独立评审 | round-2 复审中 | REVIEW.md（round1: request-changes，B1/M1/m1-m5/NOTE 已修复并记录；复审追加中） |

## 下一步可执行动作
1. 等独立评审 REVIEW.md → 按 BLOCKER/MAJOR 修复 → 复跑最小回归 → 复审。
2. push 分支 + gh pr create（PR_BODY.md）。
3. 遗留非阻断：Pi 子进程端到端（vendor 未构建，G22 方向）；Postgres real_services lane（CI）；浏览器 E2E 回归。

## 有效契约（其他方向接入时依赖的事实）
- job kind：`data_import`（task_type=data_import_commit）/`field_compute`（field_compute_apply）
- 幂等键形状：`{TASK}:{owner-scope}:{batch|plan}:v{ver}:{收据态指纹}` / `field_compute:{session}:{plan}:{fingerprint}`
- 批次状态文件新增 `active_job_id`（缺省 None=旧格式）
- JobView 新增 `member_status`（仅 data_import 终态填充，≤50）
- Pi 工具新面：`import_batch_commit(wait_max_s=20)`/`import_commit_status`；`field_compute_apply(wait_max_s=20)`/`field_compute_status`；`POST /field-compute/plan/{id}/execute {background:true}`

## 未解阻断
无（外部条件限制已在 TEST_EVIDENCE §5 如实记录，均有明确 owner/路径）。
