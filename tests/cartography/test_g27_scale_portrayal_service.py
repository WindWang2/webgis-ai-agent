"""G27 P4 service 回归 —— 只读解释 / 局部写 / CAS / 未知策略拒绝。

使用**明确授权的隔离 fixture**：monkeypatch 掉 committed spec 读取与
GISWorldState mutation 门面（不连 DB、不启动服务），断言的是真实 service
的局部写/CAS/回执逻辑，不是断言自造 stub 的返回值。
"""
from __future__ import annotations

import copy

import pytest

from app.lib.cartography import scale_portrayal as sp
from app.services.gis_harness.scale_portrayal import service as svc

SPEC = {
    "version": "2.1",
    "view": {"zoom": 10, "center": [116.4, 39.9]},
    "layers": [
        {"id": "L1", "source": "s1", "type": "circle",
         "label": {"field": "name"}},
        {"id": "L2", "source": "s2", "type": "line"},
    ],
    "sources": {
        "s1": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
                 "properties": {"name": "a", "population": 10}},
            ],
        }},
        "s2": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": []}},
    },
}
AUTO = {"version": sp.SCALE_POLICY_VERSION, "mode": "auto_lod"}


@pytest.fixture
def stub_load(monkeypatch):
    async def _load(session_id: str):
        return copy.deepcopy(SPEC)
    monkeypatch.setattr(svc, "_load_spec", _load)


class _OkResult:
    is_error = False
    superseded = False
    mutation_revision = 7
    error_msg = None


class _SupersededResult(_OkResult):
    superseded = True


class _FakeIntent:
    """承接 service 构造的单层 UpsertLayerIntent（只截获 layer 载荷）。"""

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
async def test_inspect_returns_bounded_real_effective_policy(stub_load):
    out = await svc.inspect_scale_portrayal("sess", "L1", observed_zoom=10.0)
    assert out["status"] == "ok"
    assert out["layer_id"] == "L1"
    assert out["policy"] is None                    # 旧项目：未启用
    assert out["explanation"]["applied"] is False  # 不显示「已生效」
    assert out["explanation"]["band_id"] == "disabled"
    assert "name" in out["available_fields"]
    assert "population" in out["available_fields"]
    assert len(out["explanation"]["disclosures"]) <= 8


@pytest.mark.asyncio
async def test_preview_is_zero_mutation(stub_load, stub_mutation):
    out = await svc.apply_scale_portrayal(
        "sess", "L1", policy=AUTO, mode="preview")
    assert out["status"] == "preview"
    assert out["label_zoom_bands_count"] == 4
    assert "intent" not in stub_mutation          # 没有任何 mutation


@pytest.mark.asyncio
async def test_unknown_policy_is_rejected_with_typed_diagnostics(stub_load, stub_mutation):
    out = await svc.apply_scale_portrayal("sess", "L1", policy={
        "version": sp.SCALE_POLICY_VERSION, "mode": "turbo_lod",
    }, mode="commit")
    assert out["status"] == "rejected"
    assert any(d["code"] == "SCALE.POLICY.UNKNOWN_MODE"
               for d in out["diagnostics"])
    assert "intent" not in stub_mutation


@pytest.mark.asyncio
async def test_ghost_field_reference_is_rejected(stub_load, stub_mutation):
    out = await svc.apply_scale_portrayal("sess", "L1", policy={
        **AUTO, "priority_field": "ghost_field",
    }, mode="commit")
    assert out["status"] == "rejected"
    assert any(d["code"] == "SCALE.POLICY.GHOST_PRIORITY_FIELD"
               for d in out["diagnostics"])
    assert "intent" not in stub_mutation


@pytest.mark.asyncio
async def test_a19_a20_commit_is_local_with_cas_and_undo_chain(
        stub_load, stub_mutation):
    out = await svc.apply_scale_portrayal(
        "sess", "L1", policy=AUTO, mode="commit",
        expected_revision=5, operation_id="op-1", note="test")
    assert out["status"] == "committed"
    assert out["mapspec_revision"] == 7
    intent = stub_mutation["intent"]
    layer = intent.layer
    assert layer["id"] == "L1"                      # 只改一个 layer
    assert layer["label"]["zoomBands"] == sp.spec_label_zoom_bands()
    assert layer["scale_profile"]["g27_scale_portrayal"] == AUTO
    assert "g27_scale_portrayal" not in (layer.get("provenance") or {})
    # 其余 layer 不被写入（UpsertLayerIntent 只携带目标层）
    assert "L2" not in str(intent)
    kw = stub_mutation["kwargs"]
    assert kw["expected_revision"] == 5
    assert kw["origin"] == "agent"
    assert kw["mutation_id"] == "op:op-1"


@pytest.mark.asyncio
async def test_superseded_receipt_is_honest(stub_load, monkeypatch):
    async def _fake(session_id, intent, **kwargs):
        return _SupersededResult()
    monkeypatch.setattr(svc, "_mutation_dependencies",
                        lambda: (_FakeIntent, _fake))

    out = await svc.apply_scale_portrayal(
        "sess", "L1", policy=AUTO, mode="commit", expected_revision=1)
    assert out["status"] == "superseded"
    assert "CAS" in out["error"]


@pytest.mark.asyncio
async def test_restore_auto_removes_namespace_but_keeps_authored_bands(
        stub_load, stub_mutation, monkeypatch):
    """恢复自动：删除 G27 命名空间与自己投影的 bands，authored bands 不动。"""
    authored = [{"minZoom": 0, "maxZoom": 24,
                 "topRatio": 0.42, "sizeRatio": 0.77}]

    async def _load(session_id: str):
        spec = copy.deepcopy(SPEC)
        spec["layers"][0]["scale_profile"] = {
            "g27_scale_portrayal": AUTO,
            "some_other_profile": {"keep": True},
        }
        spec["layers"][0]["label"]["zoomBands"] = authored
        return spec
    monkeypatch.setattr(svc, "_load_spec", _load)

    out = await svc.apply_scale_portrayal(
        "sess", "L1", policy=None, mode="commit")
    assert out["status"] == "committed"
    layer = stub_mutation["intent"].layer
    assert "g27_scale_portrayal" not in layer["scale_profile"]
    assert layer["scale_profile"]["some_other_profile"] == {"keep": True}
    assert layer["label"]["zoomBands"] == authored  # user-wins
