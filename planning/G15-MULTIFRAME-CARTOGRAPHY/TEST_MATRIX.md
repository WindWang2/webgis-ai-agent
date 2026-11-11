# G15 TEST_MATRIX

| 面 | 测试 | 状态 |
|---|---|---|
| schema v2.0 契约/版本阶梯/迁移 | tests/cartography/test_mapspec_schema_v6/v19_multiscale、unit/test_mapspec_schema_v14/v17/v18、carto_ir_corpus、ts_projection_contract | ✅ |
| mutation 契约（intent/handler/registry/REST body） | tests/cartography/test_mutation_parity_corpus.py（4 新 case + goldens） | ✅ |
| resolver 帧隔离/确定性/共享盖章/悬空拒绝 | tests/unit/gis_harness/frame_products/test_resolver.py（14） | ✅ |
| 共享分级已知答案 + 资格 + 退化分布 | test_shared_expression.py（9） | ✅ |
| bind/plan/update/unbind/status 真旅程 | test_service.py（8） | ✅ |
| 工具面 dispatch（plan→bind→status→改名 patch→unbind） | test_tools.py（3） | ✅ |
| 逐画幅终验（观察对账/空帧/快照不冒充/单帧不掩盖） | test_verification.py（10） | ✅ |
| 产品页规划/编译/SVG 命名空间/槽失败 typed | test_frame_product_publication.py（9，含真实 WeasyPrint PDF） | ✅ |
| 真实导出工件（PDF 文本层/pdftoppm 像素核验/SVG 结构） | test_frame_product_export_artifacts.py（4） | ✅ |
| 基准（真实耗时，非 mock timing） | test_frame_product_benchmarks.py（4，slow 标记） | ✅ |
| 前端投影/CAS 提交/导出路由/同步组 | frame-product.test.ts（15） | ✅ |
| 面板组件旅程（恢复三幅/改名 CAS/激活） | frame-product-panel.test.tsx（2） | ✅ |
| 浏览器端到端（mock 档，真实 app shell） | e2e/journeys/j14-frame-product.journey.ts（1 passed @本机） | ✅ |
| 旧链回归 | publication/atlas/export_frames/model packs/registry 6946 通过 | ✅ |

## 已知基线失败（非本 PR 引入）

- tests/test_adr_numbering_contract.py::test_adr_numbers_are_unique — 0286/0287 撞号在
  BASE_SHA 816705218 复现（兄弟 PR 合并遗留）；本 PR 的 0288 唯一。
