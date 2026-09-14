"""Harness Replay + Benchmark + Explainability（方向 10，ADR-0183）。

轨迹级回归体系的 app 侧库层：

- :mod:`.schema`      ReplayTrace v1 —— 18 阶段证据链的**打包层**（不是第二套 trace）；
- :mod:`.recorder`    env-gated 生产录制（S13 settle 缝单点接线，fire-and-forget）；
- :mod:`.replayer`    离线确定性重放（T1 证据级 / T2 变异级 / T3 dispatch 级）；
- :mod:`.scenarios`   场景语料矩阵 + 确定性展开器；
- :mod:`.faults`      故障注入规格（落在 replayer 环境边界）；
- :mod:`.metrics`     B0 评测维度投影（复用 cartography ratchet 行契约）;
- :mod:`.ratchet`     轨迹指标 → 既有 ratchet/质量事实库（lane="replay"）；
- :mod:`.explain`     因果链 explainability bundle（JSON + Markdown）；
- :mod:`.triage`      失败六分类（禁止裸 "snapshot changed"）；
- :mod:`.bench`       benchmark runner（scripts/replay_bench.py 是 CLI 入口）。

设计约束（全部承自 ADR-0183 / docs/dev/harness-replay-benchmark-decisions.md）：
Pi 仍是 Agent Host；本包只读既有生产缝（GisTraceChain / TurnEvidence /
PiAgentHarness / HarnessEvaluator / MapSpec lifecycle / cartography ratchet），
不新建任何平行 planner/store/registry/质量指标体系。
"""
from app.lib.harness.replay.schema import (
    REPLAY_TRACE_SCHEMA_VERSION,
    ReplayTrace,
    build_trace,
)

__all__ = [
    "REPLAY_TRACE_SCHEMA_VERSION",
    "ReplayTrace",
    "build_trace",
]
