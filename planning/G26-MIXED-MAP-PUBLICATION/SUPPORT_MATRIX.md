# G26 SUPPORT_MATRIX — 图层/源 × 格式 × 画幅 支持矩阵

BASE `b8f2a6ecc`（G26 前）→ 本分支 HEAD。证据 = 测试名/文件（tests/ 下）。

## 1. 按图层/源类型

| 层/源类型 | live 视图 | 混合导出 PNG | 混合导出 SVG | 混合导出 PDF | 证据 |
|---|---|---|---|---|---|
| numeric_raster（单波段+colormap） | XYZ 瓦片（MapLibre） | ✅ 地理对齐窗口 | ✅ data-URI `<image>` + `<text>` | ✅ `<img>` 层叠（真实图像对象+可提取文本） | test_mixed_map_publication.py 全文件；test_publication_window.py |
| numeric_raster（RGB 3 波段） | XYZ 瓦片 | ✅（全通道有效才绘制） | ✅ | ✅ | test_publication_window.py::test_rgb_all_channels_valid_rule |
| 矢量 circle/line/polygon | MapLibre 矢量 | ✅（同一编译器） | ✅ `<text>` 可选取 | ✅ 文本层可提取 | 同上 + 既有 publication 链 |
| 标注（G16 覆写/避障） | live | ✅ | ✅ | ✅ | test_mixed_map_publication（西部站/东部站标注断言） |
| 图例（continuous/raster_rgb） | legends 面板 | ✅ | ✅ 色带/通道卡+notes 脚注 | ✅ | test_g26_legend_and_fonts.py |
| 烘焙 PNG raster（imageRef）/外部瓦片 | live | ❌ 不嵌入（许可不明）→ blocking 默认拒绝 / allow_partial 披露 | 同左 | 同左 | test_mixed_map_export_service.py::test_external_basemap_blocking_vs_partial |
| hillshade | live | ❌ 披露 hillshade_not_vectorizable（既有） | 同左 | 同左 | 既有链 + _unsupported_layer_report |
| wms/wmts/pmtiles/data_fabric 源 | live/受限 | ❌ 披露省略（既有语义） | 同左 | 同左 | 既有 raster_layer_unavailable_vector_pdf |
| raster-dem 源 | live | ❌（G3D 链） | 同左 | 同左 | 既有边界 |

## 2. 按画幅形态

| 形态 | PNG | SVG | PDF | 证据 |
|---|---|---|---|---|
| 单幅整图 | ✅ | ✅ | ✅ | E2E 服务测试 |
| 显式多帧（layout.frames，≤50） | 服务端单幅合成 | 单幅（帧概念属出版页） | ✅ 每帧一页（嵌入各帧窗口） | TestMultiFramePlacements |
| 帧与栅格无交集 | ✅ | ✅ | ✅ no_overlap 披露不嵌图 | test_frames_disjoint_extent_disclosed_per_frame |
| G15 产品页（frame_product） | —（产品页是 PDF 版面） | 单文件 SVG 面 | ✅ 槽内分层 `<img>`（html_body 面） | _compile_product_page 接线（回归 test_frame_product_publication.py） |
| atlas（category/feature/time） | — | — | ✅（既有）+ 栅格嵌入 | compile_publication_page 统一接缝 |

## 3. CRS / 几何语义（must-have 范围）

| 源 CRS | 支持 | 行为 |
|---|---|---|
| EPSG:4326 | ✅ | 直接窗口读 |
| EPSG:3857 | ✅ | 同网格恒等/重投影 |
| 其它投影 CRS（UTM 等） | ✅ | rasterio.warp 重投影到画布 3857 仿射网格（A05）；不可重投影 → typed INVALID_CRS |
| 无 CRS | ⚠️ 与瓦片服务同语义（视为 3857 + 披露 no_crs_assumed_3857） | notes 披露 |
| 旋转 affine（b/d≠0） | ✅ 真实窗口 transform 重投影（旋转项保留；review P1-1 修复）+ 角点足迹包络 + coverage 透明空角 + 披露 | `test_rotated_hot_pixel_lands_at_projected_position`（热像元 ±3px + 空角透明） |
| north-up | ✅ | 本轮 must-have |
| pitch/terrain 3D | ❌ | 既有 svg_3d_perspective_degrade 披露（诚实拒绝） |

## 4. 分辨率/预算

| 项 | 值 | 证据 |
|---|---|---|
| 嵌入窗口长边 | ≤2048（MAX_PUBLICATION_WINDOW_SIDE；truncated 披露「提高 DPI 不增加遥感细节」） | test_max_side_truncation_disclosed |
| 单层解码像元 | ≤22M（GDAL out_shape 降采样约束） | MAX_DECODE_PIXELS |
| 单张嵌入 base64 | ≤24MB（fetcher 拒绝） | test_image_data_uri_allowed_with_cap |
| 页矢量 SVG（剥离 data URI 后） | ≤8MB/页、32MB 累积（既有） | 既有预算 + G26 剥离核算 |
| PDF 成品 | ≤50MB（worker 既有） | publication_render_worker |
| PDF DPI | 72–600 钳制，生效值入 manifest（A16） | manifest.renderer.effective_dpi |

## 5. 不做（诚实边界）

- 外部底图瓦片**不嵌入**（许可不明；blocking/allow_partial 显式策略）。
- 不复制系统字体进仓库（vendored CJK subset 既有；fetcher 修复后真实内嵌）。
- GPKG/GeoJSON 数据交付（G18 域）；浏览器视窗交互（G25 域）；尺度决策（G27 域）。
- 不重写全图模板/不启用任意网络读取（fetcher 仅放行 pipeline 生成的 data URI）。
