# LOOP_STATE — g3dv2-a-geo-data-supply

迭代账本（goal-loop 纪律：每轮单一聚焦改动 → 亲跑验证 → 记账）。

| 轮 | 改动 | 验证 | 结果 | 下一步 |
|---|---|---|---|---|
| 0 | 基线核对+双侦查审计+worktree 建立+账本落盘 | git/API 实测（见 BASELINE） | ✅ | 实现 terrain_ingest（TDD） |
| 1 | T1 contracts/terrain_sources.py + services/terrain_ingest.py + 27 用例 fixture 族 | pytest tests/unit/test_terrain_ingest.py | ✅ 27 passed | T2 upload lane 接线 |
| 2 | T2 upload.py raster lane（表单 vertical_datum/vertical_unit + terrain 响应字段）+ 8 用例 + 既有上传回归 | pytest test_upload_raster_lane.py + test_upload_identity_v4/test_upload_executor | ✅ 8+22 passed | T3 TileJSON 端点 |
| 3 | T3 TileJSON 端点 + terrain 路由 ?token= 匿名支持 + 7 用例（含四段链闭环）+ terrain 回归 30 例 | pytest test_terrain_tilejson.py + terrain 回归 | ✅ 7+30 passed | T4 摘要 API |
| 4 | T4 可用性摘要 API（静态画像部署投影 + 会话 DEM receipt 透传 + 3D 总判）+ 7 用例 + payload name | pytest test_terrain_sources_summary.py | ✅ 7 passed | T5 quota 执行层 |
| 5 | T5 tile_rate_limiter（滑动窗+env 配置）+ MVT/PMTiles/terrain 三路由接线 + make_safe_session rate_limit 出站 seam + 9 用例 + df 路由回归 15 例 | pytest test_tile_rate_limiter.py + test_pmtiles_tile_route/test_deep_review_api_data_tiles | ✅ 9+15 passed | T6 文档 |
