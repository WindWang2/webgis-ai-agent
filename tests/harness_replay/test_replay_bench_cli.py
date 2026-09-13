"""bench CLI / triage / explainability 契约测试（B8/B9/B11）。"""
from __future__ import annotations

import json

import pytest

from app.lib.harness.replay import bench as bench_module
from app.lib.harness.replay.bench import (
    apply_profile,
    compare_results,
    run_suite,
    select_scenarios,
)
from app.lib.harness.replay.explain import (
    explain_replay,
    explain_trace,
    render_markdown,
)
from app.lib.harness.replay.scenarios import build_corpus
from app.lib.harness.replay.triage import CATEGORIES, classify, summarize
from tests.harness_replay.test_replay_replayer import (
    _cartography_ok,
    _upsert_op,
)

pytestmark = pytest.mark.cartography


def _trace_stub() -> dict:
    from tests.harness_replay.test_replay_schema import _build

    return _build().to_dict()


class TestExplain:
    def test_trace_bundle_disclosures(self):
        explanation = explain_trace(_trace_stub())
        assert explanation["kind"] == "recorded_trace"
        assert explanation["task"]["normalized_goal"] == "point_distribution"
        assert explanation["behavior_digest"]
        assert "missing_evidence" in explanation["disclosures"]
        md = render_markdown(explanation)
        assert md.startswith("# Trace Explainability")

    @pytest.mark.asyncio
    async def test_replay_bundle_and_markdown(self):
        from app.lib.harness.replay.replayer import OfflineReplayer

        corpus = {s.scenario_id: s for s in build_corpus()}
        scenario = corpus["core-point_distribution-01"]
        result = await OfflineReplayer(seed=1).replay_scenario(scenario)
        explanation = explain_replay(scenario, result)
        assert explanation["kind"] == "offline_replay"
        assert explanation["turns"][0]["verdict"]["goal"]["status"] == "pass"
        md = render_markdown(explanation)
        assert "Replay Explainability" in md
        assert "webgis_layer_upsert" in md


class TestBench:
    def test_suite_selection(self):
        corpus = build_corpus()
        assert len(select_scenarios(corpus, "core")) >= 100
        assert len(select_scenarios(corpus, "multi-turn")) >= 30
        assert len(select_scenarios(corpus, "faults")) >= 10
        assert len(select_scenarios(corpus, "all")) == len(corpus)
        only = select_scenarios(corpus, "all", only="core-heatmap-01")
        assert [s.scenario_id for s in only] == ["core-heatmap-01"]

    @pytest.mark.asyncio
    async def test_run_suite_small_and_report(self, tmp_path):
        corpus = build_corpus()
        scenarios = select_scenarios(corpus, "all", limit=6)
        report = await run_suite(scenarios, seed=3)
        assert report["scenarios"] == 6
        assert report["green"] + report["red"] == 6
        out = tmp_path / "report.json"
        bench_module.write_report(report, "json", str(out))
        assert json.loads(out.read_text(encoding="utf-8"))["scenarios"] == 6

    @pytest.mark.asyncio
    async def test_compare_detects_digest_drift(self):
        corpus = build_corpus()
        scenarios = select_scenarios(corpus, "all", limit=3)
        baseline = await run_suite(scenarios, seed=7)
        baseline_path = "/tmp/r10-baseline.json" if False else None
        # 直接以对象比对（文件形态由 json 覆盖测试保证）。
        comparison = compare_results(
            baseline, str(_write_json(baseline)))
        assert comparison["drifts"] == []
        tampered = json.loads(json.dumps(baseline))
        tampered["entries"][0]["replay_digest"] = "drifted"
        tampered_path = _write_json(tampered)
        comparison = compare_results(baseline, str(tampered_path))
        assert comparison["drifts"][0]["kind"] == "digest_drift"

    @pytest.mark.asyncio
    async def test_resume_skips_completed(self, tmp_path):
        corpus = build_corpus()
        scenarios = select_scenarios(corpus, "all", limit=4)
        state = str(tmp_path / "state.json")
        first = await run_suite(scenarios, seed=1, resume_path=state)
        assert first["scenarios"] == 4
        second = await run_suite(scenarios, seed=1, resume_path=state)
        assert second["scenarios"] == 4  # 全部命中 resume
        # 参数变化 → 不混账，重跑
        third = await run_suite(scenarios, seed=2, resume_path=state)
        assert third["scenarios"] == 4
        assert third["suite_seed"] == 2

    @pytest.mark.asyncio
    async def test_perf_profile_scales_bytes_but_keeps_semantics(self):
        corpus = build_corpus()
        scenario = select_scenarios(corpus, "all", only="core-heatmap-01")[0]
        small = apply_profile(scenario, "small")
        medium = apply_profile(scenario, "medium")
        large = apply_profile(scenario, "large")
        base_bytes = sum(len(str(op.arguments))
                         for t in small.turns for op in t.ops)
        mid_bytes = sum(len(str(op.arguments))
                        for t in medium.turns for op in t.ops)
        big_bytes = sum(len(str(op.arguments))
                        for t in large.turns for op in t.ops)
        assert mid_bytes > base_bytes * 100
        assert big_bytes > mid_bytes * 4
        # 语义不变：放大后重放 digest 与语义期望一致。
        from app.lib.harness.replay.replayer import OfflineReplayer

        result = await OfflineReplayer(seed=1).replay_scenario(medium)
        assert result.ok is True


def _write_json(payload) -> object:
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkstemp(suffix=".json")[1])
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestTriage:
    @pytest.mark.asyncio
    async def test_semantic_vs_text_and_platform(self):
        corpus = {s.scenario_id: s for s in build_corpus()}
        replayer = __import__(
            "app.lib.harness.replay.replayer",
            fromlist=["OfflineReplayer"]).OfflineReplayer(seed=2)
        green = corpus["core-point_distribution-01"]
        ok_result = await replayer.replay_scenario(green)

        import copy

        broken = copy.deepcopy(green)
        broken.turns[0].expect["gate"]["checks"]["CartographicQuality"] = {
            "evaluated": True, "passed": False}
        cq_result = await replayer.replay_scenario(broken)
        cls = classify(broken, cq_result)
        assert cls["category"] in ("cartography_visual_regression",
                                   "semantic_regression")

        text_only = copy.deepcopy(green)
        text_only.turns[0].expect["user_text"] = {"present": True,
                                                  "len_bucket": "l"}
        t_result = await replayer.replay_scenario(text_only)
        cls = classify(text_only, t_result)
        assert cls["category"] == "nondeterministic_text_only"

        drifted = copy.deepcopy(green)
        drifted.faults = [{"type": "stale_ref", "target_turn": 0}]
        f_result = await replayer.replay_scenario(
            __import__(
                "app.lib.harness.replay.faults",
                fromlist=["apply_faults"]).apply_faults(drifted))
        cls = classify(drifted, f_result)
        assert cls["category"] in CATEGORIES

    def test_summarize_counts(self):
        counts = summarize([
            {"category": "semantic_regression"},
            {"category": "semantic_regression"},
            {"category": "nondeterministic_text_only"},
        ])
        assert counts["semantic_regression"] == 2
        assert counts["nondeterministic_text_only"] == 1
        assert counts["generated_artifact_drift"] == 0
