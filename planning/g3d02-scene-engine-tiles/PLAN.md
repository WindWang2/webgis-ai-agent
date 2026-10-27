# G3D02 PLAN — 三维场景渲染内核 / 3D Tiles / glTF / 精细化交互

基线 `1115d683a`；分支 `zcode/g3d02-scene-engine-tiles-v1`。任务边界见工作单 §A：
**在现有 MapLibre GL JS 上增量构建 3D Tiles/glTF 对象渲染、分层、拾取、LOD 与 GPU
资源管理**；不替换引擎、不建 Cesium 页面、不做底图生产/科学算法/planner。

## M1 摸底结论（已完成，证据 file:line 见侦察报告）

数据流：后端 Pydantic schema（`app/lib/cartography/mapspec_schema.py`）→
`python -m app.lib.cartography.ts_projection` → `types.generated.ts`（契约锁定
`tests/cartography/test_ts_projection_contract.py`）→ compiler/reconciler/runtime →
renderer 缝 → MapLibre；RenderWork ACK 纯投影（`render-apply-ack.ts`）；scene 域
（terrain 先例）由 map-panel reconcile-settle 效应命令式重放
（`map-panel.tsx:647-670, 710-748`）。

关键扩展点：
1. **scene 域 additive 先例**：v1.4 scene / v1.5 / v1.6 均纯 additive +
   identity upgrader + `SCHEMA_EXPORT_MODELS` 登记 + 契约测试自动锁定。
2. **MapLibre v6 custom layer**：`CustomLayerInterface.render(gl, args)` 的
   `args.modelViewProjectionMatrix`（mercator→clip）是公开稳定桥接面；
   `queryTerrainElevation(lngLat)` 公开（ground clamp 可行）。
3. **mercator 数学**（`maplibre-gl-shared-dev.mjs:19322-19471`）：
   `x=(180+lng)/360`；`y=(180−(180/π)ln(tan(π/4+φπ/360)))/360`；
   `z=alt/circumferenceAtLatitude(φ)`，`circumference=2π·6371008.8·cos φ`；
   `meterInMercator(lat)=1/(2π·6371008.8·cos φ)`。
4. **3D 现状**：全库零实装（rg 证据）；`SUPPORTED_LAYER_TYPES` 穷举守卫在
   ACK 面；runtime `applySource` 对未知类型会误入 raster fallback（需显式
   model3d 分支跳过 MapLibre source 挂载）。
5. **环境**：pnpm@10 + node24；Python 3.13 uv venv（requirements.lock）；
   three@0.186.1 + @types/three@0.186.0 已装。

## 架构设计（定案，理由见 DECISIONS.md）

### 后端（MapSpec v1.7 —— "scene assets"）
- 新源类型 `MapSpecModelSource`（type:"model3d"；format: gltf|glb|3dtiles；
  url/ref_id 指针 + content_revision + fingerprint + bbox 证据）。进
  `MapSpecSource` union + `_SOURCE_KINDS` + `SOURCE_TYPE_SUPPORT`（partial：
  live 通道，headless 不消费）。
- `MapSceneConfig.assets: List[SceneAssetSpec]`（≤32）：id、source 指针
  （model3d）、visible、placement{anchor lng/lat、altitude_m、heading/pitch/
  roll、scale、ground_clamp}、vertical{datum、unit、elevation_ref}、
  render_order、lod{max_sse、max_visible_tiles}、provenance_ref。
- 悬空/错型引用 → coordinator 阻塞码 `SCENE_ASSET_SOURCE_REF/_TYPE`
  （入 BLOCKING_VALIDATION_CODES）；质量门新码（证据缺失披露）。
- 写通道：`SetSceneAssetsIntent`（仿 SetSceneIntent 模板，PRESENTATION 面，
  整表替换 scene.assets，scene 缺席时建 mode="3d" 座并披露）+ facade
  `set_scene_assets` + 工具 `set_scene_assets`（agent 面）。
- UpsertSource 的 `store_data` 增加 model3d 分类分支。

### 前端（`frontend/lib/map-kit/scene3d/` 新模块）
- `geo.ts`：纯函数坐标/矩阵数学（lngLatAlt→mercator、ENU 朝向、
  ECEF→ENU（3D Tiles transform）、SSE 公式）。**全部可无 WebGL 测试**。
- `gltf/`：自维护最小 glTF2/GLB 解析器（mesh/PBR 子集；skin/animation/
  morph/Draco → typed unsupported；accessor 越界/缓冲预算守卫——恶意文件
  防护）。产出 THREE.Group。
- `tiles3d/`：3D Tiles 1.1 subset（tileset.json、boundingVolume region/box、
  geometricError、refine REPLACE/ADD、glTF content、transform 链）+
  `scheduler.ts`（SSE 有界调度：视锥剔除、tile/triangle 预算、并发上限、
  优先级取消、LRU 缓存身份=url+revision）。
- `asset-manager.ts`：每 map 一个 Scene3DManager：单共享 THREE.WebGLRenderer
  （复用 MapLibre gl context）、每资产一个 custom layer
  （`scene3d-<assetId>`，renderingMode '3d'）、状态环（mounted/loading/
  failed/unavailable + reason → observation 披露）、dispose 纪律。
- `custom-layer.ts`：CustomLayerInterface 桥（onAdd 取 context、render 用
  args.modelViewProjectionMatrix 同步相机 + resetState、深度测试开启；
  capability 边界：terrain 深度遮挡近似、globe 不支持→typed 降级）。
- `pick.ts`：逆 MVP 造 ray → THREE.Raycaster → PickEvent（assetId、tile、
  batch/metadata 属性、lngLatAlt）；点击先 2D 查询后 3D raycast。
- 挂接：map-panel scene effect + reconcile.then() 重放（terrain 同款）；
  runtime `applySource` model3d 分支跳过；renderer.ts `syncSceneAssets` 缝。
- Interaction IR：复用 `feature_selected`（词表不动），payload 带模型元数据。
- ACK：model3d 源在 applied spec sources 内 → per-source ACK 免费搭车。

### 里程碑推进
M2 后端契约 → M3 解析/桥 → M4 生命周期 → M5 拾取 → M6 调度/降级 →
M7 fixtures/E2E/性能实测 → 独立 review ×2 → PR。

## 验证策略（TEST_MATRIX.md 详列）
- Python：schema v1.7 单测（形状/披露/round-trip）、coordinator 阻塞、
  质量码、envelope model3d 依赖、capability、intent/tool、ts_projection 契约。
- TS：geo 数学已知值、glTF 解析（程序化 GLB fixture）、tileset/SSE 调度、
  manager 生命周期幂等/泄漏、ACK 投影、map-panel 集成（house fake map）、
  maplibre-mock-surface 契约扩展。
- E2E：Playwright journey（mock 路由 fixtures + 真 WebGL 若 headless 可用）。
- 负例：断网/404、非法 glTF、超大 accessor、悬空 source、无权限 ref、
  context lost、重复 apply、2D↔3D 切换、底图切换重放。
- 性能：调度决策纯函数基准 + journey 帧耗时/内存 + bundle 增量实测。
