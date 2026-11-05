# TEST_MATRIX — goal-04-carto-pub（随实施填充真实结果；PASSED/FAILED/NOT_RUN 三态 + 环境）

## 基线探测（R1）— 2026-11-06

| 项 | 命令 | 结果 | 环境 |
|---|---|---|---|
| Python venv | 复用主检出 `/home/kevin/projects/webgis/webgis-ai-agent/.venv`（Python 3.13.17） | ✅ | 本机 |
| cartography 定向 before（layout/label/publication/export 20 文件） | `pytest tests/cartography/test_layout_* test_label_* test_publication_* test_export_* test_scene_export_honesty test_grammar_label_layout_contract_v1 --no-cov -q` | **PASSED 221/221（9.46s）** | 主 venv + worktree 源码 |
| 前端依赖 | `pnpm install --prefer-offline`（worktree frontend） | ✅ exit 0 | pnpm 10.34.9 / node 24.21.0 |
| 前端定向 before（export/map-components/label-solver/legend-parity/wysiwyg 13 文件） | `pnpm exec vitest run …` | **PASSED 104/104（4.80s）** | vitest（jsdom） |
| playwright/chromium | `node_modules/.bin/playwright --version` + `~/.cache/ms-playwright/` | ✅ 1.64.1 + chromium-1234/1248 + headless_shell | 真实渲染 harness 可行 |

## 里程碑验证（R3+ 随轮追加）

（待填）
