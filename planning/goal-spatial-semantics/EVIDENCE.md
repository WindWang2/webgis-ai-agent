# EVIDENCE — GOAL 01（真实命令输出，可复现）

## 环境
- worktree: ../webgis-wt-goal-spatial-semantics @ zcode/goal-spatial-semantics-contract-v1
- 基线: master 6c5e71c2c（2026-10-31）
- python: 主仓 .venv（pytest pythonpath=. 使 worktree 源码优先）
- 前端: worktree 内 pnpm install --frozen-lockfile（vitest 4.1.11）

## M1-M3 契约 + 矩阵（42 tests）
`python -m pytest tests/unit/gis_harness/spatial_semantics/test_contract_v1.py test_preflight_matrix.py -q --no-cov`
→ 42 passed（含 16 场景锁定：S1-S15 + 轴序 + 单位数学 + 披影无几何）

## M4 工具适配（15 tests）+ M6 双入口
`python -m pytest tests/unit/gis_harness/spatial_semantics/test_tool_adapters.py -q --no-cov`
→ 15 passed（dispatch 端到端 / typed error / Pi-legacy 一致 / G2 G3）
回归：test_spatial_buffer + test_advanced_spatial_crs_1110 + crs_passthrough +
audit_764_765 → 38 passed；geocompute_1718/boundary → 222 passed；
geometry_ops_hardening + spatial_analyzer_module → 19 passed

## M5 MapSpec 单位链路（3 tests）+ 前端
`python -m pytest tests/unit/gis_harness/spatial_semantics/test_mapspec_unit_flow.py -q --no-cov` → 3 passed
`pnpm exec vitest run components/map/legends/graduated-legend.test.tsx` → 4 passed
（含新：unit chip 渲染 / 无 unit 不渲染）

## M7 fixture + 性能（14 tests）
`python -m pytest tests/unit/gis_harness/spatial_semantics/test_fixtures_and_perf.py -q --no-cov` → 14 passed
- 50k feature：采样=200（打满即停）/ 扫描≤2000 / 耗时 <1.0s 预算 / 50k:5k 比 <20
- 重复运行 2 次判定与披露完全一致（无网无密钥）

## M8 端到端证据脚本
`python scripts/spatial_semantics_e2e_evidence.py` → EXIT=0 三场景 PASS
- 场景 A：成都 500m（UTM 48N，测地半径基准 500.1m）→ 聚合 → legend_spec.unit=个/km²
- 场景 B：per_km² 密度（rate=1.177 个/km²，换算链 per_m²→per_km² 入证据）+ 口径冲突拒绝
- 场景 C：未声明投影样 blocked / 度样 rfc7946_default / 3857 失真 16.25% 降级 + 安全变换建议 / inspect 工具 unknown 如实

## 全量目录回归
`python -m pytest tests/unit/gis_harness/ -q --no-cov`（除 scene3d）
→ 2803 passed, 4 skipped, 5 failed + 1 deselected
**5 失败 + 1 deselected 均为 master 既有红灯**（干净基线同命令复现：
test_component_composition×2 / test_semantic_v2_families registry 交叉验证 /
test_workflow_guards×3 —— #1868-#1875 时代注册表校验遗留），非本 PR 引入。

## 静态检查
- ruff check app/（改动面 + 全 app）→ All checks passed
- python -m compileall -q（新包 + 改动文件）→ OK
- 前端 typecheck：仅 2 个 #1875 既有错误文件（paged-table-data.test /
  viewport-aggregate.test），本 PR 文件干净（干净基线同错）
