# G01 RECON — GIS 数据语义身份、可用性与恢复链路（基线 b1b2ec23）

日期：2026-10-21 · 基线：`origin/master` = `b1b2ec23f10f251ddf8cf4d9fc0baabd03117013`（经 gh API 与本地 refs 双确认）
worktree：`../webgis-g01-dataset-readiness` · 分支：`feat/g01-dataset-semantic-readiness-vnext`

## 1. 真实入口调用链（现状证据）

### 1.1 dataset-ref → semantic descriptor → 消费

- **ref 创建**：`app/services/data_ingest/pipeline.py:193-196`（`session_data_manager.store`）、`app/services/mapspec_store.py:244-248`（内联数据）。ref = 会话作用域能力令牌，跨 session 不复用 id（`app/services/project_runtime/resume.py:254-262` 注释）。
- **语义铸造（生产已接线）**：
  - ingest：`pipeline.py:209-231`（`derive_descriptor_from_v3` + `store.put`），指纹进 artifact metadata（`pipeline.py:254`）；
  - MapSpec source 绑定：`mapspec_store.py:279-332`（store.get → 缺则零扫描补铸 + put；source 落 `descriptor_fingerprint`）；
  - 重绑定复用门：`mapspec_store.py:365-379`（`evaluate_reuse_with_history` → `source["descriptor_reuse"]`）；
  - fabric：`data_fabric/manager.py:734-747`（`build_descriptor_from_fabric_descriptor`，只算指纹不入 store —— 目录级身份域）；
  - 谱系：`geocompute/ops.py:678-687`（`lineage_provenance_mint`）。
- **身份锚三层**：载荷身份（ref descriptor `content_hash`/`content_revision`，`resume_verify.py:167-190`）；语义身份（store `descriptor_fingerprint`，`dsd-v1:` 前缀，`store.py:63`）；绑定事实（`recovery_state.source_fingerprints ≤16`，`durable_context.py:114`）。

### 1.2 角色/多数据现状

- **角色词表已存在**：`workflow_schema.py:34-50` `DATA_ROLES` 15 值（subject/measure/boundary/denominator/network/elevation/hazard/receptor/population/criteria/constraint/reference/baseline/target_time/comparison_time）。方法级 `requires_roles`（`workflow_v4/methodology.py:94`，公平性要求 subject+boundary+denominator）。
- **绑定生产者**：`planner.py:1899-1918` —— 从真实计划行（data_requirements: capability→role 经 capability_hint，**同 hint 多角色按声明序首个胜出** :1905-1908）构造 `bound_refs: Dict[role→ref]`，喂 `resolve_data_roles`。
- **单 profile 假设（核心缺口）**：`resolve_data_roles`（workflow_schema.py:387-460）与 `qualify_workflow_data_roles`（data_qualification.py:735-759）只接受**一个** `resolver_profile`/`descriptor` 服务全部角色。多数据 session 第二数据集的事实永远进不了资格链。
- **primary「首个命中」**：`dataset_semantics/qualification_bridge.py:19-46` `primary_session_descriptor(session_id)` 按 `list_refs` dict 迭代序取**首个**有 descriptor 的 ref。唯一生产调用方：`app/tools/semantic_tools.py:344-352`（tier-2 advisory 工具 `compile_workflow_semantics`）。

### 1.3 data_qualification 消费面

- `app/services/gis_harness/data_qualification.py:402` `qualify_data_role(..., descriptor=None, expected_descriptor_fingerprint="", recorded_descriptor=None)`；freshness guard `:496-528`（DESCRIPTOR_STALE_CRS/SCHEMA/CONTENT/METADATA/UNCOMPARABLE + FINGERPRINT_MISMATCH；无指纹 fail-closed）。
- 生产调用方：`plan_candidates.py:325-331`、`workflow_compiler.py:268-284`、`workflow_v4/compiler_v4.py:388-401`（均为**透传管道**）；`planner.py:1938-1941`（finalize 契约，只传 resolver_profile 无 descriptor）。
- **真正注入非 None descriptor 的生产入口只有 semantic_tools**（advisory）；`workflow_runtime/service.py:113`、`chat/plan_orchestrator.py:693-698`、`runtime_bridge.py:166` 均不注入。**`expected_descriptor_fingerprint` 无任何生产调用方供值** —— MapSpec source 已存 `descriptor_fingerprint` 却从不回喂资格链。

### 1.4 replay/recovery 面

- **已接线**：workflow resume 锚（`resume_anchor.py:368-388` 语义 store 跨 session 迁移 + `resume_verify.py:161-170` `evaluate_reuse` 对账）；MapSpec 重绑定（mapspec_store.py:365-379）。
- **缺口（ADR-0215 明示 follow-up）**：durable project runtime（`project_runtime/resume.py:112-251` + `rebind.py:198-270`）全包 grep `dataset_semantics|descriptor_fingerprint` **零命中** —— 恢复链只做载荷重水合 + ref 键位改写，descriptor 语义身份不迁移、不对账。rebind 的 `semantics_preserved` 只要求 source 指纹字节一致（rebind.py:110-119），不复核新 session 语义 store。
- **零调用 API**：`qualification_bridge.descriptor_evidence`（全仓 0 调用）；projections 四件套生产 0 调用。

### 1.5 map_plan_compiler

入口 `MapPlanCompilerService`（`map_plan_compiler/service.py:35-192`），唯一生产链 `app/tools/map_plan_tools.py:115-197`。`projector.py:95-103` 把 `data_requirements` 逐行转录为 `DatasetRef(dataset_id=capability, bound_ref)` —— **编译器不做数据选择**；每层 `source_ref = PlannedLayer.bound_ref`。「primary」语义在 planner 层图层角色（`planner.py:581` role ∈ primary/secondary/reference），`product_compiler.py:196` 取第一个 primary 层。数据闸在 `obligations.py:161-176`（DATA_REF_UNRESOLVED blocking）。

## 2. 与 #1758 及其他 open PR 的交叠矩阵

| PR | 分支 | 状态 | 与本方向文件交叠 | 语义冲突 |
|---|---|---|---|---|
| #1758 | zcode/swarm-review-fixes-v1 | open（429 文件） | **主责区 0 文件**（dataset_semantics/、dataset_descriptor.py、data_qualification.py、map_plan_compiler/ 全部未触碰）；相邻区 50 文件（data_fabric 12、data_quality 2、gis_context 1、mapspec* 5、cartography 4 等）——本方向不改这些文件（仅复用 manager.py 等的既有 API，无写冲突） | 低 |
| #1701 | dependabot pandas | open | requirements 变更 | 无 |
| #1698 | dependabot sqlalchemy | open | requirements 变更 | 无 |

结论：本方向主责文件与所有 open PR **零写冲突**；#1758 对 data_fabric/data_quality 的修复不影响本方向缝合点（只读调用）。

## 3. 缺口清单（本方向要填的）

| # | 缺口 | 证据 | 归属 WP |
|---|---|---|---|
| G1 | 多数据 session 角色绑定是「首个命中」+ 单 profile 服务全角色，第二数据集事实进不了资格 | qualification_bridge.py:26-28; data_qualification.py:735-759 | WP-A |
| G2 | 无 not_found/ambiguous/not_authorized/stale/partial 的结构化绑定判据（同名数据集、跨项目引用、损坏描述符均无区分） | 全链无此词表 | WP-A |
| G3 | expected_descriptor_fingerprint 无生产供值（MapSpec 指纹不回喂资格） | 1.3 节 | WP-B |
| G4 | chat 主链/workflow_runtime/runtime_bridge 编译不注入 descriptor | 1.3 节 | WP-B |
| G5 | project_runtime 恢复链 descriptor 身份不迁移不对账 | 1.4 节 | WP-B/C |
| G6 | 数据缺口无 Agent 可消费的 typed 澄清（缺行政边界/数量vs密度/范围不相交/单位混合/时间不匹配） | 全链无 | WP-B/C |
| G7 | 多数据联接 preflight 不存在（CRS/时间/覆盖兼容性 metadata-only 判据） | 全链无 | WP-C |
| G8 | descriptor_evidence / projections 零调用（要么接线要么保留并注明） | 1.4 节 | WP-B |

## 4. 可复用的权威模块（不重造）

- `GISDatasetDescriptor`（dataset_descriptor.py:285+，kind/geometry/fields/unit/temporal/bbox/crs/指纹，载荷 ≤96KiB）
- `DatasetSemanticStore`（store.py，内容寻址版本链 ≤8 版本，fail-closed reason codes）
- `evaluate_reuse`/`evaluate_reuse_with_history`（reuse.py，valid/stale/recompute/unknown）
- `DATA_ROLES` + `resolve_data_roles` + `qualify_workflow_data_roles`（角色词表/解析/裁决单一事实源）
- `build_descriptor_from_ref_descriptor`（builder.py:411，零扫描补铸——恢复迁移复用）
- `descriptor_resolver_profile`（projections.py:98，descriptor→资格事实投影）
- `record_source_fingerprints` / context_bridge（durable facts 面）

## 5. 已核对的相关 issue（open 93 个中）

与本方向直接相关：#1739（reuse 结论带死 anchor——G5 关联）、#1738（ref 失效兜底）、#1716/#1715（data_quality/data_fabric——#1758 认领区，不碰）。#1753（全量套件 ~80% 处挂起）→ 只跑目录级目标测试，不跑全量。
