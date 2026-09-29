"""MapSpec v1.5（C11 CartoIR 语义层）schema 行为测试。

与 test_carto_ir_corpus_v1.py 的分工：corpus 文件锁 golden 语义，
本文件锁 v1.5 typed 模型的**行为矩阵**（校验/迁移/披露/词表）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography import mapspec_schema as ms
from app.lib.cartography.ts_projection import emit_typescript


def _spec(layer_extra: dict) -> dict:
    return {
        "version": "1.5",
        "layers": [{"id": "l", "source": "s", "type": "fill", **layer_extra}],
        "sources": {"s": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}}},
    }


class TestVisibilityBlock:
    def test_min_max_zoom_valid(self):
        r = ms.parse_mapspec(_spec({"visibility": {"min_zoom": 8.0, "max_zoom": 14.0}}))
        assert r.valid is True
        assert r.invalid_fields == []
        assert r.document["layers"][0]["visibility"] == {"min_zoom": 8.0, "max_zoom": 14.0}

    def test_hints_typed_values(self):
        r = ms.parse_mapspec(_spec({
            "visibility": {"hints": {"street_detail_minzoom": 14.0, "admin_boundary_detail": 8.0}},
        }))
        assert r.valid is True

    def test_hints_non_numeric_value_invalid(self):
        r = ms.parse_mapspec(_spec({"visibility": {"hints": {"street_detail_minzoom": "high"}}}))
        assert r.valid is False
        assert any("hints" in d.path for d in r.invalid_fields)

    def test_unknown_hint_key_disclosed_as_unknown(self):
        """未知键进 unknown 披露、值保留（extra=allow 纪律）。"""
        r = ms.parse_mapspec(_spec({"visibility": {"hints": {"future_hint": 5}}}))
        assert r.document["layers"][0]["visibility"]["hints"]["future_hint"] == 5


class TestBivariateBlock:
    def test_full_block_valid(self):
        r = ms.parse_mapspec(_spec({
            "bivariate": {
                "x_field": "a", "y_field": "b", "matrix": 3,
                "class_field": "__biv_class", "palette_id": "BiPurpleOrange",
            },
        }))
        assert r.valid is True
        assert r.document["layers"][0]["bivariate"]["matrix"] == 3

    def test_matrix_two_valid(self):
        r = ms.parse_mapspec(_spec({"bivariate": {"x_field": "a", "y_field": "b", "matrix": 2}}))
        assert r.valid is True

    def test_matrix_four_invalid_disclosed(self):
        r = ms.parse_mapspec(_spec({"bivariate": {"x_field": "a", "y_field": "b", "matrix": 4}}))
        assert r.valid is False
        assert any("bivariate.matrix" in d.path for d in r.invalid_fields)

    def test_fields_required(self):
        r = ms.parse_mapspec(_spec({"bivariate": {"x_field": "a"}}))
        assert r.valid is False
        assert any("y_field" in d.path for d in r.invalid_fields)


class TestDataBindingBlock:
    def test_field_type_vocabulary(self):
        for ft in ("number", "string", "boolean", "date"):
            r = ms.parse_mapspec(_spec({"data_binding": {"field": "f", "field_type": ft}}))
            assert r.valid is True, ft

    def test_unknown_field_type_invalid(self):
        r = ms.parse_mapspec(_spec({"data_binding": {"field": "f", "field_type": "tensor"}}))
        assert r.valid is False
        assert any("data_binding.field_type" in d.path for d in r.invalid_fields)


class TestUpgradePath:
    def test_14_to_15_identity(self):
        doc = {"version": "1.4", "layers": []}
        r = ms.parse_mapspec(doc)
        assert r.effective_version == "1.5"
        assert r.migrated is True
        # identity upgrader：不新增键、不改写 version 字段
        assert "version" not in r.document or r.document.get("version") == "1.4"

    def test_register_upgrader_hook_preserved(self):
        """register_upgrader 公共 API 仍可用（显式破坏性迁移的扩展点）。"""
        assert callable(ms.register_upgrader)


class TestTsProjectionV15:
    def test_generated_ts_contains_v15_models(self):
        ts = emit_typescript()
        assert "export interface MapSpecLayerVisibility {" in ts
        assert "export interface MapSpecLayerBivariate {" in ts
        assert "export interface MapSpecLayerDataBinding {" in ts
        # int Literal 投影（matrix?: 2 | 3）
        assert "matrix?: 2 | 3;" in ts
        assert "field_type?: \"number\" | \"string\" | \"boolean\" | \"date\";" in ts
        # layer 字段挂接
        assert "visibility?: MapSpecLayerVisibility;" in ts
        assert "bivariate?: MapSpecLayerBivariate;" in ts
        assert "data_binding?: MapSpecLayerDataBinding;" in ts
