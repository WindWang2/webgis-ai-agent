# ads-v1 · DS0 波次计划：契约、Fixture 基建与债清（ADR-0170）

> 状态：**done（M1，与 DS1 同车）** · 台账 `ads-v1-ds0-ledger.md` · 复核纪要 `ads-v1-review-notes.md` · 债清 `ads-v1-debt-scan.csv`

## 目标（任务书 §3 DS0）

1. **D1–D4 契约定稿**：`app/services/data_fabric/contracts.py`（Pydantic）+ JSON Schema 落 `docs/dev/ads-v1-contracts/`，版本字段 + 升级路径。
2. **A11 fixture 基建**（最高优先）：`tests/data/fabric_fixtures.py` — `FakeSourceServer`（responses 拦截：WFS/OGC-API/STAC/ArcGIS 四类 HTTP 协议最小真响应；PostGIS 走既有 fake-pool 缝），**socket 阻断器**（`ADS_FORCE_OFFLINE=1` 全局断网，防测试悄悄联网）。
3. **A7 阈值单点化**：`app/services/data_fabric/acquisition_limits.py` — 七处字面量收敛为「要素数 × 几何复杂度 × 视口」连续策略 + 命名表面常量；七处机械替换为 import。
4. **A12 取数前握手点**：D1 `quality_signals{declared_crs, declared_completeness, known_issues}` — 只声明不修复（修复归 V11 spatial_repair_pipeline）。
5. **基线测量**：`scripts/ads_baseline.py` → `docs/dev/ads-v1-baseline.md`（取数 P50/P95、内联命中率、外部源可用率——离线环境诚实标注）。
6. **债清盘点**：✅ 已完成（S1 subagent → `ads-v1-debt-scan.csv` + 复核纪要 §6）。

## 关键设计决定（已定，不问人）

- **D1 = 子类扩展**：`app/schemas/data_fabric_schema.py` 不可改（不在 §8.2 可改清单）。D1 在 contracts.py 里 `class D1DatasetDescriptor(DatasetDescriptor)`，新增字段全部可选（向后兼容），`from_fabric_descriptor()` 从既有实例构造。
- **与 `limits.py` 分工**：limits.py=查询结果运行时硬护栏（settings 驱动）；acquisition_limits.py=取数面**策略阈值**单点。不重复定义、不互改。
- **阈值语义**：表面常量（`INLINE_REF_LIMIT=5000` / `MVT_TILE_FEATURE_LIMIT=20000` / `PROFILE_INLINE_LIMIT=20000` / `MAPSPEC_MAX_FEATURES=50000` / `EXPORT_MAX_FEATURES=50000`）+ `effective_feature_limit(surface, complexity, viewport)` 连续策略函数。首轮 `provisional`（DS8 校准）。
- **契约版本**：`CONTRACTS_VERSION="1.0"`；schema dump 脚本 `scripts/ads_dump_contracts.py`；测试断言 dump 与模型一致（漂移即红）。

## 交付物清单

| 文件 | 说明 |
|---|---|
| `app/services/data_fabric/contracts.py` | D1–D4 模型 |
| `app/services/data_fabric/acquisition_limits.py` | A7 单点 |
| `tests/data/fabric_fixtures.py` | A11 fixture 层 |
| `tests/data/offline_guard.py` | socket 阻断器 |
| `tests/unit/test_data_fabric_ads_contracts.py` | 契约 schema/升级测试 |
| `tests/unit/test_data_fabric_acquisition_limits.py` | 策略函数 + grep 归零断言 |
| `tests/data/test_ads_fixture_infra.py` | 5 协议离线跑通 + 断网全绿 |
| `scripts/ads_dump_contracts.py` / `scripts/ads_baseline.py` | schema dump / 基线测量 |
| `docs/dev/ads-v1-contracts/*.schema.json` | 四份 JSON Schema |
| `docs/dev/ads-v1-baseline.md` | 首轮基线 |
| `docs/adr/0170-ads-v1-contracts-fixture-baseline.md` | ADR |

## 验收（任务书硬闸）

- [ ] 四份契约有 schema + 升级测试
- [ ] fixture 层离线跑通 5 类协议（`ADS_FORCE_OFFLINE=1` 全绿）
- [ ] 七处阈值收敛且 grep 断言归零
- [ ] 基线数值入库；债清 CSV 完整
- [ ] `pytest tests/data -q` / 相关 unit 全绿；ruff 变更文件 0 告警

## 台账

→ 完成后填 `ads-v1-ds0-ledger.md`
