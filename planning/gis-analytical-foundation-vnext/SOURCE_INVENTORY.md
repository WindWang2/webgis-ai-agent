# SOURCE_INVENTORY — B0 能力盘点（2026-11-06，基线 b6ac970eb）

> 数据来源：2 只读勘察 subagent 报告 + 主 agent 亲读 contracts/executor/families/orchestrator/recompute/state 全文复核。行号在 worktree 上核验。

## 1. 需求状态表（implemented / partial / missing / in_other_PR / issue_assigned）

| 需求（任务书） | 状态 | 证据 |
|---|---|---|
| AnalysisWorkflowContract 薄编排（五族/steps/role/units/precondition） | implemented（#1869） | contracts.py:106-119；families.py:71-182 |
| 真实 dispatch 单一执行缝 + 幂等指纹复用 | implemented | executor.py:229-278,326-331 |
| 选择性重算（role/param 变更→下游闭包；style 恒 ∅） | implemented | recompute.py:68-123 |
| typed 阶段产物（geometry/CRS 实测/vertical datum/time/aggregation basis/extent/nodata/ownership/provenance） | **missing** | envelope 仅 ref/声明 units/count/stats（contracts.py:138-159）；executor.py:406 crs 恒空（工具响应无 crs 键，core.py:66-83） |
| ref 衔接 typed schema 严格门 | **missing** | _hydrate 仅 isinstance+行数（executor.py:156-165）；geometry_kinds 声明不校验（orchestrator 无几何分支） |
| 链① 行政统计 | partial | 族在（families.py:72-99）；per_capita_rate 硬编码 denominator="population"（:95）；分母字段缺失=运行期 failed 而非 plan 期 typed skipped |
| 链② 栅格→zonal→矢量→图 | **missing（族级）** | zonal_stats 走 raster_path: str（advanced_spatial.py:102）；role 水合只产 dict 载荷（executor.py:197）→ 类型不通；栅格链不在五族 |
| 链③ 网络→覆盖→行政汇总 | partial | 族在（families.py:100-119）；不可达设施 5m 假面无门（network.py:319-325→覆盖统计）；无最近设施距离步；>10 万行路网 plan 期无预算估计 |
| 已知答案 oracle（跨180/无效多边形/图级断网/zonal 覆盖率） | partial | science_oracles 15 域：CRS 分类/边界/nodata 覆盖好；跨180 仅 1 条行为锚；无效多边形/断图/zonal 覆盖率缺 |
| 状态机 stale/validated/not_evaluated | partial | stale 词表在但无人赋值；final gate 只是返回体字段不进 state（analysis_workflow_tools.py:407） |
| partial 不当 final（产品层） | partial | awf 工具诚实（:393）；map_product 仅全图层失败才 success=False（gis_harness/tools.py:1675-1680）；science_blocked 仍 ok=True（product_compiler.py:343-385） |
| 预算预检/估计 | missing | executor 只有执行期硬门（executor.py:142-146）；plan 期无 estimate |
| discovery metadata/推荐下一步 | partial | execution_catalog 有能力/资源面；workflow plan 工具无结构化 next_step |
| 五类 NL 黄金任务 | partial | ①②已覆盖（f01/f03/f04a/f04b/f28-01）；③高风险区域设施统计**缺场景**（hazard_points fixture 现成 fixtures_data.py:209）；④栅格按行政区汇总只有单元锁；⑤最近医院网络距离只有工具+perf 锁无场景 |

## 2. 真实断点（8+2，勘察 A 报告，主 agent 抽核）

1. `statistical_zones.grid_rate` 退化为改名计数（families.py:164-173 + advanced_spatial.py:2448 分支条件使 count 型走纯计数；2544-2548 只 rename）
2. 断网 5m 假面进覆盖统计：network.py:319-325 reachable=False 披露只在 summary 文本；envelope.warnings 只收结果 `warnings` 键（executor.py:397-398），to_llm_response 无该键（core.py:72-83）→ summary 丢失
3. envelope.crs 恒空 + units 声明非实测（executor.py:406-407）
4. 10 万行预算门封死真实路网（executor.py:51,142-146；max_features 只能调低 analysis_workflow_tools.py:320-324）；plan 期无估计
5. 角色几何类型不校验 + h3 静默质心化（families.py:189 从不对照 binding；aggregation.py:589-591 centroid()）
6. per_capita_rate 分母字段缺失=运行期 failed（families.py:95 硬编码；aggregation.py:423-426 MissingRequiredField）而非 plan 期 typed skipped
7. 同 turn dispatch 去重把合法步骤判死（tool_dispatch_service.py:330-346 status=repeated → executor.py:250-255 标 failed TOOL_DISPATCH_REPEATED）
8. hazard M:N 无收口算子 + spatial_join 无算法目录锚点（evidence.py:24-31 TOOL_ALGORITHM_IDS 缺失）
9. （备选）zonal 无逐 zone 有效像元覆盖率（raster_ops.py:219-221 剔 nodata 后不可区分）；栅格侧 preflight not_evaluated（advanced_spatial.py:108-126）
10. （备选）product 层 science_blocked 仍 ok=True（product_compiler.py:343-385）

## 3. 三链组成现状（file:line 见勘察 A 报告 §4）

- 链①：plan/run 工具 → admin_facility_distribution（zones_count/area_density/per_capita_rate 全走 spatial_aggregate）→ aggregate_with_denominator（aggregation.py:346-542，显式分母/零分母 rate=None/intersects 约定:453）
- 链②：raster_resample（raster_transform_tools.py:137，产物 ref）/clip_layer → zonal_stats（advanced_spatial.py:80-126，zones FC + raster_path 接受 ref）→ SpatialAnalyzer.zonal_stats（spatial_analyzer.py:485-531）→ raster_ops._windowed_zonal_stats（128-249，窗口读/nodata 剔除:219-221/无效几何修复披露:184-193/越界 zone None 行:196-205）
- 链③：service_accessibility（isochrone_network→overlay intersection→coverage_population optional）→ geo_analysis/network.calculate_isochrones（32-375，Dijkstra/设施投影:133-139/不连通 5m 假面:319-325）；最近设施：network_closest_facility（network_tools.py:513，engine.solve_closest_facility engine.py:652，routes 带几何+snap 证据）

## 4. 测试/数据资产（勘察 B 报告）

- 测试车道：`-m cartography`（G05 69 场景）/ `-m perf`（baselines.json fail-closed，PERF_UPDATE_BASELINES=1 刷）/ `-m heavy` / `-m real_services`；无全局测试锁（xdist per-worker sqlite）
- G05 CLI：`python scripts/g05_task_acceptance.py run --profile smoke|focused|heavy`
- science_oracles：15 域 data/*.json + 显式消费测试 + sha256 漂移钉扎（test_corpus_drift_pin.py）——新增数据集须同步 pin
- fixtures（合成确定性）：school_points 12点×5区（fixtures_data.py:46）/ district_polygons 5面带 population（:100）/ road_network 5×4 网格（:222）/ raster_nir/red/nodata 32×32（:192-200）/ raster_dem cone 61×61（:299）/ hazard_points（:209）/ wrong_crs+unit_conflict（:127,138）/ gis_samples bowtie（gis_samples.py:22-25）
- 真实本地数据在 LOCAL_GEODATA_DIR（仓库外）：ChinaAdminDivisonSHP（GCJ-02 壳！local_admin.py:32-37）/ osm_gpkg / gd_pois.gpkg / yearbook.sqlite——测试不依赖
- 能力对账：8 面引擎 tool_capability_conformance.py + live registry 棘轮（fail=0）

## 5. 环境

- venv：`/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python`（3.13.17；geopandas/shapely/networkx/h3/rasterio/pytest 9）
- worktree 无 frontend/node_modules（本任务计划零前端改动；如需 tsc/vitest 再 symlink）
- 同机多 agent → -j2；基线：analysis_workflows 83P/1S
