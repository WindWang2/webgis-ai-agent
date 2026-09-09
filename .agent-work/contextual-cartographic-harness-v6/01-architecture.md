# 01 — 目标架构（Contextual Cartographic Harness V6）

## 定位

Harness 是 GIS-specific runtime；Pi 是 agent host。不重造 planner/conversation/tool-call engine。LLM 负责语言与软判断；状态转换、依赖、影响范围、完成条件、修复预算全部由代码契约保证。

## 闭环（目标形态）

```text
User Intent → Semantic Understanding → Canonical Typed Workflow DAG
→ Runtime Workflow Instance → GIS Execution → Artifacts / MapSpec
→ Browser Rendering → Runtime Observation + Visual Observation
→ Unified Findings → Completion Verdict → Affected Subgraph
→ Repair / Partial Recompute → Re-render → Re-observe
→ READY / DEGRADED / BLOCKED / FAILED
```

## 单一事实源裁决（红线落地）

| 事实 | 唯一拥有者 | 其他系统的合法形态 |
|---|---|---|
| 工具/算法/能力目录 | 现有 registries（tool/algorithm/capability/artifact） | 派生索引、投影 |
| 计划与执行状态 | `SessionPlan.gis_chapter`（含 `workflow_instance` 键） | Compiler V4 = 编译期权威投影，经 bridge 回写 |
| Workflow 语义图 | Compiler V4 typed DAG | runtime 侧只存 node runtime 状态（索引到同一 node_id） |
| 地图语义 | MapSpec（lifecycle engine + CAS） | observation 只产 findings，不写 MapSpec |
| 诊断/发现词汇 | 各 domain 词表原处保留 | Completion Engine 消费统一投影（UnifiedFinding adapter） |
| 组件解析 | `resolveMapComponents`（前端单点） | scene oracle / observation 复用同一解析器 |

## 关键架构决策（初定，各 wave 落地时细化并记 ADR）

1. **Runtime Graph = typed DAG node_id 命名空间上的 runtime 状态投影**，不是第二套图。`PlanNode`/`StageState` 经 adapter 与 typed DAG node 对齐；状态词汇以现有 `PlanNodeStatus`/`StageState` 为准增补（STALE/REPAIRING 已有对应物则复用），禁止平行枚举。
2. **Compiler → Runtime bridge**：plan 落地时编译 V4 并把 compilation 摘要 + node 映射写入 `gis_chapter`（additive 键）；执行事件（tool 结果、artifact 产出、observation）经 bridge 更新 node runtime 状态。
3. **Change → recompute**：所有变更先分类（复用 `WorkflowEventKind` + `CHANGE_TARGETS` 并补齐 STYLE/LAYOUT/VIEWPORT 等维度，落 `workflow_schema.RECOMPUTE_DIMENSIONS` 词表），走 `diff → affected roots → downstream closure → reuse validation → RecomputePlan`，由 `maybe_update_workflow_instance` 消费。
4. **Reuse validation**：复用现有行指纹 `rows_fingerprint`（algorithm+params 哈希）+ `ArtifactRecord.status` + contract 指纹（data_fingerprint/profile_fingerprint/content_revision）；证据不足 → `reuse=unknown/unsafe` → 保守重算。
5. **Visual Observation 两层**：deterministic（先接线 `label_engine.solve`、消费前端实测 rect、layout solver reasons → findings）+ soft（`VisualEvaluator` seam，只输出 `VisualFinding[]`，禁改 MapSpec，默认关闭/可离线）。
6. **UnifiedFinding 是 adapter 投影**：`domain/code/severity/source/scope/affected_entity/evidence/repair_class/retryable/blocks_completion/degradation_only`；domain 词表原地保留。
7. **锁下沉**：`lockedLayerIds`（+新增 component 锁词表，复用 workbench doc）在 `lifecycle_engine` 引擎层统一 enforce，前端命令层退化为乐观提示；user override 分 semantic/presentation/transient 三类。
8. **Repair loop 防循环**：finding fingerprint + attempt count + state epoch + mapspec_revision + workflow revision；重复 finding+state → `NO_PROGRESS`/`REPAIR_EXHAUSTED` → disclosure。
9. **检索 V6**：registry 仍是唯一工具事实源；语义/hybrid 检索经 `TOOL_RETRIEVAL_SEMANTIC` 既有 hook 接线项目内可控实现（embedding index 可离线、可确定性退化 lexical）；reranker 消费 workflow stage/artifact/CRS/budget 情境。
10. **Context assembly**：Node-local + Workflow-global + Map Situation 三层投影，落 `context_assembler`/`pi_turn_context` 现有块机制，不新建通道。

## Waves（按 Prompt §50，经审计微调）

- W1 Canonical Workflow Runtime Projection（gis_chapter additive 键 + node 映射）
- W2 Compiler→Runtime bridge（plan 落地编译 + 执行事件回写 + LLM 路径补 V4 证据）
- W3 Artifact/MapSpec/Node lineage 双向索引
- W4 Semantic Diff→Affected Subgraph 生产接线（change 分类词表收口）
- W5 Partial Recompute + Reuse Validation（执行调度消费 RecomputePlan）
- W6 Unified Findings adapter + Completion Engine 消费统一投影
- W7 Completion Verdict 单一化（视觉/组件状态纳入七维消费）
- W8 Deterministic Cartographic Observation（label solve 接线、rect 消费、组件生命周期状态）
- W9 Visual Observation seam（VisualFinding + evaluator 接口 + 隐私策略）
- W10 Repair Planner（finding→repair class→plan，复用 RemediationAction/REMEDIATION_POLICY）
- W11 Repair Loop 防循环（指纹/epoch/预算）
- W12 Tool Retrieval V6（semantic/hybrid 接线 + 情境 rerank + 语料扩至 ≥300）
- W13 Contextual Context Assembly（三层投影）
- W14 Resume VNext（恢复后 artifact/mapspec/workflow 指纹验证 + stale 标记）
- W15 Human-Agent 状态收敛（锁下沉 + override 三分类 + component 锁）
- W16 Closed-loop Corpus（≥100 场景 + 10 个关键 E2E）
- W17 Performance/Security（结构预算 + O(n²) 审查 + 隐私）
- W18 Docs/ADR/CHANGELOG + 两轮 review + rebase + PR

每个 wave：inspect → design → implement → unit test → integration test → commit。
