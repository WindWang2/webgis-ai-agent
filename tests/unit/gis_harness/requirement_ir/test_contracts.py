"""F02 Requirement IR 契约单测（ADR-0215）。

覆盖：schema 版本化、round-trip 序列化、extra=forbid 边界、有界性、
Provenance ownership 语义、Ambiguity/PatchRecord 形状。
全部离线、零 LLM、确定性。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir import (
    REQUIREMENT_DOCUMENT_SCHEMA,
    Ambiguity,
    GISIntentSpec,
    PatchRecord,
    Provenance,
    RequirementDocument,
    UserLock,
    build_document,
)
from app.services.gis_harness.requirement_ir.contracts import (
    MAX_AMBIGUITIES,
    MAX_LOCKS,
    MAX_MEASURES,
    MAX_PATCHES,
)


def _core(query: str = "统计成都各区小学数量"):
    return resolve_map_request_intent(query)


def _doc(query: str = "统计成都各区小学数量", turn: int = 1) -> RequirementDocument:
    return build_document(query, _core(query), turn=turn)


class TestSchemaVersioning:
    def test_schema_tag(self):
        assert _doc().schema_version == REQUIREMENT_DOCUMENT_SCHEMA
        assert REQUIREMENT_DOCUMENT_SCHEMA == "requirement_document.v1"

    def test_round_trip(self):
        doc = _doc()
        dumped = doc.model_dump()
        restored = RequirementDocument.model_validate(dumped)
        assert restored == doc

    def test_extra_forbid_document(self):
        payload = _doc().model_dump()
        payload["unknown_field"] = 1
        with pytest.raises(ValidationError):
            RequirementDocument.model_validate(payload)

    def test_extra_forbid_provenance(self):
        with pytest.raises(ValidationError):
            Provenance(origin="rule", unknown="x")

    def test_extra_forbid_lock(self):
        with pytest.raises(ValidationError):
            UserLock(scope="palette", value="blue", hacker=True)


class TestProvenance:
    def test_user_ownership(self):
        assert Provenance(origin="user").is_user() is True
        for origin in ("rule", "llm", "ontology", "memory", "service", "default"):
            assert Provenance(origin=origin).is_user() is False  # type: ignore[arg-type]

    def test_origin_vocab_closed(self):
        with pytest.raises(ValidationError):
            Provenance(origin="nobody")

    def test_turn_bounded(self):
        with pytest.raises(ValidationError):
            Provenance(origin="user", turn=-1)


class TestSections:
    def test_gis_intent_spec_embeds_core(self):
        doc = _doc("统计成都各区小学数量")
        assert doc.intent.core.task == "administrative_statistic"
        assert doc.intent.aoi.name == "成都"
        assert doc.intent.statistics.group_by == "district"

    def test_measure_bound(self):
        from app.services.gis_harness.requirement_ir.contracts import MeasureSpec
        with pytest.raises(ValidationError):
            GISIntentSpec(
                core=_core(),
                measures=[MeasureSpec(id=f"m{i}", statistic="count")
                          for i in range(MAX_MEASURES + 1)],
            )

    def test_ambiguity_context_key_required_shape(self):
        ambiguity = Ambiguity(code="aoi_unresolved", path="aoi.name", context_key="k1")
        assert ambiguity.state == "open"
        assert Ambiguity.model_validate(ambiguity.model_dump()) == ambiguity

    def test_patch_record_value_accepts_scalars(self):
        record = PatchRecord(
            op_id="p1", turn=1, actor="user", op="set",
            path="representation.palette", value="blue")
        assert PatchRecord.model_validate(record.model_dump()).value == "blue"

    def test_patch_op_whitelist(self):
        with pytest.raises(ValidationError):
            PatchRecord(op_id="p1", turn=1, actor="user", op="hack", path="x")


class TestBounds:
    def test_locks_bound(self):
        with pytest.raises(ValidationError):
            GISIntentSpec(
                core=_core(),
                locks=[UserLock(scope="other", value=str(i))
                       for i in range(MAX_LOCKS + 1)],
            )

    def test_ambiguities_bound(self):
        with pytest.raises(ValidationError):
            GISIntentSpec(
                core=_core(),
                ambiguities=[
                    Ambiguity(code="c", context_key=str(i))
                    for i in range(MAX_AMBIGUITIES + 1)
                ])

    def test_patches_bound(self):
        with pytest.raises(ValidationError):
            RequirementDocument(
                document_id="d1",
                intent=GISIntentSpec(core=_core()),
                patches=[
                    PatchRecord(op_id=str(i), turn=1, actor="user", op="accept")
                    for i in range(MAX_PATCHES + 1)
                ])


class TestMapRequirementSpec:
    def test_items_aligned_with_goal_vocab(self):
        doc = _doc("统计成都各区小学数量")
        kinds = {item.kind for item in doc.requirements.items}
        assert kinds <= {"map", "analysis", "comparison", "statistics",
                         "chart", "export"}
        assert "statistics" in kinds
