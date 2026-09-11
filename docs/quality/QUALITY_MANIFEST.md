# Quality Manifest（自动生成）

> 本文件由 `python scripts/gen_quality_manifest.py` 从各 registry 与测试引用索引派生，请勿手改。唯一事实源：ToolRegistry / AlgorithmRegistry / CapabilityRegistry / ArtifactTypeRegistry / RecipeRegistry 与 tests/ 源码本身。

- Manifest 版本：**2**
- 内容指纹：`9d3d7bf333878858…`

## 总览

| section | total | 静态测试引用 | findings |
|---|---|---|---|
| tools | 327 | 319 | 8 |
| algorithms | 222 | 175 | 1 |
| capabilities | 145 | 94 | 0 |
| artifact_types | 21 | 21 | 0 |
| recipes | 164 | 164（conformance 由 workflow 闸保护） | 0 |

## 描述符富化闸（ADR-0103，可执行工具）

| field | coverage | threshold | status |
|---|---|---|---|
| side_effect | 100% | 95% | PASS（缺 0） |
| tags | 100% | 95% | PASS（缺 0） |
| latency_class | 100% | 95% | PASS（缺 0） |
| memory_class | 100% | 95% | PASS（缺 0） |
| capabilities | 100% | 95% | PASS（缺 0） |

**gate: PASS**（`total=327`）

## 行为化覆盖与 findings 棘轮（Quality V2）

- 工具行为证据：dispatch **159** / mention 160 / none 8（dispatch 覆盖率 49%）
- findings 棘轮：**FAIL**（dispatch 下限 151，当前 159；active waivers 0，过期 0，被豁免 findings 0 —— 豁免项仍在上方法量清单中可见）
  - 违规：`ALGO_HEAVY_NO_VARIANTS` 当前 1 > 基线 0
  - 违规：`TOOL_UNTESTED` 当前 8 > 基线 0

## Findings（派生线索，非缺陷判定）

> 静态引用 ≠ 行为覆盖。findings 只回答"哪里没有任何测试证据"，修复优先级需结合 02-coverage-risk-map 的风险分级。

### TOOL_UNTESTED（8）

- `habitat_suitability_analysis`（medium）— registered tool without any static test reference
- `landscape_metrics_analysis`（medium）— registered tool without any static test reference
- `modelops_model_history`（medium）— registered tool without any static test reference
- `modelops_record_metrics`（medium）— registered tool without any static test reference
- `sample_random_points`（medium）— registered tool without any static test reference
- `sample_stratified_points`（medium）— registered tool without any static test reference
- `sample_systematic_grid`（medium）— registered tool without any static test reference
- `ts_smooth_gapfill`（medium）— registered tool without any static test reference

### TOOL_DESTRUCTIVE_UNTESTED（0）

（无）

### TOOL_DESCRIPTOR_INCOMPLETE（0）

（无）

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
