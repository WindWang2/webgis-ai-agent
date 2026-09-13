# ADR-0177: ads-v1 本地数据资产索引（统一清单 + 归一 meta + sources-scan）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-data-supply/v1-master · DS7（自适应数据供给与接入 · 并行线 P1）
- 关联: ADR-0171（源注册表 local_* 声明与 priority）、ADR-0172（检索卡片合并）、ADR-0174（本地优先链注册表化）、local_first/local_osm/local_poi/local_yearbook（ owning 模块，只读消费）

## 1. 背景（缺口 A9）

三个本地库各自为政：`local_osm` 目录 glob + `local_poi` 自由格式 meta.json
边车 + `local_yearbook` 约定路径——没有全局清单，LOCAL_GEODATA_DIR（缺省空）
无统一入口，「本地有什么」不可检索。

## 2. 决策一：归一 meta schema（`local_index.META_SCHEMA`）

三库边车/元信息统一到 8 键：`crs / bbox / rows / temporal_start /
temporal_end / fields / source / ingested_at`。
- `normalize_meta()` 兼容既有 sidecar 键名（srs→crs、total_rows→rows、
  generated_at→ingested_at）；
- **补齐 = 用 owning 模块的已知事实填充**（THEME_SPECS、yearbook 时间范围、
  poi CRS/字段表），未知值诚实缺位（进 `meta_missing` 报告）——不伪造。

## 3. 决策二：一次扫描 → 清单（`scan_local_assets`）

- **available**：`LocalAsset{library, asset_id, layers, meta, path}`——层清单
  来自 pyogrio.list_layers / sqlite_master（真实读取），行数来自
  pyogrio.read_info（零行读）；
- **unavailable**：`{library, reason, ingest_hint}`——**显式 unavailable +
  可执行的灌数指引**（osm-ingest / gd-poi-ingest / yearbook-ingest），
  禁止伪造空结果；
- root 注入缝：扫描支持注入 LOCAL_GEODATA_DIR 替身（tests），缺省 settings。

## 4. 决策三：检索合并与代价排序（DS2/DS3 消费）

`assets_to_cards()` → DatasetCard（`local=True`、`verified=True`）并入
DS2 索引——本地资产 `cost=1.0`（DS3 代价模型）在相关性同权时自然胜出；
**相关性不足绝不强行置顶**（ranker 权重 rel.55 主导）。10 组本地/在线
混合检索用例锁行为（本地显式查询→本地第一；纯在线查询→在线优先）。

## 5. 决策四：manage.py `sources-scan`

新增子命令（任务书 DS7.3）：扫描 → 控制台报告（✓ 可用层与 meta 缺失项 /
✗ 不可用库 + 灌数指引）。全不可用时显式声明「不伪造空结果」。

## 6. 后果

- DS8 埋点把本地资产取数（geopackage/local_file adapter）计入 facts；
- DS9 死代码清理时，本地优先链的硬编码兜底可引用本索引等价性结论；
- 无新迁移（清单是扫描产物，不是持久状态——meta 才是持久事实，归 schema 表
  的需求随 DS8 facts 库一并评估）。
