# G02-GIS-EXECUTION-COMPILER BASELINE（M0 审计）

- 审计时间：2026-11-07（实时重查，非快照假设）
- 基线 `origin/master`：`585812ee4e2d75612f2b124e6fe6587af3deca5c`（2026-11-07 17:54 +0800，Merge PR #2010）
- worktree：`../webgis-wt-gis-execution-compiler-20261107`，branch `zcode/goal-gis-execution-compiler-20261107`

## 1. 远端事实

- **开放 PR**：仅 8 个 dependabot（#1978–#1986），与本方向零冲突。
- **未开 PR 的远端分支**：`feat/g0*-vnext`/`feat/h0*-vnext`（2026-10-21/22）与
  `fix/planner-capability-state-20261104` 等经 `git rev-list --count origin/master..origin/<b>`
  验证 **0 commits ahead**（已全量合入），无在途冲突。
- **快照后已合并 PR**（不得重做）：#1991–#1998（事件钉扎/空间选择重构/可用性/出版守门/PostGIS 池/完成结算/质量债/载荷预算/heatmap 目录同步）、#2008–#2010（真实 Story 旅程/session 锁重入/会话恢复命令可用性）。
- **开放 Issues（全部 2026-11-07 建，均 OPEN）**：#1999 A4 导出、#2000 版面标记污染、#2001 跨城市图层范围、#2002 组件族锁、#2003 指北针、#2004 框选截断、#2005 网络距离聚合秒数、#2006 工具 summary 丢数值、#2007 OpenAPI 快照过期。按任务书：仅作输入，#2005/#2006 做**合约回归验证与原生避让**，不做独立抢修。

## 2. 管线现状（真实调用点审计）

理解→执行→验收的真实链路与断点（文件:行为均为 master 实读）：

| # | 链路环节 | 现状 | 判定 |
|---|---|---|---|
| 1 | RequirementIR（ADR-0266，`requirement_document.v1`：typed sections + provenance + patch + ambiguity） | 成熟：GISIntentSpec.core 内嵌 MapRequestIntent，sections 派生，user-wins patch | ✅ 已有 |
| 2 | IR → 编译入口 | `workflow_compiler.compile_workflow(query/intent=MapRequestIntent)` 只吃 **core intent**；RequirementDocument 的 measures/statistics/spatial_relation/scene sections 不进编译 | ❌ 断链 |
| 3 | 任务族选择 | `analysis_workflows.plan_workflow(family=...)` 的 family 是 LLM 工具参数（`webgis_analysis_workflow_plan`）自由指定；无 IR→family 确定性推导 | ❌ 断链（GC-02 核心） |
| 4 | 双执行路径 | 路径 A：MapProductPlan（planner，15 阶段编译）+ PlanGraph 投影 + SessionPlan 行状态推进；路径 B：analysis_workflows 七族 contract + WorkflowExecutor（波次并发+内容指纹重算）。两路径**无桥** | ⚠️ 重复/断链 |
| 5 | family 执行回执 → goal 验收 | `WorkflowRunState` 独立持久化（`gis_chapter` 之外）；`goal_satisfaction/evaluator` 只读 chapter 行（analysis_steps/data_requirements/fallbacks/map_product）→ **family 执行成功不可被目标验收看见**；反之 chapter 行也无法引用 family 步骤 artifact | ❌ 断链（GC-08 核心） |
| 6 | 候选/资格 | `plan_candidates.generate_plan_candidates`（recipe 级多候选+科学阻断 reroute）+ `candidate_planner_v8`（capability→model/tool 资格+资源排序）已在 master；但"同一任务多种正确表达"（点图/密度/分区率）无 data-scale/几何/语义驱动的表达候选推导 | ⚠️ 部分（GC-02） |
| 7 | registry 统一 | `execution_catalog`（capability/algorithm/tool/recipe 统一只读投影+对账）+ `tool_capability_conformance`（8 面对账）+ `capability_graph`（v8）已在 master；族声明（families.py 步骤→tool/capability/单位）与 catalog 的**一致性/live conformance 检查缺**（工具下线→族步骤悬空无 fail-closed 裁决投影） | ⚠️ 部分（GC-03） |
| 8 | 科学单位 | families `unit_contract` + `science_final_gate`（run_final_gate 进 state.science_validation）+ scientific_errors/crs_safety 已在；Issue #2005/#2006 表明 **dispatch→summary 汇总面**存在单位/数值丢失洞 → 本方向做合约回归语料锁行为，不直接修 issue | ⚠️ 部分（GC-05） |
| 9 | 选择性重算 | 路径 B 内：`recompute.py`（内容指纹+下游闭包+style 永不失效）成熟；路径 A 内：`plan_runtime.seed_recompute_from_failures`；**跨层 IR patch → 受影响 capability/step 的失效推导没有**（intent_diff 只服务 MapProductPlan 行） | ⚠️ 部分（GC-09） |
| 10 | 离线基准 | `app/evaluation/`（quality/goal_satisfaction/hard_negative/failure 等 15+ 语料）已在；**无「IR→族→DAG→dispatch→验收」端到端编译器 golden 语料** | ⚠️ 部分（GC-10） |

## 3. 冲突矩阵（本方向 vs 已合并能力）

| 已合并能力 | 关系 | 避让决策 |
|---|---|---|
| G02 科学方法契约（method_use_contract/science_final_gate/fail-closed 执行门） | 正交 | 只消费 `run_final_gate`/`MethodUseContract`，不重造 |
| #1972 类型化空间分析封套 ArtifactEnvelope | 下游产物形态 | 编译器产物引用 envelope refs，不改 envelope |
| #1989/#1993 Pi 可用性/地理范围求解 | 正交 | 不动 |
| #1998 载荷预算 | 正交 | 编译器资源估计只读消费 |
| analysis_workflows v2（七族+executor+指纹重算） | **直接基座** | 在其上编译，不建第二 orchestrator |
| planner v8/candidate_planner_v8 | 直接基座 | 只读消费资格/排序产物 |
| goal_satisfaction（ADR-0183） | **直接基座** | 扩展 evidence kinds 消费 family 回执，不改判词汇表 |

## 4. 本方向真实缺口（下沉后的可证明目标）

**G-EXEC-CORE**：`requirement_ir → 确定性执行编译` 缺失——IR 的 typed sections 不驱动族/候选/角色绑定推导；LLM 在聊天中自由选族。
**G-EXEC-BRIDGE**：family 执行回执与 chapter 验收链双侧断链（GC-08 的"节点成功≠目标达成"在路径 B 上完全不可验）。
**G-EXEC-CONFORMANCE**：族声明 ↔ execution_catalog/ToolRegistry 的一致性 fail-closed 投影缺失（GC-03"有工具可用但科学/地理不适合"的裁决只有零散面）。
**G-EXEC-UNITS**：单位/量纲义务的跨层静态校验（IR measure 统计词表 × family unit_contract × 算法参数单位）无单一对账点（GC-05）。
**G-EXEC-RECOMPILE**：IR patch（改色/换区/换城市/换统计）→ 受影响节点集推导缺失（GC-09，需证明未重跑无关昂贵计算）。
**G-EXEC-BENCH**：端到端编译器 golden 语料（≥20 类，正/反例+几何/单位 oracle）缺失（GC-10）。

## 5. 风险登记

- 双路径桥接必须 **additive**：不改 WorkflowRunState 持久 schema 破坏性字段；chapter 投影新键带 schema 版本。
- goal_satisfaction 证据分级红线（deterministic/structural 才可背书 PASS）必须保持——family 回执属于哪级需显式裁决并测试钉死。
- 编译器必须零 LLM/零 IO 纯函数（同 workflow_compiler 纪律），available_tools 视图注入。
- 不新增第二 planner：编译器输出**喂给**既有 plan_workflow/compile_workflow，不平行编制。
