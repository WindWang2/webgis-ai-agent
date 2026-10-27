"""ADR-0219 C1：mutation taxonomy —— 表覆盖/动态规则/一致性 gate。"""
from __future__ import annotations

import pytest

from app.services.gis_harness.representation_binding.taxonomy import (
    MutationTaxonomy,
    assert_taxonomy_covers_registry,
    classify_mutation,
    registered_taxonomy_kinds,
)


class TestTaxonomyTable:
    def test_registry_consistency_gate(self):
        # fail-closed：registry 每个 kind 必须有显式条目（新增 intent 未登记
        # 分类 → raise，绝不静默漏分）。gate 通过时返回 None——显式断言
        # 「全量 kind 已登记且无异常抛出」这一被测契约本身。
        assert assert_taxonomy_covers_registry() is None

    def test_all_registered_kinds_covered(self):
        from app.services.mapspec.mutation_registry import MUTATION_REGISTRY

        registry_kinds = {cls.__name__ for cls in MUTATION_REGISTRY.intent_classes()}
        assert registry_kinds <= set(registered_taxonomy_kinds())

    def test_requirement_significant_kinds(self):
        for kind in (
            "PatchLayerPresentationIntent", "PatchLayerStyleIntent",
            "PatchComponentIntent", "RemoveComponentIntent",
            "DuplicateComponentIntent", "RebindComponentIntent",
            "SetLayoutIntent", "SetWorkbenchStateIntent",
            "PatchWorkbenchDeltaIntent", "RemoveLayerIntent",
        ):
            taxonomy = classify_mutation(kind, "user")
            assert taxonomy.taxonomy == "requirement_significant", kind

    def test_presentation_ephemeral_kinds(self):
        assert classify_mutation("SetViewIntent", "user").taxonomy == "presentation_ephemeral"
        # opacity-only 显隐调整：持久但不跨轮 → ephemeral（不投影）
        taxonomy = classify_mutation(
            "PatchLayerPresentationIntent", "user", visible_only_opacity=True)
        assert taxonomy.taxonomy == "presentation_ephemeral"
        assert taxonomy.reason_code == "taxonomy_opacity_only"
        # visible 参与时升级回 significant
        assert classify_mutation(
            "PatchLayerPresentationIntent", "user").taxonomy == "requirement_significant"

    def test_system_repair_classification(self):
        for actor in ("runtime_repair", "map_finalizer", "finalize_display"):
            taxonomy = classify_mutation("PatchLayerPresentationIntent", "agent", actor=actor)
            assert taxonomy.taxonomy == "system_repair", actor
        assert classify_mutation(
            "PatchLayerStyleIntent", "system").taxonomy == "system_repair"
        # producer 档同样归 repair（模板/自动回填/系统默认）
        assert classify_mutation(
            "PatchComponentIntent", "agent",
            producer_class="REPAIR_AUTOFILL").taxonomy == "system_repair"
        assert classify_mutation(
            "PatchComponentIntent", "agent",
            producer_class="TEMPLATE").taxonomy == "system_repair"

    def test_data_science_kinds(self):
        for kind in ("UpsertSourceIntent", "UpsertLayerIntent", "SetTimeIntent", "SetBasemapIntent"):
            assert classify_mutation(kind, "agent").taxonomy == "data_science", kind

    def test_unbound_kinds(self):
        # G3D04：SetSceneIntent 从 unbound 升为 requirement_significant
        # （scene_mode）——用户 2D↔3D 切换跨轮投影回 requirement.scene。
        for kind in ("InitProjectIntent", "CheckpointIntent", "SetScenarioModeIntent",
                     "ReorderLayersIntent"):
            assert classify_mutation(kind, "user").taxonomy == "unbound", kind

    def test_scene_intent_user_origin_projects(self):
        taxonomy = classify_mutation("SetSceneIntent", "user")
        assert taxonomy.taxonomy == "requirement_significant"
        assert taxonomy.dimension == "scene_mode"
        assert taxonomy.projects_to_requirement is True

    def test_scene_intent_agent_origin_does_not_project(self):
        taxonomy = classify_mutation("SetSceneIntent", "agent")
        assert taxonomy.taxonomy == "requirement_significant"
        assert taxonomy.projects_to_requirement is False

    def test_unknown_kind_fail_closed(self):
        taxonomy = classify_mutation("NotARealIntent", "user")
        assert taxonomy.taxonomy == "unbound"
        assert taxonomy.reason_code == "taxonomy_unknown_kind"
        assert not taxonomy.projects_to_requirement

    def test_undo_rebaseline_only_for_user(self):
        assert classify_mutation("RollbackIntent", "user").dimension == "undo_rebaseline"
        assert classify_mutation(
            "RollbackIntent", "user").projects_to_requirement is True
        # agent/system 的回滚是组织面，不触发重定基
        agent_rollback = classify_mutation("RollbackIntent", "agent")
        assert agent_rollback.taxonomy == "unbound"
        assert agent_rollback.reason_code == "taxonomy_undo_not_user"
        assert classify_mutation("RestoreStyleIntent", "user").dimension == "undo_rebaseline"


class TestProjectionGate:
    """projects_to_requirement 门控：仅 user origin + 显著面/undo。"""

    def test_only_user_origin_projects(self):
        assert classify_mutation(
            "PatchLayerPresentationIntent", "user").projects_to_requirement
        assert not classify_mutation(
            "PatchLayerPresentationIntent", "agent").projects_to_requirement
        assert not classify_mutation(
            "PatchLayerPresentationIntent", "system").projects_to_requirement

    def test_repair_never_projects_as_user_preference(self):
        taxonomy = classify_mutation(
            "PatchLayerPresentationIntent", "agent", actor="runtime_repair")
        assert taxonomy.taxonomy == "system_repair"
        assert not taxonomy.projects_to_requirement

    def test_value_object_frozen(self):
        taxonomy = MutationTaxonomy(
            kind="k", origin="user", taxonomy="unbound", dimension="none")
        with pytest.raises(Exception):
            taxonomy.kind = "other"  # type: ignore[misc]

    def test_to_dict_roundtrip_fields(self):
        taxonomy = classify_mutation("SetViewIntent", "user")
        payload = taxonomy.to_dict()
        assert payload["taxonomy"] == "presentation_ephemeral"
        assert set(payload) == {"kind", "origin", "taxonomy", "dimension", "reason_code"}
