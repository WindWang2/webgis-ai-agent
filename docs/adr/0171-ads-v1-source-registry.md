# ADR-0171: ads-v1 声明式数据源注册表（config/sources）与四类新 adapter

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-data-supply/v1-master · DS1（自适应数据供给与接入 · 并行线 P1）
- 关联: ADR-0170（D1–D4 契约/fixture 层）、ADR-0053（adapter 注册表 append-only）、ADR-0094（诚实默认）、数据源接入指南（docs/dev/ads-v1-review-notes.md §4）

## 1. 背景（缺口 A1/A2）

加一个数据源必须改代码：政务平台硬编码在 `gov_data_adapter.PLATFORMS`（仅 3 个），
新源类型必须改 `data_fabric/registry.py`。能力（下推/配额/覆盖/许可/新鲜度/verified）
无处声明——检索与计划层（DS2/DS3）没有可消费的源元数据。

## 2. 决策一：注册表 = 配置文件（`config/sources/*.yaml`）

- **每源一文件**；pydantic schema（`SourceDefinition`）强校验；任何失败
  （YAML 语法 / 字段缺失 / 未知协议 / 重复 source_id / **明文凭据**）抛
  `SourceRegistryError` 且携带文件名——**禁止静默跳过**。
- **凭据零明文**：auth 走 `env_keys`（env 变量名），options 中形似凭据的键必须
  `${ENV_VAR}` 引用——DS9 安全复核的地基在本波已生效（有测试）。
- **热加载**：`reload_if_changed()` 按 mtime 快照检测增/改/删，无需重启。
- **能力声明 schema**：`pushdown{bbox,cql,aggregation,time_filter,projection,pagination}` /
  `quota{requests_per_minute,daily_max,max_bytes_per_request}` / `geographic_coverage` /
  `temporal_coverage` / `license` / `freshness` / `auth{type,env_keys}` / `verified`。
- **verified 纪律（任务书 §0.5）**：离线不可验证的新源一律 `verified: false`
  （本波 6 个公开源），检索层降权 + PR 披露；lint 列出全部未验证源。

## 3. 决策二：协议三层映射（不并注册表写路径）

- fabric 原生协议（postgis/ogc_api/wfs/wms/arcgis/stac/geoparquet/flatgeobuf/pmtiles/s3）
  → 经 `data_fabric.registry.resolve/build`（签名零改动，12+ 既有调用点不受影响）；
- ads-v1 新协议（local_file/geopackage/cog/stats_api）→ 四类新 adapter 注册进同一注册表；
- explorer 协议（gov_portal）→ 只声明不 fabric 化，由 `GovDataAdapter._platforms()`
  读注册表（`PLATFORMS` 保留为 deprecated 过渡兜底，DS9 确认零引用后删）。

债清扫描（ads-v1-debt-scan.csv）确认「数据集注册写路径」已有 4+ 处（lakehouse /
spatial_catalog / artifact_registry / UploadRecord）——本注册表只做**源与能力注册**，
数据集写路径统一走 `adapter.sync() → SpatialCatalogService`（既有路径），
不制造第 5 份。

## 4. 决策三：四类新 adapter（全部诚实语义）

| adapter | 数据面 | 诚实边界 |
|---|---|---|
| `geopackage_adapter` | 本地 GPKG（local_osm 四主题 / local_poi）；pyogrio `read_info` 零行读 schema；bbox 下推 + 投影 + 分页 | 文件缺失/未灌数 → `SourceUnreachableError`（绝不空结果冒充成功） |
| `local_file_adapter` | GeoJSON 目录导出 + sqlite（年鉴四表只读） | 未知格式 typed `InvalidQueryError`；表名仅取自发现清单 |
| `cog_adapter` | COG/GeoTIFF 头信息（bounds/CRS/分辨率/波段）；preview 为真实降采样像素 | `query` typed `QueryUnsupportedError`（栅格提取归物化路径） |
| `stats_api_adapter` | 公开统计 API（响应映射声明式：`path`/`items_path`/`lat_field`/`page_param`） | 响应映射不可猜——未声明数据集 typed 拒绝；HTTP 会话走 `make_safe_session`（SSRF 同防） |

路径安全：本地路径一律过 `security.resolve_safe_local_path`（realpath 规整 +
敏感目录拦截 + 声明根约束）。

## 5. 决策四：lint（`scripts/check_source_registry.py`）

加载校验 / 重复 ID / bbox 合法性 / 网络源配额缺失（warn）/ `fallbacks` 引用存在性 /
**能力声明与 adapter 实际能力比对**（声明过实 = error）/ 凭据卫生 / verified 披露。
本波已按 lint 结论下调 3 个 YAML 的 bbox/cql 声明（GBIF、Overpass、Planetary Computer
的 CQL/bbox 下推待 DS3 接入后再上调）——声明纪律由 lint 锁死。

## 6. 后果

- DS2 语义检索直接消费注册表的 `DatasetDecl` + `verified` + `freshness`（可检索文本齐备）；
- DS3 代价模型消费 `pushdown` 与 `quota`；DS4 降级链消费 `fallbacks`（引用存在性已由 lint 保证）；
- DS7 本地资产索引复用 geopackage/local_file adapter 与 `LOCAL_GEODATA_DIR` 解析规则；
- 迁移未用（本波无新表）。
