# ADR-0217: Situational Memory Graph — Dependency-Anchored Recompute（H09）

- 状态：Proposed
- 日期：2026-09-29
- 方向：H09（Context Revalidation → Recompute + Situational Memory Graph）
- 基线：master `77d2678d`
- 相关：ADR-0206（layered scopes/失效引擎——本 ADR 补其精度半边）、
  ADR-0215（evidence-backed revalidation——本 ADR 补其 recompute 半边）、
  ADR-0211/0214（critique 闭环的生产派生源）

## 背景与问题

F05（ADR-0215）落地了 stale→current 的证据闭环，但其失效语义沿用 ADR-0206 的
保守方向：任何 basis-affecting 变更无差别 stale 全部旧 basis_revision 的
findings/decisions（`invalidation.py` 的 `_attr` 只归因不过滤）。review 记录的
缺口在长会话中逐项成立：

1. **全会话级失效**：一个数据集换版即截断全部结论；"会变 stale"永远长不成
   情境记忆。
2. **无 recompute**：stale 恢复=重验证既有 claim；不存在"新 basis 下重新派生"。
3. **durable edit 只捕获 hide**：restyle/透明度/顺序/组件位置/语义突变在
   `_gis_provenance` 有原始记录（带 mutation_id + override_kind）但不进
   working context。
4. **ephemeral 与 durable 无一等分界**：viewport/hover 的处置靠惯例而非词表。
5. **token 预算不作用于事实投影**：F04 管块，card 是 char 截断，没有
   "按优先级+依赖闭包"的选择纪律。
6. **reuse 判定三档且无证据结构**：exact/recompute_partial/not_reusable +
   文本 causes，无操作档位与 token 证据。

## 决策

### D1 — 图是 working context 的 additive v3，不是第二存储

`gis_working_context.v2 → v3`：`ContextFact`（闭集 `FACT_KINDS =
{dataset, mapspec}` 的 durable token 锚点；决策在 `user_edits`/`DecisionRecord`、
工具结果在 ClaimStore——复制即第二真相源，禁止）、`DependencyEdge`
（闭集 `EDGE_DIMS`：7 个 `basis.*` 维 + `data`/`mapspec` token 维；anchor=派生时
token）、`DerivedFinding`（`family ∈ {critique, reuse}`、`generation`=发布时
context revision、`depends_on ≤6`、status 闭集 current/stale/superseded）。
全部有界（facts ≤24、findings ≤8、edits ≤12）；v1/v2 payload 照常加载；
`GIS_CONTEXT_MEMORY_GRAPH`（default ON）kill-switch 回退 v2 行为。

### D2 — 失效按依赖精确，缺锚 fail-closed

`invalidate_graph`：change → (dim, ref) 维度（与既有规则表对齐）；命中条件
——token 维（data/mapspec）按 `edge.anchor ≠ live token`（天然按 ref 精确），
basis 维按 `generation < at_revision`；无边行 fail-closed（与 FindingRef 同语义，
保守方向不回归）。**capture 的 token drift 本身驱动走线**（agent 侧地图突变推进
mutation_revision 而无用户语义编辑时同样失效——漏判 stale 是危险方向）。
FindingRef/decision 的 F05 语义逐字节不动。

### D3 — recompute 编排：优先级、原子发布、双栅栏

`plan_recompute`（priority desc，≤4/turn，families 过滤——无生产 executor 的
family 不进计划，防收据环洪泛）→ `execute_recompute`（family 键控 executor 只
派生不写；发布=原位替换：新 generation、新锚点、清 stale_reasons；任何失败保
stale）。双栅栏：**发布栅栏**——executor 的 fresh anchors 与 live token（resolver
契约；unknown fail-open，mismatch 拒绝 `superseded_by_newer_world`）比对，用户在
派生期间编辑地图 → 旧 generation 拒绝发布；**提交栅栏**——`store.save(expected_
revision)` 现有 CAS，输了走 rebase 不覆盖并发写者。每次尝试落
`FINDING_RECOMPUTED` 收据（复用 ADR-0215 收据环，additive 一个闭集 kind）；
transient 拒绝（如本轮未取数/取数空）不进持久环。

### D4 — 生产派生源：终验即派生

critique family 的捕获点在 `run_map_finalization` 尾部——**本次终验就是派生**，
图行按当前 generation/current 发布并锚定（mission/org 由捕获入口自解析：
durable binding → session turn context → tenant scan；同步体走 `to_thread`）。
stale 的 critique 行由下一次终验自然重算；中间回合卡片只以"需重算"+归因披露。
reuse family 的 executor 复用本轮已取 candidates（不二次检索；事件循环上无 DB）。

### D5 — durable edits：provenance 是唯一观察源

observation 投影 `_gis_provenance`（origin=user）为 `ObservedUserEdit`：
闭集 kind {show/opacity/restyle/reorder/component/delete/semantic}（hide 仍走
ADR-0215 D6 的专道），`op_id`=mutation_id 幂等，`detail`=reason-grade 摘要。
`analysis_affecting` 对齐权威自身的 `classify_override` 词表：presentation 编辑
只记账（user-wins 永不失效任何结论）；semantic 用户突变（reorder/delete/
semantic override）发射 `MAPSPEC_SEMANTIC_CHANGED` 供图 walk 重派生渲染结论——
**永不**触及 FindingRef/decision（claim 级 user-wins 不变）。重观察已记录编辑
零 transition（读多写少纪律：transition 且仅 transition 推进 revision）。

### D6 — ephemeral perception 永不入图

viewport/hover 只存在于每 turn 派生的 `SessionObservation`；`FACT_KINDS` 闭集
是第二道门——没有 perception 类，capture 无法收纳。durable decision 唯一入口是
provenance 突变记录（服务端盖章 origin）。

### D7 — token 预算投影 + 依赖闭包

`project_memory`：确定性全序（current findings priority desc → facts → stale
行）；est_tokens=ceil(chars/4) 不低估；**闭包纪律**——结论只与其锚点事实同投影，
锚点未入选则结论降级不展示（无依据的结论不冒充 current）；stale 行只以
"需重算"+归因出现。card 的 1600 char 硬门仍是外层上限；F04 allocator 不动
（单一 assembler）。

### D8 — reuse 四档操作决策

`decide_reuse`（纯映射，不做第二检索）：retrieval exact→`exact`
（evidence=claimed 指纹 token）；recompute_partial→`compatible`；
not_reusable + version 类 cause（version_bump/head_changed/request_input_stale）
→`must_recompute`（evidence 含 claimed token 对）；其余（claim_lost/scope_gone/
manual）→`stale_but_informative`；未知 verdict →informative（fail-closed）。
闭集 reason 码 + token 证据进卡片与收据。

## 禁止破坏的 invariants（实现即测试）

- user-wins：编辑记录永不失效/覆盖；presentation 守卫不动。
- F05 语义：FindingRef/decisions 失效/恢复逐字节保持；收据环 ≤8 复用。
- fail-closed：未知/异常保 stale；未知 token 不覆盖已知；学习≠漂移。
- 16KB payload 门、全部新集合有界、card 1600、单行日志制。
- transition 且仅 transition 推进 CAS token（读多写少）。

## Failure / Security / Resource Semantics

- 无 TTL：时间永不恢复任何东西；恢复只经引擎派生。
- 隔离：org guard 覆盖全部新入口（含捕获）；agent 无法伪造 user origin
  （provenance origin 由 `apply_gis_mutation` 服务端盖章）。
- 有界：图 walk O(changes×findings×edges) 全硬帽（<1s/50 turn 合成基准）；
  满图 payload 增量 ≤~4KB；每 turn 重算 ≤4 任务；收据环 FIFO ≤8。
- 可解释：为什么复用（四档 reason+token 证据）、为什么重算（边 anchor
  漂移证据进收据 prior_reason）、哪个事实使结果失效（stale_reasons 逐边归因）。

## Acceptance Matrix

| 要求 | 测试 |
|---|---|
| 数据换版/MapSpec 语义变更/用户 durable edit 精确失效相关 finding | `test_memory_graph.py`、`test_wiring_h09.py::test_semantic_user_edit_*`、`::test_agent_side_map_advance_*` |
| recompute 只发布新 basis 结果；旧 generation 不可越权 | `test_recompute_h09.py::test_concurrent_world_advance_*`、`::test_store_cas_is_the_second_fence` |
| 长会话 token 上限且保留关键依赖 | `test_projection_h09.py`（金标+闭包） |
| reuse 决策结构化 reason/evidence | `test_reuse_policy_h09.py` |
| 与 F04/F05 兼容、单一 assembler | 既有 103 个 F04/F05 测试零回归 + kill-switch parity 测试 |
| 并发编辑 vs recompute race / 复制收敛 | `test_recompute_h09.py`、`test_store_graph_rebase.py` |
