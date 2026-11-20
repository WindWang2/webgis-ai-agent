# feat(g26): 矢量—栅格混合地图的统一出版链（数值栅格嵌入 PublicationIR 编译面）

## 功能增量

把 G20 已提交的数值栅格表达（raster_expression.v1）嵌入既有出版编译链，
同一冻结 MapSpec/data/style revision 输出**矢量边界 + 数值栅格 + 标注 +
图例/标题/比例尺**的完整地图：

- **P2 窗口渲染器** `app/services/raster_authoring/publication_window.py`：
  按 frame extent/CRS/像素网格做地理对齐窗口渲染（与瓦片服务同语义：源 CRS
  窗口读、overview 降采样、冻结全局 stretch、全通道有效才绘制、masked→alpha、
  opacity 单点；2048 边 + 22M 解码像元预算；部分相交只渲染交集、完全不相交
  返回透明 + 披露，绝不夹边缘像元；GDAL nodata 回写容差）。
- **P1 冻结绑定** `publication_bindings.py`：一次读 MapSpec（CAS/复检）+
  逐层 owner 校验（resolve_source 三重校验）+ stat 身份 + style_id/dataRevision
  复核；离线产物只携带嵌入 bytes，无 session URL/token。
- **P3 统一合成**：`mapspec_to_svg` raster 分支按图层顺序发射 data-URI
  `<image>`（放置矩形与矢量投影闭包同口径，对画布为 EPSG:3857 仿射）；
  PDF 面因 WeasyPrint 70 丢弃含 `<image>` 的内嵌 SVG（实证）改为 HTML `<img>`
  分层（z 序 = 层序，文本层保持可提取）；多帧/产品页槽内同语义。
- **P4 图例/字体**：`derive_legend_items` 新增 raster_rgb 分支（通道条目、
  不造连续物理量色标）+ notes 脚注（constant/all-nodata/approximate）；
  `_deny_url_fetcher` 修复 WeasyPrint 70 下 vendored CJK 字体内嵌静默失效
  （dict → URLFetcherResponse）并受控放行 data:image/png（≤24MB）。
- **P5 入口**：`mixed_map_export` 服务（png/svg/pdf 三面 + blocking/
  allow_partial + manifest）→ `POST /api/v1/export/mixed` 路由 +
  `webgis_map_export` Pi 工具 + 前端 `export_vector_pdf` 检测数值栅格层自动
  路由（纯矢量零变化）。UI/Agent 双入口到同一条编译路径。
- **P6 审计**：产物 + `.manifest.json` sidecar（schema 版本/mapspec_revision/
  partial/omitted_layers/raster_layers(style_id·data_revision·overlap·网格)/
  renderer(引擎·页数·有效 DPI)/diagnostics/sha256 checksum）+ 所有权注册。

## 诚实纪律（关键语义）

- 请求「完整地图」而会遗漏层（烘焙 raster/外部瓦片/hillshade）→ 默认
  **blocking typed 拒绝**（EXPORT_BLOCKED_INCOMPLETE）；`allow_partial` 显式
  选择才携带完整 omitted_layers 清单。
- 修复三处 BASE 静默/失效：numeric_raster 无绑定静默丢层（补披露）、PDF
  披露词表漏 numeric_raster、栅格窗口异常被层级宽 except 吞（披露 +
  回归测试锁死）。嵌入在场时绝不进 omitted。
- 冻结失败/修订移动 → typed 拒绝（CAS_SUPERSEDED / STALE_BINDING /
  STALE_STYLE），不拼两个修订。

## 排除项（明确不做）

- 外部底图瓦片不嵌入（许可不明 → blocking/partial 策略披露）。
- GPKG/GeoJSON 数据交付（G18）；浏览器视窗（G25）；尺度决策（G27）；
  Pi vendor 修改（G22 —— 本向只 build 不改）。
- G20 raster-only 专用 snapshot 出口保留不动（`webgis_raster_expression_export`
  兼容不变）。

## 证据（本地，不等待线上 CI）

- 方向核心 **52 个新测试**全绿（窗口 11 / 混合编译 13 / 图例字体 13 /
  服务 10 / 路由工具 6 —— 含像素级 PNG 探针、PDF 图像对象+中文提取核验、
  4 帧窗口唯一性、双栅格坐标对齐、blocking/partial、CAS）。
- 受影响目录全量回归：**3585 passed, 3 skipped, 3 xfailed**
  （cartography + raster_authoring + publication_basics，含 golden/atlas/
  frame_product/label 既有套件 —— 无绑定调用面输出不变）。
- 前端：触达文件 16 测试全绿；全量 5179 passed / 4 failed（4 个失败在
  同 SHA base 侧同样复现 —— jsdom 31 归一化 + G17 既有裸中文，非本向引入）。
- **A26 真实 Pi 子进程旅程**（PI_JOURNEY_E2E=1）：node stub LLM → 生产 app
  （vendor/pi v1.5.1）→ tool_search 发现激活 → `webgis_map_export` dispatch →
  签名 URL 下载产物含 data:image/png + manifest 冻结修订核验 —— 1 passed。
- A13 CJK 缺字扫描：24 个覆盖字符（标题/标注/图例/归属）在真实混合 PDF
  全部可提取，缺字为空。
- 复跑命令见 `planning/G26-MIXED-MAP-PUBLICATION/TEST_EVIDENCE.md`。

## 性能

- 窗口渲染进程级内容寻址缓存（同 (path,stat,style,revision,view,grid) 直接
  复用 bytes；多帧/多槽同窗口不重复解码）；GDAL out_shape 降采样使解码内存
  ≈ 输出网格；PDF SVG 预算按剥离 data URI 的矢量部分核算（嵌入另有 24MB/张、
  页预算独立），单层最坏 2048² PNG。
- DPI 72–600 钳制；生效值与 `resolution_capped` 披露进 manifest（提高 DPI
  不增加遥感细节）。

## 兼容 / 回退

- **行为变化披露**：numeric_raster 源层带静态（非 style_id）瓦片模板 URL 时，
  既有 SVG 编译曾输出远程 `<image>` 外链；现统一披露
  `numeric_raster_offline_snapshot_required`（离线可解析性优先 —— 无绑定调用
  面的外链行为不再静默保留）。G20 提交链的真实会话瓦片 URL 此前即被披露，
  golden/回归全部通过。
- **画布口径**：服务端 SVG/PNG 混合导出为固定画布（SVG 1200×800 / PNG
  1600×1100 @150DPI，v1；DPI 语义由 PDF 面承载）。

- 纯 MapSpec additive（无 schema 变更、无迁移）；无 `raster_bindings` 入参的
  所有既有调用面（vector PDF/atlas/frame product/canvas 链）输出不变。
- fetcher 放行仅限服务端自组 data URI（字体/图像），http/file/外链仍全拒
  （A21 测试锁定）。
- Docker slim 无 rsvg-convert → PNG 面 typed 503（`PNG_RASTERIZER_UNAVAILABLE`），
  引导 svg/pdf 或浏览器导出（部署面可加装 librsvg）。

## 外部依赖 / 未测项

- A27 真实浏览器 E2E 未执行（无授权浏览器会话环境）：前端链路以 mock-API
  级 vitest 覆盖 + 产物下载服务级核验；如实记 partial。
- WeasyPrint 行为实证基于 70.0（URLFetcherResponse 契约 / SVG image 丢弃），
  升级需复跑 E0 探针（测试已固化）。

## 独立评审

见 `planning/G26-MIXED-MAP-PUBLICATION/REVIEW.md`（review → fix → review 循环
记录；reviewer 未参与实现，复跑了关键用例）。
