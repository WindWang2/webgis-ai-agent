# Cartographic Grammar & Constraint Solver Foundation — 设计与勘察（方向 4）

状态：本文件随 `feat/cartographic-grammar-solver` 分支交付；ADR 见
`docs/adr/0204-cartographic-grammar-visual-variables.md`。

## 0. Recon（2026-09-20，origin/master=5a4d4632）

### 已合并能力（不得重复实现）

| 能力 | 权威位置 | ADR |
| --- | --- | --- |
| 符号化唯一裁决（method×k×palette×clip） | `app/lib/cartography/symbology.py::resolve_symbology` | ADR-0152 |
| 分布驱动分类裁决 | `visualization_plan.py::choose_classification` | ADR-0073 |
| 双变量/时序/不确定度/成本提示 | `symbology_v2.py` | ADR-0162 |
| 标注字段挑选 + 策略编排（C4 基础） | `label_plan.py`（choose_label_field / plan_label_strategy / build_label_spec） | ADR-0154 |
| 布局槽位求解（碰撞/exclusive/堆叠/page profile） | `layout_solver.py`（solve_layout_v2/v3）+ `layout_constraints.py` | ADR-0101 |
| 必配组件基线 | `component_composer.required_components_for` | ADR-0156 |
| zoom 分档（标注密度/符号/terrain/抽稀） | `label_plan.DEFAULT_ZOOM_BANDS`、`scene_lod.LOD_BANDS` | ADR-0154 / ADR-0199 |
| 事后语义校验（含 `carto.visualvar.overload` Bertin 通道过载） | `semantic_checks.py`（≈50 检查） | ADR-0078 |
| 义务 rule graph + QA pack | `standards/`（12 kinds core v1.0.0） | ADR-0200 |
| 地图模型/分类法/色带元数据目录 | `model_library.py` | ADR-0073 |
| Recipe 先验（166 配方：资格/回退/组件偏好） | `app/services/gis_harness/recipes.py` + `recipe_packs/` | gis-harness spec |

### 真正缺口（本方向）

1. **视觉变量无事前规划**。`carto.visualvar.overload` 只能在成图后报警
   「通道过载」；没有任何模块在成图**前**决定「哪个字段以哪个测量语义、
   占用哪个视觉通道」。ADR-0189 把这记为痛点文字，无代码。
2. **`data_kind` 无人推导（正确性缺口）**。`resolve_symbology` 以
   `SymbologyProfile.data_kind`（sequential/diverging/qualitative/cyclic）
   决定整个色带族（`_FAMILY_ORDER`），但全仓仅 `scale_matrix.py` 硬编码
   传 `"sequential"`，其余调用点全部走默认 `"sequential"`。后果：signed
   change（增减变化）数据被画成 sequential 单向色带，制图学上错误
   （正负语义丢失，应 diverging RdBu/PuOr 族）。
3. **表达选择（representation）规则未成文**。密集点 → heatmap/grid/
   cluster、低 N → 符号图、类别过多 → 收纳 "Other"：这些判断散落在
   recipe 资格规则与工具硬编码中，无确定性、可解释、带 reason code 的
   裁决面。
4. **尺度语义与表达选择未接线**。`label_plan`/`scene_lod` 已有 zoom 分档，
   但「什么尺度下该聚合/泛化/换表达/调可见范围」无契约。

### 与并行分支的热区（本 PR 避让）

- `semantic_checks.py` / `mapspec_schema` / 契约 JSON：ADR-0200 D7 竞争
  纪律延续——本 PR **零改动**这两个文件。
- i18n 横向工作（#1436）：不碰前端文案面。
- recipe 资格/回退链（#1405 已合并）：只读消费，不改资格语义。

## 1. Problem Statement

制图决策的**事前规划层**缺失：数据字段的测量语义（nominal/ordinal/
quantitative/ratio/rate/signed_change/uncertainty/temporal）→ 视觉变量
通道占用（hue/size/lightness/…）→ data_kind → 表达选择 → 尺度动作，
这条链没有系统性裁决入口。每新增一种字段形态，就要在某个工具入口或
recipe 里再硬编码一次假设（现状即「所有数值默认 sequential」）。

目标（任务书原式）：

```
Intent + Data Semantics + Scale + Product Goal
  → Cartographic Constraints
  → Map Model / Visual Variables / Classification / Components / Layout
```

Recipe 保留为高层产品模式；底层规则上收到 grammar。

## 2. Current Architecture（消费关系，全部既有）

```
tools/create_thematic_map ─┐
tools/apply_template ──────┼─→ symbology_decision_from_values(data_kind=默认 sequential!) ─→ resolve_symbology（唯一裁决）
tools/spatial(heatmap) ────┘        ↑ 这里就是缺口
label_plan.build_label_spec ─→ MapSpec layer.label ─→ 前端 label-layout.ts
required_components_for ─→ components ─→ layout_solver.solve_layout_v3 ─→ layout
semantic_checks / standards QA ─→ 事后校验（visualvar.overload / count_vs_rate / legend…）
```

## 3. Ownership / Authority（本设计的红线）

| 裁决 | 权威 | Grammar 的角色 |
| --- | --- | --- |
| method/k/palette/clip 最终值 | `resolve_symbology`（ADR-0152） | 只产出 `data_kind` 与 recommended 输入 |
| 分类算法 | `classify.py` / `choose_classification` | 不重写；分类法选择仍由引擎裁决 |
| 标注字段/策略 | `label_plan`（ADR-0154） | 只消费 `build_label_spec`，不建第二引擎 |
| 布局槽位 | `layout_solver` v2/v3 | 只产出参与者与 pin 语义，放置归引擎 |
| 必配组件 | `required_components_for` | 只在其上叠加 grammar 义务（legend 形态等） |
| 事后校验 | `semantic_checks` / standards | grammar 提供**只读对账** `audit_*`，不输出第二 verdict |
| 修复 | quality_loop AUTO_SAFE | 零 mutation；repair 只以 fix_hint 路由建议出现 |

**无第二真相**：GrammarDecision 是规划工件（一等、可序列化、版本化、
带指纹），不是状态存储；MapSpec 仍是 desired state 唯一权威。

## 4. Canonical Data Contracts（新增）

`app/lib/cartography/visual_variables.py`（C1）：

- `MEASUREMENT_KINDS = (nominal, ordinal, quantitative, ratio, rate,
  signed_change, uncertainty, temporal)` —— 测量语义冻结词表。
- `MEASUREMENT_TO_DATA_SEMANTICS` —— 单向投影到 standards 的
  `DATA_SEMANTICS`（count/rate/density/category/continuous/temporal/
  uncertainty，A-0200 冻结词表不改动）；无反向依赖。
- `VISUAL_VARIABLES = (position, size, shape, hue, lightness, saturation,
  opacity, texture, orientation)`；`CHANNEL_RUNTIME_STATUS` 诚实标注
  texture=unsupported、orientation=partial（任务书「若 runtime 支持」）。
- `CHANNEL_FIT[measurement][variable] -> ChannelFit(level, reason_code)`，
  level ∈ preferred/allowed/rejected（Bertin/Mackinlay 表达力原则）。
- `infer_measurement_kind(field_name, dtype, values, …) ->
  MeasurementDecision`：确定性证据推断（负值占比→signed_change；
  有界[0,1]+rate/ratio/pct/share 词素→rate；低基数样本→nominal；
  rank/level 词素→ordinal；时间词素→temporal），附 reasons/rejected。
- `derive_data_kind(measurement) -> DataKind`：nominal→qualitative、
  signed_change→diverging、temporal(cyclic 词素)→cyclic、其余→sequential。

`app/lib/cartography/scale_rules.py`（C3）：

- `SCALE_TIERS`：语义尺度带 world / province / city / street，边界与
  `label_plan.DEFAULT_ZOOM_BANDS` 完全一致（0-8/8-11/11-14/14-24，
  契约测试锁定相等）——同一 zoom 心智模型覆盖标注与表达两层。
- `resolve_scale_tier(zoom)`、`scale_actions(geometry, feature_count,
  zoom, density) -> ScaleDecision`：每带给出聚合建议（行政级）、点表达
  约束（cluster/heatmap 候选资格）、可见范围建议、泛化提示、符号系数
  （符号/字号系数**引用** `scene_lod.LOD_BANDS` 的 size_ratio，不重抄数值）。

`app/lib/cartography/grammar_solver.py`（C2 + 接线）：

- `FieldEvidence(name, dtype, values, unique_ratio, null_rate,
  measurement_override)`；`GrammarRequest(geometry, feature_count,
  viewport, zoom, purpose/audience/medium（standards 同词表）, fields,
  pinned 用户显式选择)`。
- `GrammarDecision`（pydantic，`GRAMMAR_VERSION` + 内容指纹）：
  - `channel_bindings[]`：field → measurement/channel/data_kind/role；
  - `symbology_inputs`：data_kind + recommended_*（喂 resolve_symbology）；
  - `representation`：selected + candidates 排序 + rejected[]（reason code）；
  - `obligations`：legend 形态（categorical_legend vs colorbar）、组件
    义务（复用 required_components_for 词表）、label 意图；
  - `scale_actions`、`disclosures`、`user_wins` 记录；
  - `audit(mapspec_layers) -> GrammarAudit`：只读对账（色带族↔data_kind、
    legend↔colorbar 配对、通道数与 `semantic_checks._VISUALVAR_*` 阈值
    对齐——契约测试锁定不漂移）。

### Reason code 家族（稳定、可审计）

`GRAMMAR.MEAS.*`（测量推断）/ `GRAMMAR.CHAN.*`（通道适配/拒绝）/
`GRAMMAR.REP.*`（表达选择）/ `GRAMMAR.PAIR.*`（legend/colorbar 配对）/
`GRAMMAR.SCALE.*`（尺度动作）/ `GRAMMAR.PIN.*`（user-wins）。

## 5. State Transitions

无新增持久状态。GrammarDecision 生命周期：请求 → 求解（纯函数）→
随工具结果/plan evidence 下发 → audit 消费（只读）→ 丢弃。用户 pin
（显式通道/色带/表达）在求解时以最高优先写入 `user_wins`，冲突只披露
不覆盖（ADR-0118 user-wins 同语义）。

## 6. Integration Seams（本 PR 改动的生产文件）

1. `app/tools/cartography.py::create_thematic_map`：method 未显式时，
   以 `infer_measurement_kind` 推导 data_kind 传入
   `symbology_decision_from_values`（signed→diverging 修正），结果
   evidence 增加 `grammar` 摘要（测量语义/reasons/data_kind）。
2. `app/tools/templates.py::apply_template` 数值字段路径：同上推导
   （原 `data_kind` 默认 sequential）。
3. `app/lib/cartography/quality_loop.py`：review 报告新增只读
   `grammar_audit` 段（有 GrammarDecision 时才评；缺失 =
   not_evaluated，诚实不新增阻断）——map critique 消费面。

## 7. Failure Semantics

- 推断证据不足（n<2 / 全 null）→ measurement=fallback(quantitative)
  + `GRAMMAR.MEAS.INSUFFICIENT_EVIDENCE` 披露，行为与现状一致
  （sequential），**不改变既有默认路径的输出**。
- 非法用户 pin（nominal 域 pin 到 size 等 rejected 级通道）→ 不静默
  改写：记录 `user_wins` + `GRAMMAR.CHAN.PIN_CONFLICT` 披露，最终
  无障碍/可分辨硬约束仍由 resolve_symbology 兜底。
- audit 永不 fail 整体交付：输出 findings + severity，无第二 verdict
  （ADR-0200 D2 同纪律）。

## 8. Idempotency / Replay

求解为纯函数（同输入同 `model_dump()`），指纹 = sha256(版本+规范化
输入)。同一请求重放产出逐字节一致；audit 只读无副作用。

## 9. Security / Permission

不接触凭据/要素体之外的数据；输入是有界 profile 证据（字段名/dtype/
值样本 ≤64/基数/空值率，与 label_plan.FieldStats 同源形态）。evidence
不含 CoT、不含原始 payload（大数据 ref/descriptor 纪律不变）。

## 10. Resource / Cost

纯函数、O(fields × values样本)；无 IO、无模型调用。审计 O(layers)。
不新增任何常驻进程/缓存。

## 11. Observability

GrammarDecision 随 classification_plan 同一下发通道（工具结果
evidence）；reason code 全表在模块 docstring 与 `catalog` 帮助函数
中可见；quality_loop review 报告携带 grammar 段供 trace 反查。

## 12. Backward Compatibility

- 未显式传 data_kind 的调用点行为变化仅限「证据支持的更正确族选择」
  （signed→diverging）；sequential 数据路径输出不变（golden 回归锁定）。
- `GrammarDecision` 全字段 additive；旧消费方不读即不受影响。
- 前端零改动（本方向无 UI 面）。

## 13. Migration Plan

第一步（本 PR）：接线两个工具入口 + quality_loop 只读审计。后续：
recipe packs 的 `default_components`/表达偏好逐步改为消费 grammar
候选（不在本 PR）；更大范围入口（spatial/advanced_spatial）按同模式
迁移（follow-up）。

## 14. Rollback / Feature Flag

工具入口接线包裹在 try/except + 证据缺失回落现状（推导异常时退回
默认 sequential 路径并披露），等效天然回滚开关；不引入持久 flag。

## 15. Acceptance Matrix（对应任务书「必须新增的测试」）

| 任务书要求 | 测试 |
| --- | --- |
| nominal/ordinal/quantitative mapping | `test_visual_variables.py::measurement→channel/data_kind` |
| count vs rate | `test_grammar_solver.py::rate_field_disclosure/count_vs_rate` |
| signed diverging | `test_grammar_solver.py::signed_change→diverging 族`（端到端过 resolve_symbology） |
| legend vs colorbar | `test_grammar_solver.py::pairing`（qualitative↔colorbar 拒绝 + 修复路由） |
| dense/low-N point switch | `test_grammar_solver.py::dense_points_candidates/low_n_symbol` |
| excessive categories | `test_grammar_solver.py::too_many_categories_collapse` |
| multi-scale visibility/generalization | `test_scale_rules.py::tier_boundary/point_representation_by_tier` |
| label density | `test_grammar_label_contract.py`（委托 label_plan 的契约全覆盖） |
| layout collision | `test_grammar_layout_contract.py::collision_fallback` |
| user-pinned component ownership | `test_grammar_layout_contract.py::user_pinned_wins` + `PIN_CONFLICT` |
| live/export grammar parity | `test_grammar_layout_contract.py::page_profiles_same_decision` |
| critique 消费 grammar | `test_quality_loop_grammar_audit.py` |
| 既有行为回归 | golden：sequential 路径逐字节不变；`resolve_symbology` 既有 golden 不动 |
