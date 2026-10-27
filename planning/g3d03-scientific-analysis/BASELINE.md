# G3D03 Baseline — 科学计算算法与确定性空间量测

- **基线**：`origin/master` = `1115d683a5c8343e44d8a9777e4ce05e073c29e5`（2026-10-26 fetch；与工作单快照一致）
- **worktree**：`../webgis-wt-g3d03-scientific-analysis`，分支 `zcode/g3d03-scientific-analysis-v1`（新建，无冲突；g3d01/g3d02 worktree 不受影响）
- **开放 PR**：0 个（`gh pr list --state open` 为空，2026-10-26）
- **开放 issue**：仅 #1694（Swarm P2/P3 聚合审计 —— 工作单明确不在授权范围）
- **近期合并（内容级重叠核对对象）**：#1763 模板资产、#1762 任务级验收、#1761 工具能力对账、#1760 科学守门/MethodUseContract、#1759 数据集资格、#1758 issue 清偿、#1757 CI 红灯、#1756 Pi 结果契约、#1755 导出一致性、#1622–#1623 RenderWork/Interaction IR、#1621 项目恢复、#1617 语义缩放。

## 关键基线事实（文件:行号证据）

| 事实 | 证据 |
| --- | --- |
| viewshed 已实现（扇区视线角、observer/target height、max_distance、chunk 取消、_guard_cells） | `app/lib/geo_analysis/terrain.py:410-518`；descriptor `app/lib/gis/algorithms/terrain.py:290-324`；工具 `app/tools/terrain_analysis.py:494-563` |
| 坡度/坡向/曲率/TPI/TRI/粗糙度/hillshade/openness/geomorphon/SVF 已实现 | `app/tools/terrain_analysis.py:412`（derivatives）、`app/lib/geo_analysis/terrain.py:283`（curvature）等 |
| 水文族（D8/Dinf/watershed/contours 等 20+ 工具）已实现 | `app/tools/terrain_analysis.py` register_terrain_tools 全量 |
| **垂直基准概念全库不存在** | `grep vertical_datum|VerticalReference|垂直基准` 仅命中注释披露（`app/lib/cartography/terrain_encoding.py:16`、`methodology/descriptors.py:530` 文案）；`GISDatasetDescriptor` 无字段 |
| **无 LOS/通视线/地形剖面/坡面距离/地表面积 三维量测算法与工具** | `grep line_of_sight|surface_distance|surface_area|剖面` 在 lib/tools 零命中（"profile_curvature" 是曲率不是剖面） |
| 椭球测地已有（2D 点间距离/面积） | `app/lib/gis/crs_safety.py:84-85`（pyproj Geod）、`app/lib/geo_analysis/aggregation.py:241+`（geodesic 面积） |
| cos(lat) 米制换算政策 | `app/tools/terrain_analysis.py:176-190` `_metric_cell_sizes` |
| 栅格预算护栏（读取侧 + 算法侧） | `app/lib/geo_analysis/raster_guard.py:59`（250M px/1 GiB）、`terrain.py:943` `_guard_cells` → `ResourceScaleMismatch` |
| 协作式取消（checkpoint/cancellable） | `app/lib/cancellation.py:38/150/165`；viewshed 扇区边界 `terrain.py:518` |
| 科学证据块（MethodUseContract 输入侧） | `app/lib/gis/scientific_evidence.py:233` `build_evidence`；工具侧 `terrain_analysis.py:75` `_terrain_evidence` |
| 算法 descriptor 契约 ratchet（resource/cancel/tolerance 必填） | `app/lib/gis/algorithms/science_contract_v4.py:84-85`；测试 `tests/unit/gis/test_science_contract_v4_ratchet.py` |
| 参数契约注册表 | `app/lib/gis/parameter_contracts.py`（`apply_contract` 工具侧严格入口） |
| dispatch 自动 store FeatureCollection → geojson ref → ArtifactRecord | `app/services/tool_dispatch_service.py:1129-1147`（store）、`:1245-1275`（register_tool_artifact） |
| MapSpec 图层装配 | `app/services/analysis_cartography_converter.py:274`、`app/services/mapspec/pipeline.py:59-66`；前端 `frontend/lib/mapspec-runtime/` |
| oracle corpus + 漂移 pin | `tests/science_oracles/data/terrain.json`（216 cases）+ `test_corpus_drift_pin.py`（改数据须 `scripts/regen_science_oracle_manifest.py` 同 PR） |
| execution catalog 快照 | `docs/catalog/execution-catalog/execution-catalog.manifest.json`（counts 237/160/166/359，由 `scripts/gen_science_catalog.py` 再生成） |
| cache/复用键 | `app/lib/gis/analysis_reuse.py:51` sha256(tool::args) + 栅格内容指纹 + ref revisions（数据代次）；算法版本靠 descriptor version + 24h TTL 兜底 |

## M1 能力矩阵（已有 / 缺口 / 禁止重复）

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| viewshed（视域） | **已有，禁止重复** | 扇区扫描 + observer/target height + max_distance + 取消 + 护栏；无曲率/折射（已在 descriptor assumptions 披露，保持不动） |
| LOS/通视线剖面（两点、遮挡点、曲率/折射可选、双向） | **缺口 → 本 PR M3** | 全库无；新 `terrain.line_of_sight` + `line_of_sight_analysis` 工具 |
| 地形剖面（沿折线采样、累计爬升/下降） | **缺口 → 本 PR M2/M4** | 全库无；新 `terrain.profile` + `terrain_profile` 工具 |
| 坡面（地表）距离 vs 水平 vs 三维斜距 | **缺口 → 本 PR M2** | 全库无；新 `terrain.surface_distance` |
| 地表面积（3D facet vs 投影面积） | **缺口 → 本 PR M2** | 全库无；新 `terrain.surface_area`（2D geodesic 面积已有，归 aggregation，不重写） |
| 垂直基准/高程单位治理 | **缺口（全库级）→ 本 PR M2** | 新 `VerticalReference` 类型化契约：datum 词表 + z_units + 混算拒绝 |
| 建筑高度/体积、机场净空扫描 | **不在本 PR**（M4 可选项，需合格专业规则/参数，无实证数据源时不做虚构） | 诚实降级：PR 中说明 |
| 精度/误差输出（ErrorBounds、采样收敛披露） | **缺口 → 本 PR M2/M6** | 半步采样收敛指示 + 方法局限披露 |

## 高度 vs DEM 可观测来源区分

测量工具的 z 覆盖值（observer/target height、z override）与 DEM 高程属于**不同可观测来源**：
契约要求 z override 必须显式携带与 DEM 相同声明的 `vertical_datum` + `z_units`，否则类型化拒绝
（`VERTICAL_REFERENCE_MISMATCH`），绝不静默混算。DEM 自身无标准 z 元数据（GeoTIFF 不带 z 单位），
故由调用方声明 + 运行时披露，缺省按米 + `datum="unknown"`（允许单源运算，禁止跨 datum 混算）。
