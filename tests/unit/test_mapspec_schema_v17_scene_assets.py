"""MapSpec v1.7 additive scene-assets contract tests（G3D02 M2）。

Oracle anchors:
- 旧 MapSpec 未用新字段时行为不变（byte-stable canonical round-trip）。
- 纯 additive：identity upgrader、Optional-only 新字段。
- 数据诚信：datum/format 词表 typed 拒绝；放置域在质量门/写路径裁决。
"""
from __future__ import annotations

import json

import pytest

from app.lib.cartography import mapspec_schema as ms
from app.lib.cartography.scene_quality import evaluate_scene_quality


def _minimal_spec() -> dict:
    return {
        "version": "1.0",
        "view": {"center": [104.06, 30.67], "zoom": 12},
        "sources": {
            "s1": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}}
        },
        "layers": [{"id": "l1", "source": "s1", "type": "fill"}],
    }


def _asset_spec() -> dict:
    spec = _minimal_spec()
    spec["version"] = "1.7"
    spec["sources"]["model-a"] = {
        "type": "model3d",
        "format": "3dtiles",
        "url": "https://example.test/tileset/tileset.json",
        "content_revision": 2,
    }
    spec["scene"] = {
        "mode": "3d",
        "assets": [
            {
                "id": "campus",
                "source": "model-a",
                "placement": {
                    "anchor": [104.06, 30.67],
                    "altitude_m": 500.0,
                    "heading_deg": 15.0,
                    "scale": 1.0,
                },
                "vertical": {"datum": "egm96", "unit": "m", "elevation_ref": "ref:dem-abc123"},
                "render_order": 10,
                "lod": {"max_sse": 16, "max_visible_tiles": 64},
                "provenance_ref": "ref:asset-campus-license",
            }
        ],
    }
    return spec


class TestVersionBump:
    def test_known_versions_include_17(self):
        assert "1.7" in ms.KNOWN_VERSIONS
        assert ms.LATEST_VERSION == "1.7"
        assert ms._find_upgrade_path("1.6", "1.7") == [("1.6", "1.7")]

    def test_old_spec_canonical_bytes_unchanged(self):
        spec = _minimal_spec()
        canonical = ms.canonicalize_mapspec(spec)
        assert json.dumps(canonical, ensure_ascii=False, separators=(",", ":")) == json.dumps(
            spec, ensure_ascii=False, separators=(",", ":")
        )
        assert "assets" not in (canonical.get("scene") or {})


class TestModelSourceContract:
    def test_model_source_parses_valid(self):
        result = ms.parse_mapspec(_asset_spec())
        assert result.valid is True
        assert result.forward_version is False

    def test_model_source_requires_format_vocab(self):
        spec = _asset_spec()
        spec["sources"]["model-a"]["format"] = "fbx"
        result = ms.parse_mapspec(spec)
        assert result.valid is False
        assert any(d.path.startswith("sources.model-a") for d in result.invalid_fields)

    def test_model_source_requires_type_literal(self):
        spec = _asset_spec()
        spec["sources"]["model-a"]["type"] = "gltf"
        result = ms.parse_mapspec(spec)
        assert result.valid is False

    @pytest.mark.parametrize("fmt", ["gltf", "glb", "3dtiles"])
    def test_format_vocab_members_valid(self, fmt):
        spec = _asset_spec()
        spec["sources"]["model-a"]["format"] = fmt
        result = ms.parse_mapspec(spec)
        assert result.valid is True

    def test_unknown_source_keys_disclosed_not_dropped(self):
        spec = _asset_spec()
        spec["sources"]["model-a"]["x-custom"] = {"vendor": 1}
        result = ms.parse_mapspec(spec)
        assert result.valid is True
        assert any(d.path == "sources.model-a.x-custom" and d.kind == "unknown"
                   for d in result.unknown_fields)
        doc = ms.canonicalize_mapspec(spec)
        assert doc["sources"]["model-a"]["x-custom"] == {"vendor": 1}


class TestSceneAssetsContract:
    def test_assets_spec_parses_valid(self):
        result = ms.parse_mapspec(_asset_spec())
        assert result.valid is True

    def test_asset_requires_id_and_source(self):
        spec = _asset_spec()
        del spec["scene"]["assets"][0]["source"]
        result = ms.parse_mapspec(spec)
        assert result.valid is False

    def test_asset_vertical_datum_vocab(self):
        spec = _asset_spec()
        spec["scene"]["assets"][0]["vertical"]["datum"] = "navd88"
        result = ms.parse_mapspec(spec)
        assert result.valid is False

    def test_asset_vertical_unit_vocab(self):
        spec = _asset_spec()
        spec["scene"]["assets"][0]["vertical"]["unit"] = "ft"
        result = ms.parse_mapspec(spec)
        assert result.valid is False

    def test_assets_budget_enforced_by_schema(self):
        spec = _asset_spec()
        asset = spec["scene"]["assets"][0]
        spec["scene"]["assets"] = [dict(asset, id=f"a{i}") for i in range(ms.MAX_SCENE_ASSETS + 1)]
        result = ms.parse_mapspec(spec)
        assert result.valid is False

    def test_scene_assets_exported_to_ts_projection_models(self):
        for name in (
            "Model3dMapSpecSource", "SceneAssetSpec", "SceneAssetPlacement",
            "SceneAssetVertical", "SceneAssetLodPolicy",
        ):
            assert any(n == name for n, _ in ms.SCHEMA_EXPORT_MODELS), name

    def test_generated_types_contain_asset_contract(self):
        from pathlib import Path
        generated = (
            Path(__file__).resolve().parents[2]
            / "frontend" / "lib" / "mapspec-compiler" / "types.generated.ts"
        ).read_text(encoding="utf-8")
        assert "model3d" in generated
        assert "SceneAssetSpec" in generated
        assert 'type: "3dtiles"' in generated or '"3dtiles"' in generated


class TestSceneAssetQualityGate:
    def test_valid_assets_pass(self):
        report = evaluate_scene_quality(_asset_spec())
        blockings = [f for f in report["findings"] if f["severity"] == "blocking"]
        assert blockings == []
        assert report["passed"] is True

    def test_dangling_source_blocks(self):
        spec = _asset_spec()
        spec["scene"]["assets"][0]["source"] = "missing"
        report = evaluate_scene_quality(spec)
        codes = {f["code"] for f in report["findings"]}
        assert "SCENE_ASSET_SOURCE_REF" in codes
        assert report["passed"] is False

    def test_wrong_source_type_blocks(self):
        spec = _asset_spec()
        spec["scene"]["assets"][0]["source"] = "s1"  # geojson
        report = evaluate_scene_quality(spec)
        codes = {f["code"] for f in report["findings"]}
        assert "SCENE_ASSET_SOURCE_TYPE" in codes

    def test_source_without_url_or_ref_blocks(self):
        spec = _asset_spec()
        spec["sources"]["model-a"].pop("url")
        report = evaluate_scene_quality(spec)
        codes = {f["code"] for f in report["findings"]}
        assert "SCENE_ASSET_SOURCE_REF" in codes

    def test_duplicate_id_blocks(self):
        spec = _asset_spec()
        spec["scene"]["assets"].append(dict(spec["scene"]["assets"][0]))
        report = evaluate_scene_quality(spec)
        codes = {f["code"] for f in report["findings"]}
        assert "SCENE_ASSET_DUPLICATE_ID" in codes

    def test_bad_anchor_blocks(self):
        spec = _asset_spec()
        spec["scene"]["assets"][0]["placement"]["anchor"] = [200.0, 30.67]
        report = evaluate_scene_quality(spec)
        codes = {f["code"] for f in report["findings"]}
        assert "SCENE_ASSET_PLACEMENT_RANGE" in codes

    def test_infinite_scale_blocks(self):
        """review P1：0 < inf 通过旧检查 —— 有限性门。"""
        spec = _asset_spec()
        spec["scene"]["assets"][0]["placement"]["scale"] = float("inf")
        report = evaluate_scene_quality(spec)
        codes = {f["code"] for f in report["findings"]}
        assert "SCENE_ASSET_PLACEMENT_RANGE" in codes

    def test_polar_latitude_blocks(self):
        """review P2：|lat|>85.0511 出 Web Mercator 世界域。"""
        spec = _asset_spec()
        spec["scene"]["assets"][0]["placement"]["anchor"] = [104.06, 89.0]
        report = evaluate_scene_quality(spec)
        codes = {f["code"] for f in report["findings"]}
        assert "SCENE_ASSET_PLACEMENT_RANGE" in codes

    def test_nonpositive_scale_blocks(self):
        spec = _asset_spec()
        spec["scene"]["assets"][0]["placement"]["scale"] = 0
        report = evaluate_scene_quality(spec)
        codes = {f["code"] for f in report["findings"]}
        assert "SCENE_ASSET_PLACEMENT_RANGE" in codes

    def test_missing_evidence_warns_not_blocks(self):
        spec = _asset_spec()
        del spec["scene"]["assets"][0]["provenance_ref"]
        del spec["scene"]["assets"][0]["vertical"]
        report = evaluate_scene_quality(spec)
        codes = {f["code"] for f in report["findings"]}
        assert "SCENE_ASSET_NO_EVIDENCE" in codes
        assert report["passed"] is True  # warning 不阻塞

    def test_assets_count_as_vertical_content(self):
        """mode=3d + 仅 assets（无挤出层/terrain）不是 empty-3d。"""
        spec = _asset_spec()
        spec["scene"].pop("terrain", None)
        report = evaluate_scene_quality(spec)
        assert all(f["code"] != "SCENE_MODE_EMPTY_3D" for f in report["findings"])


class TestCoordinatorValidation:
    def test_coordinator_blocks_dangling_asset_source(self):
        from app.services.mapspec.coordinator import validate
        spec = _asset_spec()
        spec["scene"]["assets"][0]["source"] = "missing"
        result = validate(spec)
        codes = {e["code"] for e in result["errors"]}
        assert "SCENE_ASSET_SOURCE_REF" in codes
        assert result["success"] is False

    def test_coordinator_blocks_wrong_source_type(self):
        from app.services.mapspec.coordinator import validate
        spec = _asset_spec()
        spec["scene"]["assets"][0]["source"] = "s1"
        result = validate(spec)
        codes = {e["code"] for e in result["errors"]}
        assert "SCENE_ASSET_SOURCE_TYPE" in codes

    def test_coordinator_blocks_duplicate_ids(self):
        from app.services.mapspec.coordinator import validate
        spec = _asset_spec()
        spec["scene"]["assets"].append(dict(spec["scene"]["assets"][0]))
        result = validate(spec)
        codes = {e["code"] for e in result["errors"]}
        assert "SCENE_ASSET_DUPLICATE_ID" in codes

    def test_coordinator_accepts_valid_assets(self):
        from app.services.mapspec.coordinator import validate
        result = validate(_asset_spec())
        assert result["success"] is True


class TestRenderWorkEnvelopeModelDeps:
    def test_model3d_dependency_projected(self):
        from app.lib.cartography.render_work_envelope import (
            DataDependency,
            _data_dependency,
        )
        dep = _data_dependency("model-a", {
            "type": "model3d", "format": "3dtiles",
            "url": "https://example.test/tileset.json",
            "content_revision": 3,
            "data_fingerprint": "sha256:abc",
        })
        assert dep.kind == "model3d"
        assert dep.revision == "3"
        assert dep.fingerprint == "sha256:abc"
        assert dep == DataDependency(
            source_id="model-a", kind="model3d",
            revision="3", fingerprint="sha256:abc", ref=None,
        )


class TestSourceStoreClassification:
    def test_store_data_model3d_branch(self):
        from app.services.mapspec_source import store_data
        entry: dict = {}
        store_data(entry, {
            "type": "model3d", "format": "glb",
            "url": "https://example.test/model.glb",
            "content_revision": 1,
        })
        assert entry["type"] == "model3d"
        assert entry["format"] == "glb"

    def test_model3d_ref_resolution(self):
        from app.services.mapspec_source import ref
        assert ref({"type": "model3d", "format": "glb", "url": "https://x.test/m.glb"}) == "https://x.test/m.glb"
        assert ref({"type": "model3d", "format": "glb", "ref_id": "ref:model-1"}) == "ref:model-1"
