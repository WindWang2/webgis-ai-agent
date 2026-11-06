# TEST_MATRIX

| 面 | 命令 | 覆盖 |
|---|---|---|
| M1 引擎单测 | `pytest tests/cartography/test_design_candidates_v1.py -m cartography --no-cov -q` | R1–R6 正/负例、≤4 上限、确定性、无 query 字符串负例 |
| M2 接线 | `pytest tests/unit/test_map_product_design_trace_v1.py -m cartography --no-cov -q` | 真实 dispatch webgis_map_product payload 含 trace；best_effort 降级 |
| M3 选择 | `pytest tests/unit/test_map_design_select_v1.py -m cartography --no-cov -q` | pin 生效/幂等/quality_loop 不覆盖/落选理由持久 |
| M4 检查码 | `pytest tests/cartography/test_semantic_checks_contract.py tests/cartography/test_semantic_checks_design_v1.py -m cartography --no-cov -q` | 48 码契约 + 每码正/负/not_evaluated |
| M5 五族 | `pytest tests/cartography/test_design_families_e2e_v1.py -m cartography --no-cov -q` | F1–F5 planner→MapSpec→组件 + heatmap blocked 负例 |
| M6 G05 | `pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=120 -q` | 既有 61 场景不红 + 新增族 |
| M7 前端 | `cd frontend && pnpm vitest run components/map/map-components/design-candidates` | 组件渲染/接受动作 |
| 回归 lane | `pytest -m cartography --no-cov -q`（全量制图门禁） | 无既有红引入 |
| 类型/lint | `ruff check`、`mypy`（按仓库现有门禁面） | 零新红 |

证据落 EVIDENCE.md；每里程碑原子 commit。
