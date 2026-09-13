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

## M1 — ReplayTrace schema + determinism + sanitizer + B0 投影器（2026-09-14）

- 改动: `app/lib/harness/replay/{__init__,schema,determinism,sanitize,metrics}.py` + `tests/harness_replay/test_replay_schema.py`（17 tests）。
- 契约: ReplayTrace v1（additive-only 演进，未知字段保留）；behavior_digest（语义投影：剥墙钟/关联 id/计时精度）；sanitize（秘密键 REDACTED、禁值键 digest 块、字符串秘密正则剥离、超限降级 digest-only）。
- 关键发现: 生产链 payload 经 `bound_meta` 把嵌套 dict repr 化且嵌套 secrets 不脱敏 → sanitizer 补 `ast.literal_eval` 安全还原 + 字符串级秘密剥离。
- 测试: 17/17 绿 + ruff 绿。回滚面: 纯新增文件。

## M2 — Recorder + 生产接线（2026-09-14）

- 改动: `app/lib/harness/replay/recorder.py`；`app/agent_pi_bridge.py` 两处 settle（stream_prompt finally + prompt settle）各 +1 个 try 包裹的 `maybe_record_turn` 调用。
- 契约: `HARNESS_REPLAY_RECORD`/`HARNESS_REPLAY_DIR` env 总闸；64 条/session FIFO 保留；never-raises；排队期早退路径不接（无轨迹可录）。
- 测试: recorder 6/6 绿；`tests/test_pi_integration.py` 24 通过 + 1 失败（heartbeat flake，已在干净 master 复现同败 = 预存，非本线回归）。
- 回滚面: bridge revert 2 个 hunk 即回 master 行为。

## M3 — Offline replayer T1/T2（2026-09-14）

- 改动: `app/lib/harness/replay/replayer.py`；`tests/harness_replay/{test_replay_replayer.py,replay_judge.py}` + `tests/fixtures/replay/map.png`。
- 契约: Scenario/ScenarioOp/TurnSpec schema v1；T1 冻结 ops → 真实 harness/gate/L5；T2 mutations → 真实 lifecycle facade（`source_profile`/`layer_upsert` 等 13 op）；比对 exact/tolerant/nondeterministic_text；replay_digest 含 gate score（canned 数据的纯函数）。
- 关键发现: 生产信任阶梯要求 session 持有的 frontend observation（freshness/指纹收敛/style/图层在场）→ 绿场景 fixture 携带完整 observation；占位符 `$cartographic_fingerprint`（同 `cartographic_fingerprint` 生产函数）/`$session_id`；T2 指纹用剥 session 别名（ref/ref_id/data_fingerprint）的归一化指纹；离线 L5 走 `CARTO_VISUAL_JUDGE` env 缝 + 确定性 fixture judge（`tests.harness_replay.replay_judge:deterministic_judge`）。
- 测试: 35/35 绿 + ruff 绿。
- 未解决项: T3（ToolDispatchService 假服务级重放）留接口点（`dispatch_backed` 字段 → not_run 不静默）；CQ 绿路径的 observation fixture 仅覆盖单点图层形态，多图层/表达式样式形态留给语料扩展。


## M4 — Scenario corpus + multi-turn（B4/B5）

- 状态: 计划中。

## M5 — Fault injection + ratchet 接流（B6/B7）

- 状态: 计划中。

## M6 — Explainability + bench CLI + perf profiles + triage（B8-B11）

- 状态: 计划中。

## M7 — 回归、独立 review、PR

- 状态: 计划中。
