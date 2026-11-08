# AUDIT_COVERAGE — 审查覆盖与未覆盖声明

基线：origin/master 585812ee4。审查执行：3 个并行 subagents（A/B/C）+ 主 Agent 自审与整合验证。

## 子代理 A（后端 GIS/科学算法）— 深读约 24,000 行

深读（全文或核心大部）：spatial_analyzer.py(717)、spatial_operator.py(51)、spatial_tasks.py(847)、feature_aggregate.py(233)、network/ 全部 17 文件(6,697)、geo_processor/geometry.py+overlay.py(336)、geo_analysis/statistics.py(~2,300 深读)、aggregation.py(667)、raster_ops.py(266)、raster_math.py(759)、density.py(910)、network.py(477)、heatmap_grid/geometry_ops/spatial_weights/_vector(1,083)、geo_raster/reader.py(367)、lib/gis/longitude+measurement+crs_safety(1,245)、raster_store/raster_stretch/raster_tile_service/feature_pages(1,729)、mvt.py(~770 关键段)、geocompute/ops.py(~1,070)+normalization+task_identity、spatial_decision 3 文件(705)、gis_harness/completion/geo_bounds+heatmap_restyle(163)。

运行时验证：路由中点切边/同边双向/单行道 3 组行为；zonal_statistics 像元中心语义精确值；Moran I/Geary C 与 esda 对拍（0.7996 vs 0.8017 合理差异）；geocompute 混合 CRS join；service_area 缺 dataset 崩溃。

未深读（标注）：geo_analysis 的 terrain(3480)/kriging(3471)/spatial_regression(1990)/rs_*/sar_*/point_pattern(1722)/measurements(908)/interpolation 系；geocompute executor(2428)/cluster/*/durable/budgets/partitioning；spatial_decision 其余 15 文件；gis_harness 其余 ~38k 行；data_fabric/query/federation 仅 spatial_join_local 一节。

## 子代理 B（安全/并发）— 深读约 14,000 行

深读（全文）：session_ownership(27)、session_data(1,087)、session_data_redis(1,952)、session_state_atomic/redis_aliases/redis_maintenance/control_budget/control_refs/context_refs(496)、distributed_lock(695)、singleflight/task_queue/task_tracker(757)、export_image_security/export_paths/export_batch_queue/export_lineage(536)、s3_blob_store/durable_blob_store/ref_payload_cache/ref_lifecycle(1,954)、lib 的 tool_security/tool_cache/tool_result_errors/cancellation/secret_lease/redaction/json_size(1,510)、auth_history_bridge/audit/artifact_registry/artifact_lifecycle/artifact_revisions/runtime_asset_assembly(3,037)、ws_service/cache_broadcast/ws 路由(728)、路由 15 个(~5,100 定向深读)、capability_runtime/extensions_platform(~600 定向)、mvt SingleFlightManager(93)、所有权模型追踪(~400)。

未深读（标注）：atlas_jobs/cockpit/geoai/geocompute/knowledge/lakehouse*/mission_runtime/portfolio/project*/storymap/visual_repairs/review_proposals/workflow_*/ws_collab/explorer/data_quality/data_preparation/display_confirmation/interaction_events/mapspec_mutations/situation_views/feature_edits 路由（部分仅验鉴权面）；spatial_events/ 内部；extensions_platform 其余。PROTECTED 文件未做结论性审计。

## 子代理 C（前端/制图交付）— 深读约 12,000 行 TS/TSX + 5,000 行后端

深读（全文）：前端 store 9 文件(1,594)、SSE/API 8 文件(2,463)、hooks 11 文件(3,786)、组件/导出(~2,000)、lib/session/map-state-restore(562)、collab/adopt(262)、layers/layer-ops(183)、mapspec/user-mutation 关键段；后端 mapspec_layer_pipeline/source/filter/expr/svg_bounds/svg_labels/svg_payloads(1,086)、publication_export(957)、publication_render_worker(90)、render_work_supply(249)、map_product_service(580)、cartography_service(320)、cartography_ratchet(380)、atlas_batch(805)、storymap(93)、mutation_registry restore 段、gis_world_state/mutation 段、project restore 路由段。

未深读（标注）：layers-tab.tsx(1349)/chat-tab/template-gallery-v2(576) 仅结构浏览；cartography_runtime repair 主循环(929-1537)；raster_cartography_converter(565)；mapspec/ 其余；atlas_batch manifest/page_compiler/planner/tasks 交叉引用读。

## 主 Agent 自审

- 工具/算法契约层（链路 4，未分配区）：app/services/tool_catalog.py(472 全文)、tool_metrics.py(426 全文)、tool_dispatch_gates.py(165 全文)、app/tools/registry.py dispatch 链(1145-1345 深读)、local_poi.py 摄入链(96-280)。结论：多轮加固区（#374→#1549 注记密集），未发现新缺陷。
- C-1/A-1/B-5/A-2/B-4/B-1 六项 finding 的逐行源码交叉验证 + 修复实现 + 测试先红后绿。

## 覆盖率诚实声明

- 一方生产代码总量：app/ 654k 行 Python + frontend/ 226k 行 TS/TSX。本轮四路审计实际深读约 57,000 行（≈6.4%），聚焦六条链路的高风险面（科学算法核、并发原语、鉴权边界、状态同步、制图交付）。
- 未深读区域已在各 subagent 报告逐项标注；其中 PROTECTED 文件（#2011-#2017 占有）按任务规则不做结论性审计。
- 审计方法：真实 Read + 调用链追踪 + 运行时行为验证（3 组网络行为、esda 对拍、CRS join、崩溃复现），非仅 grep。
