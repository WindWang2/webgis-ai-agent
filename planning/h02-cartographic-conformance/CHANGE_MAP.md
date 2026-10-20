# H02 CHANGE MAP（随波次更新）

## 交叠排除

- 他属 issue 修复归属：#1747/#1726/#1713/#1719/#1720/#1753/#1702/#1707（RECON §5b）；H02 只在新测试文件建回归契约。
- dependabot PR #1695–#1701：不升级任何依赖。
- H01 分支 feat/h01-gis-harness-capability-foundation-vnext：不读不碰其未合并内容；上游接口用 master 现状（MapProductPlan/MapRequestIntent/ProductFacetContract）。

## 变更登记

- W1: tests/cartography/test_h02_fingerprint_report_gaps.py 新增（G1/G2 契约，cartography marker）。
- W1: tests/cartography/test_h02_product_pipeline_gaps.py 新增（G3–G6 + W3 user-wins 契约）。
- W2: app/lib/cartography/quality_loop.py — _LAYER_METADATA_KEYS 增 representation/visibility；to_dict 序列化 representation_report；review_and_repair 结果携带报告。
- W2: app/services/gis_harness/tools.py — webgis_map_product layout_set 携带 composition 身份；新 helper _product_composition_identity（用户契约不覆盖）。
- W2: app/services/gis_harness/completion/inputs.py — 提取 resolve_required_component_slots（物理 spec 身份块恢复真契约）。
- W2: app/services/gis_harness/planner.py — finalize 重选规则扩展到显式 export intents（W9 清偿：候选查询传实际 output_target）。
- W2: app/services/gis_harness/product_runtime.py — produce_product_layer 新增 product_conformance 快照（_product_conformance_snapshot）。
- W3: tools.py — 被锁组件 placement/position 在再制图保持（locked_component_ids_of 单源）。
- W5: app/lib/cartography/product_manifest.py 新增（manifest v1 + 六维验证）；app/api/routes/map.py 挂载（W9 清偿：verify_session_owner + to_thread）。
- W5: thematic_spec.py / ts_projection.py / types.generated.ts — StyleMethod 可选 nodata。
- W5: frontend compiler.ts / mapspec-to-svg.ts / mapspec_to_svg.py — nodata 四下降点（W9 清偿：空串语义三端一致）。
- W5: exporter.ts SVG 分支 injectResolvedRefSources；mapspec-to-svg.ts + mapspec_to_svg.py layer_ref_unavailable（W9 清偿：Python 侧补发 + EMITTER_REGISTRY 双端登记）。
- W5: render_diagnostics.py — +layer_ref_unavailable/title_truncated 词表码；component-catalog.generated.json 受控再生成。
- W6: test_h02_issue_regression_contracts.py（#1713×2/#1726 xfail）+ heatmap-export-palette.regression.test.ts（#1747 it.fails）—— 零触碰他属实现文件。
- W7: golden_corpus/h02_scenarios/{generate.py,scenarios.json} + test_h02_golden_factory.py + h02-scenarios.compile.test.ts（33 场景跨语言）。
- W8: tests/benchmarks/test_h02_cartography_perf.py（W9 清偿：公共 API all_templates）；ADR-0228；PERF.md。
- W4: layout-description.ts 截断 + exporter/vector-svg-export title_truncated 披露 + layout-truncation.contract.test.ts。
- W9: 评审清偿 commit c99ec307（详见 REVIEW.md）。


