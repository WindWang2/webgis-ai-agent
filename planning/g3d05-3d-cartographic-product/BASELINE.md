# G3D05 基线快照（三维智能制图产品、场景化 UI、StoryMap 与导出质量）

- **基线 SHA**：`1115d683a5c8343e44d8a9777e4ce05e073c29e5`（`origin/master`，2026-10-27 fetch）
- **worktree**：`../webgis-wt-g3d05-3d-cartographic-product`，分支 `zcode/g3d05-3d-cartographic-product-v1`
- **工作日期**：2026-10-27 起

## 已核实的现状（文件/符号级证据）

### 已存在且必须复用（不重造）

| 能力 | 位置 |
| --- | --- |
| MapSpec v1.6 `scene` 块（mode 2d/2.5d/3d、terrain、camera pitch/bearing、exaggeration≤10、vertical_unit、elevation_ref） | `app/lib/cartography/mapspec_schema.py:244-291`（`MapSceneConfig` L273、`TerrainSceneSpec` L244、`SceneCameraSpec` L261） |
| SceneIntent→SceneDecision 决策表（fail-closed：无高度证据不出挤出、静态媒介不出 3D） | `app/lib/cartography/scene_planning.py:116-255` |
| Agent 场景工具 `plan_map_scene`/`set_map_scene`（经 `SetSceneIntent` 事务管线） | `app/tools/scene_tools.py:38-187`、`app/services/mapspec_store.py:176-199` |
| 场景质量门（10 个代码，pitch>85 blocking / >60 warning、exaggeration 披露阈值 1.5） | `app/lib/cartography/scene_quality.py:25-35,91-203` |
| 组合模板 pack DSL + 注册（追加式 `COMPOSITION_PACK_TEMPLATES`） | `app/lib/cartography/composition_packs/_base.py`、`__init__.py:29-40` |
| 组件 ABI（版本化只读投影，fail-closed：native 类型必须有 META 条目） | `app/lib/cartography/component_abi.py:31-246` |
| live/export 真值矩阵（翻转 `publication=True` 自动进入语义语料与 manifest） | `app/lib/cartography/component_renderers.py`；`export_semantic_corpus.py:15-18` |
| MapProductPlan / product_manifest（live↔export 语义一致性 + 降级码） | `app/services/gis_harness/planner.py:615-663`、`app/lib/cartography/product_manifest.py` |
| 渲染/导出降级词汇单一真相（RENDER_DIAGNOSTICS，前端经 component-catalog 消费） | `app/lib/cartography/render_diagnostics.py:43` |
| 3D 相关 map model 已在库：`extrusion_3d`、`terrain_analytical_surface`、`hillshade`、`elevation_tint_hillshade`、`viewshed_map`、`isoline_contour` | `app/lib/cartography/model_library.py`、`model_packs/raster_remote_sensing.py` |
| StoryMap 后端（spec 1.0 / 相机规划含 pitch≤60 / 编译器 / 导出打包 / 路由，**无持久化、章节无场景块**） | `app/lib/storymap/{spec,camera_planner,story_compiler,export_packager}.py`、`app/api/routes/storymap.py` |
| 前端场景相机/terrain 管线（sceneCameraIdentity、buildSceneTerrainOptions、enable3DTerrain、挤出证据门） | `frontend/components/map/map-panel.tsx:40-53,472-504,647-670`、`frontend/lib/mapspec-runtime/adapter.ts:523-533`、`frontend/lib/map-kit/renderer.ts:1148-1165` |
| 双视窗比较（swipe + side-by-side，相机同步含 pitch/bearing） | `frontend/components/map/comparison/comparison-view.tsx`、`comparison-sync.ts` |
| 浮动组件系统（FloatingChrome、dock、registry、commitComponentPatch） | `frontend/components/map/map-components/{floating-chrome,registry}.tsx`、`frontend/lib/map-components/*` |
| 用户 MapSpec mutation 通道（`intent` 判别联合 → body_to_intent → 事务管线，client_mutation_id 幂等） | `app/api/routes/mapspec_mutations.py:40-128`、`app/schemas/mapspec_mutation_schema.py`、`app/services/mapspec/intent_codec.py` |
| 客户端 spec 真相（commitMapSpecDocument 单调 revision、乐观 pending） | `frontend/lib/mapspec/session-cursor.ts:140-166` |
| 图层 7 态词汇（loading/ready/rendering/hidden/stale/failed/expired，纯投影） | `frontend/lib/layers/layer-status.ts:31-118` |
| StoryMap 前端播放器（/story 路由、narrator、chapter 编排 localStorage） | `frontend/app/story/story-view.tsx`、`components/story/story-narrator.tsx`、`lib/api/storymap.ts` |
| 比例尺数学（纯 2D Web Mercator，无 pitch 修正） | `frontend/lib/map-kit/meters-per-pixel.ts`、`scale-math.ts` |

### 已确认缺失（本方向的工作面）

1. **无任何 3D 组合模板**：composition_packs 10 个 pack 全部 2D；无模板声明 `scene_disclosure`/`elevation_profile` 类组件，无交互专属输出目标的 3D 模板。
2. **无 `scene_disclosure` / `elevation_profile` 组件类型**：`COMPONENT_TYPES`（mapspec_schema.py:545-570）22 类中无一覆盖"场景模式/夸张/垂直基准披露"或"线路高程剖面"；ABI META 亦无。
3. **透视比例尺诚实性缺失**：`meters-per-pixel.ts` 无 pitch 修正；导出 chrome 比例尺在 pitch>0 时不做任何"不可信"披露；无对应降级诊断码。
4. **SVG/PDF 矢量导出无 3D 降级语义**：矢量孪生天然平面，但 spec.scene.mode=3d 时无 typed 降级说明"已降级为 2D 表达"。
5. **scene_quality 无导出/遮挡向检查**：缺比例尺透视不可信、标注被地形遮挡风险、矢量导出降级披露三个代码。
6. **StoryMap 章节无场景状态**：keyframe 有 pitch/bearing 但无 exaggeration/mode、无 per-chapter visible layers；**无服务端持久化**（compile/export 全无状态）。
7. **用户侧无场景控制器**：仅 map-toolbar-hud 一个 2D/3D 开关（pitch 显示硬编码 60）；无 exaggeration/pitch/bearing/垂直基准/性能模式面板；用户改场景无 mutation 通道（SetSceneIntent 仅 Agent 工具可达）。
8. **StoryMap 播放无用户接管语义**：autoplay 无 pause&resume 于用户拖动相机。

## 基线测试状态（本地首跑记录）

见 `EVIDENCE.md`（实施过程持续追加；首跑命令与结果在 Phase 0 落盘）。

## 姊妹方向接口约定（跨 worktree 只读消费，不 cherry-pick）

- **G3D01（PR #1771 未合并）**：basemap profile/terrain governance（`frontend/lib/map-kit/terrain-governance.ts`、`basemap-profiles.generated.ts` 在 master 不存在）。本方向只依赖 master 已有的 `buildSceneTerrainOptions` + AWS terrarium 回退；不引用 G3D01 新文件。
- **G3D02（无 PR）**：scene render receipts —— 不存在；本方向不消费。
- **G3D03（PR #1773 未合并）**：地形剖面/LOS 量测后端（`app/lib/geo_analysis/measurements.py` master 不存在）。本方向 `elevation_profile` 组件以 inline 数据 + `ref:profile-*` 工件契约先行，与 G3D03 合并后由其填充真实剖面计算（接口：`{distance_m, elevation_m}[]` 序列）。
- **G3D04（无 PR）**：产品计划增强 —— 不依赖。
