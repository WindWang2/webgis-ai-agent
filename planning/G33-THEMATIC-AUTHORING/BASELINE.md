# G33-THEMATIC-AUTHORING — BASELINE

- 执行时间：2026-11-13（worktree stamp 20261112T174914Z，UTC 命名）
- base SHA：`ca7b3ec273f2b43336852951e2292a416b2238e6`（origin/master，与研究快照一致；执行时 fetch 后无新提交）
- 主工作区：`/home/kevin/projects/webgis/webgis-ai-agent`（HEAD 816705218，落后 origin/master，含用户未提交修改——不动）
- 本任务 worktree：`../webgis-worktrees/g33-thematic-authoring-20261112T174914Z`
- 分支：`zcode/g33-thematic-authoring-20261112T174914Z`
- default branch：master（gh repo view 确认 nameWithOwner=WindWang2/webgis-ai-agent）

## Open PR / 历史基线

- Open PR：仅 #2094（G30 可执行 GIS 技能作业）。G29/G31/G32/G34/G35 有 worktree 但尚无 PR。
- 最近合并：#2087(G27)/#2088(G24)/#2089(G23)/#2090(G28)/#2091(G26)/#2092(G25)/#2093(G22)；#2084-#2086 集成修复；#2033-#2036（Map Studio/undo/Pi-only）；G10-G20 见 #2021-#2081。
- G22-G28 的 GitHub reviews/inline/conversation comments 经查为空（与快照一致）；独立评审记录在 `planning/G22-*`…`G28-*` 内，已读取架构要点。
- Open issues：全部为 R1 审计项与 2026-11 前旧审计 issue（#2004-#2007 已由 G04/G05 时代 PR 清偿或部分清偿），与本领域无直接重叠；R1 不作为本任务目标。

## Pi 基线

- `.gitmodules` 上游 `earendil-works/pi.git`；gitlink v1.5.2 / b4afdddf（G22 #2093 已对齐）。本任务**不改 vendor**，在固定 pin 上本地构建/验证。
- Pi 工具链：`app/tools/registry.py` @tool → `_TOOL_MODULES`（app/tools/__init__.py:11-143）→ `pi_native_surface.py`/`tool_surface_v3.py` 动态投影 → dispatch 真相 `tool_dispatch_service.py:308`。

## 核心生产链（已核实，file:line 属 base SHA）

| 环节 | 位置 |
|---|---|
| 分类算法（5 法 + Jenks 采样上限） | app/lib/cartography/classify.py:18-177 |
| legend_spec 合同 + spec_to_paint 单一投影 | app/lib/cartography/thematic_spec.py:150-618 |
| create_thematic_map 工具 | app/tools/cartography.py:338-757 |
| 工具结果→MapSpec 层 | tool_dispatch_service.py:1819 `_author_display_result` → analysis_cartography_converter.py:274 `convert_analysis_to_mapspec_layer` → mapspec_store.layer_upsert (dispatch:1907) |
| 层 id/source id | `result-<callid>` / `<layer_id>-source`；source type=geojson + ref_id + profile + data_fingerprint + content_revision (pipeline.py:115-161) |
| MapSpec 存储版本/CAS/undo | mapspec/store.py:169；lifecycle_engine.py:319 apply_mutation（CAS :471-485）；agent_undo.py（_agent_undo_points 有界 20）；checkpoint.py |
| UpsertLayerIntent | mapspec/intents.py:188；handler mutation_handlers.py:99（同 id 整层替换 + _preserve_durable_presentation :169） |
| 用户 mutation 路由 | app/api/routes/mapspec_mutations.py:42-130（apply_gis_mutation origin=user, expected_revision） |
| apply_gis_mutation | app/services/gis_world_state/mutation.py（user-wins 守卫 + provenance） |
| 前端提交 | frontend/lib/mapspec/user-mutation.ts:362 commitLayerStyleAndCommit → POST mapspec/mutations；session-cursor.ts getCommittedMapSpec |
| live 渲染 | frontend/lib/mapspec-runtime/adapter.ts:369 legendSpecToColorExpression；paint-bridge.ts:158 toMapLibrePaint（规范键 radius/width + StyleMethod → compileStyleMethod 降低） |
| 图例渲染 | frontend/components/map/thematic-legend.tsx:17-48 → legends/{graduated,continuous,categorical,divergent} |
| SVG 静态导出 | app/services/mapspec_to_svg.py:369 `_resolve_paint_value`（constant/field/match/step/interpolate，含 circle-radius/radius :1702） |
| 字段统计现成面 | mapspec sources[sid].profile.fields (spatial_meta_profiler.py:360-406)；全量水合 session_data.get_ref_data_shared；分页 feature_pages.py:647；REST /layers/data/{ref_id} 等 (routes/layer.py) |
| G20 兄弟架构 | app/services/raster_authoring/（service.py commit_expression:203 单笔 UpsertLayerIntent+typed passthrough）；routes/raster_authoring.py（/raster-authoring/*）；tools/raster_expression_tools.py（webgis_raster_expression_*）；frontend/components/hud/raster-authoring-panel.tsx 挂载于 layer-style-panel.tsx:445-460；tests/unit/raster_authoring/ |
| widget 链 | histogram-slider.tsx:97 sendWidgetReply → canvas_affordance.py:1002 extract_pending_widgets；reply 值仅有尺寸封顶（:233-250），无语义校验 |

## 关键既有事实（去重依据）

1. create_thematic_map 只能“新建层”（result-<callid>），无 inspect/preview/apply 对已有层。
2. legend_spec 是 paint+图例共同来源（ADR-0078）；paint 投影 `spec_to_paint` 单点；前端镜像 thematic-paint.ts。
3. 直方图滑块 widget 已存在且发 widget_reply，但 reply 不直接产生 mutation——只进下一轮对话上下文。
4. HUD 样式面板只提交常数 color/width/radius（user-mutation patch_layer_style），无字段分布、断点、类表。
5. 大小/线宽数据驱动映射在 paint-bridge（live）与 _resolve_paint_value（SVG）两端均支持 StyleMethod，但**无作者入口**。
6. 常数字段合法退化（[v,v] 单级 graduated）已建模（thematic_spec.py:249-253）。
7. nodata 语义已建模（ADR-0078，三端一致：live withNoDataGuard / SVG nodata guard / legend）。
8. G27 scale_profile 已有；G16 标签覆写已有；G15 shared_expression 跨幅共享表达已有——G33 不动它们的所有权。
