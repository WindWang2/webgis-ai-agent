# TEST_MATRIX — goal-04-carto-pub（随实施填充真实结果；PASSED/FAILED/NOT_RUN 三态 + 环境）

## 基线探测（R1）— 2026-11-06

| 项 | 命令 | 结果 | 环境 |
|---|---|---|---|
| Python venv | 复用主检出 `/home/kevin/projects/webgis/webgis-ai-agent/.venv`（Python 3.13.17） | ✅ | 本机 |
| cartography 定向 before（layout/label/publication/export 20 文件） | `pytest tests/cartography/test_layout_* test_label_* test_publication_* test_export_* test_scene_export_honesty test_grammar_label_layout_contract_v1 --no-cov -q` | **PASSED 221/221（9.46s）** | 主 venv + worktree 源码 |
| 前端依赖 | `pnpm install --prefer-offline`（worktree frontend） | ✅ exit 0 | pnpm 10.34.9 / node 24.21.0 |
| 前端定向 before（export/map-components/label-solver/legend-parity/wysiwyg 13 文件） | `pnpm exec vitest run …` | **PASSED 104/104（4.80s）** | vitest（jsdom） |
| playwright/chromium | `node_modules/.bin/playwright --version` + `~/.cache/ms-playwright/` | ✅ 1.64.1 + chromium-1234/1248 + headless_shell | 真实渲染 harness 可行 |

## 里程碑验证（R3+ 随轮追加；环境：主检出 .venv Py3.13.17 / pnpm 10.34.9 / node 24.21.0）

| 轮 | 命令 | 结果 |
|---|---|---|
| R3 | `pytest tests/cartography/test_layout_constraint_spec_v1.py` | PASSED 10/10（后扩 15/15） |
| R3 回归 | `pytest test_layout_solver_v3/v4 test_layout_geometry_v7 test_ac07_zero_regression test_visual_judge_selfheal` | PASSED 45+33 |
| W1 前端 | `vitest run lib/export/ + export-chrome(+wrap) + vector-svg-export(+wrap) + ir-consumer + wysiwyg` | PASSED 150/150 |
| W1 后端 | `pytest test_layout_description_golden test_layout_description_wrap_v1 test_publication_chrome test_export_sample_pdf_text` | PASSED 19；+render_diagnostics_contract 11 |
| W1 静态 | `tsc --noEmit` / `eslint <changed> --max-warnings 0` / `ruff check app/` | 全部 0 错 |
| W4 | `pytest test_semantic_checks_layout_v1 + closed_loop_v11 + visual_judge_selfheal + layout_constraint_spec` | PASSED 67 |
| W3 前端 | `vitest tile-miss-account + runtime-basemap-evidence + adapter` | PASSED 46；freeze 3 + pdf-disclosure 5 + wysiwyg/upload 13 |
| W5 | `pytest test_publication_cases_v1` | PASSED 29/29 |
| R5/R6 | `pytest test_layout_constraint_spec_v1`（边界+120 参与者冒烟） | PASSED 15/15 |
| R7 Python 全 lane | `pytest -m cartography --no-cov --timeout=180 -q` | **PASSED 2657 / 8 skipped / 3 xfailed（148.5s，exit 0）** |
| R7 前端全量 | `pnpm exec vitest run`（×3 轮） | 第 1 轮 1 失败（与 Python lane 并跑的负载抖动）；**第 2、3 轮连续全绿：522 文件 / 4875 passed + 1 expected fail，exit 0** |
| W2（subagent） | obstacles/字段守卫/跨入口一致性 | （进行中，obstacles 已 commit ea492dede） |

## NOT_RUN / 受限披露

- 全栈浏览器 E2E（真实 Next+FastAPI 栈的出图旅程）：未启动全栈（重型构建 + 多 agent 同机资源约束）；以结构 golden + jsdom 渲染 harness + Playwright 可用性记录替代。PR 中如实标注。
- `pnpm build` 生产构建：未跑（非本任务验收门；typecheck 已覆盖类型面）。
- #1910 后端回执一致性：blocked on 他人修复合入；本任务只交付前端不变式（pdf-disclosure.test.ts）与后端对拍测试挂起说明。
