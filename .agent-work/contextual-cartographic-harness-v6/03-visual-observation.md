# 03 — Visual Observation 设计（Waves 8–9）

## 现状（证据见 00-baseline §M）

- runtime observation 链路完整（增维不换通道，ADR-0086）；缺像素级确定性检查与 visual seam。
- `label_engine.solve` 生产零接线；前端组件实测 `rect` 已上报未消费；组件无统一生命周期状态。

## W8：Deterministic Cartographic Observation

优先级：能用确定性算法做的不交给 LLM/VLM。

1. **前端增维（不换通道）**：`render-observation.ts`/`runtime-evidence.ts` 增补：
   - 组件实测 rect 已在 `ObservedComponent.rect`——后端开始消费（overlap/offscreen/安全边距判定）
   - label 实测：渲染后 label 边界盒采样（有界，上限如 256 条），报 clipping/overlap 计数
   - legend 实测高度 vs 容器（overflow）；title 溢出；chart 与 map focus 重叠
   - 预算：observation DTO 仍 ≤256KB（chat.py:1450 白名单扩展）
2. **后端确定性检查**（落 `render_observation.py` 或新 `visual_checks.py`，findings 复用既有码优先）：
   - component overlap（实测 rect 交集）→ 复用/扩展 `layout_conflict`
   - component offscreen / outside safe margin → 新码需过 findings 棘轮（先查 `zone_collision`/`layout_conflict` 可否映射）
   - result layer invisible / fully transparent → 已有 `layer_hidden`/`layer_transparent`（V4W7）
   - legend-data mismatch → 已有 `semantic_legend_mismatch`
   - chart 三态（visible 未 render / rendered data_points=0 / linked layer stale）→ 已有 `chart_data_missing` 扩展 evidence
   - label clipping/overlap → 接线 `label_engine.solve`（mapspec_to_svg 导出侧先接）+ live 采样计数
3. **组件生命周期状态**：在 observation/scene oracle 投影统一字段：`requested/spec enabled → mounted(materialized) → rendered → visible → layout_valid → data_bound → diagnostics`。chart 先行（registry 已有 rendered/data_points/pending），table 跟进（`table-data.ts`）。

## W9：Visual Observation Seam（soft）

1. `VisualEvaluator` 协议（新模块，如 `app/services/gis_harness/visual_evaluator.py`）：
   - 输入：rendered snapshot + deterministic evidence + scene oracle 摘要
   - 输出：**仅 `VisualFinding[]`**（`finding_code/severity/confidence/evidence/affected_component_ids/affected_layer_ids/affected_region/suggested_repair_class/deterministic_or_soft`）
   - 禁止直接修改 MapSpec；默认关闭，经环境变量/opt-in 开启；无 evaluator 时系统行为不变（deterministic 仍工作）
2. 触发策略（§40）：finalization、major layout change、map-model change、visual finding 修复后、用户显式请求；pan/zoom 不触发。
3. finding code：先审计复用（`semantic_checks`/render diagnostics/quality）；确需新码走棘轮登记，词汇如 `V_LABEL_COLLISION/V_LEGEND_DOMINATES/V_WEAK_VISUAL_HIERARCHY/V_RESULT_NOT_SALIENT/V_COMPONENT_OCCLUSION/V_COLOR_CONFUSION/V_DENSE_ANNOTATION` 仅作候选，不机械全建。
4. 隐私（§42）：截图可能含用户数据——遵循现有 provider/privacy 策略、不静默上传第三方、可关闭、失败时 deterministic observation 不受影响；snapshot/trace/finding 不泄露 token/key/私有 URL（复用现有 secret redaction）。

## 测试（§37）

- structural geometry tests（rect 交集/offscreen 纯函数）
- render scene semantic tests（scene oracle golden 扩维）
- deterministic synthetic layouts（构造重叠/越界/溢出场景）
- golden finding corpus（`tests/cartography/golden_corpus/` 扩展）
- optional screenshot image-diff（不进硬门）
