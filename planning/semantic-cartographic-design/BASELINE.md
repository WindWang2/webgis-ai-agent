# BASELINE — 专题制图语义决策与多方案可解释设计（/goal 04）

- 基线 commit：`4b3629c90ee977e287f7b1fb4fd827b0fad7428c`（origin/master，2026-10-29 PR #1867 合并后）
- worktree：`../webgis-worktrees/gis-carto-design`，分支 `zcode/cartography-design-choices-vnext`
- 审计时间：2026-10-30；开放 PR 仅 #1868（数据准备方向，文件面零重叠）

## master 真实能力归档（源码调用链证明）

| 能力 | 判定 | 关键证据（file:line） |
|---|---|---|
| MapProductPlanner（intent→plan→finalize） | production | `app/services/gis_harness/planner.py:690,809,1228`；调用面 tools.py:492/856/899、workflow_compiler.py:381/387、map_plan_tools.py:165、plan_orchestrator.py:591 |
| choose_classification（分布→分类法） | production | `app/lib/cartography/visualization_plan.py:91`；唯一裁决面 symbology.py:663（:261/:303/:316/:328 四处消费） |
| VisualizationPlan 工件（QA 反查） | **scaffold-only** | `build_visualization_plan`（visualization_plan.py:228）全仓零生产调用 |
| model_library + model_packs（~64 MapModel） | production | `app/lib/cartography/model_library.py:151-200`（MapModel 含 accepted_artifact_types/data_preconditions_zh/fallback_model_id/runtime_status）；registry 校验 :571 |
| composition packs（12 pack）+ selection | production | `composition_selection.py:236` select_composition_alternatives（3 候选+评分+理由）；生产消费 planner.py:86 `_composition_alternatives_evidence` → template_selection.composition_alternatives（:1737-1759）；composition_tools.py:180/209 |
| plan_candidates（recipe 级多候选） | production | `plan_candidates.py:83-104` PlanCandidate（9 维分/rejection_reasons/disclosures）；消费 workflow_compiler.py:333 |
| data_qualification（五态） | production | `data_qualification.py:28,402,735`；DENOMINATOR_FIELD_HINTS 词表（workflow_schema） |
| methodology_warnings（缺分母披露） | production | planner.py:643-647,1804 `_append_methodology_disclosure` |
| user-wins 表达绑定 | production（flag 门控） | `representation_binding/`（requirement_to_mapspec.py build_binding_proposals）；tools.py:724-732 前置；MapSpec layers[].representation（mapspec_schema.py:582-598,646） |
| MapSpec v1.7 + mutation SEMANTIC/PRESENTATION | production | mapspec_schema.py KNOWN_VERSIONS:71；mutation_registry.py:94-123（patch_layer_style=PRESENTATION 不触层重算 :502-511） |
| Interaction IR（观察+层突变） | production | interaction_ir/contracts.py:100-166；语义编辑（换图表型/子区过滤）**词表显式留白** |
| 前端改图命令 | production | map-commands/catalogue.ts:22-33（10 slice）；chart_switch_type=variant patch 不重算（chartCommands.ts:172-183）；set_classification 诚实拒绝（style-intent.ts:134-139，后端 reclassify 通道不存在） |
| 语义检查（45 冻结码） | production | semantic_checks.py:2491 唯一入口；契约 docs/dev/ac-v11-contracts/semantic-checks.v1.json + test_semantic_checks_contract.py（码增删须显式改契约+计数） |
| quality_loop / selfheal / map_critique | production | quality_loop.py:695（MAX_REPAIR_ITERATIONS=2、user-wins 抑制 :759）；selfheal_actions.py:94-139（14 动作） |
| G05 验收 lab | production | tests/acceptance/gis_agent/（61 场景真实 dispatch_tool）；成都学校合成 fixtures fixtures_data.py:46-147 |
| undo/redo + durable runtime | production | frontend/lib/workbench/undo.ts；project_runtime/（快照+ResumeReceipt） |
| DecisionTrace / AlternativesReceipt | **absent** | 全仓 0 命中；最近似 PlanDecision journal（models.py:272，不投前端）与 explain bundle（replay/explain.py，开发者向无路由） |
| 候选用户接受/拒绝 UI | **absent** | frontend 内 plan_candidates/alternatives 0 命中；decision-panel.tsx 为只读 MCDA 排名 |

## 已有 QA 缺口（本任务 M4 的靶点，勘察三号报告核实）

1. **统计图↔地图口径对账**：完全缺失（chart_panel 有 artifact 绑定 ABI，无跨组件数据口径检查）。
2. **视口含必要结果**：`carto.load.ratio` 计算 bbox_coverage（semantic_checks.py:1056-1059）但 coverage=0 时负载为 0 反而 pass——无 fail 路径。
3. **分布图误标密度**：仅 representation 窗口的聚合披露级（representation.py:73-82）+ G05 任务级负例，无图面语义检查码。
4. 确定性 contrast 检查：palettes.py:59-78 WCAG 工具存在但 semantic_checks 不消费（低优先，不在本任务必做面）。

## 与本任务的差异化增量（不是重复建设）

- master 的候选都在「组合模板层」（composition）或「recipe 层」（plan_candidates）；**表达模型层的可比较方案**（点图 vs 聚合 vs 密度面 vs 区级统计，带数据资格 typed 门控与降级链）不存在。
- master 的决策证据是散片（template_selection.composition_alternatives / plan evidence / methodology_warnings）；**统一 DecisionTrace 工件 + 候选落选理由 + 用户可见**不存在。
- 语义检查三缺口如上。
