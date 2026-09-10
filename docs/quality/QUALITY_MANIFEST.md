# Quality Manifest（自动生成）

> 本文件由 `python scripts/gen_quality_manifest.py` 从各 registry 与测试引用索引派生，请勿手改。唯一事实源：ToolRegistry / AlgorithmRegistry / CapabilityRegistry / ArtifactTypeRegistry / RecipeRegistry 与 tests/ 源码本身。

- Manifest 版本：**2**
- 内容指纹：`915a5dd8615d9bcc…`

## 总览

| section | total | 静态测试引用 | findings |
|---|---|---|---|
| tools | 315 | 315 | 16 |
| algorithms | 213 | 166 | 0 |
| capabilities | 139 | 89 | 0 |
| artifact_types | 21 | 21 | 0 |
| recipes | 164 | 164（conformance 由 workflow 闸保护） | 0 |

## 描述符富化闸（ADR-0103，可执行工具）

| field | coverage | threshold | status |
|---|---|---|---|
| side_effect | 97% | 95% | PASS（缺 10） |
| tags | 97% | 95% | PASS（缺 10） |
| latency_class | 100% | 95% | PASS（缺 0） |
| memory_class | 100% | 95% | PASS（缺 0） |
| capabilities | 95% | 95% | PASS（缺 16） |

**gate: PASS**（`total=315`）

## 行为化覆盖与 findings 棘轮（Quality V2）

- 工具行为证据：dispatch **157** / mention 158 / none 0（dispatch 覆盖率 50%）
- findings 棘轮：**FAIL**（dispatch 下限 151，当前 157；active waivers 0，过期 0，被豁免 findings 0 —— 豁免项仍在上方法量清单中可见）
  - 违规：`TOOL_DESCRIPTOR_INCOMPLETE` 当前 16 > 基线 0

## Findings（派生线索，非缺陷判定）

> 静态引用 ≠ 行为覆盖。findings 只回答"哪里没有任何测试证据"，修复优先级需结合 02-coverage-risk-map 的风险分级。

### TOOL_UNTESTED（0）

（无）

### TOOL_DESTRUCTIVE_UNTESTED（0）

（无）

### TOOL_DESCRIPTOR_INCOMPLETE（16）

- `gis_component_query`（low）— missing descriptor fields: capabilities
- `gis_method_explain`（low）— missing descriptor fields: capabilities
- `gis_method_qualify`（low）— missing descriptor fields: capabilities
- `gis_method_rank`（low）— missing descriptor fields: capabilities
- `gis_task_classify`（low）— missing descriptor fields: capabilities
- `gis_template_plan`（low）— missing descriptor fields: capabilities
- `modelops_cancel_inference`（low）— missing descriptor fields: side_effect,tags,capabilities
- `modelops_check_compatibility`（low）— missing descriptor fields: side_effect,tags,capabilities
- `modelops_compare_results`（low）— missing descriptor fields: side_effect,tags,capabilities
- `modelops_estimate_resources`（low）— missing descriptor fields: side_effect,tags,capabilities
- `modelops_evaluate_model`（low）— missing descriptor fields: side_effect,tags,capabilities
- `modelops_inspect_model`（low）— missing descriptor fields: side_effect,tags,capabilities
- `modelops_inspect_provenance`（low）— missing descriptor fields: side_effect,tags,capabilities
- `modelops_list_models`（low）— missing descriptor fields: side_effect,tags,capabilities
- `modelops_run_inference`（low）— missing descriptor fields: side_effect,tags,capabilities
- `modelops_run_promptable`（low）— missing descriptor fields: side_effect,tags,capabilities

### CAPABILITY_NO_PRODUCER（0）

（无）

### CAPABILITY_NO_CONFORMANCE（0）

（无）

### ALGO_NO_CONFORMANCE（0）

（无）

### ALGO_HEAVY_NO_VARIANTS（0）

（无）

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
