# 协议能力矩阵（M0 实测产出；三态：✅ supported / ❌ unsupported / ❔ unknown-declared-absent）

基线 `6c5e71c2c`。行=协议；「 Tiles」列指 OGC API Tiles 端点族（tilesets/{z}/{x}/{y}）；「物化」列指受控下载为本地资产/ref 的能力。

## 既有协议（实测，全部有代码证据）

| 协议 | 发现 | 查询 | Tiles | Coverage | 物化 | 备注（file:line） |
|---|---|---|---|---|---|---|
| OGC API Features | ✅ /collections | ✅ items+bbox+CQL2 门控 | ❌（无端点消费） | ❌ | ✅ 矢量 lane | ogc_api_adapter.py:43；CRS 硬编码 CRS84 :357 |
| WFS 2.0 | ✅ GetCapabilities | ✅ bbox/FES | ❌ | ❌ | ✅ 矢量 | wfs_adapter.py:100 |
| WMS | ✅ Capabilities XML | metadata-only | ❌（仅 URL 模板 :412-441） | ❌ | ❌ | wms_wmts_adapter.py:186 |
| WMTS | ✅（同 WMS 条目） | metadata-only | ❌（无 TileMatrixSet 解析） | ❌ | ❌ | registry.py:152 别名 |
| STAC | ✅ /search | ✅ bbox/datetime/CQL2 | ❌ | ❌ | ❌（asset 断链） | stac_adapter.py:544；registry.py:162 |
| COG | ✅ describe | preview ≤16×16 | ❌ | ❌ | ❌（悬空指向 :163-167） | cog_adapter.py:60 |
| PMTiles | ✅ 归档元数据 | read_tile_bytes | ✅ 后端 XYZ 代理 | ❌ | ❌ | pmtiles_tile_service.py:105 |
| PostGIS | ✅ | ✅ 全下推+MVT | ✅ server MVT | ❌ | ✅ 矢量 | tile_service.py:110 |
| ArcGIS | ✅ | ✅ | ❌ | ❌ | ✅ 矢量 | arcgis_adapter.py:73 |

## 本任务新增目标子集（诚实封闭枚举，不宣称完整协议）

### OGC API - Tiles（新 adapter `ogc_api_tiles`）
| 能力 | 支持子集 | 依据 |
|---|---|---|
| 发现 | landing(❔可选)/conformance/collections/{id}/tiles、顶层 /tiles | OGC API - Tiles Part 1&2 已实现服务器通行布局；conformance 声明三态判定 |
| tileset descriptor | tileMatrixSetDefinition URI、crs、bbox、媒体类型（vector/raster 封闭枚举）、链接 | tileset JSON |
| TileMatrixSet | **仅 WebMercatorQuad（含 URI 别名识别）显示路径**；其他 TMS → typed 拒绝 + reason | 不伪造投影；GoogleMapsCompatible 同族识别 |
| 瓦片获取 | z/x/y 代理（受 SSRF/字节帽/缓存/ETag 治理） | 仅 200/304/204 语义；404=空瓦片 |
| y 轴 | WebMercatorQuad top-left（=XYZ 语义），服务 TMS 定义内 matrixHeight 校验 | 不猜 |
| 拒绝面 | 范围外 z/x/y、未声明媒体类型、非 200/204/304、>单瓦片字节帽、认证缺失（401/403） | reason code 封闭 |

### OGC API - Coverages（新 adapter `ogc_api_coverages`）
| 能力 | 支持子集 | 依据 |
|---|---|---|
| 发现 | collections（itemType=coverage 识别）、coverage domainset/rangetype | Part 1 布局 |
| 读取 | subset(bbox/时间/波段)、scale-factor/size 预算、声明格式（COVJSON/GeoTIFF 封闭子集） | 服务 rangetype/links 声明，未声明即 unknown→拒绝 |
| 预算 | max cells / max bytes / 超预算 typed reason | 不下载国家尺度 |
| 产出 | bounded raster → COG DataObject + session ref（fingerprint/provenance） | 复用 raster_object.publish |

### STAC→COG 物化（新 cog_materialization 模块 + 工具）
| 能力 | 支持子集 |
|---|---|
| discover | 复用 stac_adapter search；枚举 item assets（type=image/tiff;application=geotiff 或 COG 结构探测） |
| select | asset key 显式选择；bands/overview/AOI/time 显式参数 |
| preview/validate | validate_remote_href + rasterio open（头信息）+ cog_structure_ok advisory |
| materialize | 窗口读（RemoteReadSession 预算）→ 本地 COG → publish_cog_data_object（CAS 幂等）→ ref |
| 治理 | 取消令牌、变化作废（fingerprint 不匹配）、签名 URL 脱敏（redact_url）、密钥不持久化 |

## 明确不做（诚实边界）
- 不实现 WMTS REST/KVP GetTile（WMS/WMTS 维持 metadata-only，不宣称统一栅格源）。
- 不做非 WebMercatorQuad TileMatrixSet 的显示重投影（显式拒绝 + reason）。
- 不做 Coverage 的全部格式（如 GeoPackage coverage tiles）与 range 子集代数。
- 不重写 #1868 准备/绑定工作台核心；不碰 03 时间轴/04 符号律/05 恢复内核。
