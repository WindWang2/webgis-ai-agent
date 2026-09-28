"""统一 runner / 报告 / 基线测试（E15）：全部旅程绿 + 确定性双跑 + 基线纪律。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lib.harness.lab.journeys import default_spec_paths, load_specs
from app.lib.harness.lab.metrics import (
    DIMENSIONS,
    FAIL,
    NOT_EVALUATED,
    PASS,
    DimensionVerdict,
    SpecEval,
    merge_contributions,
    spec_ok,
)
from app.lib.harness.lab.report import (
    build_report,
    compare_baseline,
    render_markdown,
    write_baseline,
)
from app.lib.harness.lab.runner import LabRunner
from app.lib.harness.lab.spec import LabScenario

REPO = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO / "tests" / "fixtures" / "lab" / "baseline.json"


@pytest.fixture(scope="module", autouse=True)
def _full_adapter_chain():
    """runner 自测装配完整生产链（与 scripts/eval_lab.py CLI 同一装配）——
    入库基线嵌的是全链投影，缺 science adapter 即漂移。"""
    import importlib.util
    import sys

    scripts_dir = REPO / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location(
        "eval_lab_module_runner", scripts_dir / "eval_lab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from app.lib.harness.lab.adapters import register_adapter

    register_adapter(module.ScienceOracleAdapter())


@pytest.fixture(scope="module")
def all_specs():
    return load_specs(default_spec_paths())


class TestJourneysEndToEnd:
    @pytest.mark.asyncio
    async def test_all_representative_journeys_green(self, all_specs):
        assert len(all_specs) >= 7
        evals = await LabRunner(seed=0).run_specs(all_specs)
        red = [e for e in evals if not spec_ok(e)]
        assert not red, [
            (e.spec_id, {k: v.status for k, v in e.dimensions.items()},
             e.detail_failures)
            for e in red
        ]

    @pytest.mark.asyncio
    async def test_every_dimension_covered_by_some_spec(self, all_specs):
        """九维词表中每个语义维至少被一条旅程真实评测（观测维除外）。"""
        evals = await LabRunner(seed=0).run_specs(all_specs)
        exercised = {
            d
            for e in evals
            for d, v in e.dimensions.items()
            if v.status in (PASS, FAIL)
        }
        semantic = [d for d in DIMENSIONS
                    if d not in ("context_token_cost", "tool_retries",
                                 "wall_clock_ms")]
        missing = set(semantic) - exercised
        assert not missing, f"dimensions never evaluated: {sorted(missing)}"

    @pytest.mark.asyncio
    async def test_double_run_same_digest(self, all_specs):
        """同 seed 同 fixture → state/evidence/metrics 确定性（digest 相等）。"""
        runner = LabRunner(seed=0)
        evals1 = await runner.run_specs(all_specs)
        evals2 = await LabRunner(seed=0).run_specs(all_specs)
        r1 = build_report(evals1, seed=0)
        r2 = build_report(evals2, seed=0)
        assert r1["report_digest"] == r2["report_digest"]
        assert r1["green"] == len(all_specs)

    @pytest.mark.asyncio
    async def test_committed_baseline_matches_current_run(self, all_specs):
        """入库基线 = 当前 master 事实（漂移即红，杜绝基线失真）。"""
        evals = await LabRunner(seed=0).run_specs(all_specs)
        report = build_report(evals, seed=0)
        comparison = compare_baseline(report, str(BASELINE_PATH))
        assert comparison["drifts"] == [], comparison["drifts"]
        assert comparison["green_current"] == comparison["green_baseline"]


class TestMetricsSemantics:
    def _eval(self, contributions, declared) -> SpecEval:
        ev = SpecEval(spec_id="t", kind="replay")
        ev.dimensions = merge_contributions(contributions, declared)
        return ev

    def test_fail_dominates_pass(self):
        ev = self._eval(
            [DimensionVerdict("goal_completion", PASS),
             DimensionVerdict("goal_completion", FAIL, detail="x")],
            declared=set())
        assert ev.dimensions["goal_completion"].status == FAIL

    def test_declared_not_evaluated_is_not_ok(self):
        ev = self._eval([], declared={"goal_completion"})
        assert ev.dimensions["goal_completion"].status == NOT_EVALUATED
        assert not spec_ok(ev)

    def test_undeclared_not_evaluated_is_honest_ok(self):
        ev = self._eval([], declared=set())
        assert spec_ok(ev)

    def test_observation_dims_never_verdict(self):
        ev = self._eval(
            [DimensionVerdict("wall_clock_ms", NOT_EVALUATED, value=12.5)],
            declared=set())
        assert ev.dimensions["wall_clock_ms"].status == NOT_EVALUATED
        assert ev.dimensions["wall_clock_ms"].value == 12.5

    def test_detail_failure_blocks_ok_even_all_green(self):
        ev = self._eval(
            [DimensionVerdict("goal_completion", PASS)], declared=set())
        ev.detail_failures.append("fault contract violation")
        assert not spec_ok(ev)


class TestBaselineDiscipline:
    def _green_report(self):
        ev = SpecEval(spec_id="s1", kind="replay")
        ev.dimensions = merge_contributions(
            [DimensionVerdict("goal_completion", PASS, value="pass")],
            declared=set())
        ev.replay_digest = "dig"
        report = build_report([ev], seed=0)
        return report

    def _red_report(self):
        ev = SpecEval(spec_id="s1", kind="replay")
        ev.dimensions = merge_contributions(
            [DimensionVerdict("goal_completion", FAIL, detail="boom")],
            declared=set())
        return build_report([ev], seed=0)

    def test_write_baseline_refuses_red(self, tmp_path):
        with pytest.raises(ValueError, match="refusing to write baseline"):
            write_baseline(self._red_report(), str(tmp_path / "b.json"))

    def test_write_baseline_force_records_red(self, tmp_path):
        payload = write_baseline(self._red_report(), str(tmp_path / "b.json"),
                                 force=True)
        assert payload["entries"][0]["ok"] is False

    def test_compare_detects_digest_and_verdict_drift(self, tmp_path):
        write_baseline(self._green_report(), str(tmp_path / "b.json"))
        drifted = self._green_report()
        drifted["entries"][0]["replay_digest"] = "moved"
        drifted["entries"][0]["dimensions"]["goal_completion"]["status"] = \
            FAIL
        comparison = compare_baseline(drifted, str(tmp_path / "b.json"))
        kinds = {d["kind"] for d in comparison["drifts"]}
        assert "digest_drift" in kinds and "verdict_drift" in kinds

    def test_compare_flags_new_and_missing(self, tmp_path):
        write_baseline(self._green_report(), str(tmp_path / "b.json"))
        ev = SpecEval(spec_id="brand-new", kind="replay")
        ev.dimensions = merge_contributions([], declared=set())
        comparison = compare_baseline(build_report([ev], seed=0),
                                      str(tmp_path / "b.json"))
        kinds = sorted(d["kind"] for d in comparison["drifts"])
        assert kinds == ["missing", "new"]


class TestReportRendering:
    @pytest.mark.asyncio
    async def test_markdown_contains_dimension_table(self, all_specs):
        evals = await LabRunner(seed=0).run_specs(all_specs[:2])
        md = render_markdown(build_report(evals, seed=0))
        for dim in DIMENSIONS:
            assert dim in md
        assert "green" in md

    @pytest.mark.asyncio
    async def test_report_json_is_serializable(self, all_specs):
        evals = await LabRunner(seed=0).run_specs(all_specs[:2])
        payload = build_report(evals, seed=0)
        text = json.dumps(payload, ensure_ascii=False)
        assert payload["kind"] == "eval_lab_report"
        assert text


class TestSpecLevelHonesty:
    @pytest.mark.asyncio
    async def test_fault_journey_expects_degradation(self, all_specs):
        """设计性降级旅程：goal 期望 fail，故障合同必须真实发生。"""
        spec = next(s for s in all_specs
                    if s.spec_id == "lab-j6-provider-failure-recovery")
        ev = await LabRunner(seed=0).run_spec(spec)
        goal = ev.dimensions["goal_completion"]
        recovery = ev.dimensions["recovery_correctness"]
        assert goal.status == PASS
        assert recovery.status == PASS

    def test_spec_validate_rejects_mutation(self):
        """规格被篡改成未知词表时必须显式拒绝（不静默跑空）。"""
        spec = LabScenario.from_dict({
            "spec_id": "x", "title": "t", "kind": "replay",
            "fault_plan": [{"type": "solar_flare"}],
            "turns": [{"user_input": "u"}],
        })
        from app.lib.harness.lab.spec import SpecError

        with pytest.raises(SpecError):
            spec.validate()
