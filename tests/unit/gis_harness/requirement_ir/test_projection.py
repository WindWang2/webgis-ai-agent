"""F02 下游单向投影单测（D-09：IR 只写权威模块的输入面）。

覆盖：六路出口的形状与兼容性——重点用**真实下游类型**做构造期验证
（GrammarRequest fail-closed 校验、GoalRequirement 校验、MapRequestIntent
round-trip），确保 IR 与权威模块零漂移。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir import (
    PatchRecord,
    apply_patch,
    build_document,
    export_obligations,
    field_query_inputs,
    grammar_request_face,
    goal_requirements,
    intent_view,
    template_obligations,
)


def _doc(query: str = "统计成都各区小学数量，配上图例", turn: int = 1):
    doc = build_document(query, resolve_map_request_intent(query), turn=turn)
    # resolver 对一般统计话语不填 export_intents；显式补 pdf 交付义务
    doc, _ = apply_patch(doc, PatchRecord(
        op_id="seed-fmt", turn=turn, actor="user", op="set",
        path="output.formats", value=["pdf"]))
    return doc


class TestIntentView:
    def test_round_trip_preserves_core(self):
        doc = _doc("成都的小学分布情况，做一张图")
        view = intent_view(doc)
        assert view == doc.intent.core
        assert view.task == "distribution_overview"

    def test_view_reflects_patched_core(self):
        doc = _doc("成都的小学分布情况，做一张图")
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="p1", turn=2, actor="user", op="set",
            path="statistics.group_by", value="grid"))
        assert intent_view(doc).group_by == "grid"


class TestFieldQueryInputs:
    def test_shape_aligns_field_resolver_semantics(self):
        doc = _doc()
        inputs = field_query_inputs(doc)
        assert inputs, "统计语义应派生指标输入"
        for item in inputs:
            assert set(item) >= {"measure_id", "phrase", "denominator",
                                 "temporal_required", "role", "kind"}
            # 解析权威面：field 字段不在 IR 输入里（解析结果归 resolver）
            assert "field" not in item

    def test_resolver_smoke_with_projected_phrase(self):
        """IR 投影的短语可被 field_resolver 权威解析（输入面兼容冒烟）。"""
        from app.lib.gis.field_resolver import parse_measure_phrase
        doc = _doc()
        for item in field_query_inputs(doc):
            if item["phrase"]:
                query = parse_measure_phrase(item["phrase"])
                assert hasattr(query, "kind")


class TestGrammarRequestFace:
    def test_face_constructs_real_grammar_request(self):
        from app.lib.cartography.grammar_solver import GrammarRequest
        doc = _doc()
        face = grammar_request_face(doc, feature_count=120)
        request = GrammarRequest(**face)   # fail-closed 词表校验
        assert request.geometry in ("point", "multi_point", "line", "polygon", "raster")

    def test_pins_only_from_user_origin(self):
        doc = _doc()
        # rule 来源的 palette（模拟 derived）不得冒充用户 pin
        face = grammar_request_face(doc)
        assert face["pinned_palette"] is None
        # user patch → pin 透传
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="p1", turn=2, actor="user", op="set",
            path="representation.palette", value="blue"))
        face = grammar_request_face(doc)
        assert face["pinned_palette"] == "blue"

    def test_agent_palette_is_not_user_pin(self):
        doc = _doc()
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="p1", turn=2, actor="agent", op="set",
            path="representation.palette", value="blue"))
        face = grammar_request_face(doc)
        assert face["pinned_palette"] is None   # agent 推断 ≠ 用户显式 pin

    def test_purpose_vocab_fail_closed(self):
        doc = _doc()
        from pydantic import ValidationError
        from app.lib.cartography.grammar_solver import GrammarRequest
        face = grammar_request_face(doc)
        face["purpose"] = "made_up_purpose"
        with pytest.raises(ValidationError):
            GrammarRequest(**face)


class TestTemplateObligations:
    def test_obligation_shape(self):
        doc = _doc()
        obligations = template_obligations(doc)
        assert obligations["task"] == doc.intent.task.task_type
        assert obligations["scope"] == {"name": doc.intent.aoi.name,
                                        "level": doc.intent.aoi.level}
        assert set(obligations) >= {"task", "task_kind", "subject", "scope",
                                    "purpose", "audience", "stages",
                                    "required_components", "group_by", "formats"}


class TestExportObligations:
    def test_formats_projected(self):
        doc = _doc()
        obligations = export_obligations(doc)
        formats = {o["format"] for o in obligations}
        assert "pdf" in formats

    def test_live_map_fallback(self):
        doc = _doc("成都的小学分布情况，做一张图")
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="clear-fmt", turn=2, actor="user", op="set",
            path="output.formats", value=[]))
        obligations = export_obligations(doc)
        assert any(o["format"] == "live_map" for o in obligations)


class TestGoalRequirements:
    def test_constructs_real_goal_requirements(self):
        from app.services.gis_harness.goal_satisfaction.contracts import (
            GoalRequirement,
        )
        doc = _doc()
        items = goal_requirements(doc)
        assert items
        for item in items:
            assert isinstance(item, GoalRequirement)
            assert item.source.startswith("requirement_ir:")
            assert item.id.startswith("req-")

    def test_user_lock_pins_requirement(self):
        doc = _doc()
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="p1", turn=2, actor="user", op="set",
            path="output.formats", value=["pdf"]))
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="p2", turn=3, actor="user", op="add_lock",
            value={"scope": "export_format", "value": "pdf"}))
        pinned = [r for r in goal_requirements(doc) if r.pinned]
        assert any(r.export_format == "pdf" for r in pinned)
