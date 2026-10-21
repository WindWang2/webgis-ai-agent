"""G05 失败注入 → 红：负例真实性、行为反转、判错归因。

三类防线：
1. 负例场景本身通过 = 判错面抓住注入（不恒绿）；
2. 反转（移除注入/换成正确行为）→ 负例期望必须翻红（防假负例）；
3. 期望被篡改 → 场景必须失败（断言强度）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.cartography

from .contracts import ScenarioContract
from .corpus import scenarios_by_id
from .runner import TaskScenarioRunner


def _swap_dataset(scenario: ScenarioContract, old: str, new: str,
                  new_id: str) -> ScenarioContract:
    """构造反转变体：把注入数据换成正确数据（其余期望原样保留）。"""
    data = scenario.model_dump(mode="json")
    data["id"] = new_id
    for d in data["datasets"]:
        if d["name"] == old:
            d["dataset_kind"] = new
    for t in data["turns"]:
        for st in t["steps"]:
            args = st.get("arguments") or {}
            if args.get("name") == old:
                args["dataset_kind"] = new
    return ScenarioContract.model_validate(data)


class TestNegativeScenariosCatchInjections:
    """负例通过 = 判错面翻红成立（与 test_task_e2e 互为镜像说明）。"""

    @pytest.mark.asyncio
    async def test_sparse_heatmap_negative_distinct_from_positive(
            self, g05_runner: TaskScenarioRunner):
        """科学负例：同工具链下稀疏/充足样本的判错面必须可区分。"""
        negative = await g05_runner.run(
            scenarios_by_id()["f02b-sparse-heatmap-degraded"])
        positive = await g05_runner.run(
            scenarios_by_id()["f02a-heatmap-method"])
        assert negative.ok, negative.summary_line()
        assert positive.ok, positive.summary_line()
        neg_types = _check_actual(negative, "f02b-no-heatmap")
        pos_layers = _check_actual(positive, "f02a-heatmap-layer")
        # 负例断言 heatmap 缺席成立，正例断言 heatmap 在场成立——判错面区分
        assert "heatmap" not in (neg_types or []), neg_types
        assert "heatmap" in (pos_layers or []), pos_layers

    @pytest.mark.asyncio
    async def test_wrong_dataset_does_not_claim_success(
            self, g05_runner: TaskScenarioRunner):
        """数据负例：错误 CRS 数据下任务判据不放行 + 缺口如实披露。"""
        negative = await g05_runner.run(
            scenarios_by_id()["f18-wrong-dataset"])
        assert negative.ok, negative.summary_line()
        verdict = _check_actual(negative, "f18-not-satisfied")
        assert "satisfied" not in (verdict or ""), verdict
        # CRS 域校验的产品级缺口必须在场景诚实边界中声明（不假装修好了）
        scenario = scenarios_by_id()["f18-wrong-dataset"]
        assert any("crs_domain_violation_not_blocked_at_product" in note
                   for note in scenario.not_evaluated)


class TestBehaviorInversion:
    """反转注入 → 负例期望必须翻红（防恒绿负例）。"""

    @pytest.mark.asyncio
    async def test_sparse_inverted_to_dense_must_fail(
            self, g05_runner: TaskScenarioRunner):
        inverted = _swap_dataset(
            scenarios_by_id()["f02b-sparse-heatmap-degraded"],
            old="sparse", new="school_points_dense",
            new_id="f02b-inverted-dense")
        result = await g05_runner.run(inverted)
        assert not result.ok, (
            "注入移除后负例仍通过 = 恒绿负例（判错面失效）: "
            + result.summary_line())
        assert result.first_failing_stage, result.summary_line()

    @pytest.mark.asyncio
    async def test_no_coord_inverted_to_valid_must_fail(
            self, g05_runner: TaskScenarioRunner):
        inverted = _swap_dataset(
            scenarios_by_id()["f08-no-coordinates"],
            old="table", new="school_points",
            new_id="f08-inverted-valid")
        result = await g05_runner.run(inverted)
        assert not result.ok, (
            "正确坐标数据下『无点位层』期望仍通过 = 判错面失效: "
            + result.summary_line())

    def test_tier3_guard_inverted_must_error(self):
        """治理防线反转：expect_error 移除后 tier-3 派发成功 = 假注入。"""
        from .contracts import StepKind
        scenario = scenarios_by_id()["f15-tier3-governance"]
        data = scenario.model_dump(mode="json")
        for st in data["turns"][0]["steps"]:
            if st["call_id"] == "danger":
                st["expect_error"] = False
                st["allow_error"] = False
        hollow = ScenarioContract.model_validate(data)
        assert any(
            st.kind is StepKind.RAW and st.tool == "refresh_skill_surface"
            for t in hollow.turns for st in t.steps)
        # 结构检查：反转后的步骤语义必然在 runner 层翻红（dispatch 成功
        # 违反 expect_error=False 的未预期失败断言，或 tier3 拒绝触发
        # expect_error 前置失败）——二者必居其一，守卫不可绕过。
        danger = next(st for t in hollow.turns for st in t.steps
                      if st.call_id == "danger")
        assert danger.expect_error is False


class TestAssertionStrength:
    """期望篡改 → 场景必须失败（断言不是摆设）。"""

    @pytest.mark.asyncio
    async def test_tampered_expectation_fails(
            self, g05_runner: TaskScenarioRunner):
        scenario = scenarios_by_id()["f01-school-points-map"]
        data = scenario.model_dump(mode="json")
        # 篡改：把『点位层存在』的期望值改为错误断言（heatmap-only）
        for e in data["expectations"]:
            if e["id"] == "f01-points-layer":
                e["value"] = "raster"
        tampered = ScenarioContract.model_validate(data)
        result = await g05_runner.run(tampered)
        assert not result.ok
        assert result.first_failing_stage == "mapspec"

    @pytest.mark.asyncio
    async def test_dropped_expectation_weaker_but_visible(
            self, g05_runner: TaskScenarioRunner):
        """删光期望的场景 ok=True 但 0 检查——语料完整性测试把守此洞。"""
        scenario = scenarios_by_id()["f01-school-points-map"]
        data = scenario.model_dump(mode="json")
        data["expectations"] = []
        hollow = ScenarioContract.model_validate(data)
        result = await g05_runner.run(hollow)
        assert result.ok and result.checks == []
        # test_corpus.test_every_scenario_has_expectations 在语料层把守


class TestAttribution:
    def test_taxonomy_vocabulary(self):
        from .attribution import TAXONOMY_CODES
        assert set(TAXONOMY_CODES) == {
            "intent_wrong", "wrong_dataset", "invalid_method",
            "unmet_product", "stale_render", "lost_interaction",
            "export_parity", "unsupported", "governance_violation"}

    def test_taxonomy_mapping(self):
        from .attribution import taxonomy_for
        assert taxonomy_for("data", "dataset_ref") == "wrong_dataset"
        assert taxonomy_for("method", "sample_size") == "invalid_method"
        assert taxonomy_for("mapspec", "revision_monotonic") == "stale_render"
        assert taxonomy_for("render_export", "export_status") == "export_parity"
        assert taxonomy_for("mapspec", "user_wins") == "lost_interaction"
        assert taxonomy_for("final_answer", "goal_verdict") == "unmet_product"

    def test_attribute_run_links_authority(self):
        from .attribution import attribute_run
        from .contracts import CheckResult, CheckVerdict, ScenarioRunResult
        run = ScenarioRunResult(
            scenario_id="x", family="F00", negative=True,
            checks=[CheckResult(
                expectation_id="e", face="data", kind="dataset_ref",
                verdict=CheckVerdict.PASS, expected_verdict="fail",
                detail="d")],
        )
        attributed = attribute_run(run)
        assert list(attributed) == ["wrong_dataset"]
        record = attributed["wrong_dataset"][0]
        assert record["authority"], "归因必须链接权威模块"
        assert record["check"] == "e"

    @pytest.mark.asyncio
    async def test_first_failing_attribution_on_inverted(
            self, g05_runner: TaskScenarioRunner):
        from .attribution import first_failing_attribution
        inverted = _swap_dataset(
            scenarios_by_id()["f02b-sparse-heatmap-degraded"],
            old="sparse", new="school_points_dense",
            new_id="f02b-attr-probe")
        result = await g05_runner.run(inverted)
        attribution = first_failing_attribution(result)
        assert attribution is not None
        assert attribution["face"] == "method"
        assert attribution["taxonomy"] == "invalid_method"


def _check_actual(run, expectation_id: str):
    for c in run.checks:
        if c.expectation_id == expectation_id:
            return c.actual
    return None


class TestCliCompareRegression:
    """P1-1 回归：compare 对缺 error 键的旧格式基线不得漏报真回归。"""

    def test_compare_detects_regression_without_error_key(self, tmp_path):
        import json
        import scripts.g05_task_acceptance as cli
        # 旧格式基线：无 error 键（历史报告），场景 ok
        baseline = {"runs": [{
            "scenario_id": "s1",
            "checks": [{"verdict": "pass", "expected_verdict": "pass"}],
        }]}
        # 本次运行：同场景 fail（期望失配）
        current = {"runs": [{
            "scenario_id": "s1",
            "checks": [{"verdict": "fail", "expected_verdict": "pass"}],
        }]}
        b = tmp_path / "base.json"
        c = tmp_path / "curr.json"
        b.write_text(json.dumps(baseline))
        c.write_text(json.dumps(current))
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli._cmd_compare(
                type("A", (), {"baseline": str(b), "run": str(c)})())
        assert code == 1, f"真回归必须 exit 1，输出：{out.getvalue()}"
        assert "NEW-FAIL s1" in out.getvalue()

    def test_compare_reports_missing_scenarios(self, tmp_path):
        import json
        import scripts.g05_task_acceptance as cli
        baseline = {"runs": [
            {"scenario_id": "s1", "error": "",
             "checks": [{"verdict": "pass", "expected_verdict": "pass"}]},
            {"scenario_id": "s2", "error": "",
             "checks": [{"verdict": "pass", "expected_verdict": "pass"}]},
        ]}
        current = {"runs": [baseline["runs"][0]]}  # s2 未跑
        b, c = tmp_path / "b.json", tmp_path / "c.json"
        b.write_text(json.dumps(baseline))
        c.write_text(json.dumps(current))
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli._cmd_compare(
                type("A", (), {"baseline": str(b), "run": str(c)})())
        assert code == 1
        assert "MISSING  s2" in out.getvalue()


class TestRunnerErrorAttribution:
    def test_runner_error_not_governance(self):
        from .attribution import attribute_run
        from .contracts import ScenarioRunResult
        run = ScenarioRunResult(scenario_id="x", family="F00",
                                negative=False, error="timeout")
        attributed = attribute_run(run)
        assert "governance_violation" not in attributed
        assert "infra_error" in attributed


class TestTier3GuardBehavioral:
    """P3-11：tier-3 反转改真跑（篡改守卫语义 → runner 必须翻红）。"""

    @pytest.mark.asyncio
    async def test_tier3_inverted_actually_errors(
            self, g05_runner: TaskScenarioRunner):
        scenario = scenarios_by_id()["f15-tier3-governance"]
        data = scenario.model_dump(mode="json")
        for st in data["turns"][0]["steps"]:
            if st["call_id"] == "danger":
                # 反转一：expect_error=False 且不允许错误 → tier-3 若被
                # 错误放行会以"未预期成功"翻红；若仍拒绝则以"未预期失败"
                # 翻红——守卫不可绕过，二者必居其一。
                st["expect_error"] = False
                st["allow_error"] = False
        inverted = ScenarioContract.model_validate(data)
        result = await g05_runner.run(inverted)
        assert not result.ok, (
            "反转后场景仍绿 = tier-3 守卫演练无行为证据: "
            + result.summary_line())
