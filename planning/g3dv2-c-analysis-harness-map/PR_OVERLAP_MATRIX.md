# PR_OVERLAP_MATRIX — g3dv2-c-analysis-harness-map

基点：origin/master@70dab2b（2026-10-28 实时 gh 核对）。

## 排除矩阵（我不得做 / 仅消费）

| 来源 | 状态 | 变更面 | 本 track 关系 |
|---|---|---|---|
| PR #1835 `zcode/g3dv2-a-geo-data-supply`（A 方向） | OPEN（head 4c348c8，base=当前 master） | app/api/routes/{data_fabric,layer,upload}.py、app/contracts/terrain_sources.py、app/services/{terrain_ingest,data_fabric/security,data_fabric/tile_rate_limiter}.py、tests/unit/test_terrain_* / test_upload_raster_lane、openapi/api_contract 快照 | DEM 供给/摄入/瓦片限流归 A。本 track 只消费 data_dir GeoTIFF + 合成 fixture，不改这些文件。**潜在冲突点**：若我改 openapi.json 快照，合并时需按双方实际端点重新再生（additive 合并可行）。不主动改 upload/layer 路由。 |
| worktree `webgis-wt-g3dv2-b-scene`（B 方向，分支 zcode/g3dv2-b-scene-runtime） | 开发中（5+ commit 未推 PR） | frontend scene3d manager（presentation/lifecycle/resource budget/SceneController UI）、backend observation DTO scene3d_assets、render_work_corpus、openapi.json、frontend/messages/{zh-CN,en}/map.json | B 拥有 scene3d 前端内核与 observation 证据面。本 track 不改 frontend/lib/map-kit/scene3d/**；不改 scene3d_assets DTO；新组件若需要 i18n 键，只做 additive（map.json 双语），与 B 的 additive 键正交。后端 observation 若需携带分析 ACK，走已有 work/envelope 通道，不加第二 DTO。 |
| issue #1797 terrain 水文纯 Python DoS | open（专人） | terrain 水文 | 不抢修；我的新算子自带有界窗口/chunk，不放大该问题 |
| issue #1805 classify_crs CGCS2000 投影误判 | open（专人） | app/lib/gis CRS 分类 | 不替其修通用 classify_crs；我的算法入口独立做测地/投影米制防错试验（本 track 内防错，不越界改共享分类器） |
| issues #1809–#1811 MapSpec filter、#1821/#1824/#1825/#1826/#1827 审查汇总 | open | mapspec 编译器 filter、geo_analysis/harness/前端审查清偿 | 仅做兼容验证；若我的改动触及同类代码路径，只做与本 track 直接相关的最小非重叠修复并留互斥证据 |

## 我拥有（本 track 独有新增）

- app/lib/geo_analysis/measurements.py 的**新增**函数（cut/fill volume、viewshed 几何提取）；不改既有四算法数值路径
- app/tools/terrain_analysis.py：viewshed_analysis 工具的几何输出扩展 + 新 cut/fill 工具注册（复用既有注册器）
- app/tools/scene_orchestration_tools.py：run_3d_analysis dispatch 扩展（LOS/profile/surface/volume）+ staleness 生产接线
- app/services/gis_harness/scene3d/staleness.py：生产消费方接线（不改其纯函数语义）
- elevation_profile 生产 artifact 写入方 + chat_session_views.py 白名单扩展（ref:profile- 前缀）
- app/lib/cartography/composition_packs/ 新增 2–3 个专业组合模板（不动 scene_3d.py 既有三模板语义）
- tests/science_oracles/ 新 oracles、tests/acceptance/gis_agent/ F27 族、planning/g3dv2-c-analysis-harness-map/

## 冲突预案

- openapi.json：我若新增/修改端点参数才触碰；预期只在白名单逻辑（无 schema 变化）则不动快照。若必须动，交付前重新 `API_SNAPSHOT_UPDATE=1` 再生并在 PR 说明与 #1835/B 的 additive 合并路径。
- frontend map.json i18n：仅新增键；B 的键改动按文件行正交。
- render_work_corpus：不触碰（B 拥有）。
