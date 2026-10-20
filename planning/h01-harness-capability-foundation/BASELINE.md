# H01 BASELINE — 改动前基线（master@0415ad8d）

目的：Wave 6 前后对照用。全部数字为本机实测（worktree 内、venv
`/home/kevin/project/webgis-ai-agent/.venv/bin/python`）。

## 基线 lane（改动前，2026-10-20）

| 命令（均外套 `timeout`，`-q -p no:cacheprovider`） | 结果 |
|---|---|
| `pytest tests/unit/test_pi_tool_spec.py -x` | exit=0，18 passed，~61s |
| `pytest tests/unit/gis_harness/ --collect-only -q` | exit=0，2502 collected，~66s |
| `pytest tests/unit/gis_harness/test_task_complete_legacy_block.py` | exit=0，2 passed，~56s |
| `pytest tests/unit/gis_harness/test_methodology_central_validation.py` | exit=0，2 passed，~88s |
| `pytest tests/unit/gis_harness/test_eligibility_profile_ratio_keys.py` | exit=0，3 passed，~57s |
| `ruff check app/services/gis_harness/ app/services/chat/pi_result_abi.py app/services/chat/pi_tool_spec.py` | exit=0，0 违规 |
| `git diff --check`（worktree 干净） | exit=0 |

## 全量套件已知状态（不在 H01 门槛内，透明记录）

- #1753：全量串行执行 ~80% 处永久挂起（HEAD 存量，非 H01 引入）。
- #1707：39 个 `p4_test_*.py` 默认不收集（H01 新测试全部 `test_*.py` 命名规避）。
- #1702：同名 test_contracts 收集中断——**已修复**（收集 26652 项 / exit=0）。

## 性能基线（受影响路径，Wave 6 复测同口径）

- 单 pytest 进程固定启动开销 ~50s（`--cov=app` 插桩为主）——lane 级对比需剔除。
- `build_result_envelope`：纯内存 dict 投影，无 I/O；改动前后用微基准
  （`timeit` 重复 10k 次构造典型 ToolDispatchResult）+ envelope 字节预算断言（≤4KB）。
- `compute_turn_active_tools`：≤40 spec 纯内存（ADR-0224 实测口径）；新增
  permission 检查后保持 O(n) 无 I/O。
- 上下文 token 足迹：active-tools marker 块（drop 披露 additive）用字符数上界
  断言（≤ 既有 marker 预算 + 披露 cap）。

## 结果契约现状快照（K1 证据）

- `evidence.capability_bind` 实际产出恒 `{"code": ...}`（allowed 时 code=""）；
  `evidence.action_ir` 恒空 → 键省略。golden 化后作为 before 样本。
