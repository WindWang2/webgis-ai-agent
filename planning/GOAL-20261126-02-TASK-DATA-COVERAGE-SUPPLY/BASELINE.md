# BASELINE — GOAL-20261126-02 任务驱动数据覆盖与供给

## B 节核对结果（2026-11-27 UTC）

- origin/master = `59eb3f52747e865b29243af305abf6bb09fcc83b`（#2282 后，与任务书快照一致）
- 开放 PR = 0（gh pr list --state open 为空）；最近合并 #2282（ui-workbench-polish）、#2281（整合 #2267–#2280）
- #2272（方法实验）、#2273（多源融合）、#2274–#2280（04/03/05/01 线）均已合并 → 本线不做融合算法/方法实验/取数执行引擎
- vendor/pi gitlink = `ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe` = 上游 tag v1.6.1（ls-remote 验证），无更新 release；01 线职责，本线不动 gitlink
- 历史 worktree/分支存在（20261124/20261125 各线），均已合并或属他线，不碰

## 本线 worktree

- 目录：`/home/kevin/projects/webgis/webgis-task-data-coverage-supply-20261126T1830Z`
- 分支：`zcode/goal-task-data-coverage-supply-20261126`
- 基：origin/master `59eb3f527...`，submodule 已 init（vendor/pi @ ef4a1cfcc）

## 已确认的第一手契约事实（主 Agent 直读）

1. `docs/DATA_READINESS_CONTRACT.md`（G03/ADR-0296）：
   - 五态 `QUALIFICATION_STATES`（eligible / transform_required / blocked / unknown / …）由 `app/services/gis_harness/data_qualification.py` 权威定义，测试锁定，本线不增删
   - §4 任务范围 GD-05 已有：`preflight_multi_dataset(..., task_scope={place?, bbox?})`、`PREFLIGHT_SCOPE_MISMATCH`、`PREFLIGHT_SCOPE_UNVERIFIABLE`、`scope_fingerprint(place,bbox)`、`evaluate_scope_staleness(recorded,current)`
   - 语义身份证 `GISDatasetDescriptor` 含 `dimension_provenance` 三态（observed/inferred/unknown）
2. `app/services/gis_harness/workflow_v4/acquisition.py`：纯声明层
   - `ACQUISITION_ALTERNATIVES` 有序通道：local < derived < data_fabric_catalog < data_fabric_stac < provider_service < user_upload < synthetic_demo
   - `plan_role_acquisition(role, resolution_status, ...)` 编译期可行性裁决，零网络 I/O
   - `synthetic_demo` 仅显式 opt-in（must_not_guess 获取侧延伸）
   - **缺口**：无空间/时间范围、无覆盖评估、无预算、无候选数据集实体 —— 这正是本线增量

## 待勘察子代理回报（进行中）

- A：data_discovery / data_catalog / data_fabric（source_registry、service_qualification、coverage_read、planning/、retrieval/）+ dataset_semantics + 工具面
- B：ADS(ADR-0173) 真实位置 + requirement IR 链路 + ref/artifact/durable jobs 机制
- C：ToolRegistry 接线 + 前端数据工作台 + 测试基线 + 质量闸脚本

## R0 基线验证（待执行）

- [ ] 后端相关 pytest 子集基线
- [ ] ruff check 基线
- [ ] 前端相关测试基线（如涉及）
