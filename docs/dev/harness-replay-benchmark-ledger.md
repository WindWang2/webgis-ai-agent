# Ledger — Harness Replay + Benchmark + Explainability (R10)

规则: 每个里程碑追加一节；只记事实（改动文件、契约、测试原文摘要、证据、回滚面、未解决项）。

## M0 — Phase 0 勘察与基线（2026-09-14）

- 基线 SHA: `origin/master = 580b33e9`（Merge PR #1272）；fetch --all --prune 已执行。
- worktree: `../webgis-ai-agent-r10`，branch `harness/replay-benchmark-explainability-v1`，无夹带改动。
- 勘察: Subagent A 深读生产链/评测设施/契约/9 个 open-merged PR diffstat/ADR 0150-0182/文档惯例，产出报告（主仓 tmp/ 归档，不入库）；主 Agent 复核关键锚点（lane 自由字符串、trace_store V6、Stage IntEnum、settle 缝、pytest markers、.alloc.json）。
- open PR 对账: #1270（低碰撞）、#1273（中）、#1274/#1275/#1277/#1279（高热区，全部规避或单点 additive）、#1276/#1278（低-中）。已合并复用基座: #1269（ratchet/fact store/golden）、#1271（wave ratchet）、#1272（offline fixture/guard）。
- ADR: claim 0183（全网未占用）。
- 文档: recon/decisions 两篇落 docs/dev/（本文件为第三篇）。
- 未解决项: 无阻断项。风险登记见 recon §8。

## M1 — ReplayTrace schema + determinism + sanitizer（B1/B0 基础）

- 目标: versioned trace schema、白名单 sanitize、注入时钟/确定性 id、B0 评测维度投影器。
- 状态: 计划中。

## M2 — Recorder + 生产接线（B2）

- 目标: env-gated ReplayRecorder + agent_pi_bridge settle 单点调用 + 打包测试。
- 状态: 计划中。

## M3 — Offline replayer T1/T2/T3（B3）

- 状态: 计划中。

## M4 — Scenario corpus + multi-turn（B4/B5）

- 状态: 计划中。

## M5 — Fault injection + ratchet 接流（B6/B7）

- 状态: 计划中。

## M6 — Explainability + bench CLI + perf profiles + triage（B8-B11）

- 状态: 计划中。

## M7 — 回归、独立 review、PR

- 状态: 计划中。
