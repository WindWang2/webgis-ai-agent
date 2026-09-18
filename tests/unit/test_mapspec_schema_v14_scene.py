"""MapSpec v1.4 additive scene contract tests (ADR-0199 M2).

Oracle anchors:
- 旧 MapSpec 未用新字段时行为不变（1.0–1.3 byte-stable canonical round-trip）。
- 纯 additive：identity upgrader、Optional-only 新字段。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.lib.cartography import mapspec_schema as ms

REPO_ROOT = Path(__file__).resolve().parents[2]


def _minimal_spec() -> dict:
    return {
        "version": "1.0",
        "view": {"center": [116.0, 39.9], "zoom": 10},
        "sources": {
            "s1": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}}
        },
        "layers": [{"id": "l1", "source": "s1", "type": "fill"}],
    }


class TestVersionBump:
    def test_known_versions_include_14(self):
        assert "1.4" in ms.KNOWN_VERSIONS
        assert ms.LATEST_VERSION == "1.4"

    def test_upgrade_path_exists(self):
        assert ms._find_upgrade_path("1.0", "1.4") == [
            ("1.0", "1.1"), ("1.1", "1.2"), ("1.2", "1.3"), ("1.3", "1.4"),
        ]

    @pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
    def test_old_versions_still_parseable(self, version):
        spec = _minimal_spec()
        spec["version"] = version
        result = ms.parse_mapspec(spec)
        assert result.valid is True
        assert result.forward_version is False

    def test_old_spec_canonical_bytes_unchanged(self):
        """Oracle：旧 spec 未用新字段 → canonical 输出 byte-stable。"""
        spec = _minimal_spec()
        canonical = ms.canonicalize_mapspec(spec, target_version="1.4")
        assert json.dumps(canonical, ensure_ascii=False, separators=(",", ":")) == json.dumps(
            spec, ensure_ascii=False, separators=(",", ":")
        )
        # 新字段不在文档中出现
        assert "scene" not in canonical


class TestSceneContract:
    def _scene_spec(self) -> dict:
        spec = _minimal_spec()
        spec["version"] = "1.4"
        spec["sources"]["dem"] = {
            "type": "raster-dem",
            "url": "https://example.test/dem/{z}/{x}/{y}.png",
            "encoding": "terrarium",
        }
        spec["scene"] = {
            "mode": "3d",
            "terrain": {"source": "dem", "exaggeration": 1.5, "vertical_unit": "m"},
            "camera": {"pitch": 50, "bearing": -15, "transition_ms": 0},
            "reason_code": "SCENE_MODE_3D_EVIDENCED",
            "reduced_motion": True,
        }
        return spec

    def test_scene_spec_parses_valid(self):
        result = ms.parse_mapspec(self._scene_spec())
        assert result.valid is True
        assert result.forward_version is False

    def test_invalid_mode_rejected(self):
        spec = self._scene_spec()
        spec["scene"]["mode"] = "4d"
        result = ms.parse_mapspec(spec)
        assert result.valid is False
        assert any(d.path.startswith("scene") for d in result.invalid_fields)

    def test_terrain_requires_source_string(self):
        spec = self._scene_spec()
        spec["scene"]["terrain"] = {"exaggeration": 1.0}
        result = ms.parse_mapspec(spec)
        assert result.valid is False

    def test_mode_2d_with_extrusion_layer_valid(self):
        """2d 模式 + 挤出层不违规（层类型是既有 1.0 面）。"""
        spec = self._scene_spec()
        spec["scene"]["mode"] = "2d"
        spec["scene"]["terrain"] = None
        spec["layers"].append({"id": "bld", "source": "s1", "type": "fill-extrusion"})
        result = ms.parse_mapspec(spec)
        assert result.valid is True

    def test_scene_exported_to_ts_projection_models(self):
        for name in ("MapSceneConfig", "TerrainSceneSpec", "SceneCameraSpec", "MapSpecLayerExtrusion"):
            assert any(n == name for n, _ in ms.SCHEMA_EXPORT_MODELS), name


class TestLayerExtrusionTyping:
    def test_converter_shaped_extrusion_dict_valid(self):
        """既有 converter 写出的 extrusion 兄弟键必须通过类型化校验。"""
        spec = _minimal_spec()
        spec["layers"][0]["type"] = "fill-extrusion"
        spec["layers"][0]["extrusion"] = {
            "height_field": "height",
            "height_unit": "m",
            "transform": "linear",
            "scale_factor": 1.0,
            "min_visual_height_m": 10.0,
            "max_visual_height_m": 5000.0,
            "stats": {"valid": 100, "min": 1.0, "max": 300.0},
        }
        result = ms.parse_mapspec(spec)
        assert result.valid is True

    def test_extrusion_without_height_field_invalid(self):
        spec = _minimal_spec()
        spec["layers"][0]["extrusion"] = {"height_unit": "m"}
        result = ms.parse_mapspec(spec)
        assert result.valid is False

    def test_extrusion_elevation_ref_roundtrip(self):
        spec = _minimal_spec()
        spec["layers"][0]["type"] = "fill-extrusion"
        spec["layers"][0]["extrusion"] = {
            "height_field": "height",
            "elevation_ref": "ref:geojson-abcdef0123456789",
        }
        doc = ms.canonicalize_mapspec(spec)
        assert doc["layers"][0]["extrusion"]["elevation_ref"] == "ref:geojson-abcdef0123456789"


class TestTsProjectionRegen:
    def test_generated_types_contain_scene_contract(self):
        generated = (
            REPO_ROOT / "frontend" / "lib" / "mapspec-compiler" / "types.generated.ts"
        ).read_text(encoding="utf-8")
        assert "MapSceneConfig" in generated
        assert "TerrainSceneSpec" in generated
        assert "'2.5d'" in generated or '"2.5d"' in generated

    def test_projection_is_idempotent(self):
        """再生成必须 byte 级幂等（契约测试锁定，ADR-0120 W3）。"""
        target = REPO_ROOT / "frontend" / "lib" / "mapspec-compiler" / "types.generated.ts"
        before = target.read_bytes()
        proc = subprocess.run(
            [sys.executable, "-m", "app.lib.cartography.ts_projection"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert target.read_bytes() == before, "projection not idempotent"
