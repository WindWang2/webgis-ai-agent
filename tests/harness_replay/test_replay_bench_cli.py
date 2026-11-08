"""bench CLI / triage / explainability 契约测试（B8/B9/B11）。"""
from __future__ import annotations

import json
import math

import pytest

from app.lib.harness.replay import bench as bench_module
from app.lib.harness.replay.bench import (
    apply_profile,
    compare_results,
    run_one,
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
        journey = explanation["journey_evidence"]
        assert journey["schema_version"] == "gis_journey_evidence.v1"
        assert journey["overall"] == "fail"
        assert journey["completion_claims"]["map_rendered_and_observed"] is False
        assert "Journey: `fail`" in md

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
    """与实现无关的计量参照：compact JSON 的 UTF-8 字节数（不 import 实现）。"""
    return sum(
        len(json.dumps(_independent_clean(op.arguments), ensure_ascii=False,
                       sort_keys=True, separators=(",", ":")).encode("utf-8"))
        for t in turns for op in t.ops)


class TestContextBytesUtf8:
    @pytest.mark.asyncio
    async def test_context_bytes_proxy_counts_utf8_json_bytes(self):
        """非 ASCII 固定样例硬编码期望字节数 —— 防 #1998 UTF-8 回归。"""
        from app.lib.harness.replay.replayer import (
            OfflineReplayer, Scenario, ScenarioOp, TurnSpec)

        args = {"name": "中文图层", "query": "éèê", "tags": ["点", "线"]}
        scenario = Scenario(
            scenario_id="utf8-bytes-01", category="core",
            turns=[TurnSpec(ops=[ScenarioOp(
                call_id="c1", tool="t", arguments=dict(args))])])
        entry = await run_one(OfflineReplayer(seed=5), scenario)
        # '{"name":"中文图层","query":"éèê","tags":["点","线"]}' 的 UTF-8 字节数；
        # len(str(...)) 口径下是 52（字符），两者不可混。
        assert entry["context_bytes_proxy"] == 61

    @pytest.mark.asyncio
    async def test_context_bytes_proxy_ascii_sample(self):
        from app.lib.harness.replay.replayer import (
            OfflineReplayer, Scenario, ScenarioOp, TurnSpec)

        scenario = Scenario(
            scenario_id="utf8-bytes-02", category="core",
            turns=[TurnSpec(ops=[ScenarioOp(
                call_id="c1", tool="t", arguments={"a": "b"})])])
        entry = await run_one(OfflineReplayer(seed=5), scenario)
        assert entry["context_bytes_proxy"] == len(b'{"a":"b"}')

    @pytest.mark.asyncio
    async def test_context_bytes_proxy_float_uses_canonical_normalization(self):
        from app.lib.harness.replay.replayer import (
            OfflineReplayer, Scenario, ScenarioOp, TurnSpec)

        scenario = Scenario(
            scenario_id="utf8-bytes-float", category="core",
            turns=[TurnSpec(ops=[ScenarioOp(
                call_id="c1", tool="t", arguments={"v": 0.123456789})])])
        entry = await run_one(OfflineReplayer(seed=5), scenario)
        assert entry["context_bytes_proxy"] == len(b'{"v":0.123457}')
        assert entry["context_bytes_proxy"] == _independent_utf8_bytes(
            scenario.turns)


class TestResumeFlush:
    @pytest.mark.asyncio
    async def test_flush_interval_bounds_writes_and_final_state_complete(
            self, tmp_path, monkeypatch):
        from pathlib import Path as _Path

        corpus = build_corpus()
        scenarios = select_scenarios(corpus, "all", limit=6)
        state = str(tmp_path / "state.json")
        dumps_calls = []
        real_dumps = json.dumps

        def counting(obj, *a, **k):
            if isinstance(obj, dict) and "entries" in obj:
                dumps_calls.append(1)
            return real_dumps(obj, *a, **k)

        monkeypatch.setattr(bench_module.json, "dumps", counting)
        await run_suite(scenarios, seed=1, resume_path=state, flush_interval=2)
        # 6 entries / 每 2 条 flush = 3 次；收尾不重复写空转。
        assert len(dumps_calls) == 3
        saved = json.loads(_Path(state).read_text(encoding="utf-8"))
        assert len(saved["entries"]) == 6  # 收尾 flush → 状态完整
        # 恢复语义不变：已完成场景全部跳过。
        second = await run_suite(scenarios, seed=1, resume_path=state,
                                 flush_interval=2)
        assert second["scenarios"] == 6

    @pytest.mark.asyncio
    async def test_default_flush_interval_writes_once_per_suite(self, tmp_path,
                                                                monkeypatch):
        corpus = build_corpus()
        scenarios = select_scenarios(corpus, "all", limit=6)
        state = str(tmp_path / "state.json")
        dumps_calls = []
        real_dumps = json.dumps

        def counting(obj, *a, **k):
            if isinstance(obj, dict) and "entries" in obj:
                dumps_calls.append(1)
            return real_dumps(obj, *a, **k)

        monkeypatch.setattr(bench_module.json, "dumps", counting)
        await run_suite(scenarios, seed=1, resume_path=state)
        assert len(dumps_calls) == 1  # 默认 16 条间隔：6 场景只触发收尾 flush

    def test_flush_interval_validation(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            import asyncio as _asyncio
            _asyncio.run(run_suite([], flush_interval=0))


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
        base_bytes = _independent_utf8_bytes(small.turns)
        mid_bytes = _independent_utf8_bytes(medium.turns)
        big_bytes = _independent_utf8_bytes(large.turns)
        assert mid_bytes > base_bytes * 100
        assert big_bytes > mid_bytes * 4
        # run_one 计量口径与独立参照一致（UTF-8 bytes，非 str 字符数）。
        from app.lib.harness.replay.replayer import OfflineReplayer

        entry = await run_one(OfflineReplayer(seed=1), medium)
        assert entry["context_bytes_proxy"] == mid_bytes
        # 语义不变：放大后重放 digest 与语义期望一致。
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
        assert ok_result.ok is True  # 对照组：green 基线保持绿

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
