"""ADR-0219 C6：agent context 有界摘要 + provider 接线。"""
from __future__ import annotations

import asyncio

import pytest

from app.services.gis_harness.requirement_ir import service as req_service
from app.services.gis_harness.requirement_ir.contracts import PatchRecord, RequirementDocument
from app.services.gis_harness.requirement_ir.patch import apply_patch
from app.services.gis_harness.representation_binding.agent_context import (
    render_summary_block,
    representation_binding_summary,
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
    return asyncio.run(coro)


@pytest.fixture
def doc():
    store = MemStore()
    run(req_service.ensure_document(store, "s1", "成都小学分布图", turn=1))
    return RequirementDocument.model_validate(
        store.data["s1"][req_service.REQUIREMENT_STATE_KEY]["document"])


def _user_set(doc, path, value, op_id):
    new_doc, applied = apply_patch(doc, PatchRecord(
        op_id=op_id, turn=2, actor="user", op="set", path=path, value=value))
    assert applied
    return new_doc


class TestSummary:
    def test_bounded_shape(self, doc):
        summary = representation_binding_summary(doc, None)
        assert summary["schema"] == "representation_binding_summary.v1"
        assert summary["lifecycle"] in ("draft", "clarifying", "accepted")
        assert set(summary) >= {
            "document_id", "requirement_revision", "user_pinned",
            "hidden_layer_ids", "locked_layer_ids", "disabled_components",
            "latest_semantic_edit"}

    def test_user_faces_surface(self, doc):
        doc2 = _user_set(doc, "representation.palette", "蓝色", "c1")
        doc2 = _user_set(doc2, "representation.hidden_layer_ids", ["roads"], "c2")
        doc2 = _user_set(doc2, "components.legend", False, "c3")
        summary = representation_binding_summary(doc2, None)
        assert summary["user_pinned"]["palette"] == "blue"
        assert summary["hidden_layer_ids"] == ["roads"]
        assert summary["disabled_components"] == ["legend"]

    def test_agent_faces_do_not_surface(self, doc):
        new_doc, applied = apply_patch(doc, PatchRecord(
            op_id="a1", turn=2, actor="agent", op="set",
            path="representation.hidden_layer_ids", value=["roads"]))
        assert applied
        summary = representation_binding_summary(new_doc, None)
        assert summary["hidden_layer_ids"] == []

    def test_drift_signal_bounded(self, doc):
        doc2 = _user_set(doc, "representation.hidden_layer_ids", ["roads"], "c4")
        mapspec = {"layers": [
            {"id": "roads", "layout": {"visibility": "visible"}},
            {"id": "parks", "layout": {"visibility": "none"}},
        ]}
        summary = representation_binding_summary(doc2, mapspec)
        assert summary["drift"] == [{"layer_id": "roads", "signal": "user_hidden_but_visible"}]

    def test_superseded_short_circuit(self, doc):
        doc2 = _user_set(doc, "representation.palette", "蓝色", "c5")
        superseded = doc2.model_copy(update={"lifecycle": "superseded"})
        assert representation_binding_summary(superseded, None).get("superseded") is True


class TestRenderBlock:
    def test_render_lines(self, doc):
        doc2 = _user_set(doc, "representation.palette", "蓝色", "c6")
        doc2 = _user_set(doc2, "representation.locked_layer_ids", ["roads"], "c7")
        text = render_summary_block(representation_binding_summary(doc2, None))
        assert "用户 pin 色带: blue" in text
        assert "用户锁定图层: roads" in text
        assert "不得自动恢复" not in text  # 隐藏面为空
        assert len(text) < 1200

    def test_empty_renders_nothing(self, doc):
        assert render_summary_block(representation_binding_summary(doc, None)) == ""


class TestProvider:
    def test_provider_reads_map_state(self, doc):
        from app.services.context_assembly.contract import (
            SharedTurnFacts,
            TurnContextRequest,
        )
        from app.services.context_assembly.providers import RepresentationBindingProvider

        store = MemStore()
        run(req_service.ensure_document(store, "s1", "成都小学分布图", turn=1))
        # map_state 中的 requirement 载荷（生产形态）
        state = dict(store.data["s1"])
        doc2 = _user_set(doc, "representation.hidden_layer_ids", ["roads"], "c8")
        state[req_service.REQUIREMENT_STATE_KEY] = {
            "schema_version": "requirement_ir_state.v1",
            "genesis": {}, "document": doc2.model_dump(),
            "folded_patch_count": 0, "folded_op_ids": []}
        provider = RepresentationBindingProvider()
        req = TurnContextRequest(session_id="s1")
        facts = SharedTurnFacts(map_state=state)
        items = asyncio.run(provider.build(req, facts))
        assert len(items) == 1
        assert "用户隐藏图层: roads" in items[0].content

    def test_provider_no_document_empty(self):
        from app.services.context_assembly.contract import (
            SharedTurnFacts,
            TurnContextRequest,
        )
        from app.services.context_assembly.providers import RepresentationBindingProvider

        provider = RepresentationBindingProvider()
        items = asyncio.run(provider.build(
            TurnContextRequest(session_id="s1"), SharedTurnFacts(map_state={})))
        assert items == []

    def test_kill_switch(self, monkeypatch):
        from app.services.context_assembly.contract import (
            SharedTurnFacts,
            TurnContextRequest,
        )
        from app.services.context_assembly.providers import RepresentationBindingProvider

        monkeypatch.setenv("GIS_REQUIREMENT_CONTEXT_BLOCK", "0")
        provider = RepresentationBindingProvider()
        req = TurnContextRequest(session_id="s1")
        assert not provider.enabled(req, SharedTurnFacts(map_state={}))

    def test_wave_a_registered(self):
        from app.services.context_assembly.providers import (
            WAVE_A_PROVIDERS,
            RepresentationBindingProvider,
        )

        assert RepresentationBindingProvider in WAVE_A_PROVIDERS
