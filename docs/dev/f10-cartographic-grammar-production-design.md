# F10 — Cartographic Grammar Production Adoption · 设计与架构决策

前置：`docs/dev/f10-cartographic-grammar-production-recon.md`（勘察结论）。
上游：ADR-0205（grammar 基座，#1480）、ADR-0207（量纲语义，#1488）。

## 0. 总原则（重申）

Grammar 是**规划层**，不是第二裁决。本方向的所有接线都只做三件事：**统一语义推导的
输入源、把 grammar 产物作为既有 resolver/review 的输入、把决策工件随证据链下发**。
裁决权威（resolve_symbology / choose_classification / label_plan / layout_solver /
semantic_checks）与 user-wins pin 语义零改动。

## D1 · 测量语义统一推导（M7）—— 新模块 `app/lib/cartography/semantic_inputs.py`

**问题**（recon O1–O4）：`MEASUREMENT_KINDS`(8, ADR-0205) 与 `MeasurementKind`(11,
ADR-0207) 两套词表、两个 data_kind 推导点、两套名称词素；`create_thematic_map` 每请求
并发跑两个引擎且互不协调。

**决策**：
1. **单一推导入口** `derive_semantic_inputs(field, *, roles, value_samples, dtype,
   has_temporal, crs, unit_override, explicit_measurement, profile_semantics) ->
   SemanticInputs`，判定优先级：
   - ① `explicit_measurement`（grammar 词表显式 pin，user-wins，source=explicit）；
   - ② Dataset semantic contract（#1488 `FieldSemantics`，来自
     `derive_field_semantics`/`derive_measurement_profile`）非空 kind → **11→8 投影**
     （`MEASUREMENT_KIND_TO_GRAMMAR` 冻结映射，本模块单点）；
   - ③ #1480 `infer_measurement_kind` 值/名称/dtype 证据补残（#1488 角色缺失时的
     结构证据面：dtype 类别面、双符号值、强词素、低基数整数）。
2. **11→8 投影表**（冻结，有尽断言）：count→ratio、absolute_quantity→ratio、ratio→ratio、
   rate→rate、density→rate、percentage→rate、index→quantitative、category→nominal、
   ordinal→ordinal、signed_change→signed_change、uncertainty→uncertainty。
3. **signed-rate 升级**（适配层内，不动 #1488 内部）：#1488 判 rate 而值证据呈双符号
   （负侧占比 ≥ `visual_variables._SIGNED_NEG_SHARE`）→ 升级 signed_change，reason code
   `GRAMMAR.MEAS.SIGNED_RATE_ESCALATION`，披露「带符号率应 diverging」。
4. **data_kind 单点**：投影后的 grammar kind 一律过 `visual_variables.derive_data_kind`
   （ADR-0205 D3 单点，保留 temporal+cyclic 路径）；`uncertainty` → `data_kind=None`
   （与 `measurement_to_data_kind` 的 None 同口径：无色相族证据，resolver 保持缺省）。
5. **保守降级**（M8）：任何异常/空证据 → `data_kind=None` + `measurement_kind=""` +
   check code `GRAMMAR.MEAS.INSUFFICIENT_EVIDENCE` / `SEMANTIC_DERIVATION_FAILED`，
   绝不抛出阻断出图；checks 随 `layer_meta.measurement_checks` 下发。
6. **依赖方向**：cartography → gis 单向（与 visual_variables → standards 同风格）；
   visual_variables / gis.measurement 内部零改动（两者既有契约测试全部保持）。

`SemanticInputs`（pydantic，可序列化、有界）：field、measurement_kind、data_kind、
source（explicit|dataset_contract|evidence|fallback）、confidence、checks（≤4）、
reason_codes、disclosures、evidence（≤6）。

## D2 · Grammar solver 接受 derived 语义（GRAMMAR_VERSION 1.0.0 → 1.1.0）

`FieldEvidence` 增 `derived_measurement: Optional[str]`（grammar 词表；来自 Dataset
semantic contract，**不是 user pin**）。`solve_grammar` 判定序：
explicit pin（user-wins 记账）> derived_measurement（source="dataset_contract"，code
`GRAMMAR.MEAS.DATASET_CONTRACT`，**不记 user_wins**）> 值/名称推断。
裁决语义变化 → 版本 bump，golden/契约测试同步。旧构造（无新字段）行为逐字节不变。

## D3 · 九个调用点迁移（M1）—— 统一模式

每个调用点：`try: sem = derive_semantic_inputs(...) except: sem = None`（fail-soft），
再把 `sem.data_kind`（None → 不传/sequential 缺省）与 `sem.measurement_kind or None`
喂 `symbology_decision_from_values`。逐点边界：
- scale_matrix（eval 矩阵）：field 名 = `combo.map_type`，合成值过推导；语义工件进
  artifacts（零漂移：合成场全正、无强词素）。
- h3_binning：`stat_field_name` ∈ count/sum/mean → 显式 `MeasurementKind`
  （count/absolute_quantity/index）——仓库最强语义证据面，零色带漂移入档。
- spatial heatmap：weight_field 推导 + nominal → decision.reasons 披露
  `GRAMMAR.REP.HEATMAP_NOMINAL`（热力表达量级疏密，类别场不适用）。
- create_extrusion_layer / symbology_v2.extrusion_dual_channel / thematic_spec /
  cartography_service 数值分支 / composite_builder / cartographer 数值分支：同模式。
- create_thematic_map / apply_template：**改走统一 adapter**（消灭双引擎并发）；
  显式 method/结构模式（categorical/lisa）跳过推导的既有守卫保持。
- 全部路径：显式 requested/recommended 优先级不变（user-wins），推导只影响「未显式」
  时的色带族与证据档。

## D4 · grammar_decision 生产传递（M3/M4）—— 新模块
`app/lib/cartography/grammar_propagation.py`

1. **生产**：`create_thematic_map` 在产出 classification_plan 的同一分支构建完整
   `GrammarRequest`（geometry=geometry_of(features)、feature_count、fields=[primary
   FieldEvidence(derived_measurement=…)]）→ `solve_grammar` → 工具结果附
   `grammar_decision`（model_dump，additive 键）+ `classification_plan.grammar_decision`。
2. **随层下发**：工具结果 → MapSpec layer 的兄弟键 `grammar_decision`（与 provenance /
   correction_hint 同先例，compiler 透传）；`quality_loop._presentation_copy` 深拷白名单
   增补该键（否则 review/repair 拷贝中丢失）。
3. **review 消费**：新 `collect_grammar_decisions(mapspec)` 从 layers 收集
   （versioned fail-closed 反序列化，坏工件 → not_evaluated 披露，不阻断）；
   `composite_auditor(decisions)` 提供 `.audit(layers)`——**每个 decision 只对账自己的
   源层**（避免跨层伪 findings），结果并集成 `GrammarAudit` 形。lifecycle_engine 两处
   review/repair 调用与 mapspec_store / runtime_validator 的 review 调用接入。
4. **有界性**：收集上限 = 层数（MapSpec 本身有界）；反序列化走 `GrammarDecision`
   model_validate（额外字段忽略）。

## D5 · 类别收纳执行器（M5）—— 新模块 `app/lib/cartography/category_collapse.py`

`apply_collapse(categorical_values, *, keep_classes, other_label) -> CollapseOutcome`：
- 纯函数、确定性（保持首现序 = cartography_service 现行序）；产出
  kept entries（前 keep 个）+ `__other__` 桶 + 映射表（每个原始类 → 保留键或 other）
  + `priority_field`（`<field>:collapsed`）+ 披露。
- **三处发射器统一**：
  1. `cartography_service.build_thematic_style` categorical 分支：既有 ad-hoc
     `categorical_surplus` 块改为调用执行器（**输出逐字节兼容**：kept=k-1、
     other 色 colors[(k-1)%len]、label「其他」），行为锁回归。
  2. `cartographer.compose` categorical 分支：现无收纳且颜色循环会让第 k+1 类与第 1 类
     同色（**silent misleading map**）→ 超 `MAX_CATEGORICAL_CLASSES` 即收纳 + 披露。
  3. composite_builder categorical 槽位（placeholder 路径不动）。
- **legend/data/tooltip 同口径**：执行器附 `collapsed_property` 生成器——调用方持有
  交付 FC 时（cartographer），features 增补 `<field>:collapsed` 属性（非 kept 类 →
  other 键；纯新增属性，不改原字段），legend_spec.collapse 记录映射与
  priority_field；tooltip/label 消费同一属性即同口径；不持有 FC 的路径（style-only）
  披露 collapse 元数据（paint match default 已兜住渲染同口径）。

## D6 · recipe/harness 表达资格接线（M2）

规划期（planner）通常**无字段值**——诚实边界：不虚构数据证据。接线点：
1. `planner.py` 元素规划处：若 mission/请求上下文携带已画像的
   SemanticInputs/数据态事实（feature_count/geometry/密度），以
   `GrammarRequest(fields=[…], geometry=…, feature_count=…)` 求
   `representation.candidates/rejected`；recipe `primary_cartography` 落入 rejected
   （如 heatmap×nominal）时**沿 recipe 既有 fallback 链**取首个合格表达 + plan 级披露
   （`GRAMMAR.REP.*`），无 fallback 则保留原选择并披露风险（不静默换型、不虚构数据）。
2. `PlannedLayer` / plan evidence 附 grammar 表达证据（candidates/rejected/reason codes）。
3. 显式用户表达选择（intent/role_spec pin）恒优先——grammar 只在「recipe 默认 × 数据
   事实冲突」时经 fallback 链改道，且全程留痕。
4. **实现偏差（独立评审定案）**：资格检查落地为 `recipes.check_eligibility` 的
   **纯 advisory** 面（`grammar_representation` check，永不 gate）——硬禁用会以
   无声明 fallback 的新 reason code 绕过「数据不足 → 降级链 → 说明卡」既有契约
   （test_recipe_downgrade_regression 回归暴露），按「现有契约优先/可回滚优先」
   收敛为证据披露；表达改道的执行期裁决仍归 recipe 契约 + 工具层保守降级。

## D7 · golden corpus（M6）—— `tests/cartography/golden_corpus/grammar_decisions/`

状态集（每例 = GrammarRequest + resolve_symbology 输入 + 断言面）：
signed_change / rate_vs_count / nominal_many(>8) / nominal_few / dense_points /
low_n / multiscale_zooms(world·province·city·street) / bivariate / uncertainty /
explicit_pin_conflict。断言不止 palette 名：determinism（双解 model_dump 逐字节相等）、
reason codes（SIGNED_DIVERGING / RATE_NORMALIZED / TOO_MANY_CATEGORIES /
DENSE_POINTS_AGGREGATE / PIN.*）、diverging_center、legend form 配对、collapse spec、
scale tier 候选、resolve_symbology 族断言（signed→diverging 族且 diverging_center=0）。
bivariate 诚实面：目录内无 bivariate MapModel → 断言 grammar 不虚构表达（rejected/披露），
双通道经 MAX_THEMATIC_FIELDS=2 + extrusion_dual_channel 既有面。

## D8 · 多尺度消费（M9）

`create_thematic_map` 的 `layer_meta.display_hints` 合入 `ScaleDecision.visibility_hints`
（geometry×tier 的渲染建议），保持「advisory、非阻断」契约；对账测试锁定
scale_rules↔label_plan 分带相等（既有断言）+ scene_lod 差异如实披露（两套带表并存，
不合并、不重抄系数）。

## Out of Scope（本方向不做）

- 不写第二套 symbology/classification/label/layout 引擎；不改 scene_lod/label_plan 系数。
- 不把所有地图强制同一种表现形式；recipe 的表达偏好仍是第一输入。
- 不动前端；不改 MapSpecLayer typed schema（兄弟键先例）。
- 双变量(bivariate)原生表达模型（目录扩展）留给后续方向。
- `cartographer.py` 的 recipe.primary_cartography "prior only" 语义重构（只读消费面保持）。
