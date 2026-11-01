# DECISIONS —— GOAL-02

## D1 不新增 MapSpec schema 版本，桥接走既有 source 形状
- vector MVT tileset → 既有 `VectorTileMapSpecSource`（`tiles[]`+minzoom/maxzoom，schema v1.0 起存在，前端 applySource:1052 已渲染）。
- raster tileset → 既有 data_fabric source `url` 字符串模板 → addRasterTileSource 兜底（runtime.ts:1094）。
- 唯一兼容扩展：`mapspec_source.store_data` 增加 `type:"vector"` dict passthrough 分支（否则跌落 geojson inline）。理由：schema/前端两侧能力已存在，只差写入分类；避免 1.8 版本协商三重锁（types.generated.ts / carto-ir version / render-work corpus）连锁再生成本。
- 代理 URL 指向本仓后端路由（不直连第三方）：保 SSRF/缓存/ETag/鉴权/归属治理，MapLibre 永远只见同源 URL。

## D2 Tiles 显示路径封闭于 WebMercatorQuad
- 仅识别 WebMercatorQuad（含 `GoogleMapsCompatible` 等同族 URI）；其他 TileMatrixSet → typed 拒绝 + reason=unsupported_tile_matrix_set。绝不客户端猜投影/重投影。
- y 轴语义取自 TMS 定义的 matrixHeight/topLeftCorner 校验，而非假设。

## D3 瓦片代理复用 PMTiles XYZ 服务纪律
- `ogc_tiles_proxy.py` 以 `pmtiles_tile_service.py` 为模板：鉴权先于缓存、DfTileCache 键前缀 `ogc-tiles:`、ETag=sha256(body+fingerprint)[:16]、TileBuildCoalescer single-flight、空瓦片→204、单瓦片字节帽。
- 远端瓦片经 make_safe_session bounded_get（Content-Length 预检 + 流式帽），不经 GDAL。

## D4 Coverage 输出格式以服务声明为准（封闭子集 COVJSON+GeoTIFF）
- 服务 rangetype/links 未声明可用格式 → unknown → 拒绝（reason=capability_not_declared）。
- 预算三重：请求字节帽、输出像元帽（max cells）、解压比守卫（zip/deflate 炸弹）。
- 产出统一落 COG DataObject（publish_cog_data_object）+ session ref，数值验证走 rasterio 回读。

## D5 COG 物化是「窗口读」不是「下载」
- 物化=按 AOI/bands/overview 的窗口读集合（RemoteReadSession 请求/字节双预算）→ write_cog 落地 → CAS 发布。
- 绝不整幅 GET：远端资产 >预算 → reason=budget_exceeded 的 typed 拒绝（除非调用方显式 AOI 缩小）。
- 幂等：content_sha256 CAS 去重；同资产变化（ETag/指纹变化）→ 旧 ref 作废说明。
- 签名 URL（含 token/SAS query）经 redact_url 后才进 descriptor/回执；密钥仅 env 引用（复用 AuthDecl env_keys 纪律）。

## D6 协议名与工具名（查重后定稿）
- 协议 canonical：`ogc_api_tiles`（alias `ogc_tiles`）、`ogc_api_coverages`（alias `ogc_coverages`）。独立 AdapterSpec，不复用 ogc_api 别名（能力面完全不同）。
- 工具：`inspect_data_service`（≠既有 inspect_data_source：后者 inspect 已连接源，前者对任意 URL 做只读发现+资格判断）、`read_coverage_subset`、`materialize_remote_raster_asset`（≠既有 materialize_dataset：矢量 lane）。全仓 grep 无同名。

## D7 测试离线策略
- JSON 面（发现/瓦片代理/coverage COVJSON）：FakeSourceServer 扩展（requests seam，零 socket）。
- COG 字节面：tests 内起 127.0.0.1 线程 HTTP 文件服务器 + `allow_private=True`（内网部署合法语义），rasterio /vsicurl 真实走 HTTP range —— 全程离线（loopback），覆盖 SSRF 门在 allow_private 下的行为。
- 不引入第三方 mock 库（保持仓库自建 seam 设计）。

## D8 G01 资格双轨
- tile-only 源（含 OGC Tiles 矢量/栅格瓦片、WMS/WMTS）→ display_only：可上图，不可作 numeric raster 分析资格。
- coverage/COG 物化 ref → numeric_raster：可进栅格分析（G01 role_evidence 以 descriptor fingerprint 对账）。
- 该映射在新模块内实现（不动 data_qualification.py 核心判定，只提供 role 证据输入）。
