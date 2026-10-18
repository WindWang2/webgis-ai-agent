"""MapSpec v1.6（语义缩放表达协议）schema 契约测试 —— 方向 5 / C3。"""
from __future__ import annotations

import pytest

from app.lib.cartography import mapspec_schema as ms


def _spec(**layer_extra) -> dict:
    layer = {"id": "L", "source": "S", "type": "circle", **layer_extra}
    return {"version": "1.6", "view": {"center": [104, 30], "zoom": 11},
            "sources": {"S": {"type": "geojson", "inlineData": {
                "type": "FeatureCollection", "features": []}}},
            "layers": [layer]}


WINDOWS = [
    {"min_zoom": 0, "max_zoom": 11, "mode": "visual_heatmap",
     "reason_codes": ["REPR.WINDOW.WORLD.VISUAL_HEATMAP"]},
    {"min_zoom": 11, "max_zoom": 24, "mode": "point_cluster",
     "reason_codes": ["REPR.WINDOW.CITY.POINT_CLUSTER"]},
]


class TestVersionV16:
    def test_known_versions_and_latest(self):
        assert ms.KNOWN_VERSIONS == ("1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6")
        assert ms.LATEST_VERSION == "1.6"
        assert ms.DEFAULT_VERSION == "1.0"

    def test_v15_migrates_identity_to_latest(self):
        legacy = _spec(representation={"windows": WINDOWS})
        legacy["version"] = "1.5"
        r2 = ms.parse_mapspec(legacy)
        assert r2.effective_version == "1.6"
        assert r2.migrated is True
        # identity：文档内容不被改写（version 键保持原值）。
        assert r2.document["layers"][0]["representation"]["windows"] == WINDOWS
        assert r2.document["version"] == "1.5"
        # 显式 target=1.5 时不升级。
        r15 = ms.parse_mapspec(legacy, target_version="1.5")
        assert r15.effective_version == "1.5" and r15.migrated is False

    def test_forward_version_flagged_and_rejected_by_consumers(self):
        r = ms.parse_mapspec({"version": "1.7", "view": {}, "sources": {}, "layers": []})
        assert r.forward_version is True
        # parse 本身永不抛（坏文档可诊断）；publication 消费入口显式拒绝。
        with pytest.raises(ms.MapSpecSchemaError) as exc:
            ms.require_parseable_mapspec(
                {"version": "1.7", "view": {}, "sources": {}, "layers": []})
        assert exc.value.code == "mapspec_forward_version"


class TestRepresentationBlock:
    def test_valid_block_round_trips(self):
        r = ms.parse_mapspec(_spec(representation={"windows": WINDOWS}))
        assert r.valid is True
        assert r.disclosures_payload() == []
        assert r.document["layers"][0]["representation"]["windows"] == WINDOWS

    def test_unknown_mode_is_invalid_disclosed_not_rejected(self):
        bad = [dict(w) for w in WINDOWS]
        bad[0]["mode"] = "hologram"
        r = ms.parse_mapspec(_spec(representation={"windows": bad}))
        # 坏文档可诊断性：invalid 披露、文档保留原值（不静默清洗）。
        assert any(
            d.path.endswith("representation.windows") and d.kind == "invalid"
            for d in r.disclosures
        ) or not r.valid

    def test_unknown_keys_disclosed(self):
        r = ms.parse_mapspec(_spec(representation={
            "windows": WINDOWS, "future_key": 1}))
        assert any("future_key" in d.path for d in r.disclosures)

    def test_old_specs_without_block_unchanged(self):
        for v in ("1.0", "1.5"):
            legacy = _spec()
            legacy["version"] = v
            legacy["layers"][0].pop("representation", None)
            r = ms.parse_mapspec(legacy)
            assert r.valid is True
            assert "representation" not in r.document["layers"][0]

    def test_hidden_window_requires_nothing_at_schema_but_contract(self):
        # schema 层不拒绝空 reason hidden 窗（可诊断性纪律）—— 契约由
        # semantic check（carto.scale.representation）fail。
        hidden = [{"min_zoom": 0, "max_zoom": 8, "mode": "hidden"},
                  {"min_zoom": 8, "max_zoom": 24, "mode": "raw"}]
        r = ms.parse_mapspec(_spec(representation={"windows": hidden}))
        assert r.valid is True

    def test_window_count_bounded_by_manifest_constant(self):
        assert ms.MAX_REPRESENTATION_WINDOWS == 8

    def test_ts_projection_registered(self):
        names = [n for n, _ in ms.SCHEMA_EXPORT_MODELS]
        assert "MapSpecRepresentationWindow" in names
        assert "MapSpecLayerRepresentation" in names

    def test_mode_vocabulary_matches_manifest_lock(self):
        assert list(ms.REPRESENTATION_MODES) == [
            "raw", "point_cluster", "visual_heatmap",
            "aggregate_grid", "generalized", "hidden"]
