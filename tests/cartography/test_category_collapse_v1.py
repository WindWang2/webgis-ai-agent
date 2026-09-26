"""Category Collapse 执行器契约测试（F10 M5，design D5）.

锁定：entries 输出与 cartography_service #783 既有行为逐字节兼容、
数据侧同口径属性改写（原字段不动/geometry 共享）、collapse 元数据、
cartography_service 与 cartographer 集成面。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.category_collapse import (  # noqa: E402
    COLLAPSED_PROPERTY_SUFFIX,
    OTHER_KEY,
    REASON_TOO_MANY_CATEGORIES,
    apply_collapse,
    attach_collapse_to_spec,
    rewrite_features_with_collapse,
)
from app.lib.cartography.grammar_types import (  # noqa: E402
    COLLAPSE_KEEP_CLASSES,
    MAX_CATEGORICAL_CLASSES,
)

COLORS = ["#111111", "#222222", "#333333", "#444444", "#555555",
          "#666666", "#777777", "#888888", "#999999"]


class TestApplyCollapse:
    def test_entries_shape_and_order(self):
        vals = [f"c{i}" for i in range(12)]
        out = apply_collapse(vals, colors=COLORS, keep_classes=7,
                             other_label="Other")
        assert [e["key"] for e in out.entries] == (
            [f"c{i}" for i in range(7)] + [OTHER_KEY])
        assert out.entries[0]["color"] == COLORS[0]
        assert out.entries[7]["color"] == COLORS[7]  # colors[keep % len]
        assert out.entries[7]["label"] == "Other"
        assert out.collapsed_count == 5

    def test_byte_compat_with_legacy_783(self):
        # cartography_service 旧行为：kept = vals[:k-1]；other 色
        # colors[(k-1) % len]；label「其他」。
        k = 5
        vals = [f"v{i}" for i in range(9)]
        legacy_entries = [
            {"key": v, "color": COLORS[i % len(COLORS)], "label": str(v)}
            for i, v in enumerate(vals[: k - 1])
        ]
        legacy_entries.append({
            "key": "__other__",
            "color": COLORS[(k - 1) % len(COLORS)],
            "label": "其他",
        })
        out = apply_collapse(vals, colors=COLORS, keep_classes=k - 1,
                             other_label="其他")
        assert out.entries == legacy_entries

    def test_meta_and_reason_codes(self):
        out = apply_collapse([f"v{i}" for i in range(10)], colors=COLORS,
                             keep_classes=COLLAPSE_KEEP_CLASSES)
        assert out.meta.observed_classes == 10
        assert len(out.meta.kept_keys) == COLLAPSE_KEEP_CLASSES
        assert out.meta.other_key == OTHER_KEY
        assert out.reason_codes == [REASON_TOO_MANY_CATEGORIES]

    def test_deterministic(self):
        vals = [f"v{i}" for i in range(20)]
        a = apply_collapse(vals, colors=COLORS, keep_classes=7)
        b = apply_collapse(vals, colors=COLORS, keep_classes=7)
        assert a.model_dump() == b.model_dump()

    def test_key_mapping_membership(self):
        vals = ["a", "b", "c", "d"]
        out = apply_collapse(vals, colors=COLORS, keep_classes=2)
        assert out.collapse_key_for("a") == "a"
        assert out.collapse_key_for("d") == OTHER_KEY
        assert out.collapse_key_for("unknown") == OTHER_KEY


class TestRewriteFeatures:
    def _features(self, cats):
        return [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [i, i]},
             "properties": {"cat": c, "note": f"n{i}"}}
            for i, c in enumerate(cats)
        ]

    def test_rewrite_keeps_original_field(self):
        cats = [f"c{i}" for i in range(10)]
        feats = self._features(cats)
        out = apply_collapse(cats, colors=COLORS, keep_classes=3)
        new_feats, n = rewrite_features_with_collapse(
            feats, field="cat", outcome=out)
        assert n == 7
        assert new_feats[0]["properties"]["cat"] == "c0"
        assert new_feats[0]["properties"]["cat:collapsed"] == "c0"
        assert new_feats[5]["properties"]["cat"] == "c5"          # 原字段不动
        assert new_feats[5]["properties"]["cat:collapsed"] == OTHER_KEY
        assert f"cat{COLLAPSED_PROPERTY_SUFFIX}" in new_feats[0]["properties"]

    def test_geometry_shared_not_copied(self):
        cats = [f"c{i}" for i in range(10)]
        feats = self._features(cats)
        out = apply_collapse(cats, colors=COLORS, keep_classes=3)
        new_feats, _ = rewrite_features_with_collapse(
            feats, field="cat", outcome=out)
        assert new_feats[0]["geometry"] is feats[0]["geometry"]

    def test_input_not_mutated(self):
        cats = [f"c{i}" for i in range(10)]
        feats = self._features(cats)
        out = apply_collapse(cats, colors=COLORS, keep_classes=3)
        rewrite_features_with_collapse(feats, field="cat", outcome=out)
        assert "cat:collapsed" not in feats[0]["properties"]

    def test_non_dict_passthrough(self):
        out = apply_collapse(["a", "b"], colors=COLORS, keep_classes=1)
        new_feats, _ = rewrite_features_with_collapse(
            ["junk", {"properties": {"cat": "a"}}], field="cat", outcome=out)
        assert new_feats[0] == "junk"


class TestAttachSpec:
    def test_attach_and_idempotent(self):
        out = apply_collapse(["a", "b", "c"], colors=COLORS, keep_classes=2)
        spec: dict = {"type": "categorical"}
        attach_collapse_to_spec(spec, field="cat", outcome=out)
        meta = spec["collapse"]
        assert meta["collapsed_property"] == "cat:collapsed"
        assert meta["reason_code"] == REASON_TOO_MANY_CATEGORIES
        attach_collapse_to_spec(spec, field="cat", outcome=out)
        assert spec["collapse"] == meta


class TestCartographyServiceIntegration:
    def test_categorical_surplus_has_collapse_meta(self):
        from app.services.cartography_service import CartographyService
        cats = [f"zone_{i}" for i in range(11)]
        geojson = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Polygon", "coordinates": [
                 [[i, 0], [i + 1, 0], [i + 1, 1], [i, 1], [i, 0]]]},
             "properties": {"z": c}}
            for i, c in enumerate(cats)
        ]}
        style = CartographyService.build_thematic_style(
            geojson=geojson, field="z", method="categorical", k=8)
        assert style is not None
        assert style["type"] == "categorical"
        keys = [c["key"] for c in style["categories"]]
        assert OTHER_KEY in keys
        assert len(keys) == 8
        assert style["collapse"]["reason_code"] == REASON_TOO_MANY_CATEGORIES
        # style-only 面：不持有交付数据 → 不宣称数据侧属性（诚实披露）。
        assert style["collapse"]["collapsed_property"] == ""

    def test_categorical_few_no_collapse_meta(self):
        from app.services.cartography_service import CartographyService
        cats = ["a", "b", "c"]
        geojson = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Polygon", "coordinates": [
                 [[i, 0], [i + 1, 0], [i + 1, 1], [i, 1], [i, 0]]]},
             "properties": {"z": c}}
            for i, c in enumerate(cats)
        ]}
        style = CartographyService.build_thematic_style(
            geojson=geojson, field="z", method="categorical", k=8)
        assert style is not None
        assert "collapse" not in style
        assert [c["key"] for c in style["categories"]] == ["a", "b", "c"]


class TestCartographerIntegration:
    def _point_fc(self, cats):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [100.0 + i * 0.01,
                                                           30.0 + i * 0.01]},
             "properties": {"cat": c}}
            for i, c in enumerate(cats)
        ]}

    def _compose(self, gj):
        from app.services.agent_swarm.specialists.cartographer import (
            CartographerAgent,
        )
        from app.services.agent_swarm.specialists.ledger import ArtifactLedger
        ledger = ArtifactLedger()
        ref = CartographerAgent(ledger=ledger).compose(
            {"geojson": gj, "field": "cat", "title": "土地利用"})
        payload = ledger.get(ref.ref_id)
        assert isinstance(payload, dict)
        return ref, payload

    def test_many_categories_collapsed_not_color_cycled(self):
        from app.lib.cartography.category_collapse import (
            OTHER_KEY,
            REASON_TOO_MANY_CATEGORIES,
        )
        cats = [f"lut_{i}" for i in range(14)]
        ref, payload = self._compose(self._point_fc(cats))
        layer = payload["layers"][0]
        legend = layer["legend_spec"]
        entries = legend["categories"]
        assert len(entries) <= MAX_CATEGORICAL_CLASSES
        keys = [e["key"] for e in entries]
        assert OTHER_KEY in keys
        assert legend["collapse"]["reason_code"] == REASON_TOO_MANY_CATEGORIES
        # 数据侧同口径：inlineData 要素带收纳属性
        src_id = list(payload["sources"])[0]
        feats = payload["sources"][src_id]["inlineData"]["features"]
        assert "cat:collapsed" in feats[0]["properties"]
        # 收纳披露进交付 warnings
        assert any("category_collapse" in w for w in ref.warnings)

    def test_few_categories_untouched(self):
        cats = ["residential", "industrial", "green"]
        ref, payload = self._compose(self._point_fc(cats))
        legend = payload["layers"][0]["legend_spec"]
        # cartographer 以 sorted 序枚举类别（既有契约）。
        assert [e["key"] for e in legend["categories"]] == sorted(cats)
        assert "collapse" not in legend
