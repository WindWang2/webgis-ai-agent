# TASKS — g3dv2-a-geo-data-supply

- [x] A0 审计（BASELINE.md / GAP_MATRIX.md / PR_OVERLAP_MATRIX.md）
- [x] 账本框架（PLAN/DECISIONS/TEST_MATRIX/INTERFACE_CONTRACTS/RISKS/LOOP_STATE）
- [ ] T1 `app/contracts/terrain_sources.py` schema + `app/services/terrain_ingest.py`（校验/资格/注册/幂等）+ fixture 族单测
- [ ] T2 upload.py raster lane 接线（表单声明字段）+ test_upload_raster_lane.py
- [ ] T3 layer.py TileJSON 端点 + `?token=` 支持 + test_terrain_tilejson.py
- [ ] T4 摘要 API + test_terrain_sources_summary.py
- [ ] T5 tile_rate_limiter + 三路由接线 + test_tile_rate_limiter.py
- [ ] T6 docs/data-plane 离线手册
- [ ] T7 质量门全绿 + EVIDENCE.md/PERF 记录
- [ ] T8 独立 review → 修复 → 复验（INDEPENDENT_REVIEW.md）
- [ ] T9 PR_DESCRIPTION.md + push + gh pr create
