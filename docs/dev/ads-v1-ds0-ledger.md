# ads-v1 · DS0 交付台账（任务 → 文件 → 测试 → 证据）

> 波次：DS0 · 契约、Fixture 基建与债清 · ADR-0170 · 里程碑 M1（与 DS1 同车）
> 状态：**完成** · 2026-09-13

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 复核纪要（§0.2） | `docs/dev/ads-v1-review-notes.md` | — | 编号契约/V11 状态/七处阈值点位核实记录 |
| S1 债清扫描 | `docs/dev/ads-v1-debt-scan.csv` | — | 15 行模块边界 + 10 条高危重复结论（5 份指纹实现/6 处血缘等） |
| D1–D4 契约 | `app/services/data_fabric/contracts.py` | `tests/unit/test_data_fabric_ads_contracts.py`（11 测） | D1 子类扩展 ADR-0094 DatasetDescriptor；D2 diff/重放；D3 comparable 默认 False；D4 全字段 |
| 契约 JSON Schema | `docs/dev/ads-v1-contracts/*.schema.json` ×4；`scripts/ads_dump_contracts.py` | 同上（dump 与模型一致性断言） | schema 漂移即红；$id + 版本注记 |
| A11 fixture 层 | `tests/data/fabric_fixtures.py`（FakeSourceServer：OGC/WFS/STAC/ArcGIS/PostGIS 五协议最小真响应） | `tests/data/test_ads_fixture_infra.py`（7 测，round-trip 穿真 adapter） | `ADS_FORCE_OFFLINE=1` 下全绿 |
| socket 阻断器 | `tests/data/offline_guard.py` + `tests/conftest.py` 会话 fixture | 同上（公网 connect veto 原始断言） | 拦截语义=断外网（环回/私网放行），typed `NetworkBlockedError` |
| A7 阈值单点 | `app/services/data_fabric/acquisition_limits.py`（7 表面常量 + `effective_feature_limit` 连续策略） | `tests/unit/test_data_fabric_acquisition_limits.py`（10 测，含 grep 归零与全仓 maxFeatures 字面量断言） | 七处消费点全部改 import，行为不变 |
| A7 机械替换 | `mapspec_source.py` / `data_fabric/adapters/postgis_adapter.py` / `api/routes/data_quality.py` / `data_profile/unified.py`（2 处）/ `mapspec/composite_builder.py` / `mapspec/lifecycle_engine.py`（4 处）/ `publication_export.py`（3 处） | 同上 | 逻辑零改动；`MAP_QUALITY_GATE_FALLBACK==settings 默认` 防漂移断言 |
| A12 握手点 | D1 `quality_signals{declared_crs,declared_completeness,known_issues,verified}` | 契约测试 | 只声明不修复（修复仍归 V11 spatial_repair_pipeline） |
| 基线测量（DS0.5） | `scripts/ads_baseline.py` → `docs/dev/ads-v1-baseline.md` | — | 取数 P50=7.98ms / P95=10.09ms（200 要素/次，fixture 源）；内联缓存命中率 1.0；外部源=unavailable（诚实标注） |
| 既有 fake server 修复 | `tests/fixtures/data_fabric/fake_server.py`（`_content_consumed` 标记） | 既有 `test_data_fabric_fault_injection.py` 11 测全绿 | 修复 `iter_content` 在预物化响应上走 raw 流的问题（bounded_get 兼容） |
| CHANGELOG | `CHANGELOG.md` Unreleased DS0 段 | — | — |

## 波次验收对照（§6 DS0 行）

- [x] 四份契约有 schema + 升级测试（additive-only 演进断言）
- [x] fixture 离线全绿（`ADS_FORCE_OFFLINE=1 pytest tests/data/test_ads_fixture_infra.py` → 7 passed）
- [x] 七处阈值收敛且 grep 断言归零（`test_a7_sites_import_single_point_and_no_literal_left` + 全仓 `maxFeatures` 字面量断言）
- [x] 基线数值入库（provisional，DS8 复测转定稿）
- [x] 债清 CSV 完整（S1 产出，15 模块行 + 结论）

## 与 V11 的兼容声明（§8.1）

- **终态（2026-09-13 V11 合入后重定向）**：V11 先落地其单点 `app/lib/cartography/data_tiers.py`（ADR-0163）→ 按 §8.1.1 本线**删除自建 `acquisition_limits.py`**，七处消费点全部改 import data_tiers；仅保留 grep 断言（`tests/unit/test_data_tier_consumers.py`）。DS0 交付时（V11 未落地）的临时方向已按规则被本终态覆盖。
- 未触碰 `.github/workflows/**`、`app/lib/cartography/**`、`app/services/gis_harness/**`、`frontend/**`、`spatial_repair_pipeline.py`、`spatial_quality_gate.py`。
