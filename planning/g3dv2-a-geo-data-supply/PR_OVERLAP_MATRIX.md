# PR_OVERLAP_MATRIX — g3dv2-a-geo-data-supply

基线：origin/master=70dab2b9，开放 PR=0。矩阵覆盖已合并 PR（不重做）与开放 issue（避让/兼容验证）。

## 已合并 PR（起点，绝不重造）

| PR | 内容 | 本 track 关系 |
|---|---|---|
| #1771 | typed 底图/高程画像、OpenFreeMap、PMTiles→XYZ、合成 DEM、地形治理 | 复用其 ElevationSourceProfile 词表/不变式、terrain_encoding、PMTiles 路由；补其留白（TileJSON、quota 执行、真实 DEM ingest） |
| #1834 | 3D 场景内核/3D Tiles/glTF | 只读 scene3d 消费面（B 领地），本 track 不改 |
| #1773 | 三维量测契约/垂直基准治理 | C 领地；本 track 的资格记录与其词表对齐但不改其代码 |
| #1829 | Agent 三维编排（get_3d_capabilities 所在 scene_orchestration_tools.py） | C 领地；A6 只发布 typed schema 供其后续消费 |
| #1830 | 三维制图产品 | 无重叠 |
| #1580 (W12) | 目录瓦片服务化/统一缓存身份 | 复用 tile_identity/quota 路由模式 |
| #1759 (G01) | 数据集语义身份/资格 | A1 资格词表对齐（analysis_grade/display_only/unavailable） |

## 开放 issue 避让

| Issue | 归属 | 本 track 处置 |
|---|---|---|
| #1805 CGCS2000 classify_crs 误判地理坐标 | 审查 backlog（在修） | **不修**。本 track 摄入校验不用 classify_crs，直接 rasterio CRS→transform_bounds 实测；仅加 DEM 入口不变量（CRS 不可解析→unavailable）作兼容验证 |
| #1797 terrain 水文纯 Python DoS | 审查 backlog | 不碰 measurements；本 track 只做摄入侧预算（窗口读、剖析降采样） |
| #1809-#1811 MapSpec filter 族 | 审查 backlog | 不涉及（不动 mapspec 编译器） |
| #1821/#1824/#1826/#1827 审查汇总 | 汇总 issue | 只读参考 |
| #1784/#1785/#1786 _pinned_dns/validate_url 族 | 安全 backlog | 只记录现状（BASELINE §SSRF），不修不扩展其语义 |
| #1792 temporal_raster 任意路径读 | 安全 backlog | 与本 track 新端点无共享代码；新端点自带 validate_data_path+鉴权先于缓存 |

## 并行 track 边界（B/C）

- B（scene3d）：`frontend/lib/map-kit/scene3d/**` 本 track 零修改。
- C（量测/编排）：`app/lib/geo_analysis/measurements.py`、`app/tools/scene_orchestration_tools.py`、Agent planner 零修改。A6 摘要 schema 发布在 `app/contracts/`（新文件），供 C 后续对接。
- 共享区（允许变更）：`app/api/routes/upload.py`（raster ingest lane）、`app/api/routes/layer.py`（新端点 + tile 路由加法式 query token）、`app/services/terrain_*.py`（新建）、`app/services/data_fabric/`（quota 执行层新文件 + 路由接线）、`tests/**`、`docs/data-plane/**`、`planning/g3dv2-a-geo-data-supply/**`。
- 生成镜像 `frontend/lib/basemap-profiles.generated.ts`：**不触碰**（静态目录零变更 → 无需再生成）。
