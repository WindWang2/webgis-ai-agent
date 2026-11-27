"""图符 authoring 服务回归 —— 只读事实 / 零突变计划 / 单笔 CAS / typed 拒绝。

使用**明确授权的隔离 fixture**（G27 同款）：monkeypatch 掉 committed spec
读取与 CAS 依赖 seam（不连 DB、不启动服务），断言真实 service 的
资格/布局证据构造、局部写载荷与 CAS 参数映射，不是断言自造 stub。
"""
from __future__ import annotations

import copy

import pytest

pytestmark = pytest.mark.cartography

from app.services.diagram_symbol_authoring import service as svc  # noqa: E402

FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": "qx1",
         "geometry": {"type": "Point", "coordinates": [104.06, 30.67]},
         "properties": {"primary": 30, "middle": 20}},
        {"type": "Feature", "id": "qx2",
         "geometry": {"type": "Point", "coordinates": [104.20, 30.60]},
         "properties": {"primary": 12, "middle": 18}},
    ],
}

SPEC = {
    "version": "2.2",
    "view": {"zoom": 10, "center": [104.1, 30.6]},
    "layers": [
        {"id": "schools-layer", "source": "s1", "type": "circle",
         "paint": {"circle-radius": 6}},
        {"id": "other-layer", "source": "s2", "type": "line"},
    ],
    "sources": {
        "s1": {"type": "geojson", "inlineData": FC},
        "s2": {"type": "vector", "tiles": ["https://example/{z}/{x}/{y}.pbf"]},
    },
}

BLOCK = {
    "schema_version": "diagram_symbol.v1",
    "kind": "composition_pie",
    "categories": ["小学", "中学"],
    "fields": ["primary", "middle"],
    "unit": "所",
    "size_policy": {"r_max_px": 24.0},
    "palette_id": "Set1",
}


@pytest.fixture
def stub_load(monkeypatch):
    async def _load(session_id: str):
        return copy.deepcopy(SPEC)
    monkeypatch.setattr(svc, "_load_spec", _load)


class _OkResult:
    is_error = False
    superseded = False
    mutation_revision = 11
    error_msg = None


class _SupersededResult(_OkResult):
    superseded = True


class _FakeIntent:
    def __init__(self, layer):
        self.layer = layer


@pytest.fixture
def stub_mutation(monkeypatch):
    captured: dict = {}

    async def _fake(session_id, intent, **kwargs):
        captured["session_id"] = session_id
        captured["intent"] = intent
        captured["kwargs"] = kwargs
        return _OkResult()

    monkeypatch.setattr(svc, "_mutation_dependencies",
                        lambda: (_FakeIntent, _fake))
    return captured


@pytest.mark.asyncio
async def test_inspect_reports_facts(stub_load):
    out = await svc.inspect_diagram_symbol("sess", "schools-layer")
    assert out["status"] == "ok"
    assert out["layer_id"] == "schools-layer"
    assert out["features_total"] == 2
    assert out["geometry_counts"] == {"Point": 2}
    assert "primary" in out["available_fields"] and "middle" in out["available_fields"]
    assert out["has_existing_block"] is False


@pytest.mark.asyncio
async def test_inspect_vector_source_rejected(stub_load):
    out = await svc.inspect_diagram_symbol("sess", "other-layer")
    assert out["status"] == "rejected"
    assert out["diagnostics"][0]["code"] == "DIAGRAM.SOURCE.UNSUPPORTED_TYPE"


@pytest.mark.asyncio
async def test_inspect_layer_not_unique(stub_load):
    out = await svc.inspect_diagram_symbol("sess", "nope")
    assert out["status"] == "rejected"
    assert out["diagnostics"][0]["code"] == "DIAGRAM.LAYER.NOT_UNIQUE"


@pytest.mark.asyncio
async def test_plan_zero_mutation_full_evidence(stub_load, stub_mutation):
    out = await svc.plan_diagram_symbol("sess", "schools-layer", BLOCK)
    assert out["status"] == "plan"
    plan = out["plan"]
    assert plan["qualification"]["evidence"]["glyphs"] == 2
    assert plan["scale"]["r_max_px"] == 24.0
    assert plan["layout"]["schema"] == "diagram_symbol_layout.v1"
    assert plan["layout"]["evidence"]["placed"] == 2
    assert plan["legend"]["schema"] == "diagram_symbol_legend.v1"
    assert plan["legend"]["unit"] == "所"
    assert len(plan["block_fingerprint"]) == 64
    assert "intent" not in stub_mutation  # 零 mutation


@pytest.mark.asyncio
async def test_plan_invalid_block_rejected_typed(stub_load):
    bad = {**BLOCK, "kind": "wind_rose"}
    out = await svc.plan_diagram_symbol("sess", "schools-layer", bad)
    assert out["status"] == "rejected"
    assert out["diagnostics"][0]["code"] == "DIAGRAM.BLOCK.INVALID"


@pytest.mark.asyncio
async def test_plan_ghost_field_rejected(stub_load):
    bad = {**BLOCK, "fields": ["primary", "ghost"]}
    out = await svc.plan_diagram_symbol("sess", "schools-layer", bad)
    assert out["status"] == "rejected"
    assert out["diagnostics"][0]["code"].startswith("DIAGRAM.QUALIFICATION.")


@pytest.mark.asyncio
async def test_negative_composition_rejected_via_service(monkeypatch):
    spec = copy.deepcopy(SPEC)
    spec["sources"]["s1"]["inlineData"]["features"][0]["properties"]["primary"] = -5

    async def _load(session_id: str):
        return spec
    monkeypatch.setattr(svc, "_load_spec", _load)
    out = await svc.plan_diagram_symbol("sess", "schools-layer", BLOCK)
    assert out["status"] == "rejected"
    assert out["diagnostics"][0]["code"] == "DIAGRAM.QUALIFICATION.NEGATIVE_VALUE"
    assert out["diagnostics"][0]["hint"]


@pytest.mark.asyncio
async def test_apply_preview_zero_mutation(stub_load, stub_mutation):
    out = await svc.apply_diagram_symbol("sess", "schools-layer", BLOCK, mode="preview")
    assert out["status"] == "preview"
    assert out["evidence"]["placed"] == 2
    assert "intent" not in stub_mutation


@pytest.mark.asyncio
async def test_apply_commit_local_payload_and_cas_params(stub_load, stub_mutation):
    out = await svc.apply_diagram_symbol(
        "sess", "schools-layer", BLOCK, mode="commit",
        expected_revision=9, operation_id="op-diagram-1", note="绑定组成图符",
        origin="agent")
    assert out["status"] == "committed"
    assert out["mapspec_revision"] == 11
    assert out["operation_id"] == "op-diagram-1"
    layer = stub_mutation["intent"].layer
    assert layer["id"] == "schools-layer"
    assert layer["diagram_symbol"]["kind"] == "composition_pie"
    # 局部写：其余层不被触碰（UpsertLayerIntent 只携带目标层）
    assert "other-layer" not in str(stub_mutation["intent"].layer)
    kw = stub_mutation["kwargs"]
    assert kw["expected_revision"] == 9
    assert kw["origin"] == "agent"
    assert kw["mutation_id"] == "op:op-diagram-1"
    assert "组成图符" in kw["reason"]


@pytest.mark.asyncio
async def test_apply_user_commit_requires_cas(stub_load, stub_mutation):
    out = await svc.apply_diagram_symbol(
        "sess", "schools-layer", BLOCK, mode="commit",
        origin="user", actor="diagram_symbols_route")
    assert out["status"] == "rejected"
    assert out["diagnostics"][0]["code"] == "DIAGRAM.APPLY.MISSING_EXPECTED_REVISION"
    assert "intent" not in stub_mutation


@pytest.mark.asyncio
async def test_apply_superseded_honest(stub_load, monkeypatch):
    async def _fake(session_id, intent, **kwargs):
        return _SupersededResult()
    monkeypatch.setattr(svc, "_mutation_dependencies",
                        lambda: (_FakeIntent, _fake))
    out = await svc.apply_diagram_symbol(
        "sess", "schools-layer", BLOCK, mode="commit", expected_revision=1)
    assert out["status"] == "superseded"
    assert "CAS" in out["error"]


@pytest.mark.asyncio
async def test_unbind_removes_block_only(stub_load, stub_mutation, monkeypatch):
    async def _load(session_id: str):
        spec = copy.deepcopy(SPEC)
        spec["layers"][0]["diagram_symbol"] = dict(BLOCK)
        spec["layers"][0]["paint"] = {"circle-radius": 6}
        return spec
    monkeypatch.setattr(svc, "_load_spec", _load)
    out = await svc.apply_diagram_symbol(
        "sess", "schools-layer", None, mode="commit", expected_revision=3)
    assert out["status"] == "committed"
    layer = stub_mutation["intent"].layer
    assert "diagram_symbol" not in layer
    assert layer["paint"] == {"circle-radius": 6}  # 既有表达不动


@pytest.mark.asyncio
async def test_apply_writes_legend_spec_same_source(stub_load, stub_mutation):
    """图例与几何同源：apply 挂块时写 legend_spec（categorical 通道）。"""
    out = await svc.apply_diagram_symbol(
        "sess", "schools-layer", BLOCK, mode="commit", expected_revision=4)
    assert out["status"] == "committed"
    layer = stub_mutation["intent"].layer
    spec = layer["legend_spec"]
    assert spec["type"] == "categorical"
    assert spec["source"] == "diagram_symbol.v1"
    assert [e["label"] for e in spec["entries"]] == ["小学", "中学"]
    assert spec["unit"] == "所"
    assert spec["notes"]
    # 色值来自 palettes 权威（Set1 前两槽）
    from app.lib.cartography.palettes import resolve_palette_colors
    colors = resolve_palette_colors("Set1")
    assert spec["entries"][0]["color"] == colors[0]
    # 既有 legend 派生通道可消费该形状（live/出版图例同源断言）
    from app.lib.cartography.render_scene import derive_legend_items
    model = derive_legend_items(spec)
    assert model is not None and model["kind"] == "categorical"
    assert len(model["entries"]) == 2
    assert model["unit"] == "所"


@pytest.mark.asyncio
async def test_unbind_keeps_user_authored_legend(stub_load, stub_mutation, monkeypatch):
    """解绑只清 diagram_symbol 自己写的图例；user-wins legend_spec 不动。"""
    async def _load(session_id: str):
        spec = copy.deepcopy(SPEC)
        spec["layers"][0]["diagram_symbol"] = dict(BLOCK)
        spec["layers"][0]["legend_spec"] = {
            "type": "categorical", "title": "用户图例",
            "entries": [{"color": "#123456", "label": "x"}],
        }
        return spec
    monkeypatch.setattr(svc, "_load_spec", _load)
    out = await svc.apply_diagram_symbol(
        "sess", "schools-layer", None, mode="commit", expected_revision=5)
    assert out["status"] == "committed"
    layer = stub_mutation["intent"].layer
    assert layer["legend_spec"]["title"] == "用户图例"  # user-wins 保留
