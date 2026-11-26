"""WP2 提取测试 —— MapSpec → 设计规范草案（可移植纪律 + 负例）。"""
from __future__ import annotations

from app.services.design_specs.contract import validate_design_spec_payload
from app.services.design_specs.extract import extract_design_spec_draft_sync


def _document(**layer_overrides) -> dict:
    layer = {
        "id": "districts",
        "source": "districts",
        "type": "fill",
        "paint": {"fill-color": "#1d4ed8", "fill-opacity": 0.35},
        "legend_spec": {
            "type": "choropleth",
            "field": "primaries",
            "method": "quantile",
            "breaks": [3, 4, 5],  # 数据值 —— 绝不可进入草案
            "colors": ["#eff6ff", "#bfdbfe", "#93c5fd", "#3b82f6"],
            "unit": "所",
        },
        "data_binding": {"field": "primaries", "field_type": "number"},
    }
    layer.update(layer_overrides)
    return {
        "version": "1.1",
        "sources": {
            "districts": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}},
        },
        "layers": [layer],
        "layout": {
            "components": [
                {"id": "t", "type": "title", "options": {"text": "成都市各区小学分布"}},
                {"id": "lg", "type": "legend"},
                {"id": "sb", "type": "scale_bar"},
            ],
        },
    }


class TestExtractHappyPath:
    def test_draft_is_valid_design_spec(self):
        result = extract_design_spec_draft_sync(_document(), session_id="sess-x")
        payload = result["draft_payload"]
        assert payload["spec_version"] == "design_spec.v1"
        assert payload["lineage"]["source"] == "extracted"
        assert payload["lineage"]["source_session_id"] == "sess-x"
        report = validate_design_spec_payload(payload)
        assert report["ok"], report["violations"]

    def test_classification_method_and_k_extracted(self):
        result = extract_design_spec_draft_sync(_document())
        cls = result["draft_payload"]["classification"]
        # legend_spec 的 "quantile" 归一为 classify 词表的 "quantiles"
        assert cls["default_method"] == "quantiles"
        report = result["extraction_report"]
        assert report["classification"]["recoverable"] is True

    def test_data_breaks_never_enter_draft(self):
        result = extract_design_spec_draft_sync(_document())
        text = repr(result["draft_payload"])
        assert "[3, 4, 5]" not in text
        assert "'breaks'" not in text

    def test_palette_matched_when_possible(self):
        result = extract_design_spec_draft_sync(_document())
        report = result["extraction_report"]
        # 色集匹配是尽力而为：命中给 id，未命中给 unrecoverable 披露
        if report["matched_palette"]:
            assert report["palette_confidence"] > 0
            assert result["draft_payload"]["color"]["palette_sequential"]
        else:
            assert any("色板" in u or "色带" in u for u in report["unrecoverable"])

    def test_components_and_roles_extracted(self):
        result = extract_design_spec_draft_sync(_document())
        payload = result["draft_payload"]
        types = {c["component_type"] for c in payload["components"]["required"]}
        assert {"title", "legend", "scale_bar"} <= types
        roles = [r["name"] for r in payload["data_roles"]]
        assert "indicator" in roles
        assert payload["data_roles"][0]["unit"] == "所"

    def test_fingerprint_recorded(self):
        result = extract_design_spec_draft_sync(_document(), session_id="sess-x")
        fp = result["extraction_report"]["source_mapspec_fingerprint"]
        assert fp and len(fp) == 32


class TestExtractNegatives:
    def test_ref_cursor_source_excluded(self):
        doc = _document(**{"source": "ref:geojson-abc123"})
        result = extract_design_spec_draft_sync(doc)
        excluded_items = [e["item"] for e in result["extraction_report"]["excluded"]]
        assert any("ref:geojson-abc123" in item for item in excluded_items)
        # ref 游标绝不进入草案 payload
        assert "ref:geojson-abc123" not in repr(result["draft_payload"])

    def test_concrete_stops_without_method_notes_unrecoverable(self):
        layer = {
            "id": "zones",
            "source": "zones",
            "type": "fill",
            "paint": {"fill-color": ["step", ["get", "v"], "#fff", 10, "#aaa", 20, "#333"]},
        }
        doc = _document(**{"id": "other", "legend_spec": None, "data_binding": None})
        doc["layers"] = [doc["layers"][0], layer]
        result = extract_design_spec_draft_sync(doc)
        cls = result["extraction_report"]["classification"]
        assert cls["k"] is not None
        assert "断点" in cls["note"]  # 如实披露方法不可恢复

    def test_no_thematic_layers_yields_minimal_draft(self):
        doc = _document(**{"legend_spec": None, "data_binding": None})
        result = extract_design_spec_draft_sync(doc)
        assert result["draft_payload"]["data_roles"] == []
        assert result["extraction_report"]["thematic_layers_scanned"] >= 1
