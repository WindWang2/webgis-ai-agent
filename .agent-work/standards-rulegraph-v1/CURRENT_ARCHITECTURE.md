# CURRENT_ARCHITECTURE — 制图链路与规则分布（Phase 0 深读）

## 主链路（master @ faa453a8）

```
profile_geojson_source ──► MapSpec(sources.profile 内嵌) ──► evaluate_cartography_semantics
  (app/lib/gis/profiler*)        (mapspec_schema.py, extra=allow)   (semantic_checks.py, 确定性)
        │                                                        │
        │                                            CartographyReport(checks/findings)
        │                                                        │
        └──► cartographic_projection/fingerprint ◄── review_and_repair_cartography (quality_loop.py)
             (credential-safe, O(1) w.r.t. features)     AUTO_SAFE 白名单修复(≤2 轮,
                                                         suppression/lock guard/fingerprint 防震荡)
```

- **语义检查契约**: `evaluate_cartography_semantics` 产出 `CartographyCheck(rule,status,severity,evidence,repairability,suggested_fix)`；无证据 = `not_evaluated`，绝不伪造 pass（契约 JSON：`docs/dev/ac-v11-contracts/semantic-checks.v1.json`，#1356 正在 bump v2 — 竞争文件）。
- **修复词表（AUTO_SAFE，quality_loop._apply_repairs）**: `normalize_opacity / refresh_style_from_legend / set_layer_visibility / change_palette / set_map_legend_visibility / resolve_floating_layout`。
- **runtime self-heal**: `selfheal_actions.py`（actions_by_id/triggers_from_review/select_actions，operations: rotate_palette/clamp_layout/adjust_classification/clip_value_domain/adjust_labels）+ `app/services/mapspec/visual_healer.py`（ADR-0186 VLM 微变异）+ `app/services/cartography/selfheal_policy.py`。
- **规则 profile（既有点）**: `_review_profile` 词表 = `general_analysis/thematic_map/statistical_map/raster_result/network_result`（layer.cartographic_profile 或 mapspec.cartographic_profile）。

## 规则普查（census — 隐式制图义务的散落点）

1. **required_components_for(purpose, content)**（component_composer.py）：硬编码必配表——全用途 title/scale_bar/north_arrow/attribution；print 追加 legend/graticule/inset_map；attribution 占位「数据来源：—（待补充）」。purpose 词表 = OUTPUT_PURPOSES（screen_16_9/screen_4_3/a4_*/a3_*）。→ 本方向声明化包装的第一优先对象。
2. **context_matrix.py**：6 上下文（print/projector/screen/cvd_*）× 18 色带判定，与 resolve_symbology 同源常量；`validate_new_palette` = 注册门。→ CVD/print 义务的**测量源**（不复制）。
3. **semantic_checks.py 内联规则**（SOURCE_LAYER_REF/OPACITY_VALIDITY/CRS_EVIDENCE/CLASSIFICATION_*/LEGEND_*/STOPS_DATA_RANGE/…约 16+ 词表）：结构性检查；**缺失**：count-vs-rate、legend 单位、source/time/uncertainty disclosure、label density 义务、audience 级 CVD 硬性化。
4. **layout/labels**：layout_solver、label_engine/label_plan（maxLabels/topN/zoomBands 词汇已存在）；composition_validation（15 码）；render_diagnostics（18 码）；harness finalizer codes（~30）——`.agent-work/contextual-cartographic-harness-v6/04-unified-findings.md` 已记录「词表碎片化」痛点。
5. **设计系统/模型库**：model_library（80 模型，purpose_zh 为描述性文本非 profile 轴）、design_system_v4、theme palettes。
6. **publication/export**：pdf_renderer/svg_marginalia/atlas_layout —— 出版侧义务（署名/比例尺/指北针）目前只由 required_components_for 隐式承载。

## 本方向的新增面（无第二系统）

`app/lib/cartography/standards/**`（新子包，registry=truth / catalog=projection doctrine）：
CartographicRule（声明）+ RuleGraph（依赖/冲突/拓扑）+ StandardsPack（版本化冻结集）+ profile 解析（purpose×audience×medium → obligations）+ 确定性 evaluator（**委托**既有引擎函数为测量源）+ QA 报告（violations 携 rule ids + evidence refs + fix_hint 路由回 AUTO_SAFE/autofill 词表）+ 生成式 catalog（drift gate）。
