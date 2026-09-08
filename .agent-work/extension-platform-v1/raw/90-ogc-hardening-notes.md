# Wave 8/9 — OGC/STAC 集成加固笔记（branch: feat/gis-extension-platform-v1）

日期：2026-09-08。改动全部为增量（additive）、外科手术式，未提交（留在工作树）。
未触碰 `app/extensions_platform/`（另一 workstream 所有；该目录在我工作期间有并行
改动，与本 wave 无关）。

## A. WMS/WMTS describe() CRS 诚实性

问题：`describe()` 此前无条件伪造 `srs="EPSG:3857"` 与全球 bbox
`[-180, -90, 180, 90]`，违反项目红线「never silently assume CRS」。

修复（app/services/data_fabric/adapters/wms_wmts_adapter.py）：

- 新增模块级解析助手（:25 `_local_name`、:30 `_ancestor_layers`、:40
  `_crs_at_level`、:52 `_bbox_at_level`、:98 `_is_plausible_geographic_bbox`、
  :110 `_extract_layer_crs_and_bbox`）：
  - CRS：Layer 链上 `<CRS>`（WMS 1.3.0）/`<SRS>`（WMS 1.1.x，支持空白/逗号
    分隔列表）元素；`EPSG:XXXX`、`urn:ogc:def:crs:EPSG::NNNN` 等 URN 形式经
    `data_fabric.metadata.normalize_crs` 规范化（复用，未另写）。
  - bbox：`EX_GeographicBoundingBox`（1.3.0 子元素式）、`LatLonBoundingBox`
    （1.1.x minx/miny/maxx/maxy 属性）、`WGS84BoundingBox`（WMTS
    LowerCorner/UpperCorner）→ 统一 `[w, s, e, n]`；按 WMS 继承语义取祖先
    Layer 链上最近一级声明。
  - 语义校验：minx>maxx（反经线跨越类）或超出 WGS84 范围 → 拒绝该声明并记
    note，不猜。
- `describe()`（:271）重写 + 新增 `_get_capabilities_tree()`（:255，走
  `bounded_get` 有界下载 + `parse_safe_xml` defusedxml 解析；失败仅记
  note 诚实降级，绝不抛给调用方）：
  - `srs` = 规范化后的已声明 CRS（优先 EPSG:4326/CRS84，因地理 bbox 恒为
    WGS84）；无声明 → `None`。`crs`/`bbox` 同步诚实（`crs` 保持此前的 None
    语义，见下）。
  - 返回形状向后兼容：原有 keys 全保留；新增 additive metadata keys：
    `advertised_crs`（原始声明列表）、`normalized_crs`、`bbox_crs`
    （="EPSG:4326" 当 bbox 有值）、`notes`（未知原因说明列表）、
    `describe_error`（capabilities 缺失/层未找到，与 WFS adapter 同约定）。
    `endpoint_url`/`tile_url_template` 现经 `redact_url` 去凭证（对齐 WFS）。
- 消费方核查：DataFabricManager（manager.py:282 `normalize_crs(srs or crs)`、
  :307 `bbox_json`）对 None 均有诚实语义；query/capabilities.py 不读 srs/bbox；
  契约测试 verify_adapter_contract 只断言 id/source_type。无需改名任何 key。

## B. GDAL /vsicurl href 的 SSRF 门禁

问题：geo_raster 远端读有 timeout/字节预算但无 SSRF 校验；GDAL 会在内部把
http(s) href 转成 `/vsicurl` 读。

修复（复用 data_fabric.security，未另写 SSRF 判定）：

- app/lib/geo_raster/env.py:28 新增模块级 `validate_remote_href(uri)`：
  - 仅 http(s)（含 `/vsicurl/http(s)://…` 内嵌目标）过
    `DataFabricSecurity.validate_url`；本地路径与 `/vsi*`、`s3` 等原样放行；
  - 校验失败抛 `DataFabricSecurityError`（`ValueError` 子类）；通过则输入
    原样返回（绝不改写调用方 href）；
  - 模块级函数 = 可注入 seam（monkeypatch 本模块或 reader 模块绑定名）。
- app/lib/geo_raster/reader.py:26 顶层导入 + :95 在 `RasterReader.open()`
  中 `rasterio.open(uri)` 之前调用（RasterReader 是「唯一 sanctioned 打开
  入口」，由此覆盖 STAC asset、RemoteRasterSource、时间序列引擎等全部远端
  打开路径）。STAC href 的打开即经此门禁（见 C 的双保险显式调用）。

## C. STAC 客户端

- app/core/config.py:53 新增 `STAC_API_URL: str = "https://earth-search.aws.element84.com/v1"`。
- app/services/rs/stac_client.py：
  - :17 `_catalog_url()`：settings.STAC_API_URL 可配置，缺省回退原常量
    （:14 `_STAC_CATALOG_URL` 保留为回退默认）；`_get_catalog()`（:77）改用之。
  - :28 `_validate_asset_href()`：模块级 seam，委托 geo_raster 门禁；
    `_read_bands` 中 :230 在 `RasterReader.open(href)` 前显式校验（与 B 的
    reader 级门禁互为双保险）。被拒 href 落入既有 try/except →
    `{"error": ...}` in-band 语义，客户端结构不变。

## D. 测试（全部离线：字面公网 IP / 打桩 DNS / FakeSession，零网络）

新文件 tests/unit/extensions_platform/test_ogc_stac_hardening.py（21 用例）：

1. WMS 1.3.0（含 ns、URN CRS、继承）：describe 忠实上报 CRS 列表 + bbox；
2. WMS 1.1.1 `<SRS>` 空白分隔列表 + `LatLonBoundingBox` 属性；
3. 子 Layer 继承父 Layer 的 CRS/bbox（最近声明胜出）；
4. 无 CRS/bbox 的 capabilities → 无 EPSG:3857、srs/bbox 为 None、notes 必现；
5. 层不在 capabilities → 诚实 None + "not found" note；
6. GetCapabilities 拉取失败 → 诚实降级 + describe_error；
7. 畸形 bbox（反经线）→ 拒绝该声明 + note（父层合法声明仍可继承）；
8. describe 走 bounded_get/defusedxml；
9-15. /vsicurl 门禁：元数据 IP/回环/RFC1918/IPv6 回环/IPv4-mapped 全拒；
   localhost 与内嵌 /vsicurl 目标拒；公网字面 IP 过；非 http(s) 原样放行；
   打桩 DNS 下公网域名过、解析到私网拒；RasterReader.open 在 rasterio.open
   之前拒（mock GDAL）；本地路径过门禁进 rasterio.open（mock）；门禁 seam
   可注入；
16-18. STAC：STAC_API_URL 默认值/可配置；asset href 私网拒、公网与非 http 放行。

## E. 测试与 lint 结果

- 新增 21 用例全过：`tests/unit/extensions_platform/test_ogc_stac_hardening.py`
  → 21 passed。
- 既有相关测试全过（141 passed）：
  tests/test_618_backend_p3.py、tests/unit/test_ogc_adapters_753.py、
  test_data_fabric_adapters.py、test_data_fabric_registry.py、
  test_raster_env.py、test_stac_windowed_read.py、test_audit4_gis_fixes.py、
  test_terrain_compass.py、tests/unit/lib/test_geo_raster_v4/v5/v6.py。
- 全 lane：`python -m pytest tests/unit/extensions_platform/ -q --no-cov` →
  **2129 passed, 0 failed**（基线 2108 + 新增 21；连跑 3 次稳定；再叠
  test_data_fabric_stac_truthfulness_430.py + test_data_fabric_contract.py
  共 2143 passed ×3）。
- ruff：所触 6 个文件 `ruff check` 全部 "All checks passed!"（HEAD 版本同为
  0 错误，无新增）。修掉新测试文件自身的 2 个 F541。
- 插曲：期间一次 `test_cli.py::test_scaffold_help_does_not_import_heavy_modules`
  假失败——该文件属并行 workstream（会话中途才出现在工作树），单跑/带我的
  文件均无法稳定复现，当前树连跑全绿；与本次改动无关（经 git stash 对照验证）。

## 刻意不做（out of scope）

- WCS、OGC API Tiles、WMTS TileMatrixSet→SupportedCRS 解析（WMTS 层目前
  诚实返回未知 CRS + note，而非解析 tile matrix set）；
- `query()` 的 getmap/tile 示例 URL 中硬编码 `CRS=EPSG:3857`（示例模板，
  未改；如需可由 advertised CRS 驱动，属后续行为变更）；
- `preview()` 仍返回世界 bbox [-180,-90,180,90]（仅 describe 在本 wave
  范围内；preview 的诚实化建议单开小改）；
- geo_analysis（raster_grid/raster_ops/windowed/profiler 等）中直接
  `rasterio.open` 的本地路径调用点未加门禁（它们走本地/project 文件语义；
  统一收口到 RasterReader 属 refactor，越出 additive 范围）；
- connect-time IP pinning（DNS rebinding 残余窗口）——沿用 ADR-0053 既有
  follow-up 结论，security.py 注释已记录。
