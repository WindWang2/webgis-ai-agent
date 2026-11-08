"""Focused perf/correctness 测试：G07 M6 replay bench 切片（UTF-8 计量 + resume flush）。

- 非 perf-gate：不对比 baselines.json 阈值（本切片不改 perf/budgets.json），
  只锁「flush 降频不改变 resume 语义」与「context_bytes_proxy 为 UTF-8 bytes」
  两个可复现事实，并记录小样本计时供 docs 报告引用。
- 运行：``pytest tests/benchmarks/test_harness_reliability_bench.py -m perf -q``
  （无 ``-m perf`` 时被 conftest 确定性 skip —— perf 隔离纪律 #664）。
"""
from __future__ import annotations

import json
import math
import time

import pytest

from app.lib.harness.replay.bench import (
    apply_profile,
    run_one,
    run_suite,
    select_scenarios,
)
from app.lib.harness.replay.determinism import canonical_json
from app.lib.harness.replay.replayer import (
    OfflineReplayer,
    Scenario,
    ScenarioOp,
    TurnSpec,
)
from app.lib.harness.replay.scenarios import build_corpus

pytestmark = [pytest.mark.perf, pytest.mark.cartography]

SEED = 11


def test_benchmark_report_discloses_unmeasured_dimensions():
    from pathlib import Path

    report = json.loads((Path(__file__).resolve().parents[2] /
        "docs/quality/evidence/harness_reliability_bench_report.json")
        .read_text(encoding="utf-8"))
    dimensions = {entry["dimension"] for entry in report["unmeasured"]}
    assert {"artifact_bytes", "emitted_events", "rendering_frames"}.issubset(dimensions)
    assert all(entry["status"] == "not_evaluated" for entry in report["unmeasured"])


def _independent_clean(value):
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        if value == 0.0:
            return 0.0
        return round(value, 6)
    if isinstance(value, dict):
        return {str(key): _independent_clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_independent_clean(item) for item in value]
    return value


def _independent_utf8_bytes(turns) -> int:
    return sum(
        len(json.dumps(_independent_clean(op.arguments), ensure_ascii=False,
                       sort_keys=True, separators=(",", ":")).encode("utf-8"))
        for t in turns for op in t.ops
    )


class TestContextProxyUtf8:
    async def test_proxy_is_utf8_bytes_not_str_chars(self):
        args = {"name": "中文图层", "query": "éèê", "tags": ["点", "线"]}
        scenario = Scenario(
            scenario_id="perf-utf8-01", category="core",
            turns=[TurnSpec(ops=[ScenarioOp(
                call_id="c1", tool="t", arguments=dict(args))])])
        entry = await run_one(OfflineReplayer(seed=SEED), scenario)
        # 独立硬编码：canonical compact JSON 的 UTF-8 字节数。
        assert entry["context_bytes_proxy"] == 61
        assert len(str(args)) == 52  # 旧口径会偏低 —— 证明两者不等

    async def test_proxy_matches_independent_formula_on_profiled_scenario(self):
        corpus = build_corpus()
        scenario = select_scenarios(corpus, "all", only="core-heatmap-01")[0]
        for profile in ("small", "medium", "large"):
            profiled = apply_profile(scenario, profile)
            entry = await run_one(OfflineReplayer(seed=SEED), profiled)
            assert entry["context_bytes_proxy"] == \
                _independent_utf8_bytes(profiled.turns), profile

    def test_utf8_formula_cost_recorded(self):
        """计量口径微基准：如实记录（不当作速度优化断言）。"""
        corpus = build_corpus()
        scenario = select_scenarios(corpus, "all", only="core-heatmap-01")[0]
        profiled = apply_profile(scenario, "large")
        ops = [op for t in profiled.turns for op in t.ops]

        t0 = time.perf_counter()
        str_chars = sum(len(str(op.arguments)) for op in ops)
        str_ms = (time.perf_counter() - t0) * 1000
        assert str_ms < 1000  # 旧公式参照计时（仅记录量级，无 gate 断言）

        t0 = time.perf_counter()
        utf8_bytes = sum(
            len(canonical_json(op.arguments).encode("utf-8")) for op in ops)
        utf8_ms = (time.perf_counter() - t0) * 1000

        # ASCII filler 下 str() 带 ", " 分隔反而略长；只锁同量级 + 记录真实值。
        assert 0.5 < utf8_bytes / max(1, str_chars) < 2.0
        # 宽松上界：防止口径回退成 O(n²) 之类退化（非精确性能 gate）。
        assert utf8_ms < 1000, f"proxy formula degenerated: {utf8_ms:.1f}ms"


class TestResumeFlushSemantics:
    async def test_flush_interval_1_equiv_old_every_scenario_write(self,
                                                                    tmp_path):
        """flush_interval=1 = 旧「每场景全量重写」语义：每个场景一次写。"""
        corpus = build_corpus()
        scenarios = select_scenarios(corpus, "all", limit=4)
        state = str(tmp_path / "state.json")

        writes = []
        from app.lib.harness.replay import bench as bench_module
        real_replace = bench_module.os.replace

        def counting_replace(src, dst):
            writes.append(1)
            return real_replace(src, dst)

        import unittest.mock as mock
        with mock.patch.object(bench_module.os, "replace", counting_replace):
            report = await run_suite(scenarios, seed=SEED,
                                     resume_path=state, flush_interval=1)
        assert report["scenarios"] == 4
        assert len(writes) == 4  # 每场景一次（旧行为基线）

    async def test_flush_interval_16_writes_once_and_state_complete(self,
                                                                    tmp_path):
        corpus = build_corpus()
        scenarios = select_scenarios(corpus, "all", limit=4)
        state = str(tmp_path / "state.json")

        writes = []
        from app.lib.harness.replay import bench as bench_module
        real_replace = bench_module.os.replace

        def counting_replace(src, dst):
            writes.append(1)
            return real_replace(src, dst)

        import unittest.mock as mock
        with mock.patch.object(bench_module.os, "replace", counting_replace):
            report = await run_suite(scenarios, seed=SEED,
                                     resume_path=state, flush_interval=16)
        assert report["scenarios"] == 4
        assert len(writes) == 1  # 仅收尾 flush
        saved = json.loads(open(state, encoding="utf-8").read())
        assert len(saved["entries"]) == 4  # 收尾完整性
        # 恢复语义：第二次运行全部跳过。
        second = await run_suite(scenarios, seed=SEED, resume_path=state)
        assert second["scenarios"] == 4

    async def test_resume_flush_not_slower_wall(self, tmp_path):
        """宽松冒烟：小样本上 flush=16 不比 flush=1 慢（防灾难性回退）。

        真实 before/after 数值以
        scripts/perf/bench_harness_reliability.py 的原始 JSON 报告为准。
        """
        corpus = build_corpus()
        scenarios = select_scenarios(corpus, "all", limit=30)

        async def timed(interval):
            state = str(tmp_path / f"state_{interval}.json")
            if __import__("os").path.exists(state):
                __import__("os").remove(state)
            t0 = time.perf_counter()
            await run_suite(scenarios, seed=SEED, resume_path=state,
                            flush_interval=interval)
            return (time.perf_counter() - t0) * 1000

        # warm 一次再各测 3 次取 p50。
        await timed(1)
        walls_1 = [await timed(1) for _ in range(3)]
        walls_16 = [await timed(16) for _ in range(3)]
        p50 = lambda xs: sorted(xs)[len(xs) // 2]  # noqa: E731
        # 30 场景差异在小样本上可能淹没于噪声：只断言不显著变慢（<2x）。
        assert p50(walls_16) < p50(walls_1) * 2
