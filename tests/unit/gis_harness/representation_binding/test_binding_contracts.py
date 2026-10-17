"""ADR-0219 C2：RepresentationBinding 契约 —— 有界/确定性/序列化/fail-closed。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.gis_harness.representation_binding.contracts import (
    BINDING_SCHEMA,
    BINDING_TARGET_GRAMMAR_FACE,
    BindingStatus,
    RepresentationBinding,
    binding_fingerprint,
)


def _binding(**overrides) -> RepresentationBinding:
    payload = dict(
        binding_id="rb-abc123",
        source_requirement_path="representation.hidden_layer_ids",
        target_kind="PatchLayerPresentationIntent",
        payload={"layer_id": "roads", "visible": False},
        reason="requirement: 用户隐藏 roads",
        binding_fingerprint=binding_fingerprint({
            "doc": "d1", "path": "representation.hidden_layer_ids",
            "target": "PatchLayerPresentationIntent",
            "payload": {"layer_id": "roads", "visible": False}}),
    )
    payload.update(overrides)
    return RepresentationBinding(**payload)


class TestContract:
    def test_schema_version_default(self):
        assert _binding().schema_version == BINDING_SCHEMA == "representation_binding.v1"

    def test_fingerprint_deterministic_and_key_order_insensitive(self):
        left = binding_fingerprint({"a": 1, "b": {"x": 1, "y": 2}})
        right = binding_fingerprint({"b": {"y": 2, "x": 1}, "a": 1})
        assert left == right and len(left) == 16
        assert binding_fingerprint({"a": 2}) != left

    def test_engine_mutation_id_derivation(self):
        binding = _binding()
        assert binding.engine_mutation_id() == f"rb:{binding.binding_fingerprint}"
        assert len(binding.engine_mutation_id()) <= 128

    def test_with_status_is_copy(self):
        binding = _binding()
        terminal = binding.with_status(
            BindingStatus.APPLIED, mutation_id="m1", mutation_revision=3)
        assert binding.status == BindingStatus.PROPOSED
        assert terminal.status == BindingStatus.APPLIED
        assert terminal.mutation_revision == 3
        assert binding.mutation_revision is None

    def test_extra_forbid(self):
        with pytest.raises(ValidationError):
            _binding(bogus_field="x")

    def test_target_kind_closed_vocabulary(self):
        with pytest.raises(ValidationError):
            _binding(target_kind="NotAnIntent")

    def test_reason_bounded(self):
        with pytest.raises(ValidationError):
            _binding(reason="x" * 201)

    def test_serialization_roundtrip(self):
        binding = _binding().with_status(
            BindingStatus.REFUSED, error_code="binding_target_locked",
            disclosures=["locked"])
        restored = RepresentationBinding.model_validate(binding.to_dict())
        assert restored == binding

    def test_grammar_face_target_allowed(self):
        binding = _binding(
            target_kind=BINDING_TARGET_GRAMMAR_FACE,
            source_requirement_path="representation.palette",
            payload={"pinned_palette": "Blues"})
        assert binding.no_reanalysis is True
        assert binding.lock_semantics == "respect_existing"

    def test_status_vocabulary_closed(self):
        assert {s.value for s in BindingStatus} == {
            "proposed", "applied", "refused", "superseded", "ineligible", "skipped"}
