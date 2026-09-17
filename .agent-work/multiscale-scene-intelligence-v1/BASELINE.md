# Multiscale Scene Intelligence v1 — Baseline（真实执行记录）

- branch: `cartography/multiscale-scene-intelligence-v1`
- worktree: `/home/kevin/projects/webgis/webgis-wt-scene-intelligence-v1`
- base: `origin/master` = `faa453a8935101378c23eb6694a42c3616d9c670`（2026-09-16 fetch 复核一致）
- 执行时网络：GitHub API/网页在执行窗口内多次 TLS 失败；git fetch 在重试后成功。
  PR 状态以本地已 fetch 的分支 diff 为证据（两分支均已本地存在）。

## 0. 并行方向盘点（冲突面）

| 方向 | 状态 | 触碰面 | 与本方向重叠 |
|---|---|---|---|
| PR #1335 `fix/harness-claim-mission-failclosed` | open（本地分支 4 commits） | `gis_harness/evidence_claim/*`、`hotpath_convergence/*`、`mission_runtime/*` | **零文件重叠** |
| PR #1336 `zcode/geoai-promptable-foundation-platform-11` | open（本地分支 14 commits） | `modelops/geoai/*`、`app/tools` geoai、`frontend/components/geoai`、`tests` geoai | **零文件重叠**（共享 tests/conftest.py 无实质冲突） |
| 本地 worktree `harness/event-driven-spatial-ops-v1` | master+0 commits（空） | 无 | 无 |
| master 已合入 #1327/#1328/#1329 | merged | skill policy / evidence graph / hotpath | 不触碰制图渲染链 |

结论：本方向 ownership（scene planning、MapSpec additive 3D 契约、terrain/elevation、
LOD、scene quality、前端 map 3D adapter）当前无任何 open PR 竞争。

## 1. 现有 3D/terrain/extrusion 能力基线（file:line 证据）

### 1.1 已经存在的（复用，不重建）

| 能力 | 位置 | 状态 |
|---|---|---|
| 相机 pitch/bearing 契约 | `app/lib/cartography/mapspec_schema.py:89-93`（MapView）；TS 投影 `frontend/lib/mapspec-compiler/types.generated.ts:92-97` | 全链路（SetViewIntent→compiler→runtime→工具→storymap） |
| fill-extrusion 图层类型 | `mapspec_schema.py:227`；编译 `compiler.ts:622-645`；运行时 `adapter.ts:381-398` | 活跃 |
| 3D 挤出定量模型 | `app/lib/cartography/extrusion_model.py`（`ExtrusionHeightSpec`/表达式生成/分布分析） | 活跃；但 `ExtrusionModelContract` 聚合类零引用、`recommended_view` 只写不读 |
| 3D 挤出工具 | `app/tools/cartography.py:300-492` `create_3d_extrusion_map`（height_legend、metadata.extrusion） | 活跃 |
| 挤出转换器 | `app/services/analysis_cartography_converter.py:376-386,600-638,807-818` | 活跃 |
| 挤出语义 QA | `app/lib/cartography/semantic_checks.py:2699-2805`（EXTRUSION_* 规则） | 活跃 |
| raster-dem 源 + hillshade 层 | `mapspec_schema.py:151-160,228`；编译 `compiler.ts:389-393,522-528`；运行时挂载 `runtime.ts:853-863` | 活跃（hillshade 数据面） |
| 前端 terrain 开关 | `frontend/lib/map-kit/renderer.ts:1028-1057`（enable/disable3DTerrain）；`map-panel.tsx:429-441`（is3D effect）；toolbar/top-bar UI | 活跃但 **DEM 源硬编码 AWS terrarium URL、与 MapSpec 无关** |
| is3D 自动挤出 | `adapter.ts:409-417`：is3D 时把所有 polygon 层挤出，height=`coalesce(get height, 20)` | **伪造高度路径**（无 height 属性→编造 20m） |
| StoryMap 相机 | `app/lib/storymap/spec.py:79-93`（CameraKeyframe，pitch≤60）；`camera_planner.py:61-102` plan_camera_for_bbox | 活跃（章节级） |
| DEM 数据获取 | `app/services/rs/stac_client.py`（Copernicus GLO-30，sentinel -9999）；`spectral_engine.py:118-219` compute_terrain | 活跃（分析路径） |
| 地形科学栈 | `app/lib/geo_analysis/terrain.py`（3,464 行 numpy）；~25 个 terrain 工具 | 活跃（分析路径） |
| elevation 数据角色 | `gis_harness/workflow_schema.py:40` DATA_ROLES 含 elevation | 活跃（计划路径） |
| 视觉评判 | `app/lib/harness/visual_judge/contracts.py:26-33`（5 轴，无 3D 轴） | 活跃 |
| 自愈 | `app/services/mapspec/visual_healer.py`（4 类 ops）；`lifecycle_engine.py:2809` apply_visual_heal_patch | 活跃但无生产调用方 |
| 降级披露 | `terrain_3d_scale_caveat`（exporter.ts:2109）；SVG `3d_perspective_not_vectorized`（mapspec-to-svg.ts:459-475） | 活跃 |

### 1.2 关键缺口（本方向要补的）

1. **无 SceneIntent/SceneDecision 规划层** — 没有任何东西基于 目的/数据/比例尺/媒介 决策 2D/2.5D/3D；is3D 是纯手动布尔。
2. **MapSpec 无 terrain 对象** — MapLibre style 级 `terrain`（source+exaggeration）不在契约中；前端 terrain 与 spec 完全脱节（硬编码 AWS URL）。
3. **无 elevation 证据契约** — 挤出高度无证据 ref 绑定；`adapter.ts:413` 在无证据时伪造 20m（违反 fail-closed）。
4. **无垂直语义** — 无 vertical unit/ exaggeration/vertical datum 声明与校验。
5. **无 3D 场景质量门** — 确定性检查只有 EXTRUSION_PITCH_ADVISORY/OCCLUSION_WARNING 两条；无遮挡/穿透/夸张/legend 一致性的系统 gate。
6. **无产品级 camera planner** — storymap 有章节相机；一般产品无 overview/detail/compare 相机规划（antimeridian/安全 pitch/reduced-motion 无覆盖）。
7. **无 scale-aware 3D LOD** — symbol-law 有密度自适应，但无 terrain 分辨率/挤出抽稀/3D 标注密度的 scale-aware 策略。
8. **无 2D↔2.5D↔3D 降级决策** — SVG 有单点降级，无系统退化链与 lineage 声明。
9. **自愈无 3D 缺陷类别** — heal 词汇表只有 label/contrast/order/opacity。

## 2. 事实源与铁律（来自 CONTEXT.md/ADR，执行时必须遵守）

- MapSpec 契约权威 = `mapspec_schema.py`（Pydantic 唯一真相）；TS 投影由
  `ts_projection.py` 确定性生成，禁止手改 generated 文件。
- mutation 热路径不经过 schema 全量解析（#1082 读放大）；additive optional 字段
  + identity upgrader 是既有版本演进模式（1.1/1.2/1.3 均如此）。
- 大数据只传 ref/ticket（`ref:raster/<id>` 等），禁止进 LLM context。
- legend/classification 单源 = `thematic_spec.py` 的 legend_spec → `spec_to_paint`
  单投影；2D/3D legend 不得各写一套。
- 生命周期事务 = lifecycle_engine apply_mutation（lock→dedup→CAS→COW→validate→
  checkpoint→save→rollback）；新 mutation 必须 intent 化走同一管线。
- 自愈边界 = presentation-only；只产生既有 MapSpec mutation intents。
