# 自适应制图全自动：代码现状分析与 10 条并行优化方向

> 目标定义：**自适应制图全自动** = 用户一句自然语言 → 系统自主完成
> 「理解意图 → 取数/算数 → 剖析数据 → 选图型 → 定分类/配色 → 自动标注 →
> 自动版面 → 自检自愈 → 出版级导出」，全程零人工调参，且结果稳定可回归。
>
> 本文基于全仓只读探查（后端 `app/`、前端 `frontend/`、测试 `tests/`、决策 `docs/adr/`）写成，
> 所有结论附文件路径与行号证据。

---

## 0. 一句话结论

**"算得出来"已经解决，"画得专业"仍是半自动。**
后端 30+ 空间算子、164 个制图配方、2800 行确定性语义检查、204 条闭环语料都已就位；
但断点集中在 5 处，且这 5 处彼此**文件正交**，可以完全并行推进：

| 断点 | 证据 | 后果 |
|---|---|---|
| ① 意图理解是规则堆砌，不是推断 | `app/services/gis_harness/intent.py`：22 条正则 + 25 个 `if task ==` 分支 + 42 城词表 + 置信度常量加权 `0.5+0.2+0.15+0.15` | 换种说法/换个城市就降级成默认图 |
| ② 自适应分类只有 1 处接线 | `app/lib/cartography/visualization_plan.py:91 choose_classification` 已实现分布驱动裁决，但仅 `app/tools/cartography.py:202` 一处调用；`CartographyService.build_thematic_style` 仍是 `method="quantiles", palette="YlOrRd"` 硬编码（`cartography_service.py:40/42`）；`h3_binning`（`tools/advanced_spatial.py:2569`）硬编码 quantiles/k=5/YlOrRd | 同一份数据走不同工具出不同质量的图 |
| ③ 标注要人工给字段 | `component_taxonomy.py:70` 有 `label_layer` 定义但 `component_registry.py` 未注册；运行时 `mapspec-runtime/runtime.ts` 的 `addLabelSublayerSafe` 需 spec 显式给 `label{field}`，后端无 name-like 字段自动识别 | 出的图没有地名/类名，不像"专业图" |
| ④ 闭环只覆盖一条路径 | `app/services/chat/tool_pipeline.py:284` 仅当结果含 `mapspec_fingerprint` 才进 `evaluate_cartographic_session`；`create_thematic_map`、`apply_template` 的 symbology/heatmap 分支直接发前端 command，**永不评审** | 主成图路径没有质检 |
| ⑤ L5 视觉裁判是空转 | `app/lib/harness/pi_agent_harness.py:1747 _success_levels()` L5 恒 `not_evaluated`；`visual_evaluator.py`（153 行）无生产调用方；自愈只换色带（`_apply_palette_change`），换不了分类/图型/级数 | "图好不好看"零自动衡量，"不好看就改"无法自动发生 |

---

## 1. 现状全景与成熟度评分

### 1.1 成图主链路（一次请求实际走过的代码）

```
POST /api/v1/chat/stream            app/api/routes/chat.py
  └─ ChatExecutionEngine.chat_stream app/services/chat/execution_engine.py:1778
      ├─ context_assembler.assemble  app/services/chat/context_assembler.py:281
      │    └─ 注入上一轮 [CARTOGRAPHY_VERDICT]（:171）+ 地图态摘要 + 历史截断
      ├─ _maybe_plan (:1068) → plan_orchestrator.orchestrate_plan (:833)
      │    └─ 两级确定性短路：_minimal_chat_gate / _synth_plan_from_harness
      │       （intent 非 fallback 且 conf ≥ 0.65 → 0 次 LLM 直接合成 MapProductPlan）
      ├─ _select_tools (:457) → ToolCatalog.select_schemas（11 域关键词 + sticky，tier 1/2/3，24KB 预算）
      ├─ _call_llm_stream (:1200)
      └─ tool_pipeline.execute_tool_call  app/services/chat/tool_pipeline.py:79
           └─ ToolDispatchService.dispatch → MapSpec 突变 → lifecycle_engine.apply_mutation
              └─（仅当带 mapspec_fingerprint）cartography_runtime.evaluate_cartographic_session
                 └─ HarnessEvaluator → verdict → 下一轮注入
```

### 1.2 关键资产（已经很强，是这 10 条方向的底座）

| 资产 | 位置 | 规模 |
|---|---|---|
| 制图配方库 | `gis_harness/recipes.py` SEED 17 + `recipe_packs/*` 147 | **164 条** |
| 图面组件类型 | `gis_harness/components.py` | 21 种（含 graticule/inset/methodology_note/uncertainty_panel） |
| 确定性语义检查 | `app/lib/cartography/semantic_checks.py` | 2803 行 / ~30 类检查 |
| 分类算法 | `app/lib/cartography/classify.py` | quantiles / equal_interval / Jenks / std_dev / head_tail |
| 色板库 + 色差工具 | `palettes.py` 16 条 + 4 条热力；CIEDE2000、WCAG 对比度 | 有 |
| 闭环语料 | `app/evaluation/closed_loop_corpus.py` | 17 图型 × 12 故障 = **204 条** |
| 头照渲染场景 | `tests/fixtures/runtime/` | 9 个（含 2 个负例）+ 3 类探针 |
| 量化质量规则 | `specs/cartographic-quality-rules-and-memory-spec.md` | 6 条（load/color.sep/legend/visualvar/label/svs） |

### 1.3 自适应成熟度评分（本探查的评估值，非仓库既有指标）

| 环节 | 评分 | 判据 |
|---|---:|---|
| 意图理解 | 45 | 规则硬编码、英文为补丁、无主动澄清 |
| 配方裁决与降级 | 65 | eligibility 仅 3 类（几何/min_points/requires_fields），降级只对 2 种 cartography 生效 |
| 符号化（分类/色带/级数） | 40 | 好算法存在但只有 1 处接线 |
| 数据自适应预处理 | 30 | 14 个诊断码仅 10 个有修复 op，默认只跑 2 个 |
| 自动标注 | 20 | 字段人工给、无自研避让与抽稀 |
| 多尺度表达 | 35 | 仅 heatmap intensity 随 zoom 插值，半径/点径/线宽恒定 |
| 版面整饰 | 60 | 22 组件 + layout solver v4，但缺数字比例尺/图廓注记/真北偏角，碰撞仅告警 |
| 导出出版 | 50 | DPI 为 drawImage 放大、PDF 实为栅格、CJK 降级为图 |
| 质检自愈闭环 | 35 | 非 mapspec 路径不评审、自愈动作单一 |
| 回归基座 | 55 | 有语料与基线，但无趋势持久化、无 ratchet、覆盖闸排除制图 |

---

## 2. 十大并行优化方向

> 每条给出：**目标 / 现状痛点（带证据）/ 做什么 / 涉及文件 / 验收指标 / 依赖**。
> 文件面已刻意错开，10 条可同时开工（并行性论证见 §3）。

---

### T1 · 意图理解自适应化：从"正则堆砌"到"语义推断 + 主动澄清"

**痛点**：`intent.py`（897 行）里 `_TASK_RULES` 22 条正则按特异性先命中先停、`_task_specific_intents()` 25 个 `if task ==` 硬编码派生、`_KNOWN_CITIES` 42 城白名单、`_POINT/RASTER/POLYGON/LINE_SUBJECTS` 词表；置信度是 `0.5+0.2+0.15+0.15` 常量加权。英文支持靠手写 `(?<![a-zA-Z])` lookaround 补丁。结果是「换个说法就掉档」。

**做什么**
1. 引入 **语义槽位抽取器**（LLM 结构化输出 + 规则校验双轨），规则降级为"低成本快路径 + 兜底"，不再是唯一真相源。
2. 用 **本体表**替代 42 城词表：`gis_ontology.py` 已有 51 条 `_t()` 描述符，接地理编码/行政区服务做实体解析，城市名不再枚举。
3. **不确定度驱动的澄清**：confidence < 阈值时走"反问一轮"而不是静默 fallback（当前 fallback 直接出默认图，用户只看到一张不对的图）。
4. 意图结果统一输出 `MapRequestIntent` + **evidence 列表**（哪条规则/哪个 LLM 依据命中），供 T10 回归。
5. 多语言：把中英文合并为同一套槽位定义，删除 lookaround 补丁。

**涉及文件**：`app/services/gis_harness/intent.py`、`gis_ontology.py`、`app/services/chat/plan_orchestrator.py`（`_synth_plan_from_harness` 的 0.65 阈值）、新增 `app/services/gis_harness/intent_semantic.py`
**验收**：204 条闭环语料的意图命中率 ≥ 现有基线 +8pt；英文 query 命中率不低于中文的 90%；低置信场景 100% 触发澄清而非静默降级；`intent.py` 行数下降 ≥ 30%。
**依赖**：无（与 T2 共享 `MapRequestIntent` 契约，改结构需先对齐字段）。

---

### T2 · 制图配方自动裁决：把"窄降级"扩成"通用 fallback 图"

**痛点**：`recipes.py` `check_eligibility()` 只支持 3 类确定性检查（几何类别、`min_points`、`requires_fields`）；`planner.py` `finalize_with_profile` 的图层级降级**只对 `visual_heatmap` / `aggregate_grid` 两条硬编码分支生效**，其余 recipe 判定 `RECIPE_INELIGIBLE` 时只能全禁 + 追加一个点图兜底——"方案 B"实际上没有。

**做什么**
1. **扩展 eligibility 维度**：样本量分档、字段基数（唯一值数）、分布形态（偏度/零膨胀）、CRS 与空间尺度（研究区跨度 vs 点密度）、时间覆盖、字段缺失率。
2. **声明式 fallback 链**：recipe 自带 `fallbacks: [{to, reason_code, evidence}]`，planner 按证据**链式评估**直到找到 eligible 方案，替代现有 `break` 单条匹配。
3. **降级可解释**：每次降级产出 `FallbackDecision{from, to, reason_code, evidence}`，纳入 `MapProductPlan`，前端可展示"为什么不出热力图而出了分级图"。
4. **证据驱动而非文本驱动**：已有 `_interpolation_fact_signals`（事实覆盖文本 hint）是好设计，把它从插值场景推广到全部裁决。
5. 删除 `build_default_components` 里"模型库未收录旧词汇"的兼容分支（`components.py`），消除第二事实源。

**涉及文件**：`app/services/gis_harness/recipes.py`、`recipe_packs/*.py`（147 条补 fallback 声明）、`planner.py`、`capability_graph.py`、`components.py`
**验收**：`recipe_packs` 全量 fallback 声明覆盖率 100%；构造 30 个"数据不达标"样本（点太少/字段缺失/零方差），100% 产出 eligible 方案且带 reason_code；无 `RECIPE_INELIGIBLE` 直接崩到点图兜底。
**依赖**：与 T1 共享 intent 置信度语义；与 T4（数据剖析）共享 profile 字段。

---

### T3 · 自适应符号化引擎：让"分类/色带/级数"在一处裁决、全链路复用

**痛点**：`choose_classification()`（`visualization_plan.py:91`）已经实现了漂亮的分布驱动裁决（重尾→head_tail、近均匀→equal_interval/quantiles、其他→Jenks，含 `rejected` 落选理由），但**全仓只有 `tools/cartography.py:202` 一处接线**；其余四处仍是硬编码——`cartography_service.py:40/42`（quantiles/YlOrRd）、`h3_binning`（quantiles/k=5/YlOrRd）、`heatmap_data`、62 个 SEED 模板 payload。同一个数据、不同入口，出图质量参差。

**做什么**
1. **统一裁决入口** `resolve_symbology(profile, intent, constraints) -> SymbologyDecision`，内部串起：分类法裁决（复用 `choose_classification`）→ 级数 k 裁决（按 n、值域、屏幕密度、色带可分辨类数）→ 色带裁决（数据类型 sequential/diverging/qualitative + 底图亮度 + 色盲安全 + 打印安全 + 离群值裁剪策略）。
2. **补齐色带自适应**：`palettes.py` 已有 CIEDE2000 `min_adjacent_delta_e`、WCAG 工具，但**无色盲安全开关、无打印模式开关**（仅 PuOr 注释标注 + 一个 `tmpl_bm_print_light` 模板）。加 `context: screen|projector|print|cvd_deuteranopia|cvd_protanopia` 约束，自动换带并落 `rejected` 理由。
3. **全链路接线**：`CartographyService`、`h3_binning`、`heatmap_data`、`create_thematic_map`、`apply_template`、模板 payload 全部改走统一入口（模板 payload 从"写死 method/k/palette"退化为"只声明偏好"，作为裁决的 `recommended` 输入）。
4. **离群值策略**：`>3σ` 值会把色带拉爆（数据侧已知问题），在裁决层加 clip/head_tail 自动选择，并写入 legend 的 nodata/out-of-range 说明。
5. 输出一等工件 `SymbologyDecision`，进 `legend_spec` v2（含 method/k/palette/context/clip/why），前端 T7 图例直接消费。

**涉及文件**：`app/lib/cartography/visualization_plan.py`、`classify.py`、`palettes.py`、`themes.py`、`thematic_spec.py`；`app/services/cartography_service.py`；`app/tools/cartography.py`、`advanced_spatial.py`、`spatial.py`、`templates.py`
**验收**：新增"同数据多入口一致性"测试——5 个入口对同一数据产出同一 `SymbologyDecision`；色盲/打印模式下 CIEDE2000 与 WCAG 全部达标；`grep quantiles|YlOrRd` 在业务代码中的硬编码点归零。
**依赖**：T4 提供离群值/字段基数统计；与 T9 共享"可替换动作"清单（自愈要能改 method/k/palette）。

---

### T4 · 数据自适应预处理：把"脏数据"挡在制图之前，且自动修

**痛点**：`spatial_quality_service.py` 审计 5 维、14 个诊断码，但 `repair_linkage_for_code` 只映射 10 个码，**拓扑重叠、缝隙、离群值、高空值率只报不修**；`spatial_repair_pipeline.py` 有 7 个 op 但**默认只跑 make_valid + remove_empty**。这些脏数据会直接毁图：自交→面填充错/洞丢失、重复几何→压盖、混合几何→单图层只画一类、缺 CRS→Null Island、离群值→色带拉爆。

**做什么**
1. **制图前置门禁**：MapSpec `UpsertLayer` 前强制过一遍 profile + quality，blocking 级问题返回可自愈的修复建议（而非静默上图）。
2. **默认 op 扩展**：按诊断码自动编排 op 序列（remove_empty → make_valid → normalize_geometry_type → deduplicate → crs_transform → snap），证据 ≤16 条不变，破坏性 op 默认关闭、由裁决开启。
3. **CRS 自动识别**：缺 CRS 时按 bbox 量级 + 坐标范围推断（当前仅 info 不阻断，`crs_transform` 需人传 `source_crs`，默认 4326 易整体偏移）。
4. **离群值与值域**：输出 `outlier_policy` 建议给 T3（clip 分位 / head_tail / 对数变换），不再让单值毁掉整条色带。
5. **修复可回放**：每次修复写入 lineage（与 `SpatialRepairPipeline` 现有 provenance 对齐），支持回滚与"为何修"解释。

**涉及文件**：`app/services/spatial_quality_service.py`、`spatial_repair_pipeline.py`、`spatial_analyzer.py`、`app/services/mapspec/lifecycle_engine.py`（前置钩子）
**验收**：构造 14 个诊断码样本，blocking 类 100% 被拦截或自动修复；修复前后要素数/面积变化写入证据；`crs_transform` 人工传参场景降为 0。
**依赖**：为 T3 提供统计输入；与 T2 共享 profile 结构。

---

### T5 · 自动标注引擎：从"人工给字段"到"自动挑字段 + 自动避让"

**痛点**：`component_taxonomy.py:70` 定义了 `label_layer`，但 `component_registry.py` **未注册**该组件；运行时 `mapspec-runtime/runtime.ts` 的 `addLabelSublayerSafe` 只在 spec 显式给 `layer.label{field}` 或 `layout.labelField` 时生成 symbol 子层，且仅 `text-allow-overlap:false`（MapLibre 内置避让），**无自研避让/抽稀/优先级**；后端 `chat/context/formatters.py:77` 的 `label_keys=("name","title","label","id","OBJECTID")` 只服务于上下文拼接，不进制图。密集层只能靠 `carto.label.collision_est` 告警后人工缩字号。

**做什么**
1. **字段自动挑选**（后端）：name-like 词表 + 字段基数比（唯一值/总数接近 1 才是好标注字段）+ 语义类型（排除纯数值 ID/编码）+ 长度分布，产出 `label_field` 建议与置信度。
2. **标注策略自动编排**：按要素密度与 zoom 分档决定——标注全部 / 按重要性字段排序取 TopN / 仅 hover+popup，避免"点挤成一团全是字"。
3. **避让与抽稀**（前端）：优先级队列 + 贪心/网格碰撞剔除 + 缩放分级（zoom < X 只标大类，zoom 深入后逐级展开）+ 字号/晕圈随 zoom 插值。
4. **标注样式自适应**：浅色底自动加 halo、CJK/拉丁分别定字号；与 T7 图例、T6 符号律共用密度信号。
5. 把 `label_layer` 正式注册进 `component_registry`，让标注成为可寻址组件（支持「换个标注字段」的局部突变）。

**涉及文件**：新增 `app/lib/cartography/label_plan.py`；`app/services/gis_harness/components.py`/`component_registry.py`；`frontend/lib/mapspec-runtime/runtime.ts`、`frontend/lib/map-kit/renderer.ts`、`frontend/lib/mapspec-compiler/compiler.ts`
**验收**：10 个真实数据集上 `label_field` 自动挑选准确率 ≥ 90%；密集层（>2000 点）标注重叠率较现状下降 ≥ 50%；`carto.label.collision_est` 告警率下降可量化。
**依赖**：消费 T4 的字段剖析；与 T6 共享 zoom/密度信号。

---

### T6 · 自适应符号与运行时表达力：zoom/密度驱动的符号律 + 属性级增量

**痛点（表达）**：`renderer.ts` 里 `fill-opacity` 硬编码 0.8、`circle-radius` 硬编码 6；`resolveHeatmapRadiusPx` 的 `radius_px` 是**常量 px，不随 zoom 插值**（只有 intensity 随 zoom 插值 4→0.8, 10→1.3, 14→2.2）；无按要素数量/几何类型自动选符号或尺寸的能力 → 「1 万个点挤成一团」「放大后点还是那么大」。
**痛点（性能与表达覆盖）**：paint/layout/label/type 任一变化即 `recompile`（remove+add 层），改个颜色整层闪烁；编译器 `compileMapSpec` 的 paint 映射是白名单 if/else——**symbol 层无 paint 分支（静默空 paint）**、`interpolate` 只支持 `["linear"]`、无 dash/blur/translate、未知 source 类型静默降级为空 FeatureCollection。

**做什么**
1. **符号律引擎**：把点径/线宽/热力半径/描边/不透明度写成 `zoom × 要素数 × 几何类型` 的函数（插值表达式而非常量），出厂一套可覆盖的默认值表。
2. **密度自适应**：要素数超阈值自动切换（点→聚合/H3/热力；线→简化+宽度递减），阈值与 T5 标注策略共用同一密度信号。
3. **属性级增量更新**：diff 从 `recompile` 下沉到 `setPaintProperty` / `setLayoutProperty` / `setFilter` 增量 patch，消除闪烁；`syncLayerZOrder` 的逐层 `moveLayer` 改为批量/差序。
4. **表达力补齐**（纯增量，不动现有契约）：symbol 层 paint 分支、interpolate 支持 exponential/cubic-bezier、dash-array/blur/translate、未知 source 类型显式报错而非静默空数据。
5. diff 性能：深层比较递归整个 MapSpec（含 inline GeoJSON）在大 payload 下是热点，改为按 `content_hash` / revision 短路。

**涉及文件**：`frontend/lib/map-kit/renderer.ts`、`render-scene.ts`、`runtime-layer-registry.ts`；`frontend/lib/mapspec-compiler/compiler.ts`、`reconciler.ts`、`types.generated.ts`；`frontend/lib/mapspec-runtime/runtime.ts`、`paint-bridge.ts`
**验收**：符号/热力半径随 zoom 平滑变化（探针可测）；paint 变更不再触发 remove/add（运行时事件计数断言）；10k 要素图交互帧率 ≥ 现有基线；新增 compiler 单测覆盖全部 layer type × StyleMethod 组合。
**依赖**：与 T3 共享 `legend_spec` v2 的 paint 投影契约（前后端需同版本）。

---

### T7 · 图面整饰自动排版：版式自适应 + 碰撞自愈 + 缺项补全

**痛点**：22 个组件已注册（`map-components/registry.ts`），`map-spec-chrome.tsx:50-55` 缺 north_arrow/scale_bar 时会注入 `__fallback_*`；但——缺**数字比例尺（1:xx）**、**图廓坐标注记**、**真北/磁北偏角**、**经纬网密度可调**、图例单位与 nodata 统一（依赖后端 legend_spec）；`graticule`/`inset_map` 已有但 `inset_map` 仍 `runtime_status=planned`（渲染器未实现）；`LAYOUT_COLLISION`/`COMPONENT_OUTSIDE_CANVAS`/`COMPONENT_LINK_CYCLE` 检查目前**只告警不自愈**。

**做什么**
1. **版面约束求解升级**：把现有 layout solver v3/v4 从"选位"升级为"选位 + 冲突自愈"（自动改 anchor/缩尺寸/降级为折叠/移入溢出面板），并让 `LAYOUT_COLLISION` 从 warning 变为可自动修复。
2. **缺项自动补全**：依据输出用途（屏幕/A4/A3、横竖、DPI）与地图内容（有无投影信息/数据来源/统计面板），自动决定必配整饰清单，不再依赖 recipe 写死。
3. **补齐组件**：数字比例尺（随 zoom 与纬度动态计算）、图廓坐标注记、真北偏角（按 bbox 中心计算磁偏）、经纬网密度自适应（按跨度选间隔）、图例单位/nodata/out-of-range 统一渲染。
4. **实现 inset_map**（示意图/位置图），结束 planned 状态。
5. 版面决策落 `CompositionDecision` 工件，供 T9 评审与 T10 回归。

**涉及文件**：`frontend/components/map/map-components/*`、`map-spec-chrome.tsx`、`frontend/lib/cartography`（版式求解，若有）、`frontend/lib/map-kit/export-chrome.ts`；后端 `app/lib/cartography/component_registry.py`、`component_composer.py`、`semantic_checks.py`（碰撞规则）
**验收**：A4 横/竖、屏幕 4:3/16:9 四组版式下 `LAYOUT_COLLISION` 归零；任意 20 个 MapSpec 样本自动产出完整整饰且无越界；inset_map 可渲染。
**依赖**：消费 T3 的 legend_spec v2（单位/nodata）。

---

### T8 · 出版级导出：真高分重渲染 + 矢量 PDF + 同源渲染

**痛点**：`exporter.ts:1505 runExport` → `prepareExportCanvas`（A4/A3 按 1.414 裁切 + `dpi/96` 缩放）→ `composeLayout`（Canvas2D 画整饰）→ `toBlob` PNG 或 jsPDF。**DPI 是 drawImage 放大插值，不重渲染瓦片**——300 DPI 没有细节增益，只是更大的模糊图；PDF 实为栅格（jsPDF 嵌入 `toDataURL('image/png')`），CJK 文本降级为 `pdf_text_rasterized_cjk`；A4/A3 只裁画布，不保证出图范围 = 遮罩所见；无 CMYK/出血；SVG 路径（`mapspec-to-svg.ts`/`vector-svg-export.ts`）与 canvas 路径**非同源渲染**，存在内容漂移；后端 `pdf_renderer.py`（139 行）更弱，位图塞 A4 图框，指北针/比例尺/图例不入 PDF。

**做什么**
1. **真高分渲染**：导出时按 DPI 重建地图实例（临时容器 + `devicePixelRatio`/画布放大 + 等瓦片重载），而不是对现有画布做插值放大。
2. **矢量出版**：把 SVG 路径扶正为首选（真实矢量文字、可编辑、无限缩放），canvas 路径降级为位图兜底；两条路径统一到一个"版面描述"中间层，消除内容漂移。
3. **PDF 字形**：CJK 字体子集嵌入，取消 `pdf_text_rasterized_cjk` 降级；支持 CMYK/出血/裁切标记（可选档）。
4. **所见即所得**：遮罩范围 → 导出范围严格一致（当前只裁画布），导出前给出"出图范围预览 + 超界提示"。
5. 后端 `pdf_renderer` 与前端对齐：至少让服务端报告附图（`report_service._compile_vector_svg_for_report`）与前端导出同源。

**涉及文件**：`frontend/lib/map-kit/exporter.ts`、`frame-composer.ts`、`export-chrome.ts`；`frontend/lib/mapspec-compiler/mapspec-to-svg.ts`、`svg-marginalia.ts`；`frontend/lib/map-kit/vector-svg-export.ts`；`app/lib/cartography/pdf_renderer.py`、`app/services/report_service.py`
**验收**：300 DPI 导出可分辨最小线宽（像素探针断言，非肉眼）；PDF 文本可被选取与检索（含中文）；导出范围 = 遮罩范围（误差 ≤1px）；SVG 与 PNG 渲染差异探针通过。
**依赖**：依赖 T7 的整饰描述与 T6 的符号律（高分下符号尺寸需重算）。

---

### T9 · 视觉裁判与自愈闭环：把 L5 接通，让"不好看"能自动变好

**痛点（最关键的"全自动"断点）**：
- 评审只覆盖一条路径：`tool_pipeline.py:284` 仅 `mapspec_fingerprint` 存在时才调 `evaluate_cartographic_session`；`create_thematic_map`、`apply_template` 的 symbology/heatmap 分支直接发前端 command，**永不进评审**。
- L5 空转：`pi_agent_harness.py:1747 _success_levels()` 的 L5 `goal_satisfaction` 恒 `not_evaluated`（注释明示无 visual oracle）；`visual_evaluator.py`（153 行）是**未接线的桩**，无生产调用方。
- 自愈动作单一：`_advance_runtime_cartographic_repair`（`cartography_runtime.py:657`）的 AUTO_SAFE 只做换色带（`_apply_palette_change`），**换不了分类方法/图型/级数**；`MAX_RUNTIME_REPAIR_ITERATIONS` 上限存在但动作空间太小。

**做什么**
1. **接上视觉裁判**：截图（头照/活地图）→ VLM 评审 → 结构化 `VisualCritique{dimension, severity, suggestion}`，把 `visual` 类证据从恒 `not_evaluated` 变为可用（严格 fail-closed：无证据仍 `not_evaluated`，不得伪造 pass）。
2. **扩大评审覆盖面**：评审触发从"带 fingerprint"改为"产生了地图变更"（含 command 路径），或给 command 路径补 fingerprint，消除断裂口。
3. **自愈动作空间扩展**：从"换色带"扩到「换分类方法 / 换级数 k / 换图型 / 重裁值域 / 改标注策略 / 改版面」，每个动作带 `expected_effect` 与 `risk`，按 AUTO_SAFE 白名单分级执行，保留 ≤2 次硬上限。
4. **修复效果回归**：每次修复后重评，若未改善则回退并换下一动作（当前只有迭代上限，无效果判定）。
5. 与 T3 联动：自愈动作直接调用 `SymbologyDecision` 的替代方案（`rejected` 列表里的落选者天然就是候选修复动作）。

**涉及文件**：`app/lib/harness/visual_evaluator.py`（接线）、`pi_agent_harness.py`、`app/services/cartography_runtime.py`、`app/lib/cartography/quality_loop.py`、`semantic_checks.py`、`app/services/chat/tool_pipeline.py`
**验收**：`visual` 类证据在 ≥3 类图型上产出非 `not_evaluated` 结论；自愈动作类型 ≥5 种且每种有成功样本；修复-重评-回退链路端到端测试通过；评审覆盖地图变更路径 100%。
**依赖**：消费 T3 的可替换动作、T7 的版面决策；需要 T10 提供回归基线防倒退。**建议最后启动、但可先做接口契约。**

---

### T10 · 制图质量回归基座：让前 9 条的改动能被守护、能证明"更好了"

**痛点**：有 204 条语料、9 个头照场景、15 项 perf 基线，但——
- **无质量趋势持久化**：`calibrate_cartography_thresholds.py` 只读 evidence 给建议、**不写文件**，无法画跨版本质量曲线，无法设 ratchet（只许变好不许变坏）闸。
- **头照场景未晋升 PR**：9 个 runtime 场景全在 nightly，需连续 10 次绿且 <30s 才入 PR lane（ADR-0065），**像素级 golden-image diff 完全没有**。
- **覆盖率闸对制图无效**：CI 后端 lane 显式 `-m "not cartography"`，75% 覆盖不含制图代码。
- "自适应"本身无验收：记忆/收敛测试只验"是否收敛"，不验"下一张是否更好"。

**做什么**
1. **质量事实库**：`_cartographic_review` 结果集中落库（时间 × 会话 × 图型 × 检查项 × 值 × 版本），支撑趋势查询与告警。
2. **Ratchet 门禁**：按图型聚合 6 条量化规则（`carto.load.ratio`、`color.separability`、`legend.completeness`、`visualvar.overload`、`label.collision_est`、`scale.svs`），新版本不得劣于上一版本基线（允许带理由的临时豁免）。
3. **Golden 图像基线**：把 9 个头照场景晋升为 PR 阻断（凑满 10 次绿 + <30s），并补像素级 golden diff（容差策略复用探针的 ±16/通道经验）。
4. **把制图纳入覆盖率闸**：cartography lane 单独设覆盖下限并计入 CI。
5. **自适应验收集**：建"同需求多轮"用例——同一需求跑 3 轮，验证第 2/3 轮质量分不降且收敛（验收 T1/T2/T3/T9 的真正目标）。

**涉及文件**：新增 `app/services/cartography_metrics_store.py`（或复用现有持久化）、`scripts/calibrate_cartography_thresholds.py`、`scripts/quality_runner.py`、`scripts/ci-local.sh`、`.github/workflows/production.yml`、`tests/quality/*`、`tests/fixtures/runtime/*`
**验收**：质量曲线可查询最近 30 次运行；ratchet 闸在注入人工劣化时 100% 拦截；至少 6 个场景晋升 PR 阻断；cartography lane 覆盖率 ≥ 设定下限并进 CI。
**依赖**：为所有方向提供度量；**建议最先启动**（先有尺子，再改东西）。

---

## 3. 并行性分析

### 3.1 文件正交矩阵（✓ = 主要改动面）

| 方向 | 主改动目录 | 是否与他人重叠 |
|---|---|---|
| T1 意图 | `gis_harness/intent.py`、`gis_ontology.py` | 仅与 T2 共享 `MapRequestIntent` 字段契约 |
| T2 配方裁决 | `gis_harness/recipes.py`、`recipe_packs/`、`planner.py`、`capability_graph.py` | 与 T1/T4 共享 profile 结构（只读为主） |
| T3 符号化 | `lib/cartography/{visualization_plan,classify,palettes,themes,thematic_spec}.py`、`services/cartography_service.py`、`tools/{cartography,advanced_spatial,spatial,templates}.py` | 输出 `legend_spec v2` 被 T5/T6/T7 消费（先定契约） |
| T4 数据预处理 | `services/spatial_quality_service.py`、`spatial_repair_pipeline.py`、`spatial_analyzer.py` | 输出被 T2/T3 消费（先定 profile 字段） |
| T5 标注 | `lib/cartography/label_plan.py`(新)、`frontend/lib/mapspec-runtime/`、`map-kit/renderer.ts`、`compiler.ts` | 与 T6 同在前端 runtime/compiler，需分文件作业 |
| T6 符号与运行时 | `frontend/lib/map-kit/*`、`mapspec-compiler/*`、`mapspec-runtime/*` | 与 T5 同目录，需按文件划分（T5 走 label 相关，T6 走 paint/reconcile） |
| T7 版面 | `frontend/components/map/map-components/*`、`map-spec-chrome.tsx`、`export-chrome.ts`、`lib/cartography/component_*` | 与 T8 共享整饰描述（先定中间层） |
| T8 导出 | `frontend/lib/map-kit/exporter.ts`、`frame-composer.ts`、`mapspec-compiler/mapspec-to-svg.ts`、`app/lib/cartography/pdf_renderer.py` | 与 T7 共享整饰描述 |
| T9 闭环 | `lib/harness/*`、`services/cartography_runtime.py`、`chat/tool_pipeline.py` | 与 T3/T7 共享"可替换动作"契约 |
| T10 基座 | `scripts/*`、CI workflow、`tests/quality/*`、`tests/fixtures/runtime/*` | 基本独占，仅消费他人产出的指标 |

**结论：10 条中 8 条可完全并行，T5/T6 需按文件切分（同一前端目录），T7/T8 需先约定整饰中间层。** 所有跨方向的耦合都是**契约**（`SymbologyDecision` / `profile` / `CompositionDecision` / `label_plan`），建议开工前用一次会议定死 4 个契约的 schema，之后互不阻塞。

### 3.2 建议批次（若人力不足可分批）

- **第 0 批（先做，2 条）**：T10（先把尺子立起来）+ T3（收益最大、依赖最少的核心自适应）
- **第 1 批（可同时，5 条）**：T1、T2、T4、T5、T6
- **第 2 批（收口，3 条）**：T7、T8、T9（依赖前面产出的契约与可替换动作）

若 10 条全开，建议先冻结 4 个契约 schema，再全量并行。

### 3.3 冲突风险点（需提前约定）

1. `legend_spec` 升级到 v2 会同时触及 T3（产出）、T6（paint 投影）、T7（图例渲染）→ **先定 v2 schema，再动手**。
2. `frontend/lib/mapspec-runtime/runtime.ts` 被 T5（label 子层）与 T6（增量 patch）同时改 → 用 feature 分支 + 同日合流，或先 T6 后 T5。
3. `MapRequestIntent` 字段变更会波及 T1（产出）与 T2（消费）→ 只允许**加字段**，禁止改语义。
4. 评审触发面扩大（T9）会让部分既有"通过"变成"被评审"，可能一次性暴露大量问题 → 与 T10 的 ratchet 基线同时上线，先 record-only 观察一周再转阻断。

---

## 4. 验收总纲（跨方向统一度量）

| 维度 | 指标 | 现状 | 目标 |
|---|---|---|---|
| 全自动率 | 一句需求 → 无需人工调参即可交付的比例 | 未量化（**先补测量**） | ≥ 80% |
| 一次成功率 | 首图即通过 L4 制图质量评审 | 未量化 | ≥ 70% |
| 自愈成功率 | L4 fail → 自动修复后 pass | 仅换色带，无统计 | ≥ 50% |
| 一致性 | 同数据多入口产出 `SymbologyDecision` 一致率 | 低（硬编码分散） | 100% |
| 出版就绪 | 300 DPI 导出可分辨最小线宽 / PDF 文本可选 | 否 | 是 |
| 回归守护 | 质量 ratchet + 头照 PR 阻断 + 制图覆盖率 | 部分 | 全绿 |

> 建议把上表做成 `docs/` 里的常驻看板，由 T10 的基座自动填充。

---

## 附：本次分析覆盖的关键文件

```
后端   app/services/gis_harness/{intent,recipes,planner,components,gis_ontology,capability_graph}.py
       app/services/gis_harness/recipe_packs/*.py（147 recipe）
       app/services/mapspec/{lifecycle_engine,store,checkpoint,composite_builder}.py
       app/lib/cartography/{mapspec_schema,semantic_checks,thematic_spec,classify,visualization_plan,
                           palettes,themes,model_library,component_registry,quality_loop,pdf_renderer}.py
       app/lib/harness/{pi_agent_harness,evaluator,evidence,visual_evaluator}.py
       app/services/{cartography_service,cartography_runtime,spatial_quality_service,
                    spatial_repair_pipeline,spatial_analyzer,raster_cartography_converter}.py
       app/services/chat/{execution_engine,context_assembler,plan_orchestrator,tool_pipeline,tool_catalog}.py
       app/tools/{cartography,cartography_tools,advanced_spatial,spatial,templates}.py
       app/evaluation/closed_loop_corpus.py（204 条）
前端   frontend/lib/mapspec-compiler/{compiler,reconciler,types.generated,mapspec-to-svg}.ts
       frontend/lib/mapspec-runtime/{runtime,reconciler,paint-bridge,thematic-paint}.ts
       frontend/lib/map-kit/{renderer,exporter,frame-composer,export-chrome,render-scene}.ts
       frontend/components/map/{map-spec-chrome.tsx,export-mask.tsx,map-components/}
       frontend/components/hud/layer-style-panel.tsx、frontend/components/sidebar/map-studio-tab.tsx
测试   tests/cartography/（51）、tests/quality/（35）、tests/benchmarks/（21）、tests/fixtures/runtime/（9 场景）
工程   scripts/{ci-local.sh,quality_runner.py,calibrate_cartography_thresholds.py}、.github/workflows/production.yml
决策   docs/adr/（约 150 篇，近 30 篇与制图/质量/自适应相关）、specs/（5 篇，无"自适应制图"专项 spec）
```
