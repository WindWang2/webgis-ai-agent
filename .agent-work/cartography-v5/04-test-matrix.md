# Cartography V5 — Test Matrix（最终态）

## 新增/扩展测试（本分支）
| 层 | 文件 | 覆盖 |
|---|---|---|
| 后端契约 | tests/cartography/test_render_diagnostics_contract.py | 词表一致性/catalog 段/normalize 拒绝语义（7） |
| 后端 P0 | tests/unit/test_mapspec_layout_intent_v5.py | legend 显式关闭提交/store 链路/字段 merge/跨变异不变式/popitem/controls 类型（7） |
| 后端 label | tests/cartography/test_label_engine.py（扩展） | fit/wrap 确定性、CJK 宽度、solve label_too_long |
| 后端孪生 | tests/unit/test_mapspec_to_svg.py（扩展） | 隐藏层/阈值截断/协作超时/长文本截断+诊断 |
| 后端 report | tests/unit/test_report_service_vector_svg.py（扩展） | wait_for 有界化/占位图/部分产物 degraded 标记 |
| 后端跨孪生 | tests/unit/test_compiler_parity_long_label.py | 220 字符标注：59 前缀+…，全文本不入产物 |
| 后端 perf | tests/benchmarks/test_perf_mapspec_svg_compile.py | 20k 特征 differential：cap 生效/结构预算 15s/超时协作中止 |
| 后端 sidecar | tests/unit/test_export_diagnostics_sidecar.py | 校验/持久化/owner fail-closed/穿越/无界载荷（11） |
| 后端工具 | tests/test_export_frames_v5.py | frames 透传/形状拒绝/未知字段/上限/省略（5） |
| 前端矢量 | frontend/lib/map-kit/vector-svg-export.test.ts + .parity.test.ts | 组装/截断/回退/转义 + 跨孪生同口径 |
| 前端 PDF | frontend/lib/map-kit/exporter.pdf.test.ts | 单标题/CJK 栅格化/文本层状态/terrain 披露 |
| 前端 parity | frontend/lib/map-kit/export-chrome.parity.test.ts（扩展） | pending 合成/图例标题/死码发射/nodata/词表子集（18 码） |
| 前端 swipe | frontend/lib/map/comparison-export-registry.test.ts | 组合几何/不可用回退/注册清理 |
| 前端多帧 | frontend/lib/map-kit/frame-composer.test.ts | 快照互异（live canvas 别名回归）/filter 恢复/上限/跳帧/grid 守卫 |
| 前端 scene | frontend/lib/map-kit/render-scene.test.ts | golden corpus/live 合成语义响应/序列化口径 |
| 前端上传 | frontend/lib/map-kit/exporter.upload.test.ts | render_diagnostics 透传/空清单不发 |

## 全量回归（最终代码状态）
- 后端：`pytest tests/unit tests/cartography` → 本次 rebase 后复测（结果见 06-pr-summary）。
- 前端：`vitest run`（全量 275 文件）→ **2661 passed, 0 failed**。
- 静态：ruff repo-wide ✓；eslint repo-wide --max-warnings 0 ✓；tsc 双 tsconfig ✓。
- 契约闸：test_component_catalog_parity（catalog 字节一致）✓；test_catalog_docs（生成文档字节一致）✓；test_compiler_parity（TS/Python 孪生）✓。

## 验收指标对照
| Epic 验收项 | 状态 |
|---|---|
| 两个 cartographic regression known gaps 修复并转 passing tests | ✓（legend visibility P0 / long label 静默溢出，均带回归测试） |
| long labels 不再静默溢出 | ✓（截断 + label_truncated 诊断，跨孪生 parity corpus） |
| legend visibility mutation 不再"成功但未生效" | ✓（user-wins 不变式 + finding 诚实披露） |
| live/PNG/PDF/SVG semantic parity corpus | ✓（describeRenderScene oracle + golden + export-chrome parity + 跨孪生 corpus） |
| vector PDF/SVG 保留 vector path | ✓（SVG 真矢量复活；PDF ASCII 文本矢量层保留，CJK 诚实栅格化） |
| atlas/small multiple 真实 end-to-end export | ✓（frames 工具入口 → 命令 → frame-composer → pdf pages/png grid） |
| unsupported 特性显式降级 | ✓（18 码全部有发射器；cartogram/swipe/basemap/超时均有披露） |
