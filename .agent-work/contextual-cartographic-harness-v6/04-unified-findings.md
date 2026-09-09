# 04 — Unified Findings 设计（Wave 6–7）

## 词表现状（逐字，定义处见 00-baseline §K）

### Harness Finalizer codes（`completion/contracts.py:36-95`）
`needs_execution, execution_blocked, artifact_missing, artifact_expired, empty_result, no_result_layer, layer_missing, source_missing, layer_hidden, component_missing, component_disabled, layout_conflict, orphan_binding, viewport_no_bbox, render_unverified, render_revision_stale, render_layer_missing, render_source_missing, render_component_missing, render_error, render_incomplete, render_style_not_applied, chart_data_missing, semantic_legend_missing, semantic_legend_mismatch, title_missing_report_product, crs_not_wgs84, layer_order_issue, result_outside_viewport, stale_overlay, map_model_mismatch, layer_transparent`

### Render Diagnostics 18 码（`render_diagnostics.py:43-139`）
`chart_ref_unavailable, table_ref_unavailable, chart_kind_unsupported_export, component_skipped_invalid, label_truncated, legend_entries_truncated, features_truncated, export_timeout_partial, vector_svg_fallback_raster, basemap_omitted_vector_svg, pdf_text_rasterized_cjk, comparison_second_view_not_exported, comparison_export_composed, cartogram_unsupported, small_multiple_panel_skipped, atlas_page_skipped, atlas_page_limit_truncated, terrain_3d_scale_caveat`

### 组合校验码（`composition_validation.py:138-255`）
`unknown_composition_template, forbidden_component_present, required_slot_missing, recommended_slot_missing, cardinality_exceeded, planned_component_present, unavailable_component_present, conflicting_components, dependency_missing, position_not_allowed, output_not_supported, model_not_compatible, binding_conflict, zone_collision, orphan_layer_binding`

### 语义 check 16 码（`semantic_checks.py`）
`CATEGORICAL_DOMAIN_CONSISTENCY, CLASSIFICATION_CARDINALITY, CLASSIFICATION_DOMAIN_COVERAGE, CONTOUR_LEVELS_VALID, DIVERGENT_DOMAIN, EMPTY_DATA, EXTRUSION_HEIGHT_FIELD_VALID, GEOMETRY_LAYER_TYPE, INTERPOLATE_NUMERIC_FIELD, LEGEND_FIELD_CONSISTENCY, LEGEND_STYLE_EQUIVALENCE, NO_DATA_SEMANTICS, PAINT_FIELD_EXISTS, PALETTE_CARDINALITY, SOURCE_LAYER_REF, STOPS_DATA_RANGE`

### Verdict / 状态词表
- Product verdict：`READY, READY_WITH_WARNINGS, NEEDS_REPAIR, BLOCKED_BY_DATA, BLOCKED_BY_METHOD`（contracts.py:123-127）
- 完成态：`pending, needs_repair, complete, failed`；最终地图态：`verified, verified_with_degradation, failed, unknown`；render 态：`verified, issues, stale, unknown, not_applicable`
- 失败 11 类 + remediation 8 动作（failure_taxonomy.py）

## UnifiedFinding 投影（adapter，不迁词表）

```python
UnifiedFinding:
    domain            # harness_finalizer | render_diagnostic | composition | semantic_check | quality | visual | workflow_runtime
    code              # 原 domain code 原样
    severity          # info | warning | error（映射表单点维护）
    source            # 产生器标识（finalizer / frontend_runtime / export_sidecar / solver / evaluator）
    scope             # node | layer | component | source | chart | map | workflow
    affected_entity   # 结构化 id（node_id/layer_id/component_id/ref）
    evidence          # 原 evidence 透传（有界）
    repair_class      # 映射到 RemediationAction + runtime_repair 动作（见 05/10）
    retryable         # 由 REMEDIATION_POLICY 推导
    blocks_completion # 由 severity + domain 规则推导（单点函数）
    degradation_only  # export 降级类（18 码中 info/warning 且不阻 completion）
```

- 投影函数落点：新模块 `app/services/gis_harness/completion/unified_findings.py`；`derive_product_verdict` 改消费 UnifiedFinding 投影（内部重构，外部 verdict 词表不变）。
- `blocks_completion` 推导规则单一化（现状分散在 `_DATA_BLOCK_CODES`、render error 判定、dedup gate）。
- visual findings（W9）经同一投影进入，code 带 `V_` 前缀域。

## Completion Verdict 单一化（W7）

- 维持 `VERDICT_*` 五值 + FINAL_MAP 四值（现有词汇够用，不新增平行 verdict）。
- `evaluate_completion_contract` 七维输入全部经 UnifiedFinding 投影 + node runtime 状态（W1）+ 组件生命周期状态（W8）：
  - `observed_map` 维纳入 visual findings（deterministic 层 error 级阻 READY，soft 层默认只降级披露）
  - 不得单纯"工具执行成功"即 complete（现有七维已防，保持）
- Prompt §16 的 `READY_WITH_DEGRADATION/BLOCKED_BY_RUNTIME` 等 ↔ 现有 `READY_WITH_WARNINGS`/`FAILED`+render_status 映射表写入文档，不新立词表。

## 验收

- 任一 domain finding 可投影为 UnifiedFinding 且 round-trip 不丢 code/evidence。
- verdict 推导单测：同一输入经旧路径与 UnifiedFinding 路径产出一致（parity）。
- findings 棘轮零新增（除非新码经登记）。
