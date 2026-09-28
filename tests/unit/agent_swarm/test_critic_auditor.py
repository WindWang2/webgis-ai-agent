"""CriticAuditorAgent 审计裁判红线（ADR-0189 D2）。

fail-closed 制度：审计对象不可达 / 证据不全 / 语义检查未过 → 绝不入
pass。一票否决为四条红线（V1 图例未闭环 / V2 必配组件缺失 / V3 指标
口径未对齐 / V4 数据空洞）另设 V0（审计对象不可达），各带不可抵赖
因果链；``goal_score`` 是派生口径（携带 derivation 披露，不是第二
verdict）。
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.services.agent_swarm.base import SpecialistTimeoutError
from app.services.agent_swarm.contracts import (
    MapSpecDeliveryRef,
)
from app.services.agent_swarm.specialists.auditor import (
    CriticAuditorAgent,
    _has_data_driven_paint,
)
from app.services.agent_swarm.specialists.cartographer import CartographerAgent
from app.services.agent_swarm.specialists.ledger import ArtifactLedger


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _square(lng: float, lat: float) -> List[List[float]]:
    d = 0.02
    return [
        [lng, lat], [lng + d, lat], [lng + d, lat + d], [lng, lat + d], [lng, lat],
    ]


def _geojson(values: List[float], field: str = "schools") -> Dict[str, Any]:
    features = []
    for i, v in enumerate(values):
        features.append({
            "type": "Feature",
            "properties": {field: v, "district": f"d{i}"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [_square(104.0 + i * 0.05, 30.5 + i * 0.05)],
            },
        })
    return {"type": "FeatureCollection", "features": features}


_MODERATE = [10, 14, 18, 25, 30, 40, 52, 68, 90, 120]


def _delivery_with_ledger(**overrides: Any):
    ledger = ArtifactLedger()
    agent = CartographerAgent(ledger=ledger)
    request = {
        "geojson": _geojson(_MODERATE),
        "field": "schools",
        "title": "成都各区学校数量分布",
        "purpose": "screen_16_9",
        "source_id": "districts",
    }
    request.update(overrides)
    return agent.compose(request), ledger


class _FakeRequirement:
    def __init__(self, requirement_id: str, verdict: str) -> None:
        self.requirement_id = requirement_id
        self.verdict = type("V", (), {"value": verdict})()


class _FakeGoalReport:
    def __init__(self, verdict: str, requirements: List[_FakeRequirement]) -> None:
        self.verdict = type("V", (), {"value": verdict})()
        self.requirements = requirements


def _fake_evaluator(requirements: List[_FakeRequirement], verdict: str = "passed"):
    def _evaluate(chapter, *, user_goal="", map_product=None, cartographic_review=None):
        return _FakeGoalReport(verdict, requirements)

    return _evaluate


def _map_chapter(**overrides: Any) -> Dict[str, Any]:
    ch = {
        "query": "成都各区学校数量分布专题图",
        "intent": {
            "query": "成都各区学校数量分布专题图",
            "task": "distribution_overview",
            "scope": {"name": "成都", "level": "city"},
            "output_intents": ["map"],
        },
    }
    ch.update(overrides)
    return ch


def _contract_chapter(
    requirements: List[Dict[str, Any]], *, threshold: float = 1.0
) -> Dict[str, Any]:
    """显式 goal_contract（resolve_goal_contract 优先采信）——required_ids 确定。"""
    return _map_chapter(
        goal_contract={
            "goal_id": "goal-test",
            "summary": "测试目标契约",
            "requirements": requirements,
            "success_threshold": threshold,
        }
    )


_REQ_MAP: Dict[str, Any] = {"id": "req.map", "kind": "map", "required": True, "polarity": "must"}
_REQ_EXPORT: Dict[str, Any] = {
    "id": "req.export.png",
    "kind": "export",
    "required": True,
    "polarity": "must",
    "export_format": "png",
}


class TestV0UnreachableTarget:
    """V0：审计对象不可达 —— 绝不基于叙述放行。"""

    def test_missing_ledger_vetoes(self):
        auditor = CriticAuditorAgent()
        delivery = MapSpecDeliveryRef(ref_id="ref:mapspec-000001")
        report = auditor.audit(delivery)
        assert report.verdict == "fail"
        veto_ids = [v["veto_id"] for v in report.vetoes]
        assert "V0" in veto_ids
        veto = next(v for v in report.vetoes if v["veto_id"] == "V0")
        assert veto["rule_id"] == "audit.target_unreachable"
        assert veto["evidence"]["ref_id"] == "ref:mapspec-000001"
        assert veto["suggested_fix"] is None
        assert any("[V0]" in n for n in report.improvement_notes)

    def test_ref_not_in_ledger_vetoes(self):
        ledger = ArtifactLedger()
        auditor = CriticAuditorAgent()
        delivery = MapSpecDeliveryRef(ref_id="ref:mapspec-999999")
        report = auditor.audit(delivery, ledger=ledger)
        assert report.verdict == "fail"
        assert [v["veto_id"] for v in report.vetoes] == ["V0"]

    def test_v0_report_carries_fingerprint(self):
        auditor = CriticAuditorAgent()
        delivery = MapSpecDeliveryRef(
            ref_id="ref:mapspec-000001", mapspec_fingerprint="fp-abc"
        )
        report = auditor.audit(delivery)
        assert report.audited_fingerprint == "fp-abc"
        assert report.audited_ref_id == "ref:mapspec-000001"

    def test_v0_short_circuits_goal_evaluation(self):
        """不可达时不调用评估面（无证据不评估）。"""
        calls: List[str] = []

        def _evaluator(chapter, **kw):
            calls.append("called")
            raise AssertionError("V0 不应触发评估")

        auditor = CriticAuditorAgent(evaluator=_evaluator)
        report = auditor.audit(
            MapSpecDeliveryRef(ref_id="ref:mapspec-000001"),
            chapter=_contract_chapter([_REQ_MAP]),
        )
        assert report.verdict == "fail"
        assert calls == []
        assert report.goal_score is None
        assert report.goal_score_derivation == ""


class TestCleanDelivery:
    """干净交付（无 goal 契约）→ 纯制图审计。"""

    def test_clean_delivery_passes(self):
        delivery, ledger = _delivery_with_ledger()
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert report.verdict == "pass"
        assert report.vetoes == []
        assert report.review_status in ("passed", "passed_with_warnings")

    def test_chapter_without_contract_discloses_pure_carto_audit(self):
        delivery, ledger = _delivery_with_ledger()
        # 无可派生 GIS 目标契约的 chapter（无 output_intents）→ goal 面
        # 不设门槛，纯制图审计（披露而非阻断）
        report = CriticAuditorAgent().audit(
            delivery, ledger=ledger, chapter={"query": "随便问问"}
        )
        assert any("纯制图审计" in n for n in report.improvement_notes)

    def test_report_shape_bounded(self):
        delivery, ledger = _delivery_with_ledger()
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert report.version == "1.0"
        assert len(report.improvement_notes) <= 8
        assert len(report.cartography_risks) <= 12
        assert len(report.vetoes) <= 8
        assert report.round_index == 0

    def test_round_index_propagated(self):
        delivery, ledger = _delivery_with_ledger()
        report = CriticAuditorAgent().audit(delivery, ledger=ledger, round_index=2)
        assert report.round_index == 2


class TestV1LegendVeto:
    """V1：可见专题层无 legend_spec（读者无法解释分级）。"""

    def test_thematic_layer_without_legend_spec_vetoed(self):
        delivery, ledger = _delivery_with_ledger()
        payload = ledger.get(delivery.ref_id)
        layer = next(
            layer for layer in payload["layers"] if layer.get("legend_spec")
        )
        del layer["legend_spec"]  # 制造缺陷：数据驱动 paint 但无图例
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert report.verdict == "fail"
        v1 = [v for v in report.vetoes if v["veto_id"] == "V1"]
        assert v1, "数据驱动专题层缺 legend_spec 必须一票否决"
        veto = v1[0]
        assert veto["rule_id"] == "legend.thematic_layer_without_legend_spec"
        assert veto["evidence"]["layers"]
        assert veto["suggested_fix"]["operation"] == "attach_legend_spec"

    def test_raster_layer_without_legend_not_vetoed(self):
        delivery, ledger = _delivery_with_ledger()
        payload = ledger.get(delivery.ref_id)
        layer = next(
            layer for layer in payload["layers"] if layer.get("legend_spec")
        )
        del layer["legend_spec"]
        layer["type"] = "raster"  # 栅格层免图例要求
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert "V1" not in [v["veto_id"] for v in report.vetoes]

    def test_invisible_layer_not_vetoed(self):
        delivery, ledger = _delivery_with_ledger()
        payload = ledger.get(delivery.ref_id)
        layer = next(
            layer for layer in payload["layers"] if layer.get("legend_spec")
        )
        del layer["legend_spec"]
        layer["visible"] = False  # 不可见层不构成误导
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert "V1" not in [v["veto_id"] for v in report.vetoes]


class TestV2ComponentsVeto:
    """V2：必配组件缺失（制图学基线）。"""

    def test_missing_components_vetoed_with_baseline(self):
        delivery, ledger = _delivery_with_ledger()
        payload = ledger.get(delivery.ref_id)
        payload["layout"]["components"] = []  # 清空必配组件
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        v2 = [v for v in report.vetoes if v["veto_id"] == "V2"]
        assert v2, "必配组件缺失必须一票否决"
        veto = v2[0]
        assert veto["rule_id"] == "components.required_missing"
        assert veto["evidence"]["purpose"] == "screen_16_9"
        assert veto["suggested_fix"]["operation"] == "attach_components"


class TestGoalScoreDerivation:
    """V3 / goal_score 派生口径（counts 派生，非第二 verdict）。"""

    def test_uncovered_requirements_veto_with_derivation(self):
        delivery, ledger = _delivery_with_ledger()
        auditor = CriticAuditorAgent(
            evaluator=_fake_evaluator(
                [
                    _FakeRequirement("req.map", "fulfilled"),
                    _FakeRequirement("req.export.png", "unfulfilled"),
                ]
            )
        )
        report = auditor.audit(
            delivery,
            ledger=ledger,
            chapter=_contract_chapter([_REQ_MAP, _REQ_EXPORT]),
            user_goal="出图",
        )
        assert report.verdict == "fail"
        assert report.goal_score == pytest.approx(0.5)
        assert "fulfilled 1/2 required" in report.goal_score_derivation
        assert "req.export.png" in report.uncovered_requirements
        v3 = [v for v in report.vetoes if v["veto_id"] == "V3"]
        assert any(v["rule_id"] == "goal.requirements_uncovered" for v in v3)

    def test_all_fulfilled_passes_with_full_score(self):
        delivery, ledger = _delivery_with_ledger()
        auditor = CriticAuditorAgent(
            evaluator=_fake_evaluator(
                [
                    _FakeRequirement("req.map", "fulfilled"),
                    _FakeRequirement("req.export.png", "fulfilled"),
                ]
            )
        )
        report = auditor.audit(
            delivery,
            ledger=ledger,
            chapter=_contract_chapter([_REQ_MAP, _REQ_EXPORT]),
            user_goal="出图",
        )
        assert report.goal_score == pytest.approx(1.0)
        assert report.goal_score_derivation != ""
        assert "V3" not in [v["veto_id"] for v in report.vetoes]

    def test_blocked_goal_verdict_never_passes(self):
        delivery, ledger = _delivery_with_ledger()
        auditor = CriticAuditorAgent(
            evaluator=_fake_evaluator(
                [_FakeRequirement("req.map", "fulfilled")], verdict="blocked"
            )
        )
        report = auditor.audit(
            delivery,
            ledger=ledger,
            chapter=_contract_chapter([_REQ_MAP]),
            user_goal="出图",
        )
        assert report.verdict == "fail"

    def test_evaluator_exception_is_fail_closed(self):
        delivery, ledger = _delivery_with_ledger()

        def _boom(chapter, **kw):
            raise RuntimeError("evaluator crashed")

        auditor = CriticAuditorAgent(evaluator=_boom)
        report = auditor.audit(
            delivery,
            ledger=ledger,
            chapter=_contract_chapter([_REQ_MAP]),
            user_goal="出图",
        )
        # 评估面异常 → 无证据 → 不入 pass
        assert report.verdict != "pass"
        assert report.goal_score is None

    def test_score_below_threshold_vetoes(self):
        delivery, ledger = _delivery_with_ledger()
        auditor = CriticAuditorAgent(
            evaluator=_fake_evaluator(
                [
                    _FakeRequirement("req.map", "fulfilled"),
                    _FakeRequirement("req.export.png", "unfulfilled"),
                ]
            )
        )
        report = auditor.audit(
            delivery,
            ledger=ledger,
            chapter=_contract_chapter([_REQ_MAP, _REQ_EXPORT], threshold=0.5),
            user_goal="出图",
        )
        # score=0.5 达阈值 → 无 score_below_threshold veto（但 uncovered 仍否决）
        v3_rules = {
            v["rule_id"] for v in report.vetoes if v["veto_id"] == "V3"
        }
        assert "goal.requirements_uncovered" in v3_rules
        assert "goal.score_below_threshold" not in v3_rules

    def test_derivation_empty_when_no_score(self):
        delivery, ledger = _delivery_with_ledger()
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert report.goal_score is None
        assert report.goal_score_derivation == ""


class TestFailClosedVerdicts:
    """证据不全 / 语义检查未过 → fail-closed。"""

    def test_not_evaluated_review_status_never_passes(self):
        delivery, ledger = _delivery_with_ledger()
        payload = ledger.get(delivery.ref_id)
        # 摘掉 source profile：语义检查无评估面 → not_evaluated
        payload["sources"]["districts"].pop("profile", None)
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert report.verdict == "not_evaluated"
        assert any("not_evaluated" in n for n in report.improvement_notes)

    def test_vetoes_force_fail_regardless_of_review(self):
        delivery, ledger = _delivery_with_ledger()
        payload = ledger.get(delivery.ref_id)
        payload["layout"]["components"] = []
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert report.verdict == "fail"
        assert report.vetoes


class TestVetoChain:
    """一票否决的因果链不可抵赖。"""

    def test_veto_carries_rule_fingerprint_evidence_fix(self):
        delivery, ledger = _delivery_with_ledger()
        payload = ledger.get(delivery.ref_id)
        payload["layout"]["components"] = []
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        veto = next(v for v in report.vetoes if v["veto_id"] == "V2")
        assert veto["severity"] == "error"
        assert veto["message"]
        # veto 链携带实际审计载荷的指纹（不等同于交付券声称的指纹 ——
        # report.audited_fingerprint 是代际锁定的声称值，载荷被改后两者有别）
        assert veto["audited_fingerprint"] == cartographic_fingerprint(payload)
        assert veto["audited_fingerprint"] != report.audited_fingerprint
        assert isinstance(veto["evidence"], dict)
        assert veto["suggested_fix"] is not None

    def test_claimed_and_actual_fingerprint_agree_on_untampered_delivery(self):
        delivery, ledger = _delivery_with_ledger()
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert report.audited_fingerprint == delivery.mapspec_fingerprint

    def test_fingerprint_matches_audited_payload(self):
        delivery, ledger = _delivery_with_ledger()
        payload = ledger.get(delivery.ref_id)
        report = CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert report.audited_fingerprint == cartographic_fingerprint(payload)


class TestDataDrivenPaintHelper:
    """paint 数据驱动方法识别（step/interpolate/match）。"""

    def test_step_method_detected(self):
        assert _has_data_driven_paint(
            {"paint": {"fill-color": {"method": "step", "stops": []}}}
        )

    def test_interpolate_detected(self):
        assert _has_data_driven_paint(
            {"paint": {"circle-radius": {"method": "interpolate"}}}
        )

    def test_constant_paint_not_detected(self):
        assert not _has_data_driven_paint({"paint": {"fill-color": "#ff0000"}})

    def test_missing_paint_not_detected(self):
        assert not _has_data_driven_paint({})


class TestAuditorIdentity:
    def test_name_and_role(self):
        assert CriticAuditorAgent.name == "critic_auditor"
        assert CriticAuditorAgent.role_name == "audit_judge"

    def test_allowlist_is_readonly_audit_surface(self):
        allowlist = CriticAuditorAgent.TOOL_ALLOWLIST
        assert allowlist == frozenset({"audit_spatial_quality", "gis_skill_replay_check"})

    def test_default_deadline_from_light_role(self):
        assert CriticAuditorAgent()._deadline_s == 120.0  # audit_judge light 档

    def test_deadline_breach_raises(self):
        """时钟在心跳之间快进（僵死面）→ audit 入口 check_deadline typed 熔断。"""

        class _JumpingClock(FakeClock):
            def __call__(self) -> float:
                self.now += 10.0
                return self.now

        auditor = CriticAuditorAgent(clock=_JumpingClock(), deadline_s=5.0)
        delivery, ledger = _delivery_with_ledger()
        with pytest.raises(SpecialistTimeoutError):
            auditor.audit(delivery, ledger=ledger)

    def test_audit_is_sync_and_readonly(self):
        """audit 不落笔：账本内容在审计前后一致。"""
        import copy

        delivery, ledger = _delivery_with_ledger()
        before = copy.deepcopy(ledger.get(delivery.ref_id))
        CriticAuditorAgent().audit(delivery, ledger=ledger)
        assert ledger.get(delivery.ref_id) == before
