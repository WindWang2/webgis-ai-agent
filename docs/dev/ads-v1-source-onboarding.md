# 数据源接入指南（ads-v1 · ADR-0171/0177 · DS9 定稿）

> 加一个数据源 = 加一个 YAML 文件 +（可选）真实 adapter。零 Python 改动路径
> 由端到端测试锁死（`tests/data/test_ads_source_registry_demo.py`）。

## 1. 声明一个新源

新建 `config/sources/<source_id>.yaml`：

```yaml
source_id: my_new_source          # ^[a-z][a-z0-9_]{2,63}$，全局唯一
name: 我的数据源
protocol: ogc_api                 # 见下方协议表
endpoint: https://example.org/ogc
description: 一句话说明数据内容与范围
operations: [search, metadata]    # 源支持的操作（声明用）
pushdown: {bbox: true, cql: false, aggregation: false, time_filter: true,
           projection: true, pagination: true}   # 声明须与 adapter 实际一致（lint 校验）
quota: {requests_per_minute: 60, daily_max: 10000}  # 网络源必填（lint 警告）
geographic_coverage: [CN]
temporal_coverage: {start: "2015-01-01"}
license: cc-by-4.0
freshness: {update_frequency: monthly}
auth: {type: none}                # 有凭据时：{type: api_key, env_keys: [MY_KEY_ENV]} —— 明文即报错
verified: false                   # 离线不可验证 → false（检索降权 + PR 披露）；复验后改 true
datasets:                         # 可选：内联数据集声明（成为可检索卡片）
  - {dataset_id: my_layer, title: 我的图层, description: ……, data_type: vector,
     granularity: county, license: cc-by-4.0}
fallbacks:                        # 可选：条件降级链（DS4）
  - {source_id: other_source, on: [timeout, 5xx, 429]}
priority: 100                     # 可选：本地链参与序（越小越先）
```

## 2. 协议 → adapter 映射

| protocol | adapter | 说明 |
|---|---|---|
| postgis / ogc_api / wfs / wms / arcgis / stac / geoparquet / flatgeobuf / pmtiles / s3 | data_fabric 既有 adapter | fabric 原生协议 |
| geopackage | `adapters/geopackage_adapter.py` | 本地 GPKG（bbox 下推；缺失 = typed unavailable） |
| local_file | `adapters/local_file_adapter.py` | GeoJSON / 只读 sqlite |
| cog | `adapters/cog_adapter.py` | COG 头信息（矢量查询 typed 不支持） |
| stats_api | `adapters/stats_api_adapter.py` | 声明式响应映射（options.datasets） |
| gov_portal | explorer GovDataAdapter | 政务门户检索主题（不 fabric 化） |

## 3. 校验与登记

```bash
python scripts/check_source_registry.py   # lint：重复 ID/bbox/配额/凭据/能力声明比对 —— 0 错误才算过
python manage.py sources-scan             # 本地资产清单（available/unavailable + 灌数指引）
python scripts/ads_dump_contracts.py      # 仅在改 contracts.py 后重 dump schema
```

- 重复 source_id / 未知协议 / **明文凭据** → `SourceRegistryError`（响亮失败）；
- 凭据一律 `${ENV_VAR}` 引用，值放部署环境；
- 热加载：改 YAML 后无需重启（`reload_if_changed()` mtime 驱动）。

## 4. 本地数据灌数（local_* 三库）

```bash
python manage.py osm-ingest        # china-*.osm.pbf → <LOCAL_GEODATA_DIR>/osm_gpkg/*.gpkg
python manage.py gd-poi-ingest     # 高德 POI → <LOCAL_GEODATA_DIR>/gd_pois.gpkg
python manage.py yearbook-ingest   # 县域年鉴 xlsx → <LOCAL_GEODATA_DIR>/yearbook/yearbook.sqlite
```

未灌数时：adapter 报 typed `SourceUnavailable`、sources-scan 显式
unavailable——绝不伪造空结果。

## 5. 离线测试守则

- 新 adapter 的测试一律走 `tests/data/fabric_fixtures.py`（FakeSourceServer
  五协议 + PostGIS canned pool），canned 载荷必须过真 adapter round-trip；
- 离线门禁：`ADS_FORCE_OFFLINE=1 pytest tests/data …`（socket 阻断器证明
  不靠外网）；
- 严禁任何形式的伪造数据（registry.py:78-86 原则；geojson 别名 #767 前车之鉴）。
