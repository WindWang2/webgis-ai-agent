# G26 PLAN — 矢量—栅格混合地图的统一出版链

工作包状态机：not_started / in_progress / implemented / verified。

## 设计总纲（一条编译路径，两个消费面）

```
MapSpec(冻结修订) ──┬─ 矢量层/标注/图例/整饰（既有 compile_mapspec_to_svg_detailed）
                    ├─ 数值栅格层（新：publication raster adapter）
                    │    layer.raster_style + sources[sid].refId
                    │    → resolve_source（owner 校验/冻结修订）
                    │    → window read @ frame extent + 冻结 stretch（tile 同语义）
                    │    → RGBA PNG（≤2048 边）+ data URI + 地理放置矩形
                    │    → 按图层顺序发射 <image>（SVG/PDF 同一元素）
                    └─ 输出：SVG（自包含 data URI）→ PNG（cairosvg/栅格化）→ PDF（WeasyPrint）
```

- **SVG 是中间单一事实**：嵌入 data URI 的 `<image>` 进 SVG → PNG/PDF 都从同一
  SVG 派生（PDF 经 WeasyPrint HTML 内嵌；PNG 经服务端 SVG 栅格化——与前端 canvas
  并行的服务器编译面）。
- **矢量文本可选取性**：SVG 中文本层保持 `<text>`（既有行为），PDF 经 WeasyPrint
  文本层提取；栅格仅作为图像对象。
- **不建平行 export service**：全部落在既有 `mapspec_to_svg`（编译）+
  `publication_export`（PDF 组装）+ 新 adapter 模块。

## P0 — 支持矩阵与现状探针 [in_progress]

- [x] 矩阵基线（RECON §1.2）：SVG 静默丢层、PDF 词表漏 numeric_raster、
      snapshot 居中不对齐、raster_rgb 无图例。
- [ ] `tests/gis_harness/test_g26_support_matrix.py`：生成 fixture（本地
      GeoTIFF 单波段/3 波段 + 矢量边界 + 标注 + 图例），经**当前真实路径**
      （compile_mapspec_to_svg_detailed + render_publication_pdf + export_snapshot）
      各导出一次，断言：栅格层缺席（SVG 无 image、PDF 无图像对象）+ 披露现状。
- [ ] SUPPORT_MATRIX.md：layer/source/component × live/PNG/SVG/PDF ×
      single/multiframe 的支持/拒绝/降级，must-have 范围（north-up、EPSG:4326
      源或可重投影 CRS、png/svg/pdf）。

## P1 — 一致冻结快照 [not_started]

- 新 `frozen_publication_document(session_id, expected_revision)`（复用
  `frozen_layers` 的 CAS/单读/复检模式，但冻结**整份文档** + 逐层解析栅格绑定
  + style_id/dataRevision 一致性）。冻结失败 → typed 拒绝，不拼接两个修订。
- 离线产物只携带 ref 解析后的嵌入 bytes；不写 session URL/token。

## P2 — 有界栅格资源适配 [not_started]

- 新 `app/services/raster_authoring/publication_window.py`：
  `render_window(source, spec, view_bounds, out_w, out_h) -> {png_bytes,
  geo_bounds, width, height, all_nodata, constant, truncated, grid, notes}`。
  - 语义与 `render_raster_tile` 同源：源 CRS 窗口读、decimated out_shape、
    冻结全局 stretch、全通道有效才绘制、masked→alpha、opacity 单点应用。
  - 目标网格 = 导出画布像素（Web Mercator 投影链路同 SVG 编译器 proj_x/proj_y）；
    跨 CRS 用 rasterio.warp（reproject 到目标网格）或明确拒绝（不可重投影 CRS）。
  - 相交裁剪：窗口=画布范围∩源范围，画布内放置矩形=源范围的投影矩形；
    不相交 → 空（透明）+ `raster_layer_no_overlap` 披露，绝不取边缘像元。
  - 像素上界：MAX_RASTER_SNAPSHOT_SIDE（2048）+ 解码字节预算（decompression
    bomb 防线：窗口像元数 × 波段数 × dtype 上限）。
- 内容寻址缓存：(style_id, data_revision, view_bounds, out_size) → bytes，
  多帧/多槽复用同一窗口不重复解码。

## P3 — PublicationIR 统一合成 [not_started]

- `mapspec_to_svg` raster 分支重写：
  - numeric_raster 源 + layer.raster_style + **注入的 resolver**（导出期由
    路由/服务注入 async→sync 预解析结果，编译器保持纯同步——预解析在冻结
    阶段一次完成，按 layer_id 传入已渲染的 {data_uri, geo_bounds, ...}）。
  - 发射 `<image x y width height href="data:image/png;base64,...">`，xywh 由
    geo_bounds 经 proj_x/proj_y（与矢量同投影）计算；保留在既有 elements_svg
    的图层顺序位置（raster 层按 MapSpec layers 序）。
  - 层不可解析（无 resolver 数据）→ `layer_ref_unavailable` +
    `numeric_raster_offline_snapshot_required`（保留既有披露码，新增显式
    detail），绝不静默。
- PDF：`_deny_url_fetcher` 放行 `data:image/png;base64`（长度上限 24MB/张、
  只信 pipeline 生成 bytes）；WeasyPrint `<image data:>` → PDF 图像对象（A03
  实证；若内嵌 SVG image 不支持 → HTML 面组合，同 G15 产品页先例）。
- PNG：服务端 SVG→PNG 栅格化（cairosvg 若在依赖中；否则 Pillow+svglib 或
  明确拒绝并披露，绝不静默退化为 raster-only snapshot）。
- 多帧/产品页：槽 SVG 命名空间化已有；嵌入 data URI 不受影响；每槽独立窗口。

## P4 — 图例、比例尺、中文、归属 [not_started]

- `derive_legend_items` 增加 `raster_rgb` 分支：R/G/B 通道条目（band 标签 +
  stretch 端点 + unit_status），不造连续色标；constant/all-nodata 说明行。
- continuous 分支透传 approximate/unit notes 到图例卡脚注。
- 比例尺/标题/中文：既有 chrome 已有（scale_bar/north_arrow/title），验证
  CJK 在 PDF 的内嵌字体路径（已有 vendored subset）+ 记录缺字。
- attribution：既有组件 + preflight 警告保留。

## P5 — 导出入口与 Pi 接线 [not_started]

- 后端统一编译服务 `mixed_publication_export`（冻结 P1 → 窗口渲染 P2 → SVG
  P3 → png/pdf 分派 + manifest P6）。
- HTTP：扩展现有 `/api/v1/map/export/pdf`（已带 atlas/page_profile）→ 混合图
  直接支持；新增 `/api/v1/map/export/image`（PNG/SVG，服务端编译面）或复用
  既有路由形态（待 investigator 报告定夺，避免无人访问的新 API）。
- Pi：新工具 `webgis_map_export`（或扩展现有 export 工具族——按工具注册纪律
  新增，raster-only 工具保留不动），冻结修订 + 完整地图语义 + omitted_layers
  披露 + partial 策略参数。
- UI：前端 exporter 对支持的混合图走服务端编译路径（PNG/SVG/PDF 单源）；
  保留前端 canvas 作为回退（披露）。**默认不排除矢量层**；请求完整图而有
  遗漏 → 不能标 complete（blocking 默认 / partial 显式 opt-in）。
- 复用既有：owner 验权、artifact 下载、manifest/checksum、atomic write、取消。

## P6 — 精确可审计产物 [not_started]

- export manifest sidecar：spec/data/style/renderer 版本、包含/省略层与帧、
  像素网格、stretch 近似性、嵌入状态、checksum（sha256）。复用 export_lineage/
  product_manifest 形态。
- PDF 核验：页数/页面尺寸/pypdf 文本提取/图像对象计数/已知地理位置像素值。
- SVG 核验：唯一 id、合法 clip、data URI 内嵌、`<text>` 存在。
- PNG 核验：尺寸/alpha 通道/图层叠加顺序探针（指定像素颜色容差）。

## P7 — 安全、性能与回退 [not_started]

- fetcher 只放行 pipeline 生成的 data URI（拒绝 http/file/blob）；像素/内存
  预算 typed 失败（A22）；取消不留半文件（复用 atomic write）。
- 旧路径回归：vector-only PDF、raster-only snapshot、atlas、frame product
  golden 测试不漂移（嵌入 absent 时输出字节不变）。
- 性能：窗口读预算 + 缓存命中证据（基线/样本/重复次数）。

## 验收映射（A01–A28 → 测试）

见 ACCEPTANCE.md（实现期逐项建立）。

## 资源纪律

- pytest 单 worker；无浏览器 E2E 前置阻塞本地验证；WeasyPrint 测试串行（进程锁）。
- 不碰 Pi vendor；不整文件格式化共享文件；小型接入补丁。
