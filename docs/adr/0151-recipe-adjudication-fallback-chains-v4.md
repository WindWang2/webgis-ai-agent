# ADR-0151: Recipe 自动裁决与泛化降级 V4 — 多维资格检查 + 声明式降级链

- 状态: Proposed
- 日期: 2026-09-13
- 分支: `adaptive-cartography/02-recipe-adjudication`
- 关联: ADR-0119（Contextual Cartographic Harness V6）、ADR-0121（语义自治）、
  ADR-0129（GIS 方法论与模板智能，统一资格报告哲学）、ADR-0101（Workflow Recipe DSL）
- 编号说明: docs/adr 现有 watermark 为 0147；0148-0150 为并行线
  （adaptive-cartography/00、01 等）预留，本线按任务书预分配取 0151。

## 1. 背景

`RecipeRegistry` 已有 164 条制图配方（SEED 17 + recipe_packs 147，24 个领域
模块），用 11 层稳定排序键选候选，但**裁决与降级能力严重偏窄**（P0 勘察
`.agent-work/ac-02/p0_downgrade_probe.py` + `docs/dev/ac-02-recipe-recon.md`）：

1. `check_eligibility()` 只有三维确定性检查（几何类别 / min_points /
   requires_fields）；样本量分档、字段基数、分布形态、CRS 与空间尺度、
   时间覆盖、缺失率全不在裁决依据里。
2. `planner.finalize_with_profile` 的图层级降级只对 `visual_heatmap`/
   `density_overview` 与 `aggregate_grid` 两条**硬编码分支**生效；
   其余 recipe 判定失格后只能「全禁 + 追加点图兜底层」——没有方案 B/C。
3. `RECIPE_INELIGIBLE` 的 fallback 匹配是单条 `break`，命中即停，无链式评估。
4. **实测矛盾计划**（P0 case05/06）：recipe 级失格时 `disabled_elements`
   只含 `"recipe"`，与图层 cartography 名不匹配 → 主层存活，产出
   「失格 recipe + enabled 主层」的自相矛盾计划（点数据 + 3D 挤出主层）。
5. 降级对用户不可见：无结构化 reason_code 到前端的呈现通道。

## 2. 决策

### D1 — 多维资格裁决（additive，旧三维保留 fast-fail）

`EligibilityContext{geometry, n, fields{unique_ratio, kind, missing_ratio},
distribution{skew, kurtosis, zero_ratio, unique_value_ratio}, spatial{crs,
crs_class, extent_span_km, point_density_per_km2}, temporal{field, span_days,
coverage_ratio}}` 由 04 线（数据剖析）逐步供给；本线定义接口并提供
`from_profile` 兜底派生。六个新检查器（全部返回
`CheckResult{ok, reason_code, evidence}`，禁止裸 bool）：

| 检查器 | 拒绝码 | 触发 |
|---|---|---|
| 样本量分档 | `SAMPLE_BELOW_FLOOR` / `SAMPLE_INSUFFICIENT` | <8 恒拒；声明 min_samples 未达 |
| 字段基数 | `FIELD_NOT_CATEGORICAL` / `FIELD_NOT_CONTINUOUS` | 唯一值比与期望形态矛盾 |
| 缺失率 | `FIELD_MISSING_RATIO_HIGH` | 超 max_missing_ratio |
| 分布形态 | `DISTRIBUTION_UNFIT` | 零膨胀/偏态/重尾/近均匀不在白名单 |
| CRS 与尺度 | `PROJECTED_CRS_REQUIRED` / `SPARSE_FOR_AGGREGATION` | 地理 CRS 聚合；点密度不足 |
| 时间覆盖 | `TEMPORAL_FIELD_ABSENT` / `TEMPORAL_COVERAGE_INSUFFICIENT` | 时间字段缺席/覆盖不足 |

原则：**未知 ≠ 不满足**——事实缺席一律 unknown 放行（与义务评估同红线），
旧 profile 形态下既有行为逐位保留。规则声明驱动（`EligibilityRule` 新增
optional 字段），不声明不检查；与旧维度冲突时 AND 语义取严格者（§0.5）。
样本量 `<8` 硬下限**只在声明 min_samples 时生效**——通用产品「点少」仍走
元素级降级（golden Case B 契约不破坏）。

### D2 — 声明式降级链（recipe 级方案 B/C）

新 `CartographyRecipe.fallback_links: List[FallbackLink{to, when,
reason_code, evidence_hint, auto_generated}]`，与元素级 `RecipeFallback`
分工：后者描述「同计划内换表达元素」，前者描述「整个 recipe 不可行 →
换 recipe」。`resolve_fallback_chain()`：

- 起点 eligible → 原地 resolved；
- 否则按声明序评估 links：原因码不匹配 → 落选留痕（eligible=None）；
  匹配 → 对目标做完整复检；
- 多条目标同时 eligible → 按 registry 排序键（priority, id）取最优，
  落选者显式记录（`demoted_by_sort_key`）；
- 目标全败 → 递归进入目标的链（深度优先、环守卫、depth_limit=4）；
- 链空/穷尽 → 通用兜底 `DEFAULT_FALLBACK_CHAIN`（点图 → 分级图，
  `auto_generated=True`）。

### D3 — finalize 泛化（删除两条硬编码分支）

- 图层级：按「被禁元素 × 计划图层」通用求解（`_ELEMENT_ALIASES` 对齐
  density_overview/native_heatmap → visual_heatmap），声明 use 目标或可用
  点叠加提升 primary；每次降级落带 `downgrade_class` + `disclosure` 的
  `FallbackDecision`。
- recipe 级失格 → `resolve_fallback_chain` → 最优 eligible 目标**完整
  重规划并终稿**（真方案 B，非禁层凑合；`_chain_depth` 封顶防递归）。
- 链穷尽 → 「数据不足」说明卡（`INSUFFICIENT_DATA`，复用 methodology_note
  通道，前端零改动）——非空白图、非空 MapSpec。
- 失格 recipe 的全部非 reference 图层一律禁用（修矛盾计划）。

### D4 — 降级可解释 + 前端可见通道

`FallbackDecision` 扩展 `attempts[]`（链上每步有界转录，含落选者）与
`auto_generated`。`render_fallback_for_llm()`（对齐
`render_verdict_for_llm` 风格）注入 LLM 上下文；工具输出新增
`fallback_llm` 与 `fallback_summary{count, reason_codes, recipe_swapped}`
——只改后端事件字段，前端呈现由 07 线消费。**与 01 线（adaptive-intent）
的契约对齐点**：任务书规定 FallbackDecision 由 01 线先定、本线只读消费；
01 未合入，本线按任务书 §2-P4 定义实现，对齐时以此并入（PR 注明待对齐）。

### D5 — 事实优先信号推广

`_interpolation_fact_signals` 的「结构化事实覆盖文本 hint」模式抽象为通用
`fact_signals(ctx, intent)`：产出有界证据摘要（`plan.data_fact_signals`）
与冲突披露（`FACT_GEOMETRY_MISMATCH` / `FACT_PROJECTION_REQUIRED` /
`FACT_ZERO_INFLATED_DISTRIBUTION` → methodology_warnings）。绝不改写
intent —— `intent._HINT_OVERRIDABLE` / `_HINT_PROTECTED_TASKS` 只读消费
（intent.py 归 01 线，本线零改动）。

### D6 — 第二事实源清理

- `build_default_components` 的「模型库未收录旧词汇」兼容分支删除；
  唯一未收录词 `"graduated"` 收编为 `administrative_choropleth` 模型别名
  （model_library 仅登记）。未收录词汇 → 诚实缺省（无图例组件，不猜类型）。
- planner 内联字面量（`admin_bar`/`category_bar` 统计图表规则）外迁：
  `CartographyRecipe.default_statistics/default_charts` 声明优先，未声明按
  task 确定性派生规则（`default_charts_for_task`，单一事实源在 recipes 模块）。

### D7 — 知识库覆盖义务（P7）

164/164 recipe 具备 fallback 声明：100 条元素级 `RecipeFallback`（存量）+
64 条 `fallback_links`（本线补齐，其中 60 条 `auto_generated=true` 通用链，
4 条 seed 领域链）。`registry_validation` 新增悬空引用校验：`fallback_links.to`
或通用链目标指向未注册 recipe → 启动期 fail-loud（与 recipe packs 加载语义
一致）。`scripts/recipe_eligibility_audit.py`（gitignore allowlist）供定期审计。

## 3. 后果

- 正面：不达标数据 100% 产出 eligible 方案 + 可见原因码（30 样本回归锁
  定）；方案 B 真实可换案；矛盾计划修复；降级全链路可解释（落选者留痕）。
- 取舍：换案会改变 plan_id（query+recipe_id 派生）——发生在绑定前，
  tools.py 以 finalize 返回值为权威，无漂移；黄金 Case C 与格网几何失配
  用例按新契约更新（断言换案证据链，非弱化）。
- 中性：recipe 指纹变化 → workflow-catalog.md 重新生成（既有闸流程）。
- 测试：`test_eligibility_v4.py`（39）+ `test_fact_signals_v4.py`（10）+
  `test_recipe_downgrade_regression.py`（32）新增；gis_harness 全域绿。
