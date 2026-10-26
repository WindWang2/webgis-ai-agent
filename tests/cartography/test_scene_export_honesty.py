"""G3D05 导出诚实语义契约测试（ADR-0275）.

锁定：
- 诊断词表新码（scale_unreliable_perspective / svg_3d_perspective_degrade）
  的发射与语义（透视比例尺 warning；矢量降级 info）；
- SVG 孪生对 scene.mode != 2d 必发降级收据（2d 零噪音）；
- product_manifest scene 溯源块（相机/terrain/夸张/比例尺可信度）；
- 后端阈值（scene_quality）与前端判定（scene-export-disclosure.ts）
  的跨语言一致性（SCALE_TRUST_PITCH_MAX 同值镜像）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.lib.cartography.product_manifest import build_product_manifest
from app.lib.cartography.render_diagnostics import RENDER_DIAGNOSTICS
from app.lib.cartography.scene_quality import _SCALE_TRUST_PITCH_MAX
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[2]

_FRONTEND_DISCLOSURE = (
    REPO_ROOT / "frontend" / "lib" / "map-kit" / "scene-export-disclosure.ts"
)


def _spec(mode: str = "3d", view_pitch=None):
    spec: dict = {
        "version": "1.6",
        "view": {"center": [104.0, 30.6], "zoom": 11},
        "sources": {"dem": {"type": "raster-dem"}},
        "layers": [],
        "scene": {
            "mode": mode,
            "terrain": {"source": "dem", "exaggeration": 1.5,
                        "vertical_unit": "m", "elevation_ref": "EGM96"},
            "camera": {"pitch": 50, "bearing": -15},
        },
        "layout": {"components": []},
    }
    if view_pitch is not None:
        spec["view"]["pitch"] = view_pitch
    return spec


class TestDiagnosticsVocabulary:
    def test_new_codes_registered_with_severity(self):
        assert RENDER_DIAGNOSTICS["scale_unreliable_perspective"].severity == "warning"
        assert RENDER_DIAGNOSTICS["svg_3d_perspective_degrade"].severity == "info"

    def test_scale_threshold_mirror_frontend(self):
        """前端 SCALE_TRUST_PITCH_MAX 必须与后端 _SCALE_TRUST_PITCH_MAX 同值。"""
        source = _FRONTEND_DISCLOSURE.read_text(encoding="utf-8")
        m = re.search(r"SCALE_TRUST_PITCH_MAX\s*=\s*(\d+)", source)
        assert m is not None, "前端阈值常量缺失"
        assert int(m.group(1)) == int(_SCALE_TRUST_PITCH_MAX)


class TestSvgTwinDegradeReceipt:
    def test_3d_mode_emits_degrade_receipt(self):
        res = compile_mapspec_to_svg_detailed(_spec("3d"), include_chrome=True)
        codes = [d["code"] for d in res.diagnostics]
        assert "svg_3d_perspective_degrade" in codes
        receipt = next(d for d in res.diagnostics
                       if d["code"] == "svg_3d_perspective_degrade")
        assert receipt["detail"] == "3d"

    def test_2_5d_mode_emits_degrade_receipt(self):
        res = compile_mapspec_to_svg_detailed(_spec("2.5d"), include_chrome=True)
        codes = [d["code"] for d in res.diagnostics]
        assert "svg_3d_perspective_degrade" in codes

    def test_2d_mode_no_receipt(self):
        spec = _spec("2d")
        spec["scene"] = {"mode": "2d"}
        res = compile_mapspec_to_svg_detailed(spec, include_chrome=True)
        assert "svg_3d_perspective_degrade" not in [
            d["code"] for d in res.diagnostics]

    def test_no_scene_no_receipt(self):
        spec = _spec("3d")
        del spec["scene"]
        res = compile_mapspec_to_svg_detailed(spec, include_chrome=True)
        assert "svg_3d_perspective_degrade" not in [
            d["code"] for d in res.diagnostics]


class TestManifestSceneProvenance:
    def test_scene_block_present_with_camera_and_trust(self):
        spec = _spec("3d", view_pitch=45)
        manifest = build_product_manifest(spec, export_format="png")
        scene = manifest.get("scene")
        assert scene is not None
        assert scene["mode"] == "3d"
        assert scene["terrain"] == {
            "source": "dem", "exaggeration": 1.5,
            "vertical_unit": "m", "elevation_ref": "EGM96",
        }
        assert scene["camera"] == {"pitch": 50.0, "bearing": -15.0}
        assert scene["scale_trust"] == "perspective"

    def test_flat_view_scale_trust_reliable(self):
        spec = _spec("3d", view_pitch=0)
        manifest = build_product_manifest(spec, export_format="png")
        assert manifest["scene"]["scale_trust"] == "reliable"

    def test_camera_pitch_fallback(self):
        spec = _spec("3d")  # view 无 pitch → 回退 camera.pitch=50
        manifest = build_product_manifest(spec, export_format="png")
        assert manifest["scene"]["scale_trust"] == "perspective"

    def test_no_scene_no_block(self):
        spec = _spec("3d")
        del spec["scene"]
        manifest = build_product_manifest(spec, export_format="png")
        assert "scene" not in manifest

    def test_manifest_json_serializable_and_bounded(self):
        manifest = build_product_manifest(_spec("3d", view_pitch=45),
                                          export_format="png")
        payload = json.dumps(manifest, ensure_ascii=False)
        assert len(payload) < 8192


class TestFrontendDisclosureModuleContract:
    """前端纯函数模块的源级契约（逻辑测试在 vitest 侧）。"""

    def test_module_exists_with_both_codes(self):
        source = _FRONTEND_DISCLOSURE.read_text(encoding="utf-8")
        assert "scale_unreliable_perspective" in source
        assert "svg_3d_perspective_degrade" in source
        assert "sceneExportDisclosure" in source
        assert "vectorSceneDegradeReceipt" in source
