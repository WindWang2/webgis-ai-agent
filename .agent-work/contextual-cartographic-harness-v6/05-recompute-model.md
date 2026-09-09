# 05 — Recompute Model 设计（Waves 4–5）

## 现状

- `diff_workflow_packages`（diff.py:112）/`compute_affected_subgraph`（recompute.py:93）零生产调用（✅ 已验证）。
- `CHANGE_TARGETS`（recompute.py:28）6 种；`WorkflowEventKind`（workflow_instance.py:57-67）8 种事件已进实例；维度词表 `workflow_schema.RECOMPUTE_DIMENSIONS`(:25)。
- 行指纹 `rows_fingerprint`（algorithm+params，V2）已是去重门单一计算源。

## W4：Change 分类词表收口 + 生产接线

1. **变更分类**：目标词表（Prompt §9）→ 落 `workflow_schema`（单一词表文件），与现有 `CHANGE_TARGETS`/`WorkflowEventKind`/RECOMPUTE_DIMENSIONS 做三方映射表（不新增平行枚举，缺什么补什么并加 parity 测试）：
   `DATA_CHANGE, DATA_PROFILE_CHANGE, PARAMETER_CHANGE, ALGORITHM_CHANGE, METHOD_CHANGE, CRS_CHANGE, STYLE_CHANGE, LAYOUT_CHANGE, COMPONENT_CHANGE, VIEWPORT_CHANGE, OUTPUT_CHANGE, USER_OVERRIDE`
2. **变更来源接线**（change 产生点）：
   - mapspec mutation（lifecycle engine 意图族）→ STYLE/LAYOUT/COMPONENT/VIEWPORT/OUTPUT 分类
   - tool 参数/算法变更（plan 行编辑）→ PARAMETER/ALGORITHM/METHOD
   - data profile 更新（progressive profile）→ DATA_PROFILE
   - 新数据到达 → DATA_CHANGE；CRS 语义变化 → CRS_CHANGE
   - 用户 override → USER_OVERRIDE + 子类（semantic/presentation/transient，见 07）
3. 分类后统一走：`change → diff → affected roots → downstream closure → reuse validation → RecomputePlan`，由 `maybe_update_workflow_instance` 消费并把 STALE 传播写回 node_states（W1 投影）。

### 落地记录（W4/W5 已实现形态，2026-09-09）

- **词表裁决**：`RECOMPUTE_DIMENSIONS`（data/algorithm/parameter/style/output）+ `CHANGE_TARGETS`（+ 新增 `node` 目标类）保持单一事实源；§9 十二类映射——DATA_CHANGE/DATA_PROFILE_CHANGE/CRS_CHANGE→`data` 维（CRS 语义变化经画像/重投影行漂移体现，detail 记录）；PARAMETER_CHANGE→`parameter`；ALGORITHM_CHANGE/METHOD_CHANGE→`algorithm`；STYLE/LAYOUT/COMPONENT/VIEWPORT 变更不进科学重算（结构性免疫：mapspec 突变不触碰行签名 → 零种子零闭包）；OUTPUT_CHANGE→`output`；USER_OVERRIDE 三分类在 W15 落地。
- **生产接线**：`runtime_bridge.derive_runtime_block` 在每次触发做字段级变更分类（算法/参数指纹/bound ref）→ `WorkflowChange` → `compute_affected_subgraph`（**唯一闭包引擎**，替换桥内手擀闭包）→ `recompute_plan` + `changes` 持久化在运行态块。
- **调度面**：`[GIS Recompute]` 单行进 `format_session_plan_projection`（stale/recompute/dims/reuse_unsafe 有界）——Pi 看见 recompute 债并执行（行状态红线不变：bridge 不翻行）。
- **缺陷修复（§61）**：`compute_affected_subgraph` 对真实 `to_bounded_dict` 形状（边端点 `node.port`）闭包静默断链 —— 已修（port 后缀归一）+ 回归锁。
- **W5 reuse validation**：`records` 快照（`list_artifacts` ≤128）进 derive；校验 evidence 指纹（构造保证）/ artifact health（valid→healthy，expired/stale/superseded/failed→unhealthy，缺席→missing/unknown）/ workflow package 稳定性；`unknown` 不假设、`unsafe` 翻 stale 并强制进 recompute（`reuse_unsafe:*` reason）。
- 一次性说明：二代块起 `reuse_validation` 进 state_fingerprint（gen1→gen2 revision 一次性 +1，之后同输入稳定）。

## W5：Partial Recompute 执行 + Reuse Validation

1. **不变量（必须全部成立并有专测）**：
   - style-only 不触发科学重算（style 维度只污染 render/presentation 下游）
   - layout-only 不触发数据分析重算
   - parameter change 只污染真正依赖该参数的节点（指纹含 params）
   - upstream data revision change 污染下游依赖产物
   - unrelated component change 不污染 workflow
   - CRS semantic change 按节点 crs_requirement 精确决定范围
   - user override 三分类，不得一律 workflow invalidation
2. **Reuse Validation**（`reuse = safe | unknown | unsafe`）校验项：
   input fingerprint（bound_ref + content_revision）、parameter fingerprint、algorithm version、workflow package version、data revision、CRS、units、environment/runtime fingerprint、scientific obligation state、artifact health（`ArtifactRecord.status` 直投已有）
   ——证据不足 → `unknown` → 保守重算；`rows_fingerprint` 扩展为 node_fingerprint 单一实现。
3. **执行**：RecomputePlan 给出 recompute/reuse 集合；调度面复用现有行重跑机制（`_mark_progress`/重跑入口），不新建执行器；重算后触发 re-render → re-observe → 复验（W6/W7 入口）。

## 关键 E2E（对应 §57）

- Scenario 3：用户改参数 → 只重算受影响子图（断言未受影响节点 artifact 被复用且指纹未变）
- Scenario 4：仅改色带 → 科学计算零重跑（断言无 analysis node 进入 recompute 集合）

## 验收

- `compute_affected_subgraph`/`diff_workflow_packages` 有生产调用链（change 来源 → plan → instance 回写）。
- 7 条不变量各有确定性单测（零 LLM）。
- reuse 三态在 trace 中可见（trace_store 事件）。
