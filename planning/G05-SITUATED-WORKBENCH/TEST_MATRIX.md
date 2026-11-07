# TEST_MATRIX — G05-SITUATED-WORKBENCH

> 本地验证命令与结果（worktree `../webgis-wt-situated-gis-workbench-20261107`；
> Python 3.12 venv + pnpm lockfile 安装；-j 纪律遵守）。

## 后端

| 套件 | 命令 | 结果（2026-11-08） |
|---|---|---|
| IR + situation 域 | `pytest tests/unit/gis_harness/interaction_ir/ tests/test_gis_situation_*.py tests/unit/test_interaction_scope_tool.py tests/unit/gis_harness/map_operations/` | **191 → 219+ 全绿**（含新增；详见各 commit） |
| 回放金标 | `pytest tests/unit/gis_harness/interaction_ir/test_replay_corpus.py` | **29 passed**（17 场景双跑 + present） |
| map_operations | `pytest tests/unit/gis_harness/map_operations/` | **68 passed**（含 SW-04 3 新） |
| 旅程级（E5 多轮） | `pytest tests/acceptance/gis_agent/test_e5_multiturn_journeys.py` | 回归绿（全域跑） |
| 竞态（SSE chaos） | `pytest tests/test_adversarial_sse_chaos.py` | 回归绿（全域跑） |
| 全域 | `pytest tests/test_gis_situation_*.py tests/unit/gis_harness/ tests/unit/test_interaction_scope_tool.py tests/acceptance/gis_agent/test_e5_multiturn_journeys.py` | 首轮 3462 passed + 2 旧 characterization 更新后全绿 |
| 静态 | `ruff check app/ tests/`；`mypy`（改动文件） | 0 / 0 |

## 前端

| 套件 | 命令 | 结果 |
|---|---|---|
| interaction/selection/map-operations/temporal/workbench | `pnpm vitest run lib/interaction lib/selection lib/map-operations lib/temporal-map components/workbench` | **312 passed**（含 SW-02 5 新） |
| map-operations + workbench undo | 同上（SW-04 批） | **74 passed**（含 2 新） |
| map-harness-cards | `pnpm vitest run components/map/map-harness-cards.test.tsx` | **8 passed**（含 SW-09 3 新） |
| agent 域 | `pnpm vitest run components/agent/` | **13 passed**（含 SW-05 2 新） |
| chart/table 面板 | `pnpm vitest run components/map/map-components/panel-ir-emit.test.tsx chart-panel` | **10 passed**（含 SW-08 1 新） |
| 类型 | `pnpm typecheck`（双 tsconfig） | 0 |
| Lint | `pnpm eslint <改动文件> --max-warnings 0` | 0 |

## 新增测试清单（16 后端 + 13 前端）

- `tests/unit/gis_harness/interaction_ir/test_selection_range_disclosure.py`（13）
- `tests/unit/gis_harness/map_operations/test_service.py`（+3：mixed 数值可逆/null 不可逆/paint 可逆）
- `tests/test_gis_situation_selection_history.py`（语义更新 1）
- `tests/test_gis_situation_e0_trace.py`（语义更新 2）
- replay_corpus ×4（金标）
- `frontend/lib/interaction/emitters.test.ts`（+5 +语义更新 1）
- `frontend/lib/map-operations/receipt-bus.test.ts`（+2）
- `frontend/components/map/map-harness-cards.test.tsx`（+3）
- `frontend/components/agent/agent-run-panel.test.tsx`（新文件 2）
- `frontend/components/map/map-components/panel-ir-emit.test.tsx`（+1）

## 未验证边界（诚实披露）

- e2e 真浏览器档（playwright real）需夜间账号/E2E_USER：未跑；mock 档
  j10–j12 旅程在本 PR 回归内（lane 变更面触发条件未变）。
- display-confirmation required 模式的端到端（env 翻转）不在默认测试环境；
  组件级 3 测试覆盖其状态机。
- 线上 CI：按任务书不等待、不宣称。

## 基线已坏（非本 PR 引入，实测归因）

- `tests/quality/test_quality_manifest_gate.py::test_committed_manifest_is_current`
  与 `test_generated_artifact_graph.py::test_ledger_is_current`：在干净
  origin/master（585812ee4）上同样失败 —— 即 Issue #2007（生成质量文档
  过期）。归因=基线已坏；不在本 PR 抢修（任务书明示不抢独立 issues，
  再生成会产生大片无关 diff）。本 PR 新增 docs 将随该 issue 的修复被
  生成器自然收录。
