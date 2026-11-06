# EVIDENCE — GOAL 03
（逐阶段追加真实命令与输出摘要；未执行项标 not_evaluated）

## 预存 master 红灯隔离（M3 期间确认）
- `tests/unit/gis_harness/test_component_composition.py::test_algorithm_taxonomy`、`::test_registry_validation_includes_component_system`、`tests/unit/gis_harness/test_workflow_guards.py::test_workflow_profile_validation_is_wired_into_registry_validation`、`tests/unit/gis_harness/test_semantic_v2_families.py::TestRegistryIntegration::test_registry_cross_validation_clean`：4 例在干净 master（6c5e71c2c）即红（stash 复核，2026-11-01），根因 `data_preparation`/`layer_browse` capability 为 native 状态但无算法 descriptor（#1868/#1875 落地遗留）。本任务新增 temporal_bind/control/compare 为 planned 状态，经 validate() 的 planned 豁免（与 `_is_analysis_capability` 同门）不新增红灯 —— 修复前后失败集完全一致，均为预存 4 例。

## M1-M8 实施证据（全部本地真实执行）
- M1 契约：`pytest tests/unit/test_mapspec_schema_v18_temporal.py` 19 passed（单期拒绝/重叠拒绝/坏时区/1.7→1.8 identity/指纹敏感）；ts_projection 再生（+54 行 types.generated.ts）。
- M2 绑定：`pytest tests/unit/gis_harness/temporal_products/test_frames_and_time_filter.py` 25 passed（表示归一 5 类/字典序前提/缺期检测/时区分桶/[start,end) 边界）。
- M3 工具/IR：`test_service.py` 21 + `test_ir_and_tools.py` 13 passed（真 lifecycle engine 落盘、user-wins 光标保护、IR 对照/播放/保留字段、digest 显式才入）；工具分类门 `test_tool_registry_rationalization.py` 5 passed；taxonomy 一致性门补 SetTemporalViewIntent 条目后通过；capability planned 注册后 dangling endpoint 消除；parity corpus 4 新 case + golden 合并（不动既有 33 例预存红）。
- M4 前端：`vitest lib/temporal-map + temporal-time-controller` 35 passed（门控/乱序拒绝/LRU 防风暴/取消/同窗键稳定/搜索×时间窗合成）；`pnpm typecheck` 零新增错误（3 例预存见隔离记录）；map-panel 插槽式变更（+13 行）。
- M5 对照：E2E-2 两遍一致（breaks 固定 [10..15]、paint 跨帧逐位不变、sum 33≠42 数据真差异、缺 2024-2025 披露）。
- M6 导出：atlas_batch 55 passed（含 time driver 8 新 case）；export 回归 31 passed；SVG 时间注记断言。
- M7/M8 E2E：`test_e2e_golden.py` 4 passed（三情境 ×2 遍逐位一致 + ISO 跨年边界）；前端 metrics（requests/cacheHits/staleRejected）断言。
- 合并面回归：gis_harness 全量 2902 passed / 6 failed（4 预存 + taxonomy/capability 已修，修复前后失败集一致）；mutation parity corpus 33 failed = master 预存集（stash 复核 2026-11-01）。
- 资源纪律：pytest 单进程、vitest --maxWorkers=2、typecheck 串行。

## 复查记录
- 2026-11-01（M9/PR 前）：待 fetch 复查（见 LOOP_STATE 终局行）。
