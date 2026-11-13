# Quality Manifest（自动生成）

> 本文件由 `python scripts/gen_quality_manifest.py` 从各 registry 与测试引用索引派生，请勿手改。唯一事实源：ToolRegistry / AlgorithmRegistry / CapabilityRegistry / ArtifactTypeRegistry / RecipeRegistry 与 tests/ 源码本身。

- Manifest 版本：**2**
- 内容指纹：`78e4b6f5cf74147c…`

## 总览

| section | total | 静态测试引用 | findings |
|---|---|---|---|
| tools | 477 | 460 | 40 |
| algorithms | 256 | 195 | 1 |
| capabilities | 194 | 128 | 0 |
| artifact_types | 23 | 23 | 0 |
| recipes | 166 | 166（conformance 由 workflow 闸保护） | 0 |

## 描述符富化闸（ADR-0103，可执行工具）

| field | coverage | threshold | status |
|---|---|---|---|
| side_effect | 100% | 95% | PASS（缺 0） |
| tags | 99% | 95% | PASS（缺 4） |
| latency_class | 100% | 95% | PASS（缺 0） |
| memory_class | 100% | 95% | PASS（缺 0） |
| capabilities | 96% | 95% | PASS（缺 19） |

**gate: PASS**（`total=477`）

## 行为化覆盖与 findings 棘轮（Quality V2）

- 工具行为证据：dispatch **258** / mention 202 / none 17（dispatch 覆盖率 54%）
- findings 棘轮：**FAIL**（dispatch 下限 163，当前 258；active waivers 0，过期 0，被豁免 findings 0 —— 豁免项仍在上方法量清单中可见）
  - 违规：`TOOL_DESCRIPTOR_INCOMPLETE` 当前 23 > 基线 0
  - 违规：`TOOL_UNTESTED` 当前 17 > 基线 8

## Findings（派生线索，非缺陷判定）

> 静态引用 ≠ 行为覆盖。findings 只回答"哪里没有任何测试证据"，修复优先级需结合 02-coverage-risk-map 的风险分级。

### TOOL_UNTESTED（17）

- `habitat_suitability_analysis`（medium）— registered tool without any static test reference
- `modelops_model_history`（medium）— registered tool without any static test reference
- `resolve_field_semantics`（medium）— registered tool without any static test reference
- `sample_random_points`（medium）— registered tool without any static test reference
- `sample_stratified_points`（medium）— registered tool without any static test reference
- `sample_systematic_grid`（medium）— registered tool without any static test reference
- `topology_repair_preview`（medium）— registered tool without any static test reference
- `ts_smooth_gapfill`（medium）— registered tool without any static test reference
- `vector_change_proposal_rebase_preview`（medium）— registered tool without any static test reference
- `webgis_apply_scale_portrayal`（medium）— registered tool without any static test reference
- `webgis_inspect_scale_portrayal`（medium）— registered tool without any static test reference
- `webgis_print_layout_capabilities`（medium）— registered tool without any static test reference
- `webgis_print_layout_export`（medium）— registered tool without any static test reference
- `webgis_print_layout_inspect`（medium）— registered tool without any static test reference
- `webgis_print_layout_pages`（medium）— registered tool without any static test reference
- `webgis_print_layout_preview`（medium）— registered tool without any static test reference
- `webgis_print_layout_propose`（medium）— registered tool without any static test reference

### TOOL_DESTRUCTIVE_UNTESTED（0）

（无）

### TOOL_DESCRIPTOR_INCOMPLETE（23）

- `nearest_facility`（low）— missing descriptor fields: capabilities
- `vector_change_proposal_create`（low）— missing descriptor fields: tags
- `vector_change_proposal_diff`（low）— missing descriptor fields: tags
- `vector_change_proposal_list`（low）— missing descriptor fields: tags
- `vector_change_proposal_rebase_preview`（low）— missing descriptor fields: tags
- `webgis_map_export`（low）— missing descriptor fields: capabilities
- `webgis_print_layout_apply`（low）— missing descriptor fields: capabilities
- `webgis_print_layout_capabilities`（low）— missing descriptor fields: capabilities
- `webgis_print_layout_export`（low）— missing descriptor fields: capabilities
- `webgis_print_layout_inspect`（low）— missing descriptor fields: capabilities
- `webgis_print_layout_pages`（low）— missing descriptor fields: capabilities
- `webgis_print_layout_preview`（low）— missing descriptor fields: capabilities
- `webgis_print_layout_propose`（low）— missing descriptor fields: capabilities
- `webgis_raster_expression_apply`（low）— missing descriptor fields: capabilities
- `webgis_raster_expression_describe`（low）— missing descriptor fields: capabilities
- `webgis_raster_expression_export`（low）— missing descriptor fields: capabilities
- `webgis_raster_expression_probe`（low）— missing descriptor fields: capabilities
- `webgis_temporal_media_cancel`（low）— missing descriptor fields: capabilities
- `webgis_temporal_media_capabilities`（low）— missing descriptor fields: capabilities
- `webgis_temporal_media_plan`（low）— missing descriptor fields: capabilities
- `webgis_temporal_media_result`（low）— missing descriptor fields: capabilities
- `webgis_temporal_media_status`（low）— missing descriptor fields: capabilities
- `webgis_temporal_media_submit`（low）— missing descriptor fields: capabilities

### CAPABILITY_NO_PRODUCER（0）

（无）

### CAPABILITY_NO_CONFORMANCE（0）

（无）

### ALGO_NO_CONFORMANCE（0）

（无）

### ALGO_HEAVY_NO_VARIANTS（1）

- `terrain.cost_distance`（medium）— memory_cost=high but no backend_variants scale windows

### ALGO_SEED_POLICY_CONFLICT（0）

（无）

### ALGO_PRODUCTION_NO_UNCERTAINTY（0）

（无）

### ARTIFACT_TYPE_UNTESTED（0）

（无）

## 已知边界

- 本清单只做静态引用发现；行为正确性由各 lane（unit / oracle replay /
  cartography closed-loop / perf / chaos）保证，见 docs/quality/。
- recipes 的行为闸由 workflow conformance（test_workflow_guards）与
  gen_workflow_catalog 字节一致性闸保护，此处不重复展开。
