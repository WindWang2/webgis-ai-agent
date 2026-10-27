# BASELINE — g3dv2-a-geo-data-supply

核对时间：2026-10-27T16:03Z（执行时实测，非任务书转抄）

## Git / 仓库状态

- `origin/master` = `70dab2b92917fe4f85375de959dad360288a60ad`（`git fetch origin master --prune` 后 `git rev-parse` 实测）——与任务书基线 SHA 一致，master 未再前移。
- 本地 checkout master = `1115d683a`，落后 origin/master 72 commits（纯 ancestor，无分叉）；本 track 工作全部在独立 worktree `/home/kevin/projects/webgis/webgis-wt-g3dv2-a-data`（分支 `zcode/g3dv2-a-geo-data-supply`，自 origin/master 新建）。
- **开放 PR：0 个**（`gh pr list --state open` 空集）。
- 已合并三维五 PR 确认（mergedAt 实测）：#1771（14:22:18Z）、#1834（14:28:17Z）、#1830（14:37:09Z）、#1829（14:43:20Z）、#1773（14:22:36Z）；基础 #1759–#1762、#1755–#1756 亦全部 MERGED。
- 开放 issue 56 个，全部为审查 backlog（#1694–#1828），其中与本方向相关的避让项见 PR_OVERLAP_MATRIX.md。

## 数据链现状地图（A0 产物，file:line 均在 worktree 实读）

### 数据生命周期状态表

| 数据类型 | 摄入 | 校验 | 瓦片/服务 | 消费 | 状态 |
|---|---|---|---|---|---|
| 矢量上传（GeoJSON/SHP/…） | upload.py:106-438（50MB、sha256 dedup、CRS 诚实） | V3 ingest pipeline（data_ingest/pipeline.py） | MVT（tile_service.py:109-187） | MapSpec geojson ref | **可用** |
| 用户 GeoTIFF/COG 上传 | upload.py:222-223 parse_raster（200MB、剖析 nodata/overviews/band_stats） | 仅剖析；**ingest lane 显式跳过栅格**（upload.py:463-465） | ❌ 不能注册为 session ref | ❌ 无法成为 terrain 源 | **断链（本 track 核心缺口）** |
| 会话 raster ref → Terrarium XYZ | —（生产者只有工具 PNG 与合成 DEM） | 渲染期 fail-closed（raster_tile_service.py:592-603） | layer.py:749-794 + render_terrarium_tile（窗口读+双通道再投影+nodata 透明） | MapSpec RasterDemMapSpecSource + scene.terrain | **可用（等待上游供给）** |
| 合成 DEM | synthetic_terrain.py:92-183（SYNTHETIC_DEM tag、display_only） | 同上 | 同上 | 同上 + ELEVATION_SOURCE_PROFILES | **可用（演示路径）** |
| PMTiles 自托管 → XYZ | operator 配 config/sources/*.yaml 或 POST source | 魔数嗅探（pmtiles_tile_service.py:51-69） | data_fabric.py:914-987（ETag/304/204/nosniff） | 任意 XYZ 消费者 | **可用** |
| 目录 MVT | data fabric catalog | 鉴权先于缓存（data_fabric.py:879/_authorize_catalog_item:169-182） | tile_service.py | 前端 spatial catalog | **可用** |
| TileJSON | — | — | — | — | **完全不存在**（app/api grep 零命中；#1771 BASELINE.md:39 自认） |
| WMTS 瓦片派生 | — | 仅 GetCapabilities 元数据（wms_wmts_adapter.py:336-445） | 无服务端瓦片路由 | — | **不存在**（#1771 明示范围外） |
| quota/rpm 执行 | — | QuotaDecl 声明（source_registry.py:76-83） | **无任何执行层**：rpm 仅进规划期 hint（planning/cost_model.py:95-124）；瓦片路由无限流 | — | **不存在（#1771 D11 留白）** |
| 静态高程源画像 | ElevationSourceProfile（basemap_profiles.py:153-208，synthetic⇒display_only、analysis_grade 须 session_ref+datum） | Pydantic 不变式 | 生成镜像 frontend/lib/basemap-profiles.generated.ts（`python -m app.lib.cartography.basemap_profile_projection`） | renderer 兜底 + terrain-governance | **可用** |
| Agent 发现 terrain 源 | — | — | — | get_3d_capabilities 只看 spec 内已绑定源（scene3d/capabilities.py:114-128），无目录级查询 | **部分**（本 track A6 补 typed 摘要，schema 层对接） |

### 关键机制锚点（实现复用）

- ref 注册：`session_data_manager.store(session_id, payload, prefix)` → `ref:{prefix}-{uuid16}`（session_data.py:356-364）；terrain-tiles 路由经 `get_ref_data` 读 payload `file_path`/`path`（layer.py:820-827）+ `validate_data_path(DATA_DIR)`（layer.py:838-841）。
- 会话鉴权：`require_owned_session`（auth_history_bridge.py:57-73，X-Session-Token 头）；匿名图片取数已有 `?token=` 先例（tool_dispatch_service.py:1783-1793 `/sessions/{sid}/raster/{id}.png?token=`）。
- 缓存/失效：raster_tile_service 内部 LRU（8 元组 terrain 键）+ register_raster_ref 生命周期钩子（raster_tile_service.py:242-304）。
- SSRF 面（现状，只记录不修）：validate_url/_pinned_dns（security.py:99-188, 354-450）、PMTiles 解压预算（pmtiles_adapter.py:71-119）、resolve_safe_local_path（:365-377）。

## 已核实不复做清单（#1771 交付物）

typed basemap/elevation profile 单一真相 + 投影锁、OpenFreeMap 三风格、PMTiles→XYZ 路由、合成 DEM fixture、terrain_encoding Terrarium 编码 + fail-closed 守卫、terrain-governance 前端三态、署名治理。本 track 仅在其上补：摄入 lane、资格记录、TileJSON、quota 执行、typed 可用性摘要、fixture 契约族。
