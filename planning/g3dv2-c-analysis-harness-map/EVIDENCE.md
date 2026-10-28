# EVIDENCE — g3dv2-c-analysis-harness-map

## F27 验收场景矩阵（14 条，全部通过；pytest tests/acceptance/gis_agent -q）

| # | 场景 | 类型 | 断言要点（证据面） |
|---|---|---|---|
| f27-01 | 剖面→通视多轮 | 正例 | mapspec.layer_count≥2 + elevation_profile 组件 + 双 goal 绑定 |
| f27-02 | 视域上图 | 正例 | fill 图层在 layer_types + 绑定 + all_fresh |
| f27-03 | 换观察点判旧 | 状态迁移 | stale_reason_codes ∋ SCENE_STALE_OBSERVER_MOVED |
| f27-04 | camera-only 不判旧 | 红线反证 | all_fresh=True（视域与镜头无关） |
| f27-05 | 土方 mean 基准 | 正例 | fill 分区上图 + cut_fill_volume 绑定 |
| f27-06 | 无 DEM 源 decline | 负例 | layer_count=0（无副作用） |
| f27-07 | 垂直基准混算 | 负例 | VERTICAL_REFERENCE_MISMATCH + 无图层 |
| f27-08 | nodata 观察点 | 负例 | 类型化失败 + 无图层（宁拒不给错） |
| f27-09 | 坏 options JSON | 负例 | SCENE_ANALYSIS_BAD_OPTIONS_JSON |
| f27-10 | 用户中断恢复 | 状态迁移 | cancel 轮零副作用；重跑轮 fill 上图 |
| f27-11 | print 媒介 2D 退化 | 降级 | scene.mode≠3d + 剖面 line 照常上图 |
| f27-12 | 导出语义 | 产品面 | export.available=True + failed_aspects=[] |
| f27-13 | 距离+面积量测 | 正例 | layer_count≥2 + surface_area 绑定 |
| f27-14 | 完整旅程五轮 | 任务书验收链 | 3 层+组件+用户 viewport 记录+camera 后 all_fresh+导出零失败面 |

## 科学正确性证据（解析 oracle，非实现回声）

- cut/fill：平面常基准 20000 m³、斜面 mean 基准 1250 m³ 闭式、台阶 25000 m³、
  分区可加、ft×0.3048 精确（tests/unit/lib/test_terrain_volumes.py，16 例）；
- viewshed 几何：多边形面积重建像元计数（CRS 无关整数记账）、3×3 掩膜精确
  世界矩形 [100,130]×[170,200]、甜甜圈洞、旋转仿射 45° 面积恒等
  （tests/unit/lib/test_viewshed_geometry.py，14 例）；
- 零回归：既有 measurements 25 例 + 工具 36 例 + viewshed stats 零漂移
  （include_geometry 双模式逐位一致断言）。

## 生产链证据（非 mock）

- dispatch 缝 E2E（test_profile_artifact_seam）：ToolDispatchService 真实
  dispatch terrain_profile → ref:profile-* 落 session_data → mapspec_store
  layer_upsert + patch_component 组件 → 指纹一致；
- 编排 E2E（test_analysis_orchestration_staleness）：registry 真实 dispatch
  六算法（合成 DEM GeoTIFF 写 ./data）→ MethodUseContract 摘要 → 绑定登记；
- staleness 生产消费：apply_scene_representation ACK（拖点）+ run_3d_analysis
  prior_stale ACK + 直接裁决面（camera-only fresh、DEM 源消失）。

## 本地质量门记录（2026-10-28，机器 16C/62G）

- ruff app/ tests/ scripts/：本分支文件零违规（origin/master 既有 3 处
  E741/F401 留档：scene_quality.py:432、test_scene_3d_pack.py:25、
  test_storymap_scene_v11.py:7——git diff origin/master 为空证明非本分支引入）；
- tests/quality 契约：api_compat + field_contract 再生后 18 passed；
  generated-artifact 账本 --update 后 graph 测试通过；
- tests/integration/test_quality_v3_coordination.py 32 passed（含
  adr_watermark=276、migration_watermark=99 推进后 preflight full pass）；
- tests/cartography -m cartography：2060 passed（golden corpus 5 新案例，
  stress 报告模板零改写——模板选择无抢占回归）；
- tests/acceptance/gis_agent：61 场景全绿（pin 已更新 61）；
- tests/unit 全量：见 LOOP_STATE（后台串行运行，结果落盘）。

## 全量 unit lane 归因账（终局）

tests/unit 全量：17567 passed / 40 failed / 25 errors → 处置：
- **25 项本分支引发并已清偿**：volume_estimation 能力/midpoint_quadrature
  方法引用/descriptor vnext 三字段注册 + 全目录再生（science catalog×3/
  benchmark/quality manifest）；G02 计数 pin 244→245；#535 幽灵命令
  （sceneCommands 补登，master 即红）；_parsed 行为恢复的一处断言语义
  更新（raster 不冒充 GeoJSON 的不变量改由 geojson_ref/ref_descriptor/
  result_ref 三行钉住——不再钉"descriptor 从未被等"这一 bug 副作用）。
- **12 项 master 既有红**（干净 worktree 对照同败）：metadata 棘轮
  （159==159，新增工具不在名单）、binding taxonomy×2、execution catalog
  drift、component catalog parity、export semantic corpus、mutation
  registry×2、platform build info、behavioral webgis、response model
  coverage、debt ratchet。
- **2 项 Playwright runtime fixture**：origin/master + 同一 node_modules
  复跑同败（环境性；worktree 全新 pnpm install）。
- **1 项 vitest data-plane.perf**：并行负载抖动，单跑 5/5 通过（两轮复现
  同一模式）。
- 修复后受影响域复跑 2232 passed + 1 既有棘轮红（已归因）。

## 既有债务的显性清偿（非重叠最小修复，证据齐全）

- tool_dispatch_service `_parsed` 未初始化：E2E 显形的 UnboundLocalError
  （best_effort 曾掩盖为警告 → 非能力工具 artifact 登记被静默跳过）——
  预初始化恢复登记路径（一行 + 注释）；
- #1830 快照欠账：openapi.json 缺 KeyframeScene schema（本分支未触碰该
  面，再生吸收）；migration_watermark 98→99（0099_story_map_assets 为
  #1830 迁移，本分支未加迁移）。
