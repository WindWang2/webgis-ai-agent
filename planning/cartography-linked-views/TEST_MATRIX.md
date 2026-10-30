# TEST_MATRIX — 验收 ↔ 测试绑定

| 验收条目 | 测试（命令） | 结果 |
|---|---|---|
| M1 排序稳定序/缺失恒最后/v2 cursor 绑定 | `pytest tests/unit/test_feature_query_v2.py` | ✅ 24 |
| M2 typed 谓词 allowlist/深度/项数/值数门 | 同上 TestWhereParam | ✅ |
| M2 total_matching 精确/超限 null | 同上 TestTotalMatching + HTTP total_exact | ✅ |
| M2 聚合 null 键/非数值/__other__/超限 422 | `pytest tests/test_layer_query_v2_http.py` | ✅ 14 |
| 聚合↔分页匹配集 parity | 同上 test_aggregate_where_and_bbox_parity | ✅ |
| M3 有 id 瓦片字节不变 | `pytest tests/unit/test_mvt_positional_id.py`（chain-id 无 field 1） | ✅ 19 |
| M3 双路径字节奇偶（含 no-shapely 回退） | 同上 parity 两用例 | ✅ |
| M3 AM 分裂同 id / 重复 id first-wins | 同上 | ✅ |
| M3 单要素 O(1) 索引 | 同上 TestFeatureIdIndex | ✅ |
| M4 MVT 层分页体替换占位 | `pnpm vitest components/map/map-components/table-panel-paged.test.tsx` | ✅ 6 |
| M4 游标续页/服务端排序循环/诚实计数 | 同上 | ✅ |
| M4 409 横幅+重载 / 429 退避 / 作废纪律 | 同上 + `lib/map-components/paged-table-data.test.tsx`（5） | ✅ |
| M5 位置 id 单要素兜底（404→positional） | `pnpm vitest lib/store/layer-data.test.ts`（3 新） | ✅ |
| M6 inline 路径回归零破坏 | table-panel.test/stress + selection-store 套件 | ✅ 32 |
| M7 视口聚合 generation 取消/overlimit | `lib/map-components/viewport-aggregate.test.tsx`（4） | ✅ |
| M8 证据行/truncated 徽标/inline 回归护栏 | `components/map/map-components/chart-panel-agg.test.tsx`（4） | ✅ |
| M9 priority 名次（高活低抑/缺省原序/非数值回退） | `pytest tests/cartography/test_label_trackb_vnext.py`（8） | ✅ |
| M9 CJK 折行 tspan / 线 repeat ≤4 / 单锚回归 | 同上 + TS `mapspec-to-svg.trackb.test.ts`（4） | ✅ |
| M9 双孪生零漂移 | golden_corpus + compiler_parity（双侧）+ collision tests | ✅ 633+37 |
| M10 预算 400 不变 + 披露 | 既有 budget 测试回归 + M13 新检查 | ✅ |
| M11 布局质量（窄视口/A4/user-wins） | test_layout_solver_v3/v4 + geometry_v7 + ir_golden | ✅ 73 |
| M12 PDF CJK 文本层 | test_export_sample_pdf_text.py | ✅ |
| M13 findings 接线（budget/抑制/截断/诚实缺席） | `pytest tests/unit/gis_harness/test_export_label_diagnostics.py`（8） | ✅ |
| M14 工具注册+诚实语义+命名空间不混用 | `pytest tests/unit/test_layer_browse_tools.py`（8）+ rationalization 门 | ✅ 13 |
| M15.1 性能三档 | `pytest tests/perf/test_trackb_paging_benchmark.py -s` | ✅ 9（数值见 EVIDENCE） |
| M15.5 场景一致性 | `pytest tests/cartography/test_trackb_scenario_consistency.py` | ✅ 2 |
| 既有总回归 | 全部上述 + tool catalog + i18n key-completeness + data plane boundaries | ✅ |

运行环境：worktree venv（pytest 9.2.0）/ frontend pnpm vitest 4.1.11；`-n 0`；时间 2026-10-31。
