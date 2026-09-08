# 02-plan — 12 implementation waves

| # | wave | 主要产物 | 提交 |
|---|---|---|---|
| W1 | QualityManifest V2 + findings ratchet + waivers | manifest.py v2 字段、behavioral.py、findings-baseline.json（初始=当前实测）、waivers.json、闸测试 | feat(quality): manifest v2 |
| W2 | Descriptor 收敛 | app/tools/*.py register 富化（capabilities/tags/side_effect）；GATE_THRESHOLDS 上调；manifest 再生成 | fix(tools): descriptor enrichment |
| W3 | 行为化工具覆盖 | tests/unit/tools/test_behavioral_*.py：30 个 untested 工具真实 dispatch + validation/error/result 契约 | test(tools): behavioral dispatch |
| W4 | 算法 conformance + variants | algorithms/*.py conformance_tests 声明 + tests/unit/gis/test_algo_conformance_v2.py；backend_variants 声明 | test(gis): algo conformance |
| W5 | 安全收口 SEC-KG-01/02 | artifact_registry owner 校验 + templates/knowledge delete authZ + 矩阵回归 + security manifest 更新 | fix(security): ownership |
| W6 | WS/SSE 契约快照 | api_compat 扩展 realtime snapshot + diff 分类 + 快照 + 闸测试 | feat(quality): realtime contract |
| W7 | 生成式 property/fuzz | tests/fixtures/generative.py + tests/quality/test_property_geometry.py、test_property_mapspec.py、test_fuzz_parsers.py + fuzz_corpus | test(quality): property+fuzz |
| W8 | 差异化存储 harness | tests/quality/test_storage_differential.py（SQLite 恒跑 / PG graceful skip） | test(data): storage differential |
| W9 | chaos STORAGE 故障 + cancellation | chaos.py 新 fault + 测试；terrain/density/rs_v3/chunk checkpoint；认证再生成 | feat(quality): chaos storage + cancellation |
| W10 | 顺序卫生 + runner V2 | conftest seeded shuffle + 泄漏自检；quality_runner profiles quick/changed/full-local | feat(scripts): runner v2 |
| W11 | 性能门稳定化 | tests/fixtures/perf_budget.py + 迁移最脆固定墙钟断言（优先 <0.5s） | test(perf): stabilized budgets |
| W12 | 生成物依赖图 + stale detector + 文档 | artifact_graph.py + generated-artifacts.json + check_generated_staleness.py + ADR + README + 最终再生成 | feat(quality): artifact graph + docs |

## 每 wave 循环

契约/测试先行 → 最小实现 → targeted tests → ruff → progress 记录 → 小步 commit。

## 验收锚点

- findings 总数 241 → 目标 ≤ 个位数（真实修复，非 suppress；剩余以 waiver+expiry 管理）。
- WS/SSE 契约纳入 compatibility 闸。
- SEC-KG-01/02 有 regression 矩阵。
- 富化闸上调后 PASS。
- quality_runner full-local 本地绿（perf 车道隔离）。
