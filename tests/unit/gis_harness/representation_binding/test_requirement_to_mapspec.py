"""ADR-0219 C3：requirement → MapSpec 绑定 —— 提案构造（纯函数面）。

apply_binding 的事务面（CAS/幂等/user-wins）在
tests/cartography/test_representation_binding_scenarios.py 以真引擎覆盖。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.requirement_ir import service as req_service
from app.services.gis_harness.requirement_ir.contracts import RequirementDocument
from app.services.gis_harness.representation_binding.contracts import (
    BINDING_TARGET_GRAMMAR_FACE,
    BindingStatus,
)
from app.services.gis_harness.representation_binding.requirement_to_mapspec import (
    build_binding_proposals,
    grammar_face_pins,
)


class MemStore:
    def __init__(self):
        self.data: dict = {}

    async def get_map_state(self, session_id):
        return self.data.get(session_id, {})

    async def set_map_state(self, session_id, key, value, seq=None):
        self.data.setdefault(session_id, {})[key] = value
        return True


def run(coro):
    import asyncio
    return asyncio.run(coro)


def _mapspect(layers=None, workbench_locks=None) -> dict:
    spec = {"layers": layers or []}
    if workbench_locks is not None:
        spec["workbench"] = {"lockedLayerIds": workbench_locks}
    return spec


def _layer(layer_id: str) -> dict:
    return {"id": layer_id, "source": f"{layer_id}-src", "type": "circle"}


@pytest.fixture
def doc():
    store = MemStore()
    run(req_service.ensure_document(store, "s1", "成都小学分布图", turn=1))
    return RequirementDocument.model_validate(
        store.data["s1"][req_service.REQUIREMENT_STATE_KEY]["document"])


def _set_user(store_path, doc, path, value, op_id):
    """以 user actor 写一个 representation 字段（复用 patch 协议）。"""
    patch = req_service.TurnPatch(
        op="set", path=path, value=value, actor="user",
        op_id=op_id, reason="test", turn=2)
    from app.services.gis_harness.requirement_ir.patch import apply_patch
    from app.services.gis_harness.requirement_ir.contracts import PatchRecord
    new_doc, applied = apply_patch(doc, PatchRecord(
        op_id=patch.op_id, turn=patch.turn, actor="user", op=patch.op,
        path=patch.path, value=patch.value, reason=patch.reason))
    assert applied
    return new_doc


class TestBuildProposals:
    def test_no_user_choices_no_proposals(self, doc):
        assert build_binding_proposals(doc, _mapspect([_layer("roads")])) == []

    def test_hidden_layers_expand_family(self, doc):
        doc2 = _set_user(None, doc, "representation.hidden_layer_ids", ["roads"], "op-h1")
        mapspec = _mapspect([_layer("roads"), _layer("roads-1"), _layer("parks")])
        proposals = build_binding_proposals(doc2, mapspec, expected_revision=7)
        targets = [p.payload["layer_id"] for p in proposals
                   if p.target_kind == "PatchLayerPresentationIntent"]
        # 族语义：roads + roads-1 都隐藏；parks 不动
        assert sorted(targets) == ["roads", "roads-1"]
        for proposal in proposals:
            assert proposal.expected_mapspec_revision == 7
            assert proposal.requirement_revision == doc2.revision
            assert proposal.no_reanalysis is True

    def test_absent_layer_skips_disclosed(self, doc):
        doc2 = _set_user(None, doc, "representation.hidden_layer_ids", ["ghost"], "op-h2")
        proposals = build_binding_proposals(doc2, _mapspect([_layer("roads")]))
        assert len(proposals) == 1
        assert proposals[0].status == BindingStatus.SKIPPED
        assert proposals[0].error_code == "binding_target_absent"

    def test_locked_layers_single_delta_binding(self, doc):
        doc2 = _set_user(None, doc, "representation.locked_layer_ids", ["b", "a"], "op-l1")
        proposals = [
            p for p in build_binding_proposals(doc2, _mapspect([_layer("a")]))
            if p.target_kind == "PatchWorkbenchDeltaIntent"]
        assert len(proposals) == 1
        assert proposals[0].payload["locks_add"] == ["a", "b"]
        assert proposals[0].lock_semantics == "project_lock"

    def test_palette_is_grammar_face_not_mutation(self, doc):
        doc2 = _set_user(None, doc, "representation.palette", "Blues", "op-p1")
        proposals = build_binding_proposals(doc2, None)
        assert [p.target_kind for p in proposals] == [BINDING_TARGET_GRAMMAR_FACE]
        assert proposals[0].payload["pinned_palette"] == "blue"

    def test_unknown_geometry_kind_ineligible(self, doc):
        doc2 = _set_user(None, doc, "representation.geometry_kind", "hologram", "op-g1")
        proposals = build_binding_proposals(doc2, None)
        assert proposals[0].status == BindingStatus.INELIGIBLE
        assert proposals[0].error_code == "binding_ineligible_representation"

    def test_known_geometry_kind_is_grammar_pin(self, doc):
        doc2 = _set_user(None, doc, "representation.geometry_kind", "热力图", "op-g2")
        proposals = build_binding_proposals(doc2, None)
        assert proposals[0].target_kind == BINDING_TARGET_GRAMMAR_FACE
        assert proposals[0].status == BindingStatus.PROPOSED
        assert proposals[0].payload["pinned_representation"] == "visual_heatmap"

    def test_agent_origin_fields_do_not_bind(self, doc):
        # rule/llm 推断的隐藏集（非 user origin）绝不生成绑定（不冒充用户选择）
        from app.services.gis_harness.requirement_ir.patch import apply_patch
        from app.services.gis_harness.requirement_ir.contracts import PatchRecord
        new_doc, applied = apply_patch(doc, PatchRecord(
            op_id="op-agent", turn=2, actor="agent", op="set",
            path="representation.hidden_layer_ids", value=["roads"],
            reason="agent guess"))
        assert applied
        assert build_binding_proposals(new_doc, _mapspect([_layer("roads")])) == []

    def test_superseded_document_no_proposals(self, doc):
        from app.services.gis_harness.requirement_ir.patch import apply_patch
        from app.services.gis_harness.requirement_ir.contracts import PatchRecord
        new_doc, _ = apply_patch(doc, PatchRecord(
            op_id="op-sup", turn=3, actor="system", op="supersede"))
        assert new_doc.lifecycle == "superseded"
        doc2 = _set_user(None, doc, "representation.hidden_layer_ids", ["roads"], "op-h3")
        superseded = doc2.model_copy(update={"lifecycle": "superseded"})
        assert build_binding_proposals(superseded, _mapspect([_layer("roads")])) == []

    def test_deterministic_fingerprint(self, doc):
        doc2 = _set_user(None, doc, "representation.hidden_layer_ids", ["roads"], "op-h4")
        mapspec = _mapspect([_layer("roads")])
        left = build_binding_proposals(doc2, mapspec)
        right = build_binding_proposals(doc2, mapspec)
        assert [p.binding_fingerprint for p in left] == [p.binding_fingerprint for p in right]
        assert [p.binding_id for p in left] == [p.binding_id for p in right]


class TestGrammarFacePins:
    def test_user_pins_only(self, doc):
        doc2 = _set_user(None, doc, "representation.palette", "Blues", "op-p2")
        doc2 = _set_user(None, doc2, "representation.geometry_kind", "heatmap", "op-g3")
        pins = grammar_face_pins(doc2)
        assert pins["pinned_palette"] == "blue"
        assert pins["pinned_representation"] == "visual_heatmap"

    def test_agent_fields_never_pin(self, doc):
        from app.services.gis_harness.requirement_ir.patch import apply_patch
        from app.services.gis_harness.requirement_ir.contracts import PatchRecord
        new_doc, _ = apply_patch(doc, PatchRecord(
            op_id="op-agent2", turn=2, actor="agent", op="set",
            path="representation.palette", value="Reds", reason="agent"))
        assert grammar_face_pins(new_doc) == {}

    def test_superseded_no_pins(self, doc):
        doc2 = _set_user(None, doc, "representation.palette", "Blues", "op-p3")
        pins_doc = doc2.model_copy(update={"lifecycle": "superseded"})
        assert grammar_face_pins(pins_doc) == {}
