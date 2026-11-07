# 测试矩阵与执行模式

## 分层车道

| 车道 | 默认 | 外部依赖 | 资源约束 | 证据含义 |
|---|---|---|---|---|
| smoke | 是 | 无 | 串行或最多 2 workers | 契约/导入/关键纯函数 |
| focused backend | 是 | 无公网、无付费模型 | 最多 2 workers | 真实 Replay/Lab/MapSpec/Recovery 代码 |
| Pi with stub | 按需 | 本地 Pi + stub LLM | 单进程、有界超时 | 真实 RPC，不是真实模型质量 |
| GIS with fixture | 是 | 固定本地 fixture | 固定 seed、数据规模上限 | 真实 ToolRegistry/ToolDispatchService/GIS 算法 |
| local browser opt-in | 否 | 本地服务与浏览器 | 单浏览器、无公网底图 | 真实 MapLibre 观察；不可运行则未验证 |
| live services opt-in | 否 | 用户已有凭证/外部服务 | 默认完全禁用 | 独立标注，绝不默认触发 |

## Oracle 覆盖

| Oracle | smoke | focused | dispatch | browser | fault/recovery | benchmark |
|---|---|---|---|---|---|---|
| O01/O03/O05/O06 | 契约负例 | 证据投影/诊断 | 真实 dispatch 关联 | 可用时渲染观察 | 缺失/乱序/跨会话 | payload bytes |
| O02/O10 | manifest | 科学性质与既有回归 | 12 组可运行子集 | 明确 opt-in 子集 | 成功/失败双边界 | 守恒与规模 |
| O04/O07 | 终态纯函数 | recovery policy | 沙箱故障 | context loss opt-in | 至少 4 组对照 | 有界重试 |
| O08/O09 | 无 | 预算单测 | 大规模本地 fixture | 帧数可选 | 压力故障 | wall/CPU/RSS/bytes/events |
| O11/O12/O13 | ratchet 形状 | 原门不降级 | 双跑 | 双跑（若运行） | 独立复审 | 原始汇总与复现命令 |

## 当前执行记录

尚未执行产品测试。后续每条记录必须包含命令、HEAD、模式、pass/fail/skip、运行次数和限制。

## M2 实际执行

| 命令/范围 | 结果 | 模式与限制 |
|---|---|---|
| `pytest tests/lab tests/harness_replay --no-cov -q`（基线） | 239 passed, 4 warnings | 既有离线 Lab/Replay；未含新测试 |
| `pytest tests/harness_replay/test_journey_evidence.py tests/harness_replay/test_journey_diagnostic.py tests/harness_replay/test_replay_bench_cli.py::TestExplain --no-cov -q -p no:cacheprovider` | 10 passed | 真实投影/CLI；含缺 renderer、跨 session、悬空、乱序、完整正向、脱敏/bytes/owner 负例 |
| `pytest tests/unit/gis_harness/recovery_policy tests/unit/evaluation/test_recovery_corpus.py tests/unit/lib/test_goal_verification_reconcile.py --no-cov -q -p no:cacheprovider` | 195 passed | 真实 TDS + fake registry/工具；不是 live provider |
| `mypy app/lib/harness/replay/journey.py app/lib/harness/result_contracts.py --no-incremental` | Success, 0 issues | 两文件已加入标准 mypy whitelist |
| Ruff changed files + `git diff --check` | 通过 | 无样式/空白错误 |

## M3 实际执行

| 命令/范围 | 结果 | 模式与限制 |
|---|---|---|
| `pytest tests/acceptance/gis_agent/test_reliability_golden.py::test_all_selected_acceptance_scenarios_pass` | 1 passed（30 场景，19.44s） | 真实 dispatch_tool/TDS/MapSpec/settle；scripted LLM，本地合成数据 |
| `python scripts/harness_reliability_golden.py --out reports/g07/golden.json` | 30/30 passed，报告落盘 | 首次 30/30 Redis 环境失败已修复；browser/live 均 not_run |
| focused usability/layout/network/interaction/selection tests | 255 passed, 6 warnings | 生产纯函数/真实算法；无公网 |
| `pytest tests/unit/gis_harness/test_reliability_selection_scope.py` | 11 passed | normalize→projection→derive→validate 全链；含 ownership fail-closed |
| `pytest tests/acceptance/gis_agent/test_reliability_cli.py` | 1 passed | 真实 CLI 子进程与 JSON 报告 |
| `pytest tests/acceptance/gis_agent/test_reliability_golden.py::test_manifest_has_twelve_complete_oracle_pairs` | 1 passed | 12 类、期望 ID、科学不变量、focused path 完整 |

## M4 实际执行

| 命令/范围 | 结果 | 模式与限制 |
|---|---|---|
| `pytest tests/harness_replay/test_journey_faults.py tests/harness_replay/test_journey_evidence.py tests/harness_replay/test_journey_diagnostic.py` | 20 passed | lock/cancel/timeout/disconnect、renderer/tiles/PostGIS/export、duplicate/late/dynamic tool |
| `pytest tests/harness_replay/test_replay_faults.py tests/lab/test_lab_settlement.py tests/unit/test_nested_session_mutations.py tests/unit/test_pi_dynamic_surface.py tests/unit/test_pi_gis_usability_fixes.py` | 89 passed | 既有生产 seam；锁实现未改 |
| Mypy journey/result_contracts + Ruff | 通过 | 类型/样式无回归 |
