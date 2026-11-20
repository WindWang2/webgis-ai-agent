# TEST_MATRIX — 22 验收场景 × 测试落位

| # | 场景 | 测试（tests/data_import/…） | 状态 |
|---|---|---|---|
| 1 | GPKG 三层选两层 | test_containers.py::test_gpkg_three_layers_select_two（未选层无产物；线层真实转换） | ✅ |
| 2 | 两 GeoJSON 一次导入 | test_vertical_slice.py::test_two_geojson_vertical_slice | ✅ |
| 3 | ZIP 双套 shapefile | test_containers.py::test_zip_two_sets_grouped_correctly（字段不串用；孤儿披露） | ✅ |
| 4 | 缺 PRJ + 声明 CRS | test_containers.py::test_shapefile_missing_prj_declared_crs（不声明→CRS_UNCONFIRMED；声明→转换证据）；旧拒绝契约=tests/test_data_parser_missing_crs.py 保持绿 | ✅ |
| 5 | Easting/Northing + 投影 CRS | test_tables.py::test_csv_easting_northing_declared_crs | ✅ |
| 6 | 分号+中文编码+引号 | test_tables.py::test_csv_semicolon_chinese_quoted | ✅ |
| 7 | 前导零 + 长整型 | test_tables.py::test_leading_zeros_and_long_int（默认 text；显式 number 的 >2^53 保真字符串） | ✅ |
| 8 | WKT 混族 + 非法行 | test_tables.py::test_wkt_column_mixed_and_invalid（行号可定位） | ✅ |
| 9 | 纯统计表 | test_tables.py::test_plain_stats_table_no_geometry（ref:table-；无伪造 bbox） | ✅ |
| 10 | XLSX 多/空/公式 | test_tables.py::test_xlsx_sheets_empty_and_formula | ✅ |
| 11 | 未确认 CRS | test_tables.py::Easting 用例（预览表+requires_crs_confirmation；提交拒绝） | ✅ |
| 12 | 改 X/Y 后旧计划提交 | test_tables.py::test_stale_plan_version_rejected_after_change（409） | ✅ |
| 13 | 同源不同解释 | test_vertical_slice.py（keep_fields 变更 → 不同 hash → 不同产物） | ✅ |
| 14 | 同计划重试复用 | test_vertical_slice.py::test_two_geojson_vertical_slice 第 6 步（duplicate=True） | ✅ |
| 15 | 部分成功重试 | test_containers.py::test_partial_success_retry_keeps_completed（成功项 ref 复用） | ✅ |
| 16 | 预览后重开恢复 | test_resilience.py::test_preview_recovery_no_persisted_data + 前端 import-workbench.test.tsx | ✅ |
| 17 | 提交中取消 + 迟到结果 | test_resilience.py::test_cancel_during_commit_no_late_success | ✅ |
| 18 | 双 owner 同 bytes | test_resilience.py::test_cross_owner_same_bytes（互不可见；独立产物） | ✅ |
| 19 | 重启恢复 | test_resilience.py::test_restart_recovery_after_completed_commit（含 COMMITTING 归一化） | ✅ |
| 20 | 预算超限 | test_resilience.py::test_budget_bounded_rejections（文件数/zip 条目/成员数）+ 过期惰性清理 | ✅ |
| 21 | Pi 工具闭环 | test_tools_dispatch.py::test_tool_closed_loop_via_real_dispatcher + 跨入口一致性用例 | ✅ |
| 22 | 旧契约兼容 | 旧五套 40/40（upload_identity_v4 含 ignored_files L384-407、upload_api、shapefile、parser_crs×2、upload_tools） | ✅ |

## 故障注入反验（任务书要求）

| 注入 | 测试 | 防线 |
|---|---|---|
| 移除 layer 参数（container_ref 置空） | test_fault_injection.py::…no_layer_param… | commit 侧 CONTAINER_REF_MISSING 守卫（绝不静默读第一层） |
| preview/commit 间篡改解释 | …diverted_mapping… | hash 重推导权威比对（PLAN_HASH_MISMATCH）+ save_plan 列校验 |
| 伪造未登记 ref | …forged_ref… | 幂等复用前 `_verify_output_readable`（假 ref 不显示就绪） |

## 评审修复回归（tests/data_import/test_review_fixes.py，8 例）

P1-1 输出 CRS（spy 断言 ingest 参数）/ P1-2 表格超限诚实失败 / P2-1 列截断披露 /
P2-2 XLSX header_row / P2-3 zipset 冒号键与安全文件名 / P2-4 diag 产物可读 /
P2-8 批次展开预算+清理 / P2-9 取消意图持久化。前端：import-workbench.test.tsx
P0-1 勾选落服务端用例。修复前全部失败（已验证红→绿）。

## 其他覆盖

- API 合同：test_api_routes.py（multipart 创建→轮询→清理；404 归属；409 冲突；unsupported 披露）
- 前端：lib/api/data-import.test.ts（8）+ import-workbench.test.tsx（4）；既有 dataset-manager/upload 测试保持绿（17/17）
- 生命周期/过期：test_vertical_slice.py（生命周期+归属）+ test_resilience.py（EXPIRED 惰性清理）
- 性能：planning/goal-g17-import-workbench/PERFORMANCE.md（45.4MB 近限额单文件 + 20×小文件批次 + 取消往返）

## 既有相关回归（本分支保持通过）

tests/data/test_upload_identity_v4.py、tests/test_upload_api.py、tests/test_shapefile_upload.py、
tests/test_data_parser_crs.py、tests/test_data_parser_missing_crs.py、tests/test_upload_tools.py（40/40）
