"""ADR-0219 C4：UI mutation → Requirement 语义投影 —— 门控/幂等/重定基。"""
from __future__ import annotations

import asyncio

import pytest

from app.contracts.mapspec_intents import PatchLayerPresentationIntent, SetViewIntent
from app.services.gis_harness.requirement_ir import service as req_service
from app.services.gis_harness.requirement_ir.contracts import RequirementDocument
from app.services.gis_harness.representation_binding import semantic_projection as sp
from app.services.mapspec.intents import (
    PatchComponentIntent,
    PatchWorkbenchDeltaIntent,
    RemoveLayerIntent,
    RemoveComponentIntent,
    RollbackIntent,
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


def _spec(layers=None, workbench=None) -> dict:
    spec: dict = {"layers": layers if layers is not None else []}
    if workbench is not None:
        spec["workbench"] = workbench
    return spec


def _layer(layer_id: str, *, user_hidden=False, user_visible=False):
    layer = {"id": layer_id, "source": f"{layer_id}-src", "type": "circle",
             "layout": {}}
    if user_hidden:
        layer["layout"]["visibility"] = "none"
        layer["cartographic_intent"] = {
            "presentation_owner": "user", "expected_visible": False}
    elif user_visible:
        layer["cartographic_intent"] = {
            "presentation_owner": "user", "expected_visible": True}
    return layer


@pytest.fixture
def store_with_doc():
    store = MemStore()
    run(req_service.ensure_document(store, "s1", "成都小学分布图", turn=1))
    return store


def _doc_of(store, sid="s1") -> RequirementDocument:
    return RequirementDocument.model_validate(
        store.data[sid][req_service.REQUIREMENT_STATE_KEY]["document"])


class TestBuildProjectionPatches:
    def test_user_hide_projects_absolute_set(self):
        spec = _spec([
            _layer("roads", user_hidden=True), _layer("parks", user_visible=True)])
        patches = sp.build_projection_patches(
            PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="user", actor="mapspec_route", producer_class=None,
            mutation_id="c:m1", turn=3, result_mapspec=spec)
        assert len(patches) == 1
        assert patches[0]["path"] == "representation.hidden_layer_ids"
        assert patches[0]["value"] == ["roads"]      # 只含用户隐藏层
        assert patches[0]["op_id"] == "rbp:c:m1:hidden"
        assert patches[0]["turn"] == 3

    def test_agent_origin_never_projects(self):
        patches = sp.build_projection_patches(
            PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="agent", actor="tool:x", producer_class=None,
            mutation_id="m2", turn=1, result_mapspec=_spec())
        assert patches == []

    def test_system_repair_never_projects(self):
        patches = sp.build_projection_patches(
            PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="agent", actor="runtime_repair", producer_class=None,
            mutation_id="m3", turn=1, result_mapspec=_spec())
        assert patches == []

    def test_view_intent_never_projects(self):
        assert sp.build_projection_patches(
            SetViewIntent(zoom=12.0), origin="user", actor="r", producer_class=None,
            mutation_id="m4", turn=1, result_mapspec=_spec()) == []

    def test_opacity_only_never_projects(self):
        assert sp.build_projection_patches(
            PatchLayerPresentationIntent(layer_id="roads", opacity=0.4),
            origin="user", actor="r", producer_class=None,
            mutation_id="m5", turn=1, result_mapspec=_spec()) == []

    def test_unknown_kind_never_projects(self):
        class BogusIntent:
            pass

        assert sp.build_projection_patches(
            BogusIntent(), origin="user", actor="r", producer_class=None,
            mutation_id="m6", turn=1, result_mapspec=_spec()) == []

    def test_locks_project_from_workbench(self):
        spec = _spec(workbench={"lockedLayerIds": ["roads"], "lockedComponentIds": []})
        patches = sp.build_projection_patches(
            PatchWorkbenchDeltaIntent(delta={"locksAdd": ["roads"]}),
            origin="user", actor="r", producer_class=None,
            mutation_id="m7", turn=2, result_mapspec=spec)
        assert len(patches) == 1
        assert patches[0]["path"] == "representation.locked_layer_ids"
        assert patches[0]["value"] == ["roads"]

    def test_remove_layer_writes_absolute_sets(self):
        """review P1#2：删除后 spec 不含被删层——绝对值集清 ghost 成员、
        保留存活家族成员的用户语义（旧行 membership 对照是双向失效的）。"""
        spec = _spec([_layer("parks")], workbench={"lockedLayerIds": []})
        patches = sp.build_projection_patches(
            RemoveLayerIntent(layer_id="roads"),
            origin="user", actor="r", producer_class=None,
            mutation_id="m8", turn=2, result_mapspec=spec)
        by_path = {p["path"]: p for p in patches}
        assert set(by_path) == {"representation.hidden_layer_ids",
                                "representation.locked_layer_ids"}
        # roads 被删 → 隐藏/锁定集按提交后 spec 派生 → 双双为空（ghost 清除）
        assert by_path["representation.hidden_layer_ids"]["value"] == []
        assert by_path["representation.locked_layer_ids"]["value"] == []

    def test_remove_layer_keeps_surviving_family_semantics(self):
        """删除 rail-1 后 rail-2 仍隐藏：绝对值集保留存活成员的用户语义。"""
        spec = _spec([_layer("rail-2", user_hidden=True)],
                     workbench={"lockedLayerIds": []})
        patches = sp.build_projection_patches(
            RemoveLayerIntent(layer_id="rail-1"),
            origin="user", actor="r", producer_class=None,
            mutation_id="m9", turn=2, result_mapspec=spec)
        by_path = {p["path"]: p for p in patches}
        # 语义集按族基 id 收敛（rail-2 → rail）：用户隐藏的是 rail 族
        assert by_path["representation.hidden_layer_ids"]["value"] == ["rail"]

    def test_component_disable_projects(self):
        patches = sp.build_projection_patches(
            PatchComponentIntent(component_id="legend", component_type="legend",
                                 enabled=False),
            origin="user", actor="r", producer_class=None,
            mutation_id="m10", turn=2, result_mapspec=_spec())
        assert patches == [{
            "op": "set", "path": "components.legend", "value": False,
            "reason": patches[0]["reason"],
            "op_id": "rbp:m10:component:legend", "turn": 2,
        }]

    def test_component_without_type_skips(self):
        # 无法确定 IR 组件面（不猜）
        assert sp.build_projection_patches(
            PatchComponentIntent(component_id="chart-42", enabled=False),
            origin="user", actor="r", producer_class=None,
            mutation_id="m11", turn=2, result_mapspec=_spec()) == []

    def test_remove_component_known_type_projects(self):
        patches = sp.build_projection_patches(
            RemoveComponentIntent(component_id="north_arrow"),
            origin="user", actor="r", producer_class=None,
            mutation_id="m12", turn=2, result_mapspec=_spec())
        assert patches[0]["path"] == "components.north_arrow"
        assert patches[0]["value"] is False

    def test_undo_rebaseline_recomputes_both_sets(self):
        spec = _spec([_layer("roads", user_hidden=True)],
                     workbench={"lockedLayerIds": ["roads"]})
        patches = sp.build_projection_patches(
            RollbackIntent(checkpoint_id="ck1"),
            origin="user", actor="r", producer_class=None,
            mutation_id="m13", turn=4, result_mapspec=spec)
        by_path = {p["path"]: p for p in patches}
        assert by_path["representation.hidden_layer_ids"]["value"] == ["roads"]
        assert by_path["representation.locked_layer_ids"]["value"] == ["roads"]
        # agent 的 rollback 不触发重定基
        assert sp.build_projection_patches(
            RollbackIntent(checkpoint_id="ck1"),
            origin="agent", actor="r", producer_class=None,
            mutation_id="m14", turn=4, result_mapspec=spec) == []

    def test_style_intent_intentionally_not_mirrored(self):
        from app.services.mapspec.intents import PatchLayerStyleIntent

        assert sp.build_projection_patches(
            PatchLayerStyleIntent(layer_id="roads", paint={"color": "#123"}),
            origin="user", actor="r", producer_class=None,
            mutation_id="m15", turn=2, result_mapspec=_spec()) == []


class TestProjectUserMutation:
    def test_projection_writes_requirement(self, store_with_doc):
        spec = _spec([_layer("roads", user_hidden=True)])
        outcome = run(sp.project_user_mutation(
            "s1", PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="user", actor="mapspec_route", producer_class=None,
            mutation_id="c:u1", turn_id="t7",
            result_mapspec=spec, store=store_with_doc))
        assert outcome["projected"] == 1 and not outcome["errors"]
        doc = _doc_of(store_with_doc)
        assert doc.intent.representation.hidden_layer_ids == ("roads",)
        # provenance 是 user（跨轮 user-wins）
        prov = doc.intent.field_provenance.get("representation.hidden_layer_ids")
        assert prov is not None and prov.is_user()

    def test_duplicate_mutation_idempotent(self, store_with_doc):
        spec = _spec([_layer("roads", user_hidden=True)])
        for _ in range(3):
            run(sp.project_user_mutation(
                "s1", PatchLayerPresentationIntent(layer_id="roads", visible=False),
                origin="user", actor="r", producer_class=None,
                mutation_id="c:u2", turn_id="t1", result_mapspec=spec, store=store_with_doc))
        doc = _doc_of(store_with_doc)
        # 同 op_id 幂等：journal 只有一条该 op 的 patch
        assert len([p for p in doc.patches if p.op_id == "rbp:c:u2:hidden"]) == 1

    def test_document_absent_skips(self):
        outcome = run(sp.project_user_mutation(
            "nope", PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="user", actor="r", producer_class=None,
            mutation_id="c:u3", turn_id=None, result_mapspec=_spec()))
        assert outcome["skipped"] == "projection_document_absent"

    def test_superseded_document_not_resurrected(self, store_with_doc):
        from app.services.gis_harness.requirement_ir.patch import apply_patch
        from app.services.gis_harness.requirement_ir.contracts import PatchRecord

        doc = _doc_of(store_with_doc)
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="sup1", turn=2, actor="system", op="supersede"))
        store_with_doc.data["s1"][req_service.REQUIREMENT_STATE_KEY][
            "document"] = doc.model_dump()
        outcome = run(sp.project_user_mutation(
            "s1", PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="user", actor="r", producer_class=None,
            mutation_id="c:u4", turn_id=None,
            result_mapspec=_spec([_layer("roads", user_hidden=True)]),
            store=store_with_doc))
        assert outcome["skipped"] == "projection_document_superseded"


class TestSafeEntry:
    def test_safe_entry_never_raises(self, store_with_doc, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("store down")

        monkeypatch.setattr(sp, "project_user_mutation", boom)
        run(sp.project_user_mutation_safe(
            "s1", PatchLayerPresentationIntent(layer_id="x", visible=False),
            _FakeResult(), origin="user", mutation_id="c:u5"))

    def test_non_user_origin_short_circuits(self, store_with_doc):
        async def fail(*a, **k):
            raise AssertionError("must not project non-user origin")

        original = sp.project_user_mutation
        sp.project_user_mutation = fail
        try:
            run(sp.project_user_mutation_safe(
                "s1", PatchLayerPresentationIntent(layer_id="x", visible=False),
                _FakeResult(), origin="agent", mutation_id="m"))
        finally:
            sp.project_user_mutation = original

    def test_duplicate_result_still_compensates(self, store_with_doc):
        """review P2#6：duplicate 重放不跳过投影——首次投影失败时在此
        补偿（op_id 幂等天然安全）。"""
        calls = []

        async def spy(*a, **k):
            calls.append(1)
            return {"projected": 0, "errors": [], "skipped": None}

        original = sp.project_user_mutation
        sp.project_user_mutation = spy
        try:
            run(sp.project_user_mutation_safe(
                "s1", PatchLayerPresentationIntent(layer_id="x", visible=False),
                _FakeResult(duplicate=True), origin="user", mutation_id="m"))
            assert calls, "duplicate 必须仍尝试投影（幂等补偿）"
        finally:
            sp.project_user_mutation = original


class _FakeResult:
    def __init__(self, duplicate=False):
        self.duplicate = duplicate
        self.mapspec = None
        self.mutation_id = "fake"
