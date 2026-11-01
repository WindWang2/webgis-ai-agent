# EVIDENCE —— GOAL-02（每轮追加：命令 + 实际输出摘录/数字）

## Round 0
- `git worktree list` → `/home/kevin/projects/webgis/webgis-wt-goal-ogc-cog  <new> [zcode/goal-ogc-cog-data-services-v1]`
- `git rev-parse origin/master` → `6c5e71c2cb04afe31b40bf66ca96e6dd6480b4e7`（2026-10-31T10:54:07Z #1875 merge）
- `gh pr list --state open` → 空；`--state all --limit 40` → #1868/#1869/#1870/#1871/#1873/#1874/#1875 全 MERGED；#1868 `reviews` → `[]`（无 GitHub review）。
- `gh issue list --state open --limit 60` → 全部 audit 系列（#1799-#1866），无一与本任务认领重叠。
- 待 P1 起记录测试命令与输出。

## Round 1（P1）
- `pytest -q tests/unit/test_ogc_tiles_discovery_v1.py tests/unit/test_ogc_coverages_discovery_v1.py --no-cov` → `16 passed in 3.07s`
- 回归 `pytest -q tests/unit/test_data_fabric_adapters.py tests/unit/test_adapter_contract_v2.py tests/unit/test_ogc_adapters_753.py --no-cov` → `36 passed, 1 skipped`
- `ruff check <新文件 6 个>` → `All checks passed!`
- `compileall -q <4 文件>` → ok

## Round 2（P2）
- `pytest -q tests/unit/test_ogc_tiles_proxy_v1.py tests/unit/test_ogc_tiles_mapspec_v1.py tests/unit/test_ogc_tiles_route_v1.py --no-cov` → `31 passed`
- `pnpm exec vitest run lib/mapspec-runtime/runtime.test.ts --maxWorkers=2` → `36 passed`
- mapspec 回归（test_mapspec_source 等 4 文件）→ `57 passed`
- `ruff check <P2 文件 8 个>` → 通过

## Round 7（P7+P8）
- `pytest -q <本方向 9 个测试文件> --no-cov` → `81 passed in 116.61s`
- 回归 `pytest -q tests/unit/test_data_fabric_adapters.py test_adapter_contract_v2.py test_ogc_adapters_753.py test_mapspec_source.py test_mapspec_commit_surface_687.py test_artifact_registry.py test_pmtiles_tile_route.py test_data_fabric_routes.py --no-cov` → `99 passed, 1 skipped`
- `ruff check app/ + 新测试` → 全清
- E2E 场景明细：
  - ①inspect(supported/display_only) → sync(1) → add_tile_service_layer(vector tiles 模板) → parse_mapspec valid → 路由 200 TILE_MVT_BYTES → 缓存 hit
  - ②STAC asset(data) → materialize(ref:fabric-cog, requests≥1, 窗口 150×150≪600×600) → 3 采样点真值 approx → PNG imageRef → MapSpec raster 源 valid
  - ③no_coverage_endpoint → read_coverage_subset error_type=capability_not_declared, ref=None；对照面 3×3 真值逐点
