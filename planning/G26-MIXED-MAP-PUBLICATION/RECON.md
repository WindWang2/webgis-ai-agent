# G26 RECON — 矢量—栅格混合地图的统一出版链

BASE_SHA: `b8f2a6ecc777d0ae633202034b609a48a8d140d9` (origin/master, 2026-11-12 fetch)
Branch: `zcode/g26-mixed-map-publication-20261112T000000Z`
Worktree: `/home/kevin/projects/webgis/webgis-wt-g26-mixed-map-pub-20261112T000000Z`

## 0. 环境与并行纪律

- 6 个兄弟方向 worktree 同机并行（g22/g23/g24/g25/g27 已在列）。重任务走宿主级锁，
  编译默认 `-j2`。本方向不改 Pi vendor（G22 owner）。
- 快照时开放 PR #2084/#2085/#2086 **已合并**（master 含其修复）；当前开放 PR：
  #2087 (G27)、#2088 (G24)、#2089 (G23) —— 兄弟方向，不碰。
- 已合并前置：G15 #2076（frame_product）、G16 #2077（labels）、G20 #2078（raster
  expression）、G18 #2080（data delivery）、#2081（Pi v1.5.1 原生发现）、#2034
  （Map Studio MapSpec 主编辑面 + 导出单源）。

## 1. 已核实的关键事实（file:line 均以 BASE_SHA 为准）

### 1.1 G20 提交后的 MapSpec 形状（本方向消费的输入）

`app/services/raster_authoring/service.py:68-99`：
- source 条目：`{type:"numeric_raster", schema_version:"raster_expression.v1",
  refId, bounds(WSEN EPSG:4326), crs, dataRevision, dataFingerprint, bandCount}`
  （`app/lib/cartography/mapspec_schema.py:255-273` `NumericRasterMapSpecSource`）
- layer 条目：`{id, source:<sid>, type:"raster", paint:{}, raster_style:<RasterExpressionSpec>,
  legend_spec:<raster_expression.v1 legend>}`（mapspec_schema.py:776 `raster_style`）
- **存储的 MapSpec 源没有 tiles** —— live 视图的 XYZ tile URL 由前端
  `frontend/lib/mapspec-compiler/compiler.ts:528-536` 运行时注入
  （`/api/...?style_id=...`，owner token 走 header）。

### 1.2 当前出版链对数值栅格的真实行为（P0 探针基线）

- **SVG 编译（`app/services/mapspec_to_svg.py:1410-1475`）**：`type:"raster"` 层读
  `src.tiles`；numeric_raster 源无 tiles → `tile_url_template=""` → **静默 continue，
  无诊断**（既不渲染也不披露——G26 必须先补诚实披露再实现嵌入）。带
  `style_id=`/`__SESSION_ID__` 的模板 URL → `layer_ref_unavailable` +
  `numeric_raster_offline_snapshot_required` 披露后跳过。
- **PDF（`app/services/publication_export.py:1178-1187`）**：source type
  `raster/data_fabric/wms/wmts/pmtiles` → `raster_layer_unavailable_vector_pdf`
  逐源披露后省略。注意：`numeric_raster` **不在该词表里**（G20 additive 后该
  披露循环漏掉它 → 依赖 SVG 层的静默跳过，同样无披露）。
- **G20 专用 snapshot（`app/services/raster_authoring/snapshot.py`）**：只渲染
  数值栅格自身（thumbnail 居中叠放，**无地理变换对齐**），PNG/SVG，PDF 拒绝
  （EXPORT_UNSUPPORTED），`omitted_layers` 披露矢量层。
- 图例：`derive_legend_items`（`app/lib/cartography/render_scene.py:217-326`）对
  G20 continuous（min/max/palette_colors）走 continuous 分支可用；`raster_rgb`
  类型落空 → 返回 None（RGB 复合层当前**无图例卡**）。

### 1.3 可复用的冻结/渲染资产（G26 不重建）

- **源解析**：`raster_authoring/source.py:301-433 resolve_source(session, ref,
  owner_token|trusted_dispatch)` → `RasterSource`（owner 校验 + scoped path +
  descriptor[crs/bounds/transform/nodata] + data_revision + 缓存 + ref 失效钩子）。
- **冻结表达**：`RasterExpressionSpec`（`app/schemas/raster_spec.py:490-605`）：
  mode single/rgb、bands、冻结 stretch（含 approximate/sample_count provenance）、
  colormap、opacity、resampling、classification、data_revision、style_id（canonical
  sha256，篡改检测 `finalize()`）。
- **渲染语义（live 一致性基准）**：`raster_tile_service.py:358+ render_raster_tile`：
  瓦片 bounds→源 CRS 窗口读、decimated out_shape、全通道有效才绘制（黑填充不冒充
  实测）、全局冻结 stretch（跨瓦片无 seam）、masked→alpha、opacity 只进服务端。
- **图例/说明**：`raster_authoring/legend.py build_legend/expression_statement`。
- **PDF 安全面**：`publication_export.py:1310-1318 _deny_url_fetcher`（只放行
  `data:font/ttf;base64`）+ `render_pdf_exclusive` 进程锁 + `_MAX_PAGE_SVG_BYTES`
  8MB/页、32MB 总预算 + CJK 内嵌字体（vendored subset）。
- **帧/页面模型**：`publication_ir.py`（PublicationIR/AtlasPolicy/FrameSlotIR）+
  `publication_export.compile_publication_page`（帧覆盖/时间窗/过滤页 → 逐页编译）。
- **图幅几何**：SVG 编译内 `proj_x/proj_y` 为 Web Mercator（`_merc_y`），export
  bounds 由 `export_bounds_for_frame` 冻结数学给出。

### 1.4 导出入口（用户/Agent 实际调用面）

待调查员报告补全（HTTP 路由 + Pi 工具清单）。已见：`publication_export.
render_publication_pdf` 被 publication 路由消费；`compile_mapspec_to_svg_detailed`
同时服务 PNG 栅格导出路径（待确认）与 PDF 页编译。

## 2. 架构决定（草案，待 investigator 报告确认后定稿）

1. **不建平行 export service**。在 SVG 编译层（mapspec_to_svg）加 numeric_raster
   渲染分支：导出期经 injectable resolver 把 `layer.raster_style + source.refId`
   解析为**窗口化、地理对齐的嵌入 PNG（data URI）**，按图层顺序发射 `<image>`，
   矢量/文本层保持可选取。
2. **资源适配器（P2）**：新模块 `app/services/raster_authoring/publication_window.py`
   （命名待定）：给定 frame extent（EPSG:4326 bounds）+ 目标像素网格 + 冻结
   RasterExpressionSpec → 复用 render_raster_tile 的窗口/重采样/冻结 stretch 语义，
   输出 RGBA PNG bytes + 真实地理范围（用于 SVG 放置矩形）。无 CRS → 与瓦片服务
   同语义（诚实披露）；不相交 → 空/透明 + 披露；像素预算上界（2048 边同 G20）。
3. **PDF 面**：`_deny_url_fetcher` 增加受控放行 `data:image/png;base64`（仅本
   pipeline 生成的 bytes，长度上限），WeasyPrint 内嵌 SVG `<image data:>` 原样进
   PDF 图像对象；文本层不受影响。
4. **图例面**：`derive_legend_items` 增加 `raster_rgb` 分支（通道说明，不造物理量
   色标）；continuous 分支已可用，但需把 approximate/unit notes 带到图例卡。
5. **披露面**：`raster_layer_unavailable_vector_pdf` 词表补 `numeric_raster`（仅在
   无法嵌入时）；成功嵌入的层不得再出现在 omitted 列表。

## 3. 风险与边界

- WeasyPrint 对内嵌 SVG `<image xlink:href="data:">` 的支持需实证（PDF 图像对象
  真实出现——A03）。备选：HTML `<img src="data:">` 面（产品页已走 HTML 组合）。
- 多帧/产品页多槽：每槽 extent 不同 → 每槽一次窗口读；槽 SVG 命名空间化已存在
  （`_namespace_slot_svg`），data URI 内嵌不受 id 冲突影响，但需内容寻址缓存防止
  重复编码同一窗口。
- 像素预算：A2/A1/A0 页在 4px/mm 下 3368×2384 起；嵌入 PNG 按 2048 边封顶 +
  data URI 压缩预算，与 _MAX_PAGE_SVG_BYTES 协调（可能需按嵌入图独立预算）。

## 4. DUPLICATION_MATRIX 摘要

| 能力 | 现状 | owner/PR | 本任务剩余增量 |
|---|---|---|---|
| 数值栅格表达/瓦片/live | ✅ 已合并 | G20 #2078 | 导出窗口渲染（无瓦片网格依赖） |
| 单幅 raster snapshot | ✅ raster-only+omitted_layers | G20 #2078 | 不动（保留 raster-only 专用出口） |
| PublicationIR/多帧/产品页/atlas | ✅ | C14/G15 #2076 | 图层级 raster 嵌入（IR 本身不加字段或最小加） |
| 矢量 PDF 链 | ✅ | R1/C14 等 | fetcher 放行 image data URI + 披露词表 |
| frame product 导出 | ✅ | G15 #2076 | 槽内 raster 嵌入复用同一编译器 |
| 数据交付 GPKG/GeoJSON | ✅ | G18 #2080 | 不做（不是地图出版） |
| basemap 外部瓦片 | 存在（SVG 外链 `<image>`） | 既有 | 保持外链 + 许可披露；不嵌入（无授权物化） |
