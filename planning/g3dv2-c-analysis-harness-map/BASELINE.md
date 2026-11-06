# BASELINE — g3dv2-c-analysis-harness-map

核对时间：2026-10-28（执行时实时核对，非任务书历史基线）

## 仓库状态

- 目标仓库：WindWang2/webgis-ai-agent（remote origin，https）
- 本 track worktree：`/home/kevin/projects/webgis/webgis-wt-g3dv2-c-analysis`
- 分支：`zcode/g3dv2-c-analysis-harness-map`，自 `origin/master` = `70dab2b92917fe4f85375de959dad360288a60ad` 创建
- 与任务书写作基线 SHA 一致（master 无前移，无需 rebase 预案，交付前仍会复查）
- 主 checkout（webgis-ai-agent）本地 master 落后 origin/master 72 提交；一切代码结论以 origin/master 为准（worktree 内）

## 已合并三维 PR（不得重做）

| PR | 内容 | 关键落点 |
|---|---|---|
| #1771 | 开源底图/DEM 资源治理 | basemap_profiles ELEVATION_SOURCE_PROFILES、terrain_encoding |
| #1834 | 3D 场景渲染内核 | MapSpec v1.7 scene.assets/model3d、three 桥、3D 拾取 IR |
| #1773 | 三维量测契约 V1（ADR-0274） | app/lib/geo_analysis/measurements.py：terrain_profile/line_of_sight/surface_distance/surface_area + resolve_vertical_reference |
| #1829 | Agent 三维编排（Requirement IR SceneSpec + 4 工具） | app/tools/scene_orchestration_tools.py；app/services/gis_harness/scene3d/{cues,decision,camera,staleness,orchestrator,verdict}.py |
| #1830 | 三维制图产品（ADR-0275） | composition_packs/scene_3d.py、scene_disclosure/elevation_profile 组件、scene_quality |

基础：#1759(G01) #1760(G02 MethodUseContract) #1761(G03 工具对账) #1762(G05 验收) #1755 #1756。

## C0 全链路审计结论（file:line 以 origin/master 为准）

### 真生产链（已闭合，测试只钉契约）

1. 四量测工具（`app/tools/terrain_analysis.py:2787,2913,3126,3287`）→ dispatch geojson 通道（`app/services/tool_dispatch_service.py:1124-1147` 铸 `ref:geojson-*`）→ `_author_display_result`(:1495) → `app/services/analysis_cartography_converter.py:274 convert_analysis_to_mapspec_layer` → MapSpecLayer。契约测试 tests/unit/test_measurement_mapspec_wiring.py。
2. `resolve_vertical_reference`（measurements.py:105）：闭合垂直词表 + datum 混算类型化拒绝，四工具真实调用。
3. Requirement IR SceneSpec（`app/services/gis_harness/requirement_ir/contracts.py:304`）← cue 解析（requirement_ir/build.py:149,182）真实调用；apply_scene_representation/fly_camera → scene3d.decision/camera → SetSceneIntent/SetViewIntent 事务真实。
4. viewshed 科学内核（`app/lib/geo_analysis/terrain.py:410`，输出 bool 栅格 + visible_fraction/area）+ run_3d_analysis 真实 dispatch（scene_orchestration_tools.py:508）。

### 断链（本 track 真缺口）

| # | 断链 | 证据 | 影响 |
|---|---|---|---|
| B1 | viewshed 结果 stats-only：bool 栅格在工具层被丢弃 | terrain_analysis.py:646-651 payload 仅 stats；output_semantic_type="stats"(:593) | 视域无图层；composition_packs scene_3d viewshed_scene_3d 模板的"视域面"无生产供给 |
| B2 | run_3d_analysis 仅编排 viewshed | scene_orchestration_tools.py:452-521，LOS/profile/surface decline `SCENE_CAP_ALGORITHM_PENDING_G3D03` | #1773 量测与 #1829 编排两 PR 互相没接 |
| B3 | evaluate_staleness 零生产调用 | staleness.py:58；全 app/ 无调用（仅测试消费）；run_3d_analysis 只返回 staleness_binding schema(:543-552) 无裁决执行方 | 拖点失效/camera-only 不失效未闭环 |
| B4 | elevation_profile ref:profile-* 无生产写入方 + 端点白名单拒绝 | 全仓 grep 无写入方；chat_session_views.py:118 硬编码 `ref:chart-` 前缀 | 剖面组件 ref 通道双重断链 |
| B5 | 无土方 cut/fill 体积算法；无建筑高度数据 | measurements.py 无 volume；无 3D building geometry 源 | #1773 时代明确不做的量测，本轮以充分 DEM 数据开放 |
| B6 | build_scene3d_plan/orchestrator + verdict 仅测试消费 | scene3d/orchestrator.py:153、verdict.py；生产调用为零 | Agent 运行时不产双门 verdict |

### DEM 数据现实

- 分析工具消费面：`raster_path`（data_dir GeoTIFF）→ validate_data_path + RasterResourceGuard 有界读（terrain_analysis.py:154-176）。不吃 URL/ref。
- 前端渲染地形源 registry（basemap_profiles）与后端分析面不互通（#1835 供给未合并，归 A 方向）。
- 合成 DEM：app/services/synthetic_terrain.py 仅测试使用。本 track 验收正例用合法合成 fixture（标注 synthetic）。

## 质量门（本地确定性）

- 总入口 `scripts/ci-local.sh`（与 production.yml 逐字对齐，tests/test_ci_local_gate_contract.py 守护）。
- 关键单门：ruff（app/tests/scripts）→ mypy（白名单棘轮）→ debt_ratchet → eslint(0 warning) → vitest(test:ci) → 契约层精选 pytest → import boundaries → OpenAPI/字段快照（API_SNAPSHOT_UPDATE=1 再生）→ 后端 pytest -m "not perf and not cartography and not real_services" --cov-fail-under=75 → cartography lane。
- pytest 配置在 pytest.ini（--cov=app 默认；定向跑用 --no-cov）。python: 主 checkout .venv（绝对路径复用）。
- ADR 分配：scripts/allocate_adr.py；master 最新 0275 → 本 track 用 0276。
- G05：tests/acceptance/gis_agent/corpus.py（F01–F26，47 pin 场景）；CLI scripts/g05_task_acceptance.py run/pin/compare/report。

## 资源限制遵守

- 16 核 / 62GB；构建 -j4；pytest -n 2 上限（资源紧则串行）；重型操作 flock /tmp/webgis-build.lock；OPENBLAS/MKL=1 等按任务书导出。
