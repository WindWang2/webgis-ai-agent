"""Symbology × Measurement 语义接线测试（ADR-0205 S2）。

验收矩阵对应：A5 categorical vs quantitative、A6 signed metric palette hint
（diverging center 0）、缺省行为逐字节回归、显式 user-wins。
"""
from app.lib.cartography.symbology import (
    SymbologyDecision,
    resolve_symbology,
    symbology_decision_from_values,
)
from app.lib.cartography.thematic_spec import build_graduated_spec


def _d(values, **kw):
    return symbology_decision_from_values(values, **kw)


def test_default_behavior_unchanged_without_measurement():
    """缺省（无 measurement_kind）裁决锚定 master 既有行为（回归锁）。"""
    # 重尾计数数据 → head_tail + YlOrRd（ADR-0073/0152 既有裁决）。
    d = _d([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 50])
    assert d.method == "head_tail"
    assert d.palette == "YlOrRd"
    assert d.source == "distribution"
    assert d.diverging_center is None
    assert not any("语义证据" in r for r in d.reasons)
    # 近均匀数据 → quantiles 家族证据，palette YlOrRd。
    d2 = _d([float(i) for i in range(10, 210, 2)])
    assert d2.method in ("quantiles", "equal_interval", "natural_breaks")
    assert d2.diverging_center is None


def test_semantic_category_forces_qualitative_mode():
    """A5：语义 CATEGORY → categorical 模式 + qualitative 色带族。"""
    d = _d([1, 2, 3, 1, 2, 3], measurement_kind="category")
    assert d.method == "categorical"
    assert d.source == "semantic"
    assert any("category" in r for r in d.reasons)


def test_signed_change_gets_diverging_center_zero():
    """A6：signed_change → decision.diverging_center=0（palette hint）。"""
    d = _d([-5.0, -2.0, 0.0, 3.0, 7.0, 1.0, 2.0, 4.0, -1.0],
           measurement_kind="signed_change")
    assert d.diverging_center == 0.0
    assert any("signed_change" in r for r in d.reasons)


def test_non_signed_kinds_have_no_diverging_center():
    d = _d([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
           measurement_kind="count")
    assert d.diverging_center is None


def test_explicit_method_wins_over_semantic():
    """显式 method 恒优先（user-wins；语义只在缺席时参与）。"""
    d = _d([1, 2, 3, 1, 2, 3], measurement_kind="category",
           requested_method="quantiles")
    assert d.method == "quantiles"
    assert d.source == "explicit"


def test_semantic_kind_serializes_into_decision():
    """SymbologyDecision 一等工件包含 diverging_center（可序列化）。"""
    d = _d([-2.0, -1.0, 1.0, 2.0, 0.5, 1.5, -0.5, 0.2],
           measurement_kind="signed_change")
    payload = d.to_dict()
    assert "diverging_center" in payload
    assert payload["diverging_center"] == 0.0
    assert SymbologyDecision.model_validate(payload).diverging_center == 0.0


def test_invalid_measurement_kind_is_ignored_not_fatal():
    """未知 kind → 按无语义证据处置（不抛异常、不改变族）。"""
    d = _d([1, 2, 3, 4, 5, 6, 7, 8, 9], measurement_kind="nonsense_kind")
    assert d.diverging_center is None


def test_resolve_symbology_profile_path_with_measurement():
    """resolve_symbology 主入口同样接受 SymbologyProfile.measurement_kind。"""
    from app.lib.cartography.symbology import SymbologyIntent, SymbologyProfile

    decision = resolve_symbology(
        SymbologyProfile(
            values=[float(i) for i in range(3, 12)],
            measurement_kind="category",
        ),
        SymbologyIntent(),
    )
    assert decision.method == "categorical"


def test_build_graduated_spec_still_works_with_v2_fields():
    """graduated 构建器在 v2 字段（unit）下形状稳定。"""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": None,
             "properties": {"v": float(10 + i * 7)}}
            for i in range(10)
        ],
    }
    spec = build_graduated_spec(geojson, "v", method="quantiles", k=4,
                                palette="Blues", unit="人")
    assert spec is not None
    assert spec["unit"] == "人"
    assert spec["type"] == "graduated"
