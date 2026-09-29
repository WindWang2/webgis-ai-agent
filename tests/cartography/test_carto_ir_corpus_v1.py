"""C11 CartoIR contract corpus — 双侧共享 fixture 的后端消费面。

fixture（tests/cartography/carto_ir_corpus/*.json）被两侧消费：
- backend（本文件）：parse_mapspec 语义（版本/校验/typed 块 round-trip）；
- frontend（frontend/lib/carto-ir 的 vitest）：compileMapSpec 产物 golden
  （minzoom/maxzoom/降级决策）。

contract_manifest.json 是**双向漂移闸**：backend 断言 ≡ schema 常量，
frontend 断言 ≡ lib/carto-ir/contract-manifest.ts 常量 —— 任一侧改词表
不更新 manifest 即红。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lib.cartography import mapspec_schema as ms
from app.lib.cartography.component_abi import COMPONENT_ABI_VERSION
from app.lib.cartography.scale_rules import SCALE_TIERS

CORPUS_DIR = Path(__file__).parent / "carto_ir_corpus"
MANIFEST = CORPUS_DIR / "contract_manifest.json"

CORPUS_FILES = sorted(p for p in CORPUS_DIR.glob("*.json") if p.name != "contract_manifest.json")


class TestContractManifestParity:
    """词表漂移闸：manifest ≡ backend 权威常量。"""

    def test_manifest_matches_schema_constants(self):
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert m["mapspec_versions"] == list(ms.KNOWN_VERSIONS)
        assert m["latest_mapspec_version"] == ms.LATEST_VERSION
        assert m["default_mapspec_version"] == ms.DEFAULT_VERSION
        assert m["visibility_hint_keys"] == list(ms.VISIBILITY_HINT_KEYS)
        assert m["bivariate_matrix_sizes"] == list(ms.BIVARIATE_MATRIX_SIZES)
        assert m["bivariate_class_field_default"] == ms.BIVARIATE_CLASS_FIELD_DEFAULT
        assert m["data_binding_field_types"] == list(ms.DATA_BINDING_FIELD_TYPES)
        assert m["component_abi_version"] == COMPONENT_ABI_VERSION

    def test_visibility_hint_keys_cover_scale_rules_output(self):
        """词表 ⊇ scale_rules 全部产出键（生产方漂移即红）。"""
        produced = set()
        for tier in SCALE_TIERS:
            produced |= set(tier.visibility_hints)
        assert produced <= set(ms.VISIBILITY_HINT_KEYS), produced


class TestCorpusParse:
    """corpus spec → parse_mapspec 语义 golden。"""

    @pytest.mark.parametrize("path", CORPUS_FILES, ids=[p.stem for p in CORPUS_FILES])
    def test_corpus_spec_parses_valid_at_latest(self, path: Path):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        result = ms.parse_mapspec(fixture["spec"])
        exp = fixture["expect"]
        assert result.forward_version is False
        assert result.valid is True
        assert result.effective_version == exp["effective_version"]
        assert len(result.document["layers"]) == exp["layer_count"]
        assert len(result.unknown_fields) == exp["unknown_fields"]

    def test_bivariate_native_block_round_trips(self):
        fixture = json.loads((CORPUS_DIR / "bivariate_v15.json").read_text(encoding="utf-8"))
        result = ms.parse_mapspec(fixture["spec"])
        layer = result.document["layers"][0]
        # typed 块 round-trip 保真（extra="allow" 纪律下逐键不丢）
        assert layer["bivariate"] == fixture["expect"]["bivariate"]
        assert layer["data_binding"]["field"] == "__biv_class"

    def test_legacy_spec_without_v15_blocks_unchanged(self):
        """v1.4 存量 spec → 迁移 1.5 identity，typed 块缺省不虚构。"""
        legacy = {
            "version": "1.4",
            "view": {"center": [0, 0], "zoom": 2},
            "sources": {"s": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}}},
            "layers": [{"id": "l", "source": "s", "type": "fill"}],
        }
        result = ms.parse_mapspec(legacy)
        assert result.effective_version == ms.LATEST_VERSION
        assert result.migrated is True
        layer = result.document["layers"][0]
        assert "visibility" not in layer
        assert "bivariate" not in layer
        assert "data_binding" not in layer

    def test_v15_blocks_type_errors_disclosed_not_coerced(self):
        """visibility.min_zoom 字符串 → invalid 披露（保真纪律，不 coerce）。

        typed 块属严格脊柱（与 layer.extrusion 同口径）：invalid →
        valid=False（fail-visible，publication 消费方拒绝），但文档值
        原样保留（不 coerce 翻转语义 —— R1-C2）。"""
        spec = {
            "version": "1.5",
            "layers": [{
                "id": "l", "source": "s", "type": "fill",
                "visibility": {"min_zoom": "8"},
            }],
        }
        result = ms.parse_mapspec(spec)
        assert result.valid is False
        assert any(
            d.kind == "invalid" and d.path == "layers[0].visibility.min_zoom"
            for d in result.invalid_fields
        )
        # 原值保留（绝不 coerce "8" → 8）
        assert result.document["layers"][0]["visibility"] == {"min_zoom": "8"}

    def test_forward_version_still_rejected(self):
        spec = {"version": "2.0", "layers": []}
        with pytest.raises(ms.MapSpecSchemaError) as ei:
            ms.require_parseable_mapspec(spec)
        assert ei.value.code == "mapspec_forward_version"


class TestConverterV15Emission:
    """converter 生产接线：bivariate 分析结果 → layer typed 块。"""

    def _convert(self, analysis_result: dict, layer: dict | None = None):
        from app.services.analysis_cartography_converter import (
            convert_analysis_to_mapspec_layer,
        )
        res_layer, _geo, warnings = convert_analysis_to_mapspec_layer(
            analysis_result, layer)
        return res_layer, warnings

    def _features(self):
        # bivariate_choropleth 需面要素（几何类别 polygon 门控）。
        def _poly(cx, cy):
            return {
                "type": "Polygon",
                "coordinates": [[
                    [cx, cy], [cx + 0.5, cy], [cx + 0.5, cy + 0.5], [cx, cy + 0.5], [cx, cy],
                ]],
            }

        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": _poly(104.0 + i * 0.6, 30.0 + i * 0.4),
                 "properties": {"gdp_pc": 10000 + i * 1000, "pop_density": 200 * i, "name": f"c{i}"}}
                for i in range(30)
            ],
        }

    def test_bivariate_result_emits_typed_block(self):
        analysis = {
            "algorithm": "create_thematic_map",
            "type_hint": "bivariate_choropleth",
            "legend_spec": None,
            "metadata": {
                "field_a": "gdp_pc", "field_b": "pop_density",
                "n": 3, "matrix": "BiPurpleOrange",
            },
            "data": self._features(),
        }
        res_layer, warnings = self._convert(analysis)
        biv_errors = [w for w in warnings if w.startswith("bivariate_error")]
        assert not biv_errors, biv_errors
        biv = res_layer.get("bivariate")
        assert biv is not None
        assert biv["x_field"] == "gdp_pc"
        assert biv["y_field"] == "pop_density"
        assert biv["matrix"] == 3
        assert biv["class_field"] == "__biv_class"
        assert biv["palette_id"] == "BiPurpleOrange"
        # 与 legend_spec.class_field 双写一致（一致性契约）
        assert res_layer["legend_spec"]["class_field"] == biv["class_field"]

    def test_thematic_result_emits_data_binding(self):
        analysis = {
            "algorithm": "create_thematic_map",
            "legend_spec": {
                "type": "continuous", "field": "pop_density",
                "palette_colors": ["#eff6ff", "#1d4ed8"],
            },
            "data": self._features(),
        }
        res_layer, _ = self._convert(analysis)
        binding = res_layer.get("data_binding")
        assert binding is not None
        assert binding == {"field": "pop_density", "field_type": "number"}

    def test_categorical_binding_omits_field_type(self):
        """categorical → field_type 诚实缺失（分类字段可能是 string|number）。"""
        analysis = {
            "algorithm": "create_thematic_map",
            "legend_spec": {
                "type": "categorical", "field": "category",
                "categories": [{"key": "food", "label": "餐饮", "color": "#f00"}],
            },
            "data": self._features(),
        }
        res_layer, _ = self._convert(analysis)
        binding = res_layer.get("data_binding")
        assert binding is not None
        assert binding["field"] == "category"
        assert "field_type" not in binding
