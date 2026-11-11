# G18 TEST_MATRIX — 验收场景 → 测试映射

测试根：`tests/delivery/`（本任务新增；无全仓回归依赖）。

| 场景 | 测试 | 结果 |
|---|---|---|
| 1 session FC → GeoJSON 反读 | test_service_flow::test_session_fc_geojson_delivery_roundtrip | ✅ |
| 2 → GeoPackage | test_service_flow::test_session_fc_gpkg_delivery | ✅ |
| 3 → GeoParquet | test_service_flow::test_session_fc_geoparquet_delivery | ✅ |
| 4 fabric-parquet fast path 字节复制 | test_service_flow::test_fabric_parquet_fast_path | ✅ |
| 5 表格 → CSV 顺序/中文/空值 | test_service_flow::test_csv_delivery_field_order_unicode_null | ✅ |
| 6 投影 → GeoJSON 真实转换 | test_service_flow::test_projected_geojson_reprojects + writers 控制点 oracle | ✅ |
| 7 缺 CRS 拒绝 + 可选确认 | test_service_flow::test_missing_crs_geojson_blocked_other_formats_ack | ✅ |
| 8 长字段名 SHP 映射 | test_service_flow::test_shapefile_long_field_names_explicit_map | ✅ |
| 9 混合几何分组计数 | test_service_flow::test_mixed_geometry_grouping_counts | ✅ |
| 10 null/Z/多部件 | writers oracle（null 几何保留断言贯穿） | ✅ |
| 11 大整数/布尔/时区 | writers oracle + loss 分析（int64_downgrade/bool_to_string） | ✅ |
| 12 CSV 公式前缀两模式 | test_service_flow::test_formula_prefix_csv_modes | ✅ |
| 13 恰好 7 ID + 缺失披露 | test_service_flow::test_ids_scope_exact_seven_and_missing_disclosed | ✅ |
| 14 sample 不称全量 | test_service_flow::test_sample_ref_not_labeled_full | ✅ |
| 15 源修订变化复核 | test_service_flow::test_source_revision_changed… + faults::test_selection_epoch_change_blocked | ✅ |
| 16 项目持久 revision | resolver 层（_resolve_project_artifact durable 读回）；服务级由 16 场景的 durable store 单测覆盖 | ✅(代码) / 项目路径 e2e 待 routes 套件 |
| 17 过期 ref 不伪造成功 | test_service_flow::test_expired_ref_no_fake_success | ✅ |
| 18 幂等复用 + 重新鉴权 | test_service_flow::test_idempotent_resubmit_reauth | ✅ |
| 19 跨 owner 404 | test_service_flow::test_cross_owner_404 + faults 注入反验 | ✅ |
| 20 取消/崩溃无半成品 | test_recovery_faults::test_cancel_mid_run_no_half_file / test_writer_crash_fails_clean | ✅ |
| 21 重启后恢复 | test_recovery_faults::test_completed_survives_simulated_restart | ✅ |
| 22 Pi 工具预览→提交→收据 | test_routes_tools::test_tools_registered_and_preview_dispatch | ✅ |
| 23 驱动缺失/在装能力一致 | contracts.probe_format 真驱动探测 + routes::test_capabilities_route | ✅ |
| 24 老导出不被破坏 | routes::test_old_geojson_export_untouched + 基线套件保留 | ✅ |
| 25 框选 >50 + 谓词重放完整 | test_service_flow::test_truncated_selection_with_predicate_replays_full | ✅ |
| 26 ids_truncated 阻断/子集确认 | test_service_flow::test_truncated_selection_blocks_then_subset_ack | ✅ |
| 27 选择/epoch 变化冻结 | faults::test_selection_epoch_change_blocked | ✅ |

## 既有回归（必须继续通过）

- tests/unit/test_geoparquet_fast_lane_v5.py（含本任务修复的 path 键基线失败）
- tests/test_export_geojson.py、tests/test_map_export_download_auth.py
- 前端：test/i18n（key 完整性/裸 CJK 守卫）、tsc --noEmit

## 性能/资源记录（WP12）

- READ_BATCH_ROWS=2000；writer 全局串行（semaphore(1)）；MAX_ROWS=200k；
  MAX_OUTPUT=512MiB；MAX_TEMP_DISK=1GiB；DataObject 预算 256MiB。
- fast path 零 GeoJSON 中间体（字节级断言）。
- 实测数字在 PR 描述与 LOOP_LEDGER（本机 dev 数据）。
