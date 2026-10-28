# PLAN — g3dv2-a-geo-data-supply

目标：闭合「用户 GeoTIFF/DEM 上传 → 摄入校验与资格记录 → 会话 raster ref → Terrarium XYZ + TileJSON → 限额服务 → typed 可用性发现」生产闭环，全部 fail-closed、可测试、可审计。

## 垂直切片（单一 PR，原子 commit 分组）

```
用户上传 .tif (POST /upload, register_ref=1, vertical_datum=EGM96 声明)
  └─ _run_ingest_lane → raster 分支 → terrain_ingest.validate_and_register()
       ├─ 校验：魔数/GTiff、单波段、CRS 可解析、北向上无旋转、nodata、
       │        值域/异常尖峰、单位（声明>band tag>假定 m，非 m 显式换算+证据）、
       │        垂直基准（声明>geokey>None→display_only）、SYNTHETIC_DEM tag→强制 display_only
       ├─ 资格 receipt：analysis_grade | display_only | unavailable + reasons[]（typed）
       └─ 合格/仅显示 → session_data_manager.store(payload={file_path, kind, descriptor})
                        → ref:terrain-<hex>（幂等：同会话同 sha256 → 复用既有 ref）
  ├─ GET /layers/data/{ref}/terrain-tiles/{z}/{x}/{y}.png（既有路由 + ?token= 匿名支持）
  ├─ GET /layers/data/{ref}/terrain-tiles.json（新：TileJSON 3.0.0 + terrain 扩展字段）
  ├─ GET /layers/terrain-sources?session_id=（新：typed 可用性摘要）
  └─ tile_rate_limiter：MVT/PMTiles/terrain 路由 per (tenant|session, source) 429+Retry-After
```

## Commit 分组

1. `feat(terrain): DEM 摄入校验与资格记录服务`（terrain_ingest.py + 单测 fixture 族）
2. `feat(terrain): 上传 raster ingest lane 接线`（upload.py + 表单声明字段 + 单测）
3. `feat(terrain): TileJSON 端点 + 匿名 query token`（layer.py + 单测）
4. `feat(terrain): 地图资源可用性摘要 API + contracts schema`（layer.py/contracts + 单测）
5. `feat(data-fabric): 瓦片服务 quota 执行层`（tile_rate_limiter + 三路由接线 + 单测）
6. `docs(data-plane): 离线部署与 fixture 运行手册` + planning 账本

## 依赖顺序

terrain_ingest（核心）→ upload lane → TileJSON → 摘要 API → quota → docs。测试先行：每个 commit 先写红测再实现（goal-loop 纪律）。

## 资源预算

- 剖析读取一律降采样窗口读（_STATS_MAX_SIDE 同款 ≤2048），绝不整幅进 NumPy。
- 上传上限沿用 200MB；换算副本（非米单位）仅按需生成且记录双 sha256。
- pytest -n 2 --no-cov 定向；ruff/mypy 定向；前端无改动（镜像零变更）→ 不跑 vitest/typecheck（除非共享文件被 touched：upload/layer 均后端）。
