# ads-v1 复核纪要（§0.2 · 2026-09-13）

> 波次开工前的现状复核结论。本线分支 `adaptive-data-supply/v1-master`，worktree `../webgis-wt-ds-v1`。

## 1. 基线

- **起点 commit**：`origin/master` @ `17c77c73`（Merge PR #1266, adaptive-cartography/09-visual-judge-selfheal）。
- **worktree**：`C:/Users/wangj.KEVIN/projects/webgis-wt-ds-v1`，分支跟踪 origin/master。
- **venv**：`.venv`（Python 3.13，win_amd64）。
  - **偏差记录**：任务书 §0.1 的 `pip install -e .` 在本仓**不可用**——`pyproject.toml` 无 `[tool.setuptools]` 包发现配置，setuptools flat-layout 自动发现因 12 个顶层目录报错。README 的标准装法是 `pip install -r requirements.txt`（根目录即 sys.path）。本线按 README 装法 + requirements-dev.txt，**不修改打包配置**（越界且影响其他线）。

## 2. V11 并行线状态（§8 冲突处置的前提）

- V11 **全部 10 波已合入 master**（ADR-0150~0159；PR #1257–#1269 全部 MERGED，最新 #1269 2026-09-13）。另有 #1270 `fix(ci): adaptive-wave hygiene + mapspec CLI alias` 仍 OPEN——rebase 时留意。
- **含义**：§8.1 各条按「V11 已先合入」处置——本线是后合入方，遇文件冲突以 V11 落地方为准并做适配。

## 3. 编号契约（§0.3 核实）

| 项 | 现状 | 本线取值 |
|---|---|---|
| ADR 水位 | 最大 `0159-cartography-quality-regression-baseline.md`（V11 用 0150–0159，**非**任务书预设的 0160–0169） | **0170–0179**（任务书指定，0160–0169 留空缓冲） |
| Alembic 迁移 | 最大 `0056_cartography_quality_facts.py`（V11 只用了 56）；head 链尾 `184068cb4249` merge migration | **70–79**，每波 ≤1 个 |
| 检查码/事件名 | V11 占 `carto.*` | 本线一律 `ds.*` |

## 4. 必读资产复核（§0.2.4，全部存在、与任务书描述一致）

| 资产 | 核实要点 |
|---|---|
| `data_fabric/base_adapter.py` | 7 方法抽象缝（probe/capabilities/list_datasets/describe/preview/query/health + sync）；`DatasetDescriptor` 契约在 `app/schemas/data_fabric_schema.py:72`（ADR-0094，诚实默认：None=未知不伪造） |
| `data_fabric/registry.py` | 11 类源注册 + `UnsupportedSourceError` typed 拒绝（不 fallback mock）；`geojson` 别名已删（#767 前车之鉴写在 docstring）；被 12+ 生产点依赖 |
| `data_fabric/reliability.py` | `RetryPolicy`（full-jitter，可注入 sleep/rng）/ `is_transient` / `retry_call`（保守 POST 规则）；`circuit_breaker.py` 独立成文件 |
| `data_fabric/limits.py` | **任务书未提**：已是运行时资源护栏（settings 驱动 + 非 0 下限钳制 + `enforce_result_bounds`）。DS0 的 `acquisition_limits.py` 与其**分工**：limits.py 管查询结果硬护栏，acquisition_limits.py 管内联/瓦片/导出的**策略阈值**单点；前者消费后者时不重复定义字面量 |
| `data_ingest/pipeline.py` | detect→validate→profile→quality→register→store + 内容指纹去重 + 失败补偿 |
| `app/lib/data/versioning.py` | `SourceRevision` 四维指纹（content/schema/metadata/crs）+ `compare_revisions`→`ChangeClass`（UNKNOWN 保守语义）+ `build_version_chain`；**生产依赖仅 1 处**（data_lifecycle/service.py:32-37）——DS5 可安全扩展 |
| `local_first.py` | 硬编码链 gd_poi → OSM GPKG → 在线 API；中文类别表派生自 `osm_category_map.py` |
| `temporal/profiler.py` | 时间字段识别/粒度/缺口检测已落地（DS6 复用） |
| `gov_data_adapter.py:26` | `PLATFORMS` 硬编码 3 政务平台（DS1 迁移目标） |
| `tests/data/conftest.py` | 仅 lakehouse store-root 重置 fixture；**零 mock/HTTP 拦截基建**（A11 确认为真） |

## 5. A7 七处阈值点位核实（DS0 收敛清单）

| # | 位置 | 现值 | 用途 |
|---|---|---|---|
| 1 | `app/services/mapspec_source.py:25` | `INLINE_FEATURE_LIMIT = 5000` | 会话 ref 内联载体门 |
| 2 | `app/services/data_fabric/adapters/postgis_adapter.py:62` | `MVT_MAX_FEATURES_PER_TILE = 20_000` | MVT 每瓦片要素上限 |
| 3 | `app/api/routes/data_quality.py:40` | `_MAX_INLINE_FEATURES = 20000` | 质量路由内联门 |
| 4 | `app/services/data_profile/unified.py:22` | `_MAX_INLINE_FEATURES = 20000` | 画像内联门 |
| 5 | `app/services/mapspec/composite_builder.py:252` | `maxFeatures: 50000` | 合成 builder 阈值 |
| 6 | `app/services/mapspec/lifecycle_engine.py:1376,1405,2525` | `maxFeatures: 50000`（另 :1044-1046 有 5000 兜底） | 生命周期引擎阈值 |
| 7 | `app/services/publication_export.py:146,158` | `50000` 兜底 | 导出 maxFeatures 兜底 |

- **V11 未建任何阈值单点模块**（grep 无 `acquisition_limits`/cartography 侧单点）→ 按 §8.1.1「以先合入者为准」的适配方向：**本线建 `app/services/data_fabric/acquisition_limits.py`，七处（含 V11 主战场内的 5/6/7）只做「字面量 → import 单点」机械替换，不改任何逻辑**（与 §8.1.2 对 mapspec_source.py 的授权同纪律），PR 显式声明。

## 6. S1 债清扫描要点（全文见 `ads-v1-debt-scan.csv`）

最危险的三组重复：
1. **指纹/canonical-JSON 5 份实现**（lib/data/fingerprints、provenance/fingerprint、data_fabric/fingerprint、data_ingest/pipeline:73、lakehouse/_canonical_sha）——DS5 版本 pin 建立内容身份时以 `lib/data/fingerprints` 为口径，不新增第 6 份。
2. **血缘 6 处实现、2 个查询面且桥单向**——DS5 影响面分析走 `lineage_service` + `data_catalog/lineage_query` 既有面，不造第三条。
3. **数据集注册写路径 4+ 处**（lakehouse dataset_registry / fabric spatial_catalog / artifact_registry / UploadRecord）——DS1 注册表只做**源与能力的注册**，不并数据集写路径（那是 catalog 联邦职责）。

`versioning.py` 单点消费 → DS5 扩展安全；`registry.py` 12+ 调用点 → DS1 注册表接 `AdapterRegistry` 时**不改签名**，只新增注册路径。

## 7. 对任务书的修正记录

| 任务书预设 | 实际 | 处置 |
|---|---|---|
| `-e .` 可安装 | flat-layout 多包错误 | 只装 requirements（README 标准装法） |
| V11 ADR 占 0160–0169 | V11 实占 0150–0159 | 本线仍用 0170–0179（更保守，0160–0169 不动） |
| V11 迁移占 57–66 | V11 实占 56（已合入） | 本线仍用 70–79，67–69 缓冲扩大 |
| §8.1.1「V11 若先落地阈值单点则本线 import V11」 | V11 未落单点 | 本线建 `acquisition_limits.py`，V11 已合入文件做机械 import 替换 |
