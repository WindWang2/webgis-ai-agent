# GAP_ANALYSIS — Oracle 差距 → 交付物映射

| # | Oracle 条款 | 现状差距 | 交付物 |
|---|---|---|---|
| G1 | 同一统计产品 2D↔3D 切换统计/分级/legend 不漂移 | 切换=前端布尔，无契约；legend 同源性无验证 | scene mode = presentation-only mutation（不动 layers/sources/legend_spec）；质量门 `SCENE_LEGEND_DRIFT` 确定性校验配对层 legend_spec digest 一致；测试锁定 |
| G2 | 无 elevation 证据时不伪造高度 | `adapter.ts:413` 自动挤出 `coalesce(get height, 20)` 伪造；terrain DEM 硬编码外部源与数据无关 | ①挤出证据门控（无 layer.extrusion 契约且无 height 字段证据 → 不挤出 + 披露码）；②DEM terrarium tile 编码走 session ref（fail-closed：非 DEM ref → 结构化错误）；③`SCENE_EXTRUSION_NO_EVIDENCE` 质量门 |
| G3 | 旧 MapSpec 未用新字段时行为不变 | — | v1.4 纯 additive（Optional 字段 + identity upgrader）；golden corpus byte-stable 测试；前端无 scene 字段时走旧路径的回归测试 |
| G4 | 典型 synthetic scene 通过 deterministic quality gate | 无 scene gate | `scene_quality.py` 确定性门 + synthetic 场景矩阵测试 |
| G5 | MapLibre fallback 始终可用 | terrain 硬编码 AWS（反而只有外部源） | spec terrain 源优先；无 spec terrain / 源不可用 → 既有 enable3DTerrain 默认路径（AWS terrarium 兜底）；renderer 参数化默认值不变 |
| G6 | 性能/内存有界、不依赖巨型真实 3D 数据 | 无 scene 性能基准 | synthetic 生成 workload（N 多边形+高度场、DEM 网格）+ perf 标记测试；tile 编码窗口化有界 |
| G7 | targeted tests 全绿 | — | 见 TEST_MATRIX.md |
| G8 | review 无未处理 P0/P1 | — | adversarial review（架构/正确性 fail-open/并发取消幂等/安全租户/性能/兼容/观测诚实/前端 race/契约漂移） |
| G9 | 关键验证连续两遍一致 | — | goal-loop 完成协议双跑 |

## 里程碑 → 模块映射

| 里程碑 | 新模块/改动 | 性质 |
|---|---|---|
| M1 SceneIntent/SceneDecision | `app/lib/cartography/scene_planning.py`（纯函数） | 新 |
| M2 MapSpec additive 契约 | `mapspec_schema.py` v1.4（scene/terrain/extrusion 类型化）+ `ts_projection` 再生成 + `SetSceneIntent`（lifecycle_engine 加分支）+ facade `set_scene` | additive |
| M3 elevation/terrain ref | `raster_tile_service` terrarium 编码（DEM ref 有界窗口渲染）+ `scene_elevation.py`（ref 校验/垂直语义 fail-closed） | additive |
| M4 挤出/立体符号/drape/legend 同源 | `analysis_cartography_converter` extrusion 证据字段透传 + 前端 adapter 3D point symbol pitch-alignment + legend drift 校验 | additive |
| M5 camera planner + LOD | `app/lib/cartography/scene_camera.py` + `scene_lod.py`（纯函数） | 新 |
| M6 degradation/lineage | `scene_degradation.py`（3d→2.5d→2d 链 + 披露码）+ presentation-only 切换测试 | 新 |
| M7 scene quality gate + 自愈 | `scene_quality.py` 挂 semantic/质量闭环 + `selfheal_actions.py` scene 动作（只经既有 facade intents） | additive |
| M8 前端 adapter + 基准 + 导出 | renderer/adapter/map-panel spec 驱动 terrain + render-scene 披露码 + `tests/benchmarks/test_scene_perf.py` | additive |
| Agent 面 | `app/tools/scene_tools.py`（plan/set scene 工具）+ API route | additive |

## 明确不做（负空间）

- 不改 visual_judge v2 契约（5 轴 + extra=forbid 是 ADR-0185 冻结不变量；3D 视觉轴留待
  契约版本化 Epic，记录于 ADR）。
- 不引入 Cesium/3D Tiles/新渲染引擎；不做 globe/sky/fog。
- 不做真实 3D 建筑模型数据管线（fill-extrusion 属性高度已是既有证据通道）。
- 不动 #1335/#1336 触碰面。
