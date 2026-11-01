"""MapSpec v1.9（多尺度表达档案）schema 契约测试 —— GOAL 04 / M1。

v1.8 纯 additive：
- representation 窗口新键 ``source``（artifact 带源覆盖）；
- ``layer.scale_profile`` 开放声明（权威形状 = multiscale/profile.py）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography import mapspec_schema as ms

pytestmark = pytest.mark.cartography


def _spec(**layer_extra) -> dict:
    layer = {"id": "L", "source": "S", "type": "line", **layer_extra}
    return {"version": "1.9", "view": {"center": [104, 30], "zoom": 10},
            "sources": {
                "S": {"type": "geojson", "inlineData": {
                    "type": "FeatureCollection", "features": []}},
                "L__gen@8-11": {"type": "geojson", "inlineData": {
                    "type": "FeatureCollection", "features": []}},
            },
            "layers": [layer]}


WINDOWS_WITH_SOURCE = [
    {"min_zoom": 0, "max_zoom": 8, "mode": "generalized",
     "reason_codes": ["REPR.WINDOW.WORLD.GENERALIZED"], "source": "L__gen@8-11"},
    {"min_zoom": 8, "max_zoom": 24, "mode": "raw", "reason_codes": []},
]


class TestVersionV18:
    def test_known_versions_and_latest(self):
        assert ms.KNOWN_VERSIONS == (
            "1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8",
            "1.9")
        assert ms.LATEST_VERSION == "1.9"

    def test_v17_migrates_identity_to_latest(self):
        legacy = _spec(representation={"windows": WINDOWS_WITH_SOURCE},
                       scale_profile={"schema_version": 1})
        legacy["version"] = "1.7"
        r2 = ms.parse_mapspec(legacy)
        assert r2.effective_version == "1.9" and r2.migrated is True
        # identity：文档内容不被改写。
        assert r2.document["layers"][0]["representation"]["windows"] == (
            WINDOWS_WITH_SOURCE)
        assert r2.document["version"] == "1.7"

    def test_forward_version_rejected_by_publication(self):
        with pytest.raises(ms.MapSpecSchemaError) as exc:
            ms.require_parseable_mapspec(
                {"version": "2.0", "view": {}, "sources": {}, "layers": []})
        assert exc.value.code == "mapspec_forward_version"

    def test_v18_coordination_slot_accepted(self):
        # 1.8 = GOAL 03 / PR #1877 协调占位：词表内接受（其 temporal 字段
        # 在本分支不存在 —— extra=allow 全模型纪律，无字段也合法）。
        r = ms.parse_mapspec(
            {"version": "1.8", "view": {}, "sources": {}, "layers": []})
        assert r.valid is True and r.effective_version == "1.9"


class TestWindowSourceOverride:
    def test_window_source_round_trips(self):
        r = ms.parse_mapspec(_spec(representation={"windows": WINDOWS_WITH_SOURCE}))
        assert r.valid is True
        assert r.document["layers"][0]["representation"]["windows"][0]["source"] == (
            "L__gen@8-11")

    def test_window_source_non_string_disclosed_not_coerced(self):
        bad = [dict(w) for w in WINDOWS_WITH_SOURCE]
        bad[0]["source"] = 123  # 非字符串：typed 拒（披露），不 coerce
        r = ms.parse_mapspec(_spec(representation={"windows": bad}))
        assert r.valid is False
        assert any(
            d.path.endswith("source") and d.kind == "invalid"
            for d in r.invalid_fields
        )


class TestLayerScaleProfile:
    def test_scale_profile_open_dict_round_trips(self):
        block = {
            "schema_version": 1, "profile_id": "msp:x:1", "layer_id": "L",
            "classification_basis": "original_source",
            "bands": [{"scale": {"min_zoom": 0, "max_zoom": 24, "tier": "world"},
                       "representation_mode": "raw"}],
        }
        r = ms.parse_mapspec(_spec(scale_profile=block))
        assert r.valid is True
        assert r.document["layers"][0]["scale_profile"] == block

    def test_absent_scale_profile_keeps_legacy_shape(self):
        r = ms.parse_mapspec(_spec())
        assert r.valid is True
        assert "scale_profile" not in r.document["layers"][0]

    def test_scale_profile_roundtrip_preserved_in_canonical(self):
        block = {"schema_version": 1, "layer_id": "L", "bands": []}
        doc = _spec(scale_profile=block)
        canon = ms.canonicalize_mapspec(doc)
        assert canon["layers"][0]["scale_profile"] == block
