# ADR-0170: ads-v1 契约 D1–D4、离线 fixture 基建与取数阈值单点

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-data-supply/v1-master · DS0（自适应数据供给与接入 · 并行线 P1）
- 关联: ADR-0094（DatasetDescriptor 诚实默认）、ADR-0053（adapter 注册表）、ADR-0151（制图面降级链，D3 同构但独立命名空间）、ADR-0153（制图前置质量门禁，A12 只握手不越界）、migrations 70–79 号段（本波未用）

## 1. 背景

数据供给侧的「前一公里」是空白：源注册要改代码（A1/A2）、无语义检索（A3）、无降级链（A4）、
无版本 pin 与漂移检测（A5/A6）、阈值七处分裂（A7）、无相对时间解析（A8）、本地资产无索引（A9）、
无取数埋点（A10）、无外部源离线测试基建（A11）、取数前质量握手缺失（A12）。
复核纪要（`docs/dev/ads-v1-review-notes.md`）核实了全部点位；债清扫描
（`docs/dev/ads-v1-debt-scan.csv`）确认了五组高危重复实现。

DS0 先建三样地基：**可冻结的契约**、**可离线回归的 fixture 基建**、**可度量的阈值与基线**。

## 2. 决策一：D1–D4 契约（`app/services/data_fabric/contracts.py`）

- **D1 `D1DatasetDescriptor` = 子类扩展**，不新建平行类。`app/schemas/data_fabric_schema.py`
  的 `DatasetDescriptor`（ADR-0094）不在本线可改清单（§8.2），且被全部 adapter 消费；
  D1 以子类追加供给侧可选字段（version / version_pinned / temporal_coverage / granularity /
  license / freshness / quality_signals / cost_hint），`from_fabric_descriptor()` 提供零拷贝
  升级缝。诚实默认继承：未知即 None，不伪造。
- **A12 握手点**落在 D1 的 `quality_signals{declared_crs, declared_completeness, known_issues,
  verified}`：只声明不修复——修复仍归 V11 W3 `spatial_repair_pipeline`（§8.1 第 3 条边界）。
- **D2 `AcquisitionPlan`**：steps 词表固定（source_select / bbox_clip / field_projection /
  aggregate_pushdown / time_filter / pagination / sampling / version_pin）+ cost_estimate +
  budget + explain；可序列化、`diff()`、可重放（同 plan + 同版本 pin → 同结果，DS3 验收）。
- **D3 `FallbackDecision`**：与 ADR-0151 同构、命名空间独立；`comparable` **默认 False**
  （保守：备用源与原源粒度/覆盖不同即不可比，下游必须标注），置信度默认 0.5。
- **D4 `AcquisitionFact`**：request_id / dataset_key / source_id / version / rows / bytes /
  latency_ms / retries / degraded / outcome / fallback / drift / wave / ts——DS8 埋点落库
  与 ratchet 的输入。
- **冻结纪律**：`CONTRACTS_VERSION="1.0"`；JSON Schema 由 `scripts/ads_dump_contracts.py`
  落 `docs/dev/ads-v1-contracts/`，契约测试断言 dump 与模型一致（漂移即红）。演进只允许
  加可选字段 + 升级测试（v1 → v1.1），改型须 ADR。

## 3. 决策二：离线 fixture 基建（`tests/data/fabric_fixtures.py` + `offline_guard.py`）

- **复用既有缝不引新依赖**：`tests/fixtures/data_fabric/fake_server.py` 已提供 requests
  HTTPAdapter 级的离线 fake（`FakeFabricAdapter` 继承 `SSRFSafeHTTPAdapter`，每跳仍过 SSRF
  校验）。`FakeSourceServer` 在其上提供五类协议的**最小真响应**：
  - OGC API Features（/collections、/collections/{id}、/items，GET JSON）；
  - WFS 2.0（GetCapabilities XML + GetFeature POST/GET GeoJSON）；
  - STAC（POST /search + GET landing）；
  - ArcGIS REST（?f=json server info / layer meta / query GeoJSON）；
  - PostGIS（DB-API 级 canned pool 装进 adapter 自有缝 `_POSTGIS_POOLS`，支持
    `SELECT 1` probe / `SET LOCAL` / 按口径 canned rows）。
- **保真由 round-trip 测试锁定**：每个协议的 canned 载荷必须穿过**真 adapter**
  （probe → list_datasets → query）取回声明过的数据，载荷形状漂移即红——fixture 不允许
  臆造协议。测试数据一律声明在 `ads-fixture.invalid`（RFC 2606），零伪造语义。
- **socket 阻断器（本线硬闸）**：`ADS_FORCE_OFFLINE=1` 时 `tests/conftest.py` 的 session
  autouse fixture 全局阻断 AF_INET/AF_INET6 socket 新建（AF_UNIX 放行），离线门禁
  `ADS_FORCE_OFFLINE=1 pytest tests/data …` 以此**证明**数据 lane 不靠外网。未设 env 的
  常规跑法零影响。外呼一律 typed `NetworkBlockedError`，禁止静默联网。

## 4. 决策三：A7 取数阈值单点（`app/services/data_fabric/acquisition_limits.py`）

- 七处字面量收敛为七个表面常量（INLINE_REF_LIMIT=5000、MVT_TILE_FEATURE_LIMIT=20_000、
  PROFILE_INLINE_LIMIT=20_000、PROFILE_SCAN_ROWS_LIMIT=50_000、MAPSPEC_MAX_FEATURES=50_000、
  EXPORT_MAX_FEATURES=50_000、MAP_QUALITY_GATE_FALLBACK=5000）+ 连续策略函数
  `effective_feature_limit(base, avg_vertices=, viewport_features=)`（要素数 × 几何复杂度 ×
  视口；确定性、纯函数、DS3 重放兼容）。首轮全部 `provisional`，DS8 用实测分布校准。
- **与 `limits.py` 分工（不合并）**：limits.py 是查询结果的运行时硬护栏（settings 驱动 +
  非零下限 + ResultTooLargeError）；acquisition_limits.py 是取数面策略阈值。护栏消费策略，
  不重复定义。
- **替换纪律（§8.1 头号冲突点的落地，2026-09-13 终态）**：DS0 时 V11 尚未落地单点，
  本线先建了 `acquisition_limits.py`；**V11 随后先合入 master（ADR-0163 `data_tiers.py`）**，
  按 §8.1.1「以先合入者为准、禁止两线各建一套」执行终态适配：**本线删除自建单点**，
  七个消费点（mapspec_source、api/routes/data_quality、mapspec/composite_builder、
  mapspec/lifecycle_engine、publication_export、data_profile/unified、
  data_fabric/adapters/postgis_adapter）全部改 import `app.lib.cartography.data_tiers`
  （三档常量 5000/20000/50000，数值即既有校准锚点）；仅保留 grep 断言测试
  （`tests/unit/test_data_tier_consumers.py`：七点 import 断言 + 旧模块已删断言 +
  字面量归零 + settings 默认一致性）。planning 编译器的载体封顶改用
  `TIER_EXPORT_FEATURES`（不再自定义连续函数——单点唯一）。

## 5. 决策四：首轮基线（`scripts/ads_baseline.py` → `docs/dev/ads-v1-baseline.md`）

离线可复跑的 ratchet 锚点：adapter.query P50/P95（fixture 源）、内联缓存命中率与
put/get 延迟、外部源可用率（真实 probe、离线诚实记 `unavailable`、绝不伪造）。
DS8 以同协议复测并转定稿。

## 6. 后果

- 后续九波（DS1–DS9）全部消费本波产物：DS1 源注册表产出 D1；DS3 编译 D2；DS4 落 D3；
  DS5 消费 D1.version 与 drift；DS8 落 D4；新 adapter 测试一律走 fixture 层 + 离线门禁。
- 迁移未用（本波无新表），70–79 号段留给 DS8（acquisition facts 表）。
- 阈值字面量归零后，改阈值只改一处；DS8 校准时同一断言继续生效。
