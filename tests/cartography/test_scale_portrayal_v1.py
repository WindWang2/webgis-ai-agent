"""G27 scale_portrayal v1 —— 共享契约语料的 backend pytest 消费面.

与 frontend vitest 消费同一份 ``carto_ir_corpus/scale_portrayal_cases.json``。
测试纪律：单 worker、零 IO/零网络（resolve 为纯函数）、只跑本文件。
"""
from __future__ import annotations

import builtins
import json
import socket
from pathlib import Path

import pytest

from app.lib.cartography import scale_portrayal as sp

_CORPUS_PATH = Path(__file__).parent / "carto_ir_corpus" / "scale_portrayal_cases.json"
_CORPUS = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
_CASES = _CORPUS["cases"]
_V = sp.SCALE_POLICY_VERSION
_AUTO = {"version": _V, "mode": "auto_lod"}

_REQUIRED_IDS = {
    "lod-boundary-z3", "lod-boundary-z6", "lod-boundary-z10",
    "lod-boundary-z14", "lod-boundary-z19", "legacy-no-policy",
    "unknown-policy-mode", "unknown-policy-version", "invalid-pinned-ratio",
    "ghost-priority-field", "user-hidden-all-zooms", "pinned-size-wins",
    "fixed-density-missing-ratio", "dpi-72-vs-300-same-visibility",
    "non-mercator-crs", "degenerate-zoom-gate", "hidden-representation-window",
}


def _resolve(case, **overrides):
    """把 corpus case 折叠成 resolve_effective_scale_portrayal 的关键字参数。"""
    opts = dict(case.get("options") or {})
    kwargs = dict(
        zoom=case["zoom"],
        layer=case["layer"],
        policy=case.get("policy"),
        available_fields=case.get("available_fields", []),
    )
    for key in ("previous_tier", "source_revision", "frame", "crs", "target_dpi"):
        if key in opts:
            kwargs[key] = opts[key]
    kwargs.update(overrides)
    return sp.resolve_effective_scale_portrayal(**kwargs)


# ── corpus 结构 ────────────────────────────────────────────────────────────

def test_corpus_structure():
    assert _CORPUS["corpus_version"] == sp.SCALE_POLICY_VERSION
    assert set(_CORPUS["policy_modes"]) == set(sp.SCALE_POLICY_MODES)
    assert set(_CORPUS["units"]) == set(sp.SCALE_POLICY_UNITS)
    ids = [c["id"] for c in _CASES]
    assert len(ids) == 17
    assert len(set(ids)) == 17
    assert set(ids) == _REQUIRED_IDS
    for c in _CASES:
        for key in ("applied", "tier", "band_id", "visibility",
                    "label_top_ratio", "symbol_size_ratio", "diagnostics_contains"):
            assert key in c["expected"], (c["id"], key)
    # 可 JSON 序列化、无 Python 特有值
    json.dumps(_CORPUS, ensure_ascii=False, allow_nan=False)


# ── 逐 case 断言 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_corpus_case(case):
    decision = _resolve(case)
    exp = case["expected"]
    assert decision.applied is exp["applied"], case["id"]
    assert decision.tier == exp["tier"], case["id"]
    assert decision.band_id == exp["band_id"], case["id"]
    assert decision.visibility == exp["visibility"], case["id"]
    assert decision.label_top_ratio == pytest.approx(exp["label_top_ratio"]), case["id"]
    assert decision.symbol_size_ratio == pytest.approx(exp["symbol_size_ratio"]), case["id"]
    codes = [d.code for d in decision.diagnostics]
    for code in exp["diagnostics_contains"]:
        assert code in codes, (case["id"], code, codes)
    if "scale_denominator" in exp:
        assert decision.scale_denominator == exp["scale_denominator"], case["id"]
    if "hidden_reason" in exp:
        assert decision.hidden_reason == exp["hidden_reason"], case["id"]


# ── 常量 / 模型 ───────────────────────────────────────────────────────────

def test_constants_frozen_vocab():
    assert sp.SCALE_POLICY_VERSION == "g27-scale-portrayal/1.0.0"
    assert sp.SCALE_POLICY_MODES == ("auto_lod", "fixed_density", "disabled")
    assert sp.SCALE_POLICY_UNITS == ("zoom_level", "screen_px", "ground_m")
    assert sp.GEOMETRY_SCOPES == ("point", "line", "polygon", "raster", "unknown")
    assert sp.WEB_MERCATOR_CRS == ("EPSG:3857", "EPSG:900913", "EPSG:3785")
    assert (sp.MAX_DIAGNOSTICS, sp.MAX_DISCLOSURES, sp.MAX_OVERRIDES) == (8, 8, 8)
    assert sp.MAX_ASSUMPTIONS == 8
    assert sp.MAX_EXPLANATION_BYTES == 4096


def test_models_frozen_and_extra_forbid():
    pol = sp.ScalePortrayalPolicy(mode="auto_lod")
    with pytest.raises(Exception):
        pol.mode = "disabled"
    with pytest.raises(Exception):
        sp.ScalePortrayalPolicy(mode="auto_lod", bogus_field=1)
    diag = sp.ScalePolicyDiagnostic(code="X", field="f", detail="d")
    with pytest.raises(Exception):
        diag.code = "Y"
    with pytest.raises(Exception):
        sp.ScalePolicyDiagnostic(code="X", field="f", detail="d", extra=1)


# ── validate_scale_policy ─────────────────────────────────────────────────

def test_validate_scale_policy_legacy_and_typed_codes():
    assert sp.validate_scale_policy(None) == []
    assert sp.validate_scale_policy({}) == []                    # 旧 schema = 旧项目
    assert sp.validate_scale_policy({"mode": "auto_lod"}) == []  # 缺 version = 旧 schema
    ok = sp.validate_scale_policy(
        {"version": _V, "mode": "auto_lod", "priority_field": "population"},
        available_fields=["population"],
    )
    assert ok == []

    def codes(p, fields=()):
        return [d.code for d in sp.validate_scale_policy(p, available_fields=fields)]

    assert "SCALE.POLICY.UNKNOWN_POLICY" in codes("not-a-policy")
    assert "SCALE.POLICY.UNKNOWN_VERSION" in codes({"version": "0.0.0", "mode": "auto_lod"})
    assert "SCALE.POLICY.UNKNOWN_MODE" in codes({"version": _V, "mode": "turbo"})
    assert "SCALE.POLICY.UNKNOWN_UNIT" in codes({"version": _V, "mode": "auto_lod", "unit": "furlong"})
    assert "SCALE.POLICY.INVALID_PINNED_RATIO" in codes(
        {"version": _V, "mode": "auto_lod", "pinned_size_ratio": 99.0})
    assert "SCALE.POLICY.INVALID_PINNED_RATIO" in codes(
        {"version": _V, "mode": "auto_lod", "pinned_label_top_ratio": 1.5})
    assert "SCALE.POLICY.FIXED_DENSITY_MISSING_RATIO" in codes(
        {"version": _V, "mode": "fixed_density"})
    assert "SCALE.POLICY.GHOST_PRIORITY_FIELD" in codes(
        {"version": _V, "mode": "auto_lod", "priority_field": "ghost"},
        fields=["name", "population"])


# ── 4 档 zoomBands / 边界 ─────────────────────────────────────────────────

def test_four_bands_top_ratio_monotonic_and_continuous():
    from app.lib.cartography.scene_lod import build_label_zoom_bands

    d = sp.resolve_effective_scale_portrayal(zoom=10.0, layer={"id": "L"}, policy=_AUTO)
    bands = d.label_zoom_bands
    assert len(bands) == 4
    assert bands == build_label_zoom_bands()          # 直接取权威，camelCase
    for b in bands:
        assert set(b) == {"minZoom", "maxZoom", "topRatio", "sizeRatio"}
    tops = [b["topRatio"] for b in bands]
    assert tops == sorted(tops)                      # 单调不降
    for i in range(3):
        assert bands[i]["maxZoom"] == bands[i + 1]["minZoom"]   # 边界连续


def test_boundaries_no_dead_band():
    from app.lib.cartography.scene_lod import lod_for_zoom

    seen = []
    for z in (3.0, 6.0, 10.0, 14.0, 19.0):
        d = sp.resolve_effective_scale_portrayal(zoom=z, layer={"id": "L"}, policy=_AUTO)
        assert d.band_id in {"lod-0", "lod-1", "lod-2", "lod-3"}, z
        assert d.applied is True and d.visibility == "visible", z
        assert d.active_band in d.label_zoom_bands, z
        assert d.label_top_ratio == pytest.approx(lod_for_zoom(z)["label_top_ratio"]), z
        seen.append(d.label_top_ratio)
    assert seen == sorted(seen)                      # 无死带、无回落


# ── precedence ────────────────────────────────────────────────────────────

def test_user_hidden_constant_all_zooms():
    layer = {"id": "H", "layout": {"visibility": "none"}}
    for z in (0.0, 2.9, 3.0, 6.0, 10.0, 14.0, 19.0, 24.0):
        d = sp.resolve_effective_scale_portrayal(zoom=z, layer=layer, policy=_AUTO)
        assert d.visibility == "hidden_by_user", z
        assert d.hidden_reason == "SCALE.HIDDEN.USER", z
        assert d.applied is False, z
    # authored visible:false 与合法 policy.hidden 同样恒定
    d2 = sp.resolve_effective_scale_portrayal(
        zoom=18.0, layer={"id": "H", "visible": False}, policy=_AUTO)
    assert d2.visibility == "hidden_by_user" and d2.applied is False
    d3 = sp.resolve_effective_scale_portrayal(
        zoom=18.0, layer={"id": "L"},
        policy={"version": _V, "mode": "auto_lod", "hidden": True})
    assert d3.visibility == "hidden_by_user" and d3.applied is False


def test_pin_wins_across_zoom():
    policy = {"version": _V, "mode": "auto_lod",
              "pinned_size_ratio": 2.5, "pinned_label_top_ratio": 0.42}
    for z in (3.0, 6.0, 10.0, 14.0, 19.0):
        d = sp.resolve_effective_scale_portrayal(zoom=z, layer={"id": "L"}, policy=policy)
        assert d.symbol_size_ratio == pytest.approx(2.5), z
        assert d.label_top_ratio == pytest.approx(0.42), z
        assert d.applied is True, z
        assert any("pinned_size_ratio" in o for o in d.user_overrides), z
        assert any("pinned_label_top_ratio" in o for o in d.user_overrides), z


def test_fixed_density_success():
    from app.lib.cartography.scene_lod import lod_for_zoom

    policy = {"version": _V, "mode": "fixed_density",
              "pinned_label_top_ratio": 0.5, "pinned_size_ratio": 1.5}
    d = sp.resolve_effective_scale_portrayal(zoom=10.0, layer={"id": "L"}, policy=policy)
    assert d.applied is True and d.band_id == "fixed"
    assert d.label_top_ratio == pytest.approx(0.5)
    assert d.symbol_size_ratio == pytest.approx(1.5)
    assert d.tier == "province"                    # hysteresis 仍只影响 tier 显示
    # 缺 pinned_size_ratio：符号尺度沿用权威 LOD，不发明
    d2 = sp.resolve_effective_scale_portrayal(
        zoom=10.0, layer={"id": "L"},
        policy={"version": _V, "mode": "fixed_density", "pinned_label_top_ratio": 0.5})
    assert d2.symbol_size_ratio == pytest.approx(lod_for_zoom(10.0)["symbol_scale"])


def test_zoom_gate_half_open_and_degenerate_disclosure():
    layer = {"id": "G", "visibility": {"min_zoom": 6.0, "max_zoom": 14.0}}
    for z, expected in ((5.9, "gated_by_zoom"), (6.0, "visible"),
                        (13.9, "visible"), (14.0, "gated_by_zoom")):
        d = sp.resolve_effective_scale_portrayal(zoom=z, layer=layer, policy=_AUTO)
        assert d.visibility == expected, (z, d.visibility)
    case = next(c for c in _CASES if c["id"] == "degenerate-zoom-gate")
    dd = _resolve(case)
    assert dd.visibility == "visible"              # 病态门开门，不留死带、不黑图
    assert any("DEGENERATE" in s or "病态" in s for s in dd.disclosures)


def test_hysteresis_only_affects_tier():
    base = dict(zoom=8.1, layer={"id": "L"}, policy=_AUTO)
    a = sp.resolve_effective_scale_portrayal(**base, previous_tier=None)
    b = sp.resolve_effective_scale_portrayal(**base, previous_tier="world")
    assert a.tier != b.tier                       # province vs world（去抖留守）
    for field in ("band_id", "visibility", "label_top_ratio",
                  "symbol_size_ratio", "decimate_budget", "applied"):
        assert getattr(a, field) == getattr(b, field), field


def test_disclosures_fixed_four():
    d = sp.resolve_effective_scale_portrayal(zoom=10.0, layer={"id": "L"}, policy=_AUTO)
    text = " ".join(d.disclosures)
    assert len(d.disclosures) >= 4
    assert "source" in text and "统计" in text                    # A11/A12/A13
    assert "radius_px" in text and "bandwidth_m" in text          # A14
    assert "DPI" in text and "zoom" in text                       # A15
    assert "decimate_budget" in text and "display-only" in text    # A13


# ── 物理比例尺 / DPI ──────────────────────────────────────────────────────

def test_dpi_does_not_affect_visibility():
    vis, denoms, tiers = [], [], []
    for dpi in (72.0, 300.0, 600.0):
        d = sp.resolve_effective_scale_portrayal(
            zoom=10.0, layer={"id": "L"}, policy=_AUTO,
            frame={"center": [116.0, 39.0]}, target_dpi=dpi)
        vis.append(d.visibility)
        denoms.append(d.scale_denominator)
        tiers.append(d.tier)
        assert d.band_id == "lod-2"
    assert len(set(vis)) == 1                      # DPI 不改 visibility 裁决
    assert len(set(tiers)) == 1
    assert all(x is not None for x in denoms)
    assert len(set(denoms)) == 3                   # DPI 只进入物理比例尺换算


def test_non_mercator_scale_denominator_none():
    d = sp.resolve_effective_scale_portrayal(
        zoom=10.0, layer={"id": "L"}, policy=_AUTO,
        crs="EPSG:4326", frame={"center": [116.0, 39.0]}, target_dpi=300.0)
    assert d.scale_denominator is None
    assert any("Web Mercator" in a for a in d.scale_assumptions)
    assert d.visibility == "visible" and d.applied is True


def test_physical_scale_context_shape():
    ctx = sp.physical_scale_context(
        zoom=10.0, frame={"center": [0.0, 0.0]},
        crs="EPSG:900913", target_dpi=96.0)
    assert ctx["web_mercator"] is True
    assert isinstance(ctx["scale_denominator"], int) and ctx["scale_denominator"] > 0
    no_lat = sp.physical_scale_context(
        zoom=10.0, frame=None, crs="EPSG:3857", target_dpi=96.0)
    assert no_lat["scale_denominator"] is None
    assert no_lat["assumptions"]


# ── project_scale_portrayal_spec ──────────────────────────────────────────

def test_project_spec_shape_and_camel_case():
    d = sp.resolve_effective_scale_portrayal(zoom=10.0, layer={"id": "L"}, policy=_AUTO)
    spec = sp.project_scale_portrayal_spec(d)
    assert set(spec) == {"label", "thresholds"}    # 只有既有 spec 字段
    assert set(spec["label"]) == {"zoomBands"}
    bands = spec["label"]["zoomBands"]
    assert len(bands) == 4
    # G27 主 Agent 修复（A03/A04）：权威 LOD_BANDS 的 [3,19) 是参考区间，
    # 投影进 spec 必须覆盖渲染器全域 [0,24)，否则 z<3 / z>=19 无档死带且
    # 前端 activeBandIndex 与后端 lod_for_zoom 的 [3,18] 钳制在 z=19 漂移。
    # 只钳首尾边界，四档 topRatio/sizeRatio 与相邻边界一字不改。
    assert bands == sp.spec_label_zoom_bands()
    for b in bands:
        assert set(b) == {"minZoom", "maxZoom", "topRatio", "sizeRatio"}
    assert spec["thresholds"]["maxFeatures"] == d.decimate_budget
    json.dumps(spec, allow_nan=False)              # 可 JSON 序列化
    no_bands = sp.project_scale_portrayal_spec(d, include_bands=False)
    assert "label" not in no_bands
    assert no_bands["thresholds"]["maxFeatures"] == d.decimate_budget


def test_legacy_no_policy_project_empty():
    for policy in (None, {}, {"mode": "auto_lod"},
                   {"version": _V, "mode": "disabled"}):
        d = sp.resolve_effective_scale_portrayal(
            zoom=10.0, layer={"id": "L"}, policy=policy)
        assert d.applied is False and d.band_id == "disabled"
        assert sp.project_scale_portrayal_spec(d) == {}
    # 用户隐藏同样 applied=false → 零 spec 改动
    d = sp.resolve_effective_scale_portrayal(
        zoom=10.0, layer={"id": "H", "layout": {"visibility": "none"}}, policy=_AUTO)
    assert sp.project_scale_portrayal_spec(d) == {}


# ── bounded_explanation ───────────────────────────────────────────────────

def test_bounded_explanation_byte_cap_and_diagnostics_priority():
    d = sp.resolve_effective_scale_portrayal(zoom=10.0, layer={"id": "L"}, policy=_AUTO)
    long = "要" * 160
    big = d.model_copy(update={
        "diagnostics": [
            sp.ScalePolicyDiagnostic(code=f"C{i}", field="f", detail=long)
            for i in range(sp.MAX_DIAGNOSTICS)
        ],
        "disclosures": [long] * sp.MAX_DISCLOSURES,
        "user_overrides": [long] * sp.MAX_OVERRIDES,
        "scale_assumptions": [long] * sp.MAX_ASSUMPTIONS,
    })
    ex = sp.bounded_explanation(big)
    assert len(json.dumps(ex, ensure_ascii=False).encode("utf-8")) <= sp.MAX_EXPLANATION_BYTES
    assert ex["truncated"] is True
    assert len(ex["diagnostics"]) >= 1              # 裁剪优先保留 diagnostics
    assert len(ex["diagnostics"]) >= len(ex["disclosures"])
    for diag in ex["diagnostics"]:
        assert len(diag["detail"]) <= 160
        assert len(diag["field"]) <= 160
    # 正常 decision 不触发裁剪
    normal = sp.bounded_explanation(d)
    assert normal["truncated"] is False
    assert len(json.dumps(normal, ensure_ascii=False).encode("utf-8")) <= sp.MAX_EXPLANATION_BYTES


def test_source_revision_bounded():
    d = sp.resolve_effective_scale_portrayal(
        zoom=10.0, layer={"id": "L"}, policy=_AUTO, source_revision="R" * 200)
    assert len(d.source_revision) <= 64


def test_render_thinned_honest_when_no_exact_count():
    d = sp.resolve_effective_scale_portrayal(zoom=10.0, layer={"id": "L"}, policy=_AUTO)
    assert d.render_thinned is None
    assert d.render_thinned_exact is False


# ── 纯函数 / 真实调用 / 无 IO ─────────────────────────────────────────────

def test_idempotent_same_input():
    kw = dict(zoom=10.0, layer={"id": "L", "visibility": {"min_zoom": 5.0}},
              policy=_AUTO, frame={"center": [116.0, 39.0]})
    a = sp.resolve_effective_scale_portrayal(**kw)
    b = sp.resolve_effective_scale_portrayal(**kw)
    assert a.model_dump() == b.model_dump()


def test_scene_lod_symbols_are_real_calls(monkeypatch):
    """三个此前零生产消费的 scene_lod 符号在本模块成为真实调用。"""
    from app.lib.cartography import scene_lod

    calls = {"build": 0, "terrain": 0, "lod": 0}
    orig_build = scene_lod.build_label_zoom_bands
    orig_terrain = scene_lod.terrain_maxzoom_for_zoom
    orig_lod = scene_lod.lod_for_zoom

    def build():
        calls["build"] += 1
        return orig_build()

    def terrain(z):
        calls["terrain"] += 1
        return orig_terrain(z)

    def lod(z):
        calls["lod"] += 1
        return orig_lod(z)

    monkeypatch.setattr(scene_lod, "build_label_zoom_bands", build)
    monkeypatch.setattr(scene_lod, "terrain_maxzoom_for_zoom", terrain)
    monkeypatch.setattr(scene_lod, "lod_for_zoom", lod)

    d = sp.resolve_effective_scale_portrayal(zoom=10.0, layer={"id": "L"}, policy=_AUTO)
    assert calls["build"] >= 1
    assert calls["terrain"] >= 1
    assert calls["lod"] >= 1
    # decimate_budget 真实来自 LOD_BANDS（未重写阈值）
    budgets = {b["decimate_budget"] for b in scene_lod.LOD_BANDS}
    assert d.decimate_budget in budgets


class _SpyLayer(dict):
    """记录键访问的 layer；用于断言不遍历要素属性。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accessed = []

    def get(self, key, default=None):
        self.accessed.append(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.accessed.append(key)
        return super().__getitem__(key)


def test_no_io_no_network_no_attribute_traversal(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("resolve 期间发生了 IO/网络访问")

    monkeypatch.setattr(builtins, "open", boom)
    monkeypatch.setattr(socket, "socket", boom)

    layer = _SpyLayer({"id": "L", "layout": {},
                       "properties": {"secret": [1, 2, 3]}})
    d = sp.resolve_effective_scale_portrayal(zoom=10.0, layer=layer, policy=_AUTO)
    assert d.applied is True
    assert "properties" not in layer.accessed       # 不遍历/不上传任何要素属性
