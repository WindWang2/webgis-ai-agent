# G18 LOOP_LEDGER

基线：origin/master = 816705218a5aa6202bf15d745cb31cddb3437f44（fetch 复核一致）。
Worktree：../webgis-worktrees/goal-g18-dataset-delivery-20261110T131127Z；
branch zcode/goal-g18-dataset-delivery-20261110T131127Z。

## 已完成验收

- [x] B.1–B.6：remote/PR/issue/DEDUP 复核；worktree 创建；规划 4 文档。
- [x] WP01：输入/格式矩阵入 PLAN/DEDUP（pyogrio 0.12.1 + pyarrow 21 + pyproj 实测驱动表）。
- [x] WP02–04：schema/contracts/source_resolver/scope/loss 落盘并语法+导入验证。
- [x] WP05–06：6 格式真实 writer（GPKG/FGB/SHP-ZIP/GeoParquet/GeoJSON/CSV）单测级 round-trip
      全部通过（含成员唯一性、.prj/.cpg、geo metadata、schema sidecar、spreadsheet-safe）。
- [x] WP07–08：live harness 全链通过（GeoJSON/GPKG 交付、幂等、跨 owner 404、DataObject kind
      白名单扩展 data_delivery）。
- [x] WP09：routes + tools 挂载（main.py/tools __init__/materialization path 键修复）。
- [x] WP10：frontend 客户端/对话框/两个薄入口/i18n；tsc --noEmit 新文件零错误；
      i18n 契约测试（key completeness/裸 CJK）通过。
- [x] WP11：27 场景映射 TEST_MATRIX；writers oracle + service flow + faults + routes/tools 套件。

## 失败/修复记录（基线外）

- 基线既有失败：test_geoparquet_fast_lane_v5::test_materialize_opt_in_geoparquet_and_default_unchanged
  （KeyError 'path'）—— 由本任务以 additive 键修复（materialization_service.py）。
- aiosqlite 缺失于隔离环境（-I）→ 测试走仓库 conftest 的 worker SQLite 模式；harness 用非 -I。
- 前端 pnpm：本机 pnpm 12 与 lockfile v9/workspace 配置不兼容 → 用 pnpm@10.18.3
  --frozen-lockfile（Node 24.14 vs jsdom 要求 24.15 → --config.engine-strict=false，
  仅本地 typecheck/单测，不改仓库配置）。
- 修复：service._job_authorize 错 import（app.models.chat → db_model）；
  大整数检测 TypeError（sample_values 是 repr 字符串）；表格 CSV CRS 闸误触发；
  项目鉴权 HTTPException → typed SourceResolutionError；losses_applied 未合并分析码。

## 下一步

1. 完成 service flow 全绿 → routes/tools/recovery 三套件。
2. 既有回归：test_export_geojson / map_export_download_auth / geoparquet_fast_lane_v5。
3. 独立 reviewer（释放实现槽）→ 修复 → 复审。
4. commit 分段提交 → push → PR。
