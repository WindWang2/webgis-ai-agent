"""#2225 回归：数值 admin_level（0..5）不得把 int 词表灌进 List[str] 契约。

旧链路：importance 接纳数值 admin_level → value_order=[2,3,4]（int）→
FeatureSelectionPolicy(List[str]) 在 plan 构建处 ValidationError 崩链；
即使强过契约，int 要素值也匹配不上 str 词表，全部要素被静默抑制。
"""
from __future__ import annotations

from app.lib.cartography.multiscale.importance import (
    derive_importance_model,
)
from app.lib.cartography.multiscale.plan import build_scale_profile
from app.lib.cartography.multiscale.profile import (
    FeatureSelectionPolicy,
    SourceRevision,
)


def _fc():
    feats = []
    for i in range(12):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]},
            "properties": {"admin_level": 2 if i < 6 else 4},
        })
    return {"type": "FeatureCollection", "features": feats}


def test_numeric_admin_level_selects_with_string_vocabulary():
    d = derive_importance_model(
        field_schema={"admin_level": {"type": "number"}},
        value_counts={"admin_level": {2: 30, 4: 25}},
        feature_count=55,
    )
    assert d.selected is not None
    assert d.selected.matched_vocabulary == "admin_level"
    # 词表 canonical 化为字符串（FeatureSelectionPolicy 契约 = List[str]）。
    assert d.selected.value_order == ["2", "4"]
    FeatureSelectionPolicy(
        importance_field=d.selected.field,
        value_order=list(d.selected.value_order),
    )

    profile = build_scale_profile(
        layer_id="l1",
        feature_collection=_fc(),
        source=SourceRevision(ref="ref:x"),
        geometry_family=None,
        latitude_hint=0.0,
        importance=d,
    )
    # 数值属性 2/4 必须命中字符串词表：world 带 keep_ranks=3 全保留，
    # 12 个要素一个都不允许被静默抑制。
    world = next(b for b in profile.profile.bands
                 if b.scale.tier == "world")
    assert world.stats.original_feature_count == 12
    assert world.stats.displayed_feature_count == 12
    assert world.stats.suppressed_feature_count == 0


def test_float_admin_level_matches_integer_vocabulary():
    """2.0 与 2 在词表匹配里同义（int 规范化，不隐式改写非整数值）。"""
    from app.lib.cartography.multiscale.generalize import _value_is_kept

    assert _value_is_kept(2, ["2", "4"])
    assert _value_is_kept(2.0, ["2", "4"])
    assert _value_is_kept("2", ["2", "4"])
    assert not _value_is_kept(3, ["2", "4"])
    assert not _value_is_kept(2.5, ["2", "4"])
    assert not _value_is_kept(True, ["2", "4"])
