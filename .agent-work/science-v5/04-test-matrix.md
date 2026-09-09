# Science V5 — Test Matrix（本地验证记录 · 精确结果）

## 分 wave 车道结果（全部本地串行 / -p no:cacheprovider）

| 车道 | 范围 | 结果 |
|---|---|---|
| CV 框架 | tests/unit/lib/test_cv_framework_v5.py | 33 passed |
| Variogram v5 | tests/unit/lib/test_variogram_v5.py | 10 passed |
| LMC 批量 | tests/unit/lib/test_cokriging_lmc_batched_v5.py + test_cokriging_lmc_v4.py | 4 + 7 passed |
| ST 批量 + CV | tests/unit/lib/test_st_kriging_batched_v5.py | 10 passed（4 differential 逐位） |
| SGS batched + P1 修复 | tests/unit/lib/test_sgs_batched_v5.py + test_kriging_simulation_v4.py | 10 + 8 passed |
| Uncertainty | tests/unit/lib/test_uncertainty_artifact_v5.py | 13 passed |
| Dispatch v5 | tests/unit/lib/test_backend_dispatch_v5.py + tests/benchmarks/test_backend_scale_decisions.py | 15 + 16 passed |
| 立方体/物候/异常 | tests/unit/lib/test_phenology_v5.py + test_temporal_cube_v5.py | 28 passed |
| 水文 v5 | tests/unit/lib/test_hydrology_v5.py + test_hydrology_v4.py | 13 + 11 passed |
| 工具面 | tests/unit/lib/test_science_temporal_tools.py | 5 passed |
| Oracle 回放 | tests/science_oracles（含 science_v5 域 35 case） | **1126 passed** |
| work-count | tests/benchmarks/test_science_v5_workcount.py（-m perf） | 4 passed |
| unit/lib 全量 | tests/unit/lib | **948 passed, 2 skipped** |
| quality + unit/gis + oracle | 合并车道 | 2761 passed（修复 manifest 后 quality 334 绿） |
| tools 车道 | tool_meta/skills_tool_names/categories/tier_guardrail/rationalization | 14 passed |
| benchmarks（perf 车道） | science/backendscale/workcount/geobench/baseline | 23 passed + 25 skipped（perf 隔离语义）+ workcount -m perf 4 passed |
| ruff | app/lib/geo_analysis app/lib/gis app/tools tests/unit/lib tests/science_oracles | All checks passed（scripts/gen_science_oracles.py 的 17 项发现为 master 既有，本分支未触碰） |

## 修复轨迹（验证期间发现→修复）
1. registry validate：terrain.flow_topology_validate 的 stats_table 输出
   未被 terrain_hydrology 能力声明 → 能力包 additive 扩展（挑战 R0-#10）。
2. TOOL_UNTESTED 棘轮 +1（temporal_cube_stats）→ 新增工具级 dispatch 测试
   （discover_test_references 只扫 git 已跟踪文件——提交后回零）。
3. 工具归类完备门：science_temporal_tools 未声明 → categories.py 加
   "analysis"。
4. 每次能力/工具声明变更后生成物链（manifest/report/ledger）再再生。

## 负载敏感与隔离语义
- tests/benchmarks 的 perf 标记在无过滤全量跑自跳（#664 语义）；
  work-count 基准用 `-m perf` 隔离跑 4/4 绿。
- 墙钟断言零新增——全部 work-count/结构门（仓库哲学一致）。

## 未跑车道（与改动面无关，声明）
- frontend（vitest/tsc/build）：本分支零前端改动。
- real_services：无 DB/Redis 语义变化（零 migration）。
- 全量 tests/ 单 sweep：unit+quality+oracle+gis+benchmarks 关键车道已覆盖
  本 Epic 与邻接模块（kriging/terrain/temporal/rs 变更面回归全绿）。
