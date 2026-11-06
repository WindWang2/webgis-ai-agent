# PLAN — 专题制图语义决策与多方案可解释设计

目标体验（验收锚点）：用户说「成都小学分布、各区数量差异、密度热点，做一张清晰规范的专题图」——系统基于**数据种类与用户表达目标**产 2–4 个可比较设计方案（点图 / 区级统计 / 密度面组合），每个候选带「为什么合适/为什么不合适/科学局限/降级路径」，选择经真实生产链落地为 Map Product，成图后局部修改不触发数据重算，导出前有科学+制图双层验收收据。

## 架构（最小侵入，全部复用既有权威）

```
用户 NL → intent 解析（既有）→ webgis_map_product（tools.py 主链）
   ├─ planner.plan_from_intent / finalize_with_profile（既有，不动裁决内核）
   ├─ ★ DesignTaskContext 投影（intent+profile+qualification → 结构化事实，无 query 字符串）
   ├─ ★ design_candidates.generate_design_candidates(ctx)
   │     ├─ 复用 select_composition_alternatives（组合层候选）
   │     ├─ 复用 model_library（表达模型 + data requirements + fallback 链）
   │     ├─ 复用 choose_classification（分布→分类，有统计时）
   │     └─ ★ 新增规则层：分母门控/点密度尺度/heatmap 诚实/时态双期/类目多样性
   │           → 2–4 MapDesignCandidate（suitable/conditional/blocked + typed reasons）
   ├─ ★ DesignDecisionTrace 序列化（intent→资格→候选→选择→分级→组合→指纹）
   │     → 附着工具结果载荷（有界）+ planner evidence
   └─ 用户/Agent 选择候选 → 既有 user-wins 绑定机制 pin 表达模型（不新造状态机）
        → 下一次 map_product 以 pinned 模型出图；落选理由持久可查
```

★ = 新增；其余全部复用。不替换既有选择器（choose_classification / composition_selection / planner 裁决）。

## 新模块契约

### 1. `app/lib/cartography/design_candidates.py`（M1，纯函数）

- `DesignTaskContext(BaseModel)`：task_categories/geometry_kind/variable_kind/statistic/artifact_types/point_count/denominator(DenominatorEvidence: field,available,unit)/temporal_periods/user_requested_models/scale_hint/output_target/measurement。投影面在 tools.py（结构化事实，case corpus 负例锁无 query）。
- `MapDesignCandidate(BaseModel)`：map_model_id/expression_family/composition_template_id/score/data_requirements/denominator_condition(Required+Satisfied / Required+Missing / NotApplicable)/classification(method+k+authority，可选)/palette_semantics/components/suitability(suitable|conditional|blocked)/condition_reasons(typed codes)/scientific_risks/degradation_path/fallback_of/reasons/against（对落选维度的对照理由）。
- `generate_design_candidates(ctx) -> DesignCandidateSet`：candidates ≤4（表达族去重，一族一候选；用户显式要求族可豁免）；selected_candidate_id 与 planner 实际产物对齐（由调用方传入 primary_model_id 标注 match/divergence）。
- 规则（每条可测）：
  - R1 分母门控：statistic∈{rate,density,per-capita} 或模型∈{normalized_choropleth,...} 且 denominator 缺 → blocked/conditional（`DENOMINATOR_MISSING`），同时生成 raw-count choropleth 候选并带 `AREA_BIAS_DISCLOSURE`（区面积偏误）。
  - R2 点密度尺度：point_count 超阈（>250 或视口密度超限）→ simple_point_map 降 conditional（`POINT_OVERPLOT_RISK`），aggregate_grid/point_cluster 升序。
  - R3 heatmap 诚实：visual_heatmap 候选恒带 KDE 披露（`KDE_NOT_POPULATION_DENSITY`）；user_requested 含 heatmap 且 point_count < 下阈 → blocked（`INSUFFICIENT_POINTS_FOR_DENSITY`）——用户显式要求原则保留，但科学不成立给 typed block 而非假成功。
  - R4 时态双期：temporal_periods ≥2 → change_comparison/diverging 候选入选。
  - R5 尺度一致：district 级数据 + point 表达 → `MAU_AREA_CENTROID_FALLACY` 风险注记。
  - R6 分类策略：有数值统计 → choose_classification 接入（复用）；无统计 → 留空 + `CLASSIFICATION_DEFERRED`。
- 版本 `DESIGN_CANDIDATES_VERSION = 1`；载荷经 `to_bounded_dict()` 有界化。

### 2. `app/lib/cartography/design_decision_trace.py`（M2）

- `build_design_decision_trace(...) -> Dict`：{version, intent_summary, data_facts, candidates[](含落选理由), selected{model,reason,match_with_planner}, classification, composition_template, mapspec_fingerprint, user_request_handling{requested[], honored[], typed_blocked[]}, scientific_receipts[]}。
- 生产接线：tools.py `webgis_map_product` 结果 payload 增 `design_candidates` + `design_decision_trace`（有界、best_effort 不阻断主链——同 #1549 纪律）；planner `template_selection.design_decision_trace` 同步落盘（证据面）。

### 3. 候选选择 action（M3）

- Agent 工具 `webgis_map_design_select`（tier 对齐既有 cartography 工具族）：入参 candidate map_model_id + session；写 user-wins representation pin（复用 representation_binding 机制/或等价 pin 存储面）+ 记录落选理由到服务端 map_state 白名单键（只读于客户端，回应 #1778 纪律——仅服务端路径写入）。
- 验收：固定表达方案后，后续自动调整/quality_loop 不得覆盖（user-wins 既有守卫 quality_loop.py:759 复用）。

### 4. 语义 QA 新检查码（M4，走显式契约更新协议）

- `CHART_MAP_FIELD_COHERENCE`（maplevel）：chart_panel/table_panel 组件数据绑定字段 ↔ 主专题层 data_binding/legend_spec 字段口径对账；不一致且无披露 → fail。
- `VIEWPORT_COVERS_RESULTS`（maplevel）：view bbox 与结果层 bounds 零交集 → fail（补上 coverage=0 反 pass 的漏洞）。
- `DENSITY_SEMANTICS_HONESTY`（layer）：图面语义（title/legend 单位/per-area 措辞）声称密度而数据为原始计数或 KDE 无披露 → fail/warning。
- 契约：docs/dev/ac-v11-contracts/semantic-checks.v1.json codes 45→48 + 计数断言 + not_evaluated 化解条目。

### 5. 前端结果交互入口（M7，边界：只做入口）

`frontend/components/map/map-components/design-candidates.tsx`：只读候选卡（模型/适宜性/理由/风险/降级）+ 接受按钮 → 既有 mutation/命令通道。注册进 component registry 的 disclosure 族（或 floating chrome），i18n zh/en，色盲友好沿用既有 palette token（不硬编码色卡）。

## 里程碑与验收

| M | 内容 | 验收 oracle |
|---|---|---|
| M0 | 规划文档 + 基线（本文档组） | 落盘 |
| M1 | design_candidates 引擎 + 规则 R1–R6 | 单测：每规则正/负例；候选 ≤4；确定性（同输入两次相等） |
| M2 | DecisionTrace + tools.py 接线 | dispatch_tool('webgis_map_product') 真实链路断言 payload 含 trace；best_effort 降级不阻断 |
| M3 | 选择 action + user-wins 持久化 | 选后重建 pin 生效；quality_loop 不覆盖；落选理由可查 |
| M4 | 3 新检查码 + 契约 | 契约测试绿（48 码）；每码正/负/not_evaluated 例 |
| M5 | 5 类方案真实 pipeline | F1 POI/计数、F2 密度、F3 分母门控、F4 双期、F5 图联动：planner→MapSpec→组件断言；heatmap 稀疏 blocked 负例 |
| M6 | G05 场景族（成都学校，synthetic 披露） | 新场景经真实 dispatch 绿；负例（缺分母不标密度）绿 |
| M7 | 前端入口 | vitest 组件测试绿 |
| M8 | 独立 review R1 + 清偿 | findings 清零 P0/P1/P2 |
| M9 | 回归 + review R2 + PR | 全 lane 绿；PR body 完整 |

## 明确不做

不重复：配色/分类/标注引擎、layout solver、组合模板生命周期（#1763）、导出 conformance（#1755）、三维模板（#1830/#1867）、GIS 统计核心。不给 Interaction IR 加新 kind。不修 #1779/#1809–#1813 等既有 issues（触碰路径绕开并披露）。不造新状态机。
