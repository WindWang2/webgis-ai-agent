# Harness V7 实施进度（Waves → Commits）

分支 `feat/harness-v7-agentic-runtime`（base master 2aabdc43）。
全部本地验证，不依赖线上 CI。

## Wave 0 — 基线审计（Subagent A，只读）
- `.agent-work/harness-v7/00-baseline.md`：执行链路图（file:line）、
  状态/持久化清单、Phase A-G 缺口普查（EXISTS/PARTIAL/MISSING）、
  V7 附着接缝 17 处、测试环境事实、Top10 风险、wave 顺序建议。
- 本机基线：tests/unit/gis_harness 1103 passed / 9 env-failed（fcntl/h3，
  Windows 缺失，master 同样红）。

## Wave 1 — Phase A（0b077dbc）
`runtime_state_machine.py` + 22 测试 + 三触发点接线 + [GIS Runtime] 投影。

## Wave 2 — Phase B（739a259d）
`plan_runtime.py`（版本/回滚点/失败种子最小重算）+ LOOP_BUDGETS.replan:1
+ request_replan 驱动点（预算与驱动同 commit）+ 12 测试。

## Wave 3 — Phase C（96d9b41d）
`context_layers.py`（九域投影/预算/检查点）+ context_digest 入锚 +
turn 边界 checkpoint + 9 测试。

## Wave 4 — Phase D（a1c4ebc4）
`capability_descriptors.py`（统一描述符/前置硬过滤/可靠性反馈/CJK 二元组
检索）+ tool_surface_v3 6.7 门控信号 + `scenario_corpus.py`（14 族 ×
2316 场景生成结构 + 覆盖门 + stride 抽样评测）+ 14 测试。
V6 检索评测门复测通过（56 passed；1 个 fcntl 环境红与 V7 无关）。

## Wave 5 — Phase E（8fc00e03）
`map_critique.py`（blank_map/invalid_bounds/出版件/label_collision/
overlay_mismatch）+ finalizer `_validate_all` 增值并轨 + 8 测试
（map_completion/map_verification 回归同跑 87 全绿）。

## Wave 6 — Phase F（936c12cf）
`intent_acceptance.py`（三面独立验收，打破循环论证）+
`display_confirmation.py`（auto 默认/required ack seam）+ finalizer 出口
decide_continuation 直连 + request_replan 路由 + READY context commit +
9 测试（finalizer/verdict/observation 回归 101 全绿）。

## Wave 7 — Phase G（c28a99f7）
`delegation.py`（handoff schema/台账/失败回收/QA 驱动点默认关）+ 7 测试。

## Wave 8 — 文档（c160a6ca）
ADR-0130 + CHANGELOG 段 + 全量回归记录（1244 passed / 9 env-failed，
与基线失败集合逐项一致）。

## Wave 9 — Review
- Round 0 自审（26fd512c 记录）：3 项发现（cancelled→replan MAJOR、
  投机参数、continuation 直测缺口）全部当场修复（6b45f606）。
- Round 1 Subagent B 独立评审：见 05-review-findings-subagent.md。

## 测试环境事实（Windows Git Bash）
- `bash .agent-work/harness-v7/run-tests.sh <paths> -q`（内置 OVERPASS
  白名单主机覆盖，绕过沙箱 fake-IP DNS 的 Settings SSRF 构造拦截；
  `-o addopts=""` 因本机无 pytest-cov）。
- ruff：`python -m ruff check app tests`（选 E4/E7/E9/F）全绿。
