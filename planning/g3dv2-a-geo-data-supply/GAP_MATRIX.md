# GAP_MATRIX — g3dv2-a-geo-data-supply

从「用户合法 DEM 进入 Data Fabric → 校验 → 可消费 3D 资产 → 自托管 → 多角色共享 → Agent 发现」逐环节真实现状（证据见 BASELINE.md）。

| # | 环节 | 现状 | 差距 | 本 track 动作 |
|---|---|---|---|---|
| G1 | 上传 GeoTIFF → session raster ref | upload.py:463-465 显式 vector-only | **核心断链** | 新增 raster/terrain ingest lane：校验合格→`store(payload={file_path,descriptor})` |
| G2 | DEM 摄入期资格校验（单波段/CRS/单位/垂直基准/nodata/异常值/旋转网格） | 仅渲染期 fail-closed | 摄入期无判定、无记录 | `app/services/terrain_ingest.py`：typed 校验 + 资格 receipt |
| G3 | 垂直基准/单位治理 | 无入口声明通道 | 未知 datum 被静默当可用 | 表单声明（vertical_datum/vertical_unit）+ 未知→display_only+reason；英尺显式换算留证据 |
| G4 | 会话 terrain TileJSON | 不存在 | MapLibre/Agent 需手拼 URL | `GET /layers/data/{ref}/terrain-tiles.json`（bounds/minzoom/maxzoom/encoding/attribution/receipt） |
| G5 | quota/rpm 真实执行 | 仅规划期 hint | 瓦片路由无限流，D11 留白 | `tile_rate_limiter.py`：tenant+source 键、429+Retry-After；接线 MVT/PMTiles/terrain 路由 |
| G6 | 地图资源可用性 typed 摘要（Agent/前端发现） | get_3d_capabilities 只见已绑定源 | 无目录级查询 | `GET /layers/terrain-sources`：静态画像可用性投影 + 会话 DEM ref 清单（schema 发布 app/contracts/） |
| G7 | 匿名会话瓦片取数 | 仅 X-Session-Token 头（MapLibre img 请求带不了头） | 匿名会话 terrain 不可用 | terrain 路由/TileJSON 加法式 `?token=` query（对齐 /sessions/{sid}/raster 先例） |
| G8 | fixture 契约族（负高程/无 datum/英尺/nodata 洞/畸形 CRS/压缩炸弹/越权 304/超 zoom） | 零散（synthetic/单测） | 无系统性入口契约 | tests/unit/test_terrain_*：10+ 类 fixture + 四段链 |
| G9 | 离线部署文档 + 可运行合成 fixture | 无 data-plane 文档 | — | docs/data-plane/offline-terrain.md |
| G10 | durable 预热 job（DEM→瓦片批量） | 无地形 job 类型 | 按需渲染已满足会话级 MVP | **显式不做**（DECISIONS D4），PR 列 limitation |

明确不做（防重复）：WMTS 服务端瓦片派生（#1771 范围外且无合法源）、静态画像目录变更/前端镜像再生成（零变更）、scene3d/orchestration/planner 消费端（B/C 领地）、#1805/#1797 等既有 issue 修复。
