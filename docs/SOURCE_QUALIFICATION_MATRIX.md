# SOURCE_QUALIFICATION_MATRIX — 数据源资格矩阵（G03 / ADR-0286）

> 状态：随 zcode/goal-geodata-readiness-fabric-20261107 PR · 基线 master 585812ee4
> 权威注册表：`app/services/data_fabric/registry.py`（单一事实源）
> 安全防线：`app/services/data_fabric/security.py`（SSRF/私网/IMDS/路径）
> 预算权威：`app/services/data_fabric/limits.py`（要素 ≥1000 / ≥16MiB / 页 ≥10 硬下限）

## 矩阵（registry 17 类 adapter + 2 上传车道）

| 源类型 | 探测 probe | 预览/取样 | 预算截断 | typed 不可用/错误 | 语义身份证 | 测试证据 |
|---|---|---|---|---|---|---|
| postgis | 连接探活 | preview(limit) | 分页+页数帽 | SourceUnreachableError | builder fabric 入口 | test_ads_local_adapters |
| ogc_api (Features) | landing/conformance | items(limit) | 要素/字节/页三帽 | typed | fabric 入口 | ogc_service_fixtures |
| wfs | GetCapabilities | GetFeature | 三帽 | typed | fabric 入口 | 同上 |
| wms / wmts | raster tile（非矢量查询源） | 瓦片 | 字节帽 | typed | raster 面 | 同上 |
| ogc_api_tiles | tileset 发现 | 代理瓦片 | 字节帽 | typed | raster 面 | GOAL-02 fixtures |
| ogc_api_coverages | coverage 发现 | 受控子集读 | 读取预算 | typed | raster 面 | 同上 |
| arcgis | REST 探活 | query | 三帽 | typed | fabric 入口 | ogc fixtures |
| stac | catalog/search | item 元数据 | 三帽 | typed | fabric 入口 | 同上 |
| geoparquet | 文件元数据 | 读取 | 字节帽 | typed | fabric 入口 | bench_geocompute_v3 |
| flatgeobuf | 文件元数据 | 读取 | 三帽 | typed | fabric 入口 | — |
| pmtiles | 元数据 only | 瓦片 | bounded gzip | typed | raster 面 | pmtiles 测试 |
| s3 / minio | 对象 seam | 元数据 only | — | typed | — | — |
| geopackage (gpkg) | 声明根内文件 | 读取 | 大小帽 | typed（缺文件=unavailable） | fabric 入口 | test_ads_local_adapters |
| local_file | 声明根内文件 | 读取 | 大小帽 | typed（缺文件≠空结果） | fabric 入口 | 同上 |
| cog / geotiff | COG 元数据 seam | 受控子集 | 256MB 读取预算 | typed | raster 面 | GOAL-02 |
| stats_api | 声明式映射 | 读取 | 三帽 | typed | — | test_ads8_matrix |
| **geojson_url（G03 新增）** | 受控 GET + FC 形状 | preview(limit)+truncated | 字节帽+要素帽+30s 短缓存（网络调用=1） | **假 Content-Type/非 FC → SourceBadResponseError** | fabric 入口 | test_g03_readiness_chain B3 + perf |
| generic（demo） | 仅显式 opt-in | — | — | — | — | 不得用于真实源（#767） |

## 上传车道（upload.py）

| 车道 | 格式 | 大小帽 | 守卫 | descriptor |
|---|---|---|---|---|
| 矢量→IngestPipeline | .geojson/.json/.shp(zip)/.kml/.gpkg/.csv(+经纬度列) | 50MB | GD-09 三重守卫 + zip 解压帽 + 路径清洗 | ✓（含 license/attribution/provenance） |
| 栅格→DEM 车道 | .tif/.tiff | 200MB | 垂直基准/单位声明纪律 | terrain 面 |
| CSV 无坐标列 | — | — | **诚实拒收**（提示支持列名），不造几何 | —（不在 CSV+坐标契约内） |

## SSRF/安全边界（全部 adapter 共用）

- 私网/回环/云元数据（含阿里腾讯 IMDS）/IPv6 映射地址一律拒；
- URL 经 `DataFabricSecurity.validate_url`；HTTP 会话经 `make_safe_session`；
- 重定向到内网拒；有限下载（三重帽 + 源级 `max_bytes_per_request`）；
- 本地文件仅声明根内（路径穿越拒）。

## 环境边界披露

- 本地验证矩阵：FakeSourceServer（RFC 2606 保留域 + 真实 SSRF 校验器保持
  激活）覆盖 HTTP 源；**无真实 PostGIS/外网 OGC 服务联机验证**（CI
  real_services 车道另有覆盖，本 PR 未运行）；
- 1M 要素档为本地生成 fixture（heavy），见 INGEST_PERFORMANCE.md。
