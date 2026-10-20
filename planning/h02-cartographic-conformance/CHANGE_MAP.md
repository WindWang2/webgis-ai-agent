# H02 CHANGE MAP（随波次更新）

## 交叠排除

- 他属 issue 修复归属：#1747/#1726/#1713/#1719/#1720/#1753/#1702/#1707（RECON §5b）；H02 只在新测试文件建回归契约。
- dependabot PR #1695–#1701：不升级任何依赖。
- H01 分支 feat/h01-gis-harness-capability-foundation-vnext：不读不碰其未合并内容；上游接口用 master 现状（MapProductPlan/MapRequestIntent/ProductFacetContract）。

## 变更登记

- W1: tests/cartography/test_h02_fingerprint_report_gaps.py 新增（G1/G2 契约，cartography marker）。
- W1: tests/cartography/test_h02_product_pipeline_gaps.py 新增（G3–G6 契约，cartography marker）。
- W2: app/lib/cartography/quality_loop.py — _LAYER_METADATA_KEYS 增 representation/visibility；to_dict 序列化 representation_report；review_and_repair 结果携带报告。
- W2: app/services/gis_harness/tools.py — webgis_map_product layout_set 携带 composition 身份；新 helper _product_composition_identity（用户契约不覆盖）。
- W2: app/services/gis_harness/completion/inputs.py — 提取 resolve_required_component_slots（物理 spec 身份块恢复真契约）。
- W2: app/services/gis_harness/planner.py — finalize 重选规则扩展到显式 export intents。
- W2: app/services/gis_harness/product_runtime.py — produce_product_layer 新增 product_conformance 快照（_product_conformance_snapshot）。

