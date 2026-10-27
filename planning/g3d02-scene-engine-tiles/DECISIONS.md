# G3D02 DECISIONS — 关键决策记录

## D1 三维底座：three.js + 自维护最小桥（非 maplibre-gl-three/自写 raw WebGL）
- 候选：a) `maplibre-gl-three` 社区包 b) three.js + 自写薄桥 c) 纯 raw WebGL。
- 选 b）。理由：MapLibre v6 的 `CustomLayerInterface.render(gl, args)` 提供
  `modelViewProjectionMatrix`（已在 6.10 typings 证实，`maplibre-gl.d.ts:5562`），
  桥本身 <200 行，无需社区包的版本耦合风险；three 提供成熟矩阵/视锥/
  Raycaster/glTF 数学，自写 raw WebGL 的矩阵+拾取+剔除维护成本不成比例。
- 包体代价实测后写入 EVIDENCE.md（验收 D 要求）。

## D2 契约形态：model3d 源 + scene.assets 引用（非新 layer type）
- 3D 资产**不是** MapLibre style layer —— 新 layer type 会撞
  `SUPPORTED_LAYER_TYPES` 穷举守卫、layer 词表三向锁、golden corpus，扩散面大。
- terrain 先例（`scene.terrain.source` → raster-dem 源 id）证明
  「scene 域参数 + source 指针」是仓库既定模式。`MapSpecModelSource` 进
  sources → RenderWork data_dependencies / per-source ACK 免费搭车
  （`_data_dependency` 泛型读取 content_revision/fingerprint，仅
  `_SOURCE_KINDS` 需 +1）。
- scene.assets 挂 MapSceneConfig（随 SetScene COW 分支），写通道独立
  `SetSceneAssetsIntent`（避免 set_map_scene 整树覆写模式/地形）。

## D3 垂直基准：显式 datum 词表，绝不静默假定
- `vertical.datum ∈ {ellipsoid, egm96, local}`；unit 现仅 "m"（其他单位
  需要换算证据，先不开放）。anchor altitude 缺省 0 = 放置约定（披露
  `scene_asset_altitude_assumed_zero` 当无 elevation_ref），区别于"编造
  建筑高度"禁令（那是挤出数据语义）。ground_clamp=true 时用
  `map.queryTerrainElevation` 贴地（terrain 未启用 → 披露不阻塞）。
- glTF 依规范 y-up（渲染端固定旋转校正，无配置）；3D Tiles 依规范
  ENU+z-up；tileset ECEF transform → 解析端 WGS84 ECEF→ENU 换算。

## D4 capability 红线：headless 不消费 model3d → 声明 partial
- `SOURCE_TYPE_SUPPORT["model3d"] = partial`（live custom-layer 通道；
  headless 编译器不产出）；`FEATURE_SUPPORT["scene_assets_3d"] = true`
  （live 端实现后）。不把 3D Tiles 完整规范覆盖声明为 full —— subset
  边界在 tileset.ts 的 typed unsupported 错误码里显式化。

## D5 调度：事件驱动，禁止无限 render loop
- custom layer 的 render() 由 MapLibre 帧驱动；tile 调度挂在
  map moveend/zoom 事件 + 加载完成的再触发（`map.triggerRepaint` 由
  content 变化驱动），无自建 rAF 循环。SSE 公式：
  `sse = geometricError · viewportHeight / (distance · 2·tan(fov/2))`。

## D6 资源纪律
- fetch 全走 AbortController；tile 缓存身份 = (url, content_revision)；
  预算：maxVisibleTiles（默认 128，spec lod.max_visible_tiles 只能收紧）、
  每资产 byte 预算、accessor 范围校验（恶意 GLB 防 OOB）、并发 ≤4、
  LRU 驱逐 dispose geometry/material/texture。
- URL 纪律：仅 https 或同源相对路径；不带凭据；重定向不跟随跨协议；
  JSON 响应 ≤2MB、buffer ≤64MB 上限（超限 typed unavailable）。
  （前端防线 + 后端 SSRF 责任不豁免。）

## D7 拾取与 IR：复用 feature_selected 词表
- 3D 拾取产出与 2D feature selection 同语义事件（closed 13-kind 词表
  不动，无后端契约变更）；模型元数据（batch table/asset metadata）进
  payload properties。半透明/轮廓高亮走 THREE 材质覆盖，GPU 弱 → 降级
  为仅 bbox 线框高亮（typed degrade）。

## D8 状态披露通道
- per-asset 状态环（scene3d/status.ts，有界 64）→ render-observation
  附带 → 后端 observation/telemetry 可见；导出披露复用 exporter 的
  sceneEvidenceSnapshot 汇聚点（实现期核对最小编织面）。

## D9 版本与兼容
- MapSpec v1.6→1.7 纯 additive（identity upgrader），存量 spec 零行为
  变化；2D 项目（无 scene.assets）零开销路径（sync 早退）。
- 不触碰：Interaction IR 词表、SUPPORTED_LAYER_TYPES、layer 词表、
  provider catalog、算法层、planner/export 主导面。

## 环境事实
- pnpm@10.34.9 / node v24.21.0；three@0.186.1（exact pin）+ @types@0.186.0。
- Python 3.13.x uv venv（.venv，requirements.lock + dev）；系统 3.14 出
  requires-python 窗口（<3.14）故不可用。
- 构建互斥：/tmp/webgis-build.lock（flock）。
