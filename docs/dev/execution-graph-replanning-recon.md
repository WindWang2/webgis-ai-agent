# Execution Graph + Incremental Replanning — Recon（方向 5 / Phase 0）

- 基线：`origin/master @ 580b33e9`（#1272 合并后；2026-09-14 fetch 复核）
- 分支：`harness/execution-graph-incremental-replan-v1`（独立 worktree）
- 勘察方式：主 Agent 逐行精读核心生产文件（本文件 §2/§3/§4 一手证据）+ Subagent A 广度对账（原始笔记：`docs/dev/execution-graph-recon-subagent-a.md`）
- 结论状态：**执行时事实**（任务书与代码不一致处一律以本文件为准）

---

## 0. 执行摘要

1. **本仓库不存在"第五套 DAG"的施工空间——Execution Graph 的全部骨架已经存在**，且分成两层：
   - **计划事实层**：`SessionPlan.gis_chapter`（MapProductPlan 行，唯一行状态写手 `_mark_progress`，session 锁内）→ `plan_graph.build_plan_graph`（依赖感知 DAG 纯投影）→ 文本投影行（`[SessionPlan]`/`[GIS Plan]`/`[GIS Recompute]`/`[GIS Runtime]`）喂给 Pi。
   - **可执行运行时层**：`app/services/workflow_runtime/`（"Semantic Workflow Runtime V5"，~6,900 行）——DB 持久 instance/node 两级 CAS、`Driver`（拓扑 ready、有界并发、run/node 两级租约、取消、deadline、孤儿复位、STALE 拓扑安全重入队、复用裁决）、`ChangeApplier`（quiescence defer + `compute_affected_subgraph` 正向闭包 + STALE CAS）、`ReuseIndex`（跨实例、owner 域隔离指纹索引）、启动恢复（`app/main.py:475`）、REST API（`app/api/routes/workflow_runtime.py`）。
2. **真正缺口不是"没有执行图"，而是三条生产断链**：
   - **G1（E5/E6 核心）**：用户 follow-up 改变 situation（subject/scope/style/data）时，chat 路径**从不**把变化喂给 V5 运行时。同 goal_key 替换 → 全部行 void（`session_plan.py:690-698`）；异 goal_key → supersede 全量归档（`session_plan.py:669-688`）。两条路都是**全量重算**语义。"成都小学→成都高中"今天会重取行政边界。
   - **G2（E6）**：`compute_affected_subgraph`（`workflow_v4/recompute.py:111`）+ `ChangeApplier.apply` 只有 REST 入口在调（`routes/workflow_runtime.py:217-231`）；chat 侧唯一的自动化 hook 是 style（`mapspec_mutations.py:222`）——scope/subject/time/data 维度没有 chat 通道。
   - **G3（E3）**：driver 的执行语义（有界并发/超时/取消/重试）在 chat turn 中从不运行（`GIS_WORKFLOW_RUNTIME_AUTORUN` 默认关，`service.py:34`）；节点副作用分类（destructive vs pure）在 contracts/driver 中**不存在**（全仓 grep `side_effect|destructive` 仅 `adapters_geocompute.py:127` 的 `idempotent=True`）。
3. 其余波次现状：E1 stable IR 基本已有（`workflow_runtime.v1` 块 + `RUNTIME_SCHEMA_VERSION="1.0.0"` + typed node_id 命名空间 `cap:|data:|transform:|output:`）；E4 checkpoint/resume 大部分已有（两级租约 + 孤儿复位 + 启动 recovery + `event_resume.py`）；E7 指纹栈完备（`fingerprints.py`：content/profile_digest/shape 三级 + env_fp 全版本面 + owner 域隔离）；E8 无图级 SSE 事件（只有 capability 行 `session_plan_progress` + `map_finalization`）；E9 有成熟语料模式可循（`app/evaluation/runtime_corpus.py`）。
4. **因此本任务 = 演进接线，不是新造系统**：意图/情境变更分类器 → PendingChanges（G1/G2）+ 副作用纪律（G3）+ 图级 SSE 事件（E8）+ 最小重算性能语料（E9）。
5. **补充（Subagent A 对账 + 补读 `plan_runtime.py`）**：全仓 12+ 个图抽象分四族（行投影/编译图/持久计划/注册表图）；`compute_affected_subgraph` **无孤儿**（2 个生产调用者，但 RecomputePlan 仅 advisory）；`plan_runtime.py`（V7 D2）已有计划版本化（fingerprint+history+rollback points）、`request_replan` 预算回路、失败种子最小重算 `seed_recompute_from_failures`——**advisory 层的增量重规划骨架已在 master**，缺的只是"用户意图变更 → 最小失效"这条进线与执行层（V5）的贯通。#1276 的 qualification_v8/candidate_planner_v8 确认为孤儿（零生产调用者），不作为依赖。SSE 红线：Pi 路径禁止 CanonicalPlan 事件名（`session_plan.py:333` raise），新事件必须用 additive 词表。

## 1. 生产调用链 BEFORE（一手核验）

```
用户消息
  └─ chat.py → execution_engine._maybe_plan (execution_engine.py:1068)
       ├─ planning.followup.classify_followup (关键词分类: new_goal/style_change/ref_reuse/continuation/unclear)
       └─ plan_orchestrator.orchestrate_plan → _synth_plan_from_harness
            └─ workflow_runtime.hooks.attach_plan_safe (plan_orchestrator.py:640)   [fail-open]
                 └─ svc.attach_session_plan → compile_and_register + instantiate + _prefill_role_bindings
  └─ Pi agent loop（agent_pi_bridge.py — Agent Host，不复制）
       └─ 每个工具调用 → ToolDispatchService（串行; _SessionWaveGate 并发门）
            └─ 工具结果回写链（agent_pi_bridge.py:708-836, 全部 fail-open）:
                 ├─ session_plan.apply_tool_result (:711)      ← 唯一行状态写手 _mark_progress（session 锁内）
                 │    ├─ webgis_map_intent: goal_key 同 → replace(全 void)/异 → supersede(归档)
                 │    ├─ 其他工具成功 → capabilities_hit_by_tool → complete+bound_ref
                 │    └─ 失败 → failed（可重试, 阻塞下游）
                 ├─ workflow_runtime.hooks.record_tool_result_safe (session_plan.py:581)
                 │    └─ svc.record_tool_result → _chat_complete_node（PENDING→READY→RUNNING(chat claim)→SUCCEEDED，
                 │        上游未结算则诚实拒绝 UPSTREAM_PENDING; 完成后复查上游 STALE 窗口）
                 ├─ maybe_finalize_map_product (agent_pi_bridge.py:763)
                 ├─ maybe_update_workflow_instance (:772)      ← V4 实例投影（gis_chapter 单键）
                 ├─ maybe_update_runtime_state (:788)          ← V7 任务级状态机
                 └─ maybe_update_runtime_projection (:803)     ← V6 typed DAG 运行态块（workflow_runtime_v6）
                      └─ derive_runtime_block: 行签名漂移 → WorkflowChange 分类 → compute_affected_subgraph
                          → stale/upstream_stale + reuse_validation(W5) + artifact_index(W3)
  └─ 下一轮 Pi 上下文: format_session_plan_projection (session_plan.py:177)
       └─ [SessionPlan] + [GIS Plan] + [GIS Recompute] + [GIS Runtime] + [GIS Product] 行（全部只读投影）
```

**要点**：执行图今天在 chat 路径是"**建议性文本**"（runtime_bridge.py:770-776 明示："Harness 出状态与建议，执行仍归 Pi"）。V5 运行时作为可执行账本与 chat 并行记账，但从不驱动；REST 是它唯一的执行入口。

## 2. 图/计划抽象清单与裁决（一手）

| 抽象 | 位置 | 角色 | 裁决 |
|---|---|---|---|
| SessionPlan | `session_plan.py` | 计划唯一事实源（行状态 `_mark_progress` 单写者，session 锁） | **保留（事实源）** |
| MapProductPlan 行 | `gis_chapter.data_requirements/analysis_steps` | canonical 行（depends_on additive） | **保留** |
| PlanGraph | `gis_harness/plan_graph.py` | 依赖感知 DAG 纯投影（环跳过、fallback 解锁、ready 派生） | **保留（投影）** |
| WorkflowInstance | `gis_harness/workflow_instance.py` | V4 实例投影（StageState、gate 指纹、science recheck） | **保留（投影）** |
| runtime_bridge 块 | `gis_harness/runtime_bridge.py` | `workflow_runtime.v1` 运行态块（stale/lineage/reuse_validation） | **保留（投影）** |
| workflow_v4 typed DAG | `workflow_v4/typed_dag.py` + `compiler_v4.py` | 编译期类型化图（端口/CRS/单位、**唯一 node_id 命名空间**） | **保留（结构事实源）** |
| compute_affected_subgraph | `workflow_v4/recompute.py:111` | 受影响子图正向闭包（确定性 O(V+E)） | **复用（E6 唯一引擎）** |
| workflow_runtime V5 | `app/services/workflow_runtime/*` | 可执行账本（CAS/Driver/ChangeApplier/ReuseIndex/恢复） | **复用（ExecutionGraph 执行核心）** |
| HarnessRuntime 状态机 | `gis_harness/runtime_state_machine.py` | 任务级阶段派生（12 阶段，V7） | 保留，不动 |
| plan_runtime / repair_planner | `gis_harness/plan_runtime.py` 等 | 行失败→最小重算清单；repair 动作 | 保留，不动 |
| capability_graph / goal_graph / product_graph / analysis_graph | `gis_harness/*.py` | 能力/目标/产品图（各自投影域） | **不做**（与执行正交） |
| MapSpec lifecycle / checkpoint | `mapspec/*` | 地图产品事务 | 不动 |
| Data Fabric acquisition plan | `data_fabric/*` | 数据供给计划（#1272 刚合并） | 不动（边界） |
| chat/event_resume.py | `chat/event_resume.py` | SSE 断线续传 | 复用（E4/E8 载体） |

**E0 结论：不新增任何图结构。** ExecutionGraph = `workflow_v4.typed_dag`（结构）+ `workflow_runtime` V5（执行态账本）+ `plan_graph/runtime_bridge`（SessionPlan 投影）的既有三元组；本任务把它们在 chat 生产路径上接成闭环。

## 3. 缺口 → 波次映射（实现范围）

| 任务波次 | 现状 | 本任务实际工作 |
|---|---|---|
| E0 收敛 | 双层已存在 | recon + decisions 文档、ADR；无新 DAG |
| E1 stable IR | schema 版本已有 | 补节点级 `side_effect` 分类字段（additive）+ 图校验测试 |
| E2 compiler seam | compile_workflow_v4 已接 | 不动（#1276 若改 capability resolution，走其投影） |
| E3 executor | Driver 完整但 chat 不驱动 | **副作用纪律**：节点 side-effect 词表 + driver/chat 重试语义（pure=at-least-once 可重试；side_effect=at-most-once 不自动重试、receipt 必须）|
| E4 checkpoint/resume | 租约/孤儿/启动恢复/event_resume 已有 | 场景级回归测试（failure→resume、duplicate retry、restart）|
| E5 situation→invalidation | 无 chat 通道（G1） | **意图差异分类器**：旧/新 chapter 结构化 diff → WorkflowChange/PendingChange 种子；接 `webgis_map_intent` 分支与 apply_tool_result |
| E6 affected recompute | 引擎已有、REST-only（G2） | **chat 生产接线**：apply_changes 进 hook 链；[GIS Recompute] 债与实例决策一致 |
| E7 cache correctness | 指纹栈完备 | 验证性测试（tenant/CRS/version/params），不重造 |
| E8 graph events | 无图级事件 | **`workflow_graph` SSE 事件族**（node state 变化/recompute decision/planned diff），复用 cache_session_plan_sse 通道，不造 websocket |
| E9 corpus | runtime_corpus 模式成熟 | **replan 语料**：≥30 multi-step 场景（含 10 必做场景）+ 指标（tool calls/recomputed/reused/incorrect reuse/duplicate side effects）|

## 4. 与 open/recent PR 的重叠矩阵

| PR | 状态 | 触碰面 | 与本任务重叠 | 处置 |
|---|---|---|---|---|
| #1270 ci-hygiene | open | mapspec CLI/conftest/quality 生成物 | 无 | 不吞入；仅阻断时最小兼容 |
| #1273 qc-loop | open | cartography/harness 修复面 | runtime_state_machine/tools 顺序冲突可能 | rebase 时对账 |
| #1274 typed tool surface | open | pi_bridge/tools registry | pi_bridge 触碰（我在 apply_tool_result 附近加 hook） | 低冲突；不依赖 |
| #1275 gis_situation | open | 新包 gis_situation + diff.py + chat route | **E5 语义上游**：situation diff 将来可作变更源 | adapter protocol 接入（不复制其实现）；master 上先用 chapter 结构化 diff |
| #1276 capability graph | open | gis_harness/planner + capability_resolution | planner 相邻 | 不动 planner 主路径 |
| #1277 harness kernel + SessionPlan v2 | open | **session_plan.py / execution_engine / pi_bridge / chat route** | **高冲突面**：我的 E5 seam 在 session_plan/apply_tool_result | 冲突最小化：新逻辑放独立模块，session_plan 只加 hook 调用点；PR body 明确归因 |
| #1278 skill procedure library | open | gis_harness/skills/**（新包） + registry_validation | procedure_ir 是 plan 的另一投影 | 不动；语料可引用其 benchmark 形态 |
| #1279 resource governor | open | 新包 governor/ + tool_dispatch_service | 并发/预算相邻（我的并发在 driver，已有界） | 不重复做 admission/backpressure |
| #1271 AC-V11 / #1272 ADS-V1 | merged | cartography depth / data supply | 无直接重叠 | 边界遵守：不重做 acquisition planner / self-heal |

## 5. 保留 / 复用 / 扩展 / 不做清单

- **保留（零改动）**：SessionPlan 事实源与 `_mark_progress` 单写者纪律；plan_graph/workflow_instance/runtime_bridge 三个投影；compile_workflow_v4；MapSpec lifecycle；Data Fabric；ReuseIndex 与指纹栈；driver 主循环。
- **复用（接线）**：compute_affected_subgraph（E6 唯一引擎）；ChangeApplier.apply（变更落地唯一入口）；hooks fail-open 模式；cache_session_plan_sse SSE 通道；runtime_corpus 语料模式。
- **扩展（additive）**：PendingChange 源词表（+intent_diff）；typed DAG/契约节点 side_effect 字段；session_plan webgis_map_intent 分支的 hook 调用点；SSE 事件族 `workflow_graph`。
- **不做**：第五套 DAG/新 planner/新 tool loop/websocket 通道；MapProduct 语义库（方向 6）；cartographic self-heal 重做；全量图数据进 cache key；吞 #1270。

## 6. 风险与顺序冲突

1. **#1277 与本任务都改 `session_plan.py`/`execution_engine.py`**：hook 调用点用 try/except 局部导入（仓库既有模式），rebase 时按其最终形态迁移。
2. **#1275 若先合并**：E5 的变更源升级为 situation diff；本任务的分类器保留为 chapter-level fallback（接口点已留）。
3. **行状态纪律**：最小失效不直改行状态——扩展点在 session_plan 会话锁内（_mark_progress / _seed_progress / apply_intent_diff_to_chapter），同词表同语义（review P2-1 更正：master 本有锁内第二写手，纪律非字面唯一）。
4. Windows 本地环境：DB 相关测试（instance store）需要 SQLite/PG fixture——以仓库既有测试基建为准（Subagent A 报告确认后填）。

## 7. Subagent A 报告确认项（已回填）

- [x] PR review follow-ups：详见 `execution-graph-recon-subagent-a.md`（#1277 与本任务同文件面最高：session_plan/execution_engine/pi_bridge）。
- [x] ADR 编号：master 至 0179；open PR 占用 0180×3、0181、0182×2 → 本任务取 **0184**（0183 留隙，见 decisions D7）。
- [x] SSE 词表：Pi 路径现有 `session_plan_updated/progress/superseded` + `map_finalization` + token/step 族；CanonicalPlan 三事件名禁止；`workflow_graph` 新词可用。
- [x] workflow_runtime 测试面：`tests/unit/workflow_runtime/`（内存 SQLite + `InstanceStore(factory)` + fake `plan_executor`，确定性无 I/O）。
- [x] affected-subgraph 孤儿：**不存在**——唯一引擎 `workflow_v4/recompute.py:111`，生产调用者 `runtime_bridge.py:381` + `resume_verify.py:526`；缺的是 chat 侧变更源与调度。
