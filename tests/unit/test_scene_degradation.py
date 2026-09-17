"""Scene degradation chain tests (ADR-0201 M6).

Oracle anchors:
- 3d → 2.5d → 2d 每一跳都有确定性触发条件与披露码。
- 不可逆信息损失（挤出高度通道）必须在降级披露中声明。
- 同一输入必同输出（回放一致）。
"""
from __future__ import annotations


from app.lib.cartography.scene_degradation import (
    DEGRADE_CODES,
    degrade_scene,
    effective_mode,
)


def _spec_3d():
    return {
        "version": "1.4",
        "scene": {
            "mode": "3d",
            "terrain": {"source": "dem", "exaggeration": 1.0, "vertical_unit": "m"},
            "camera": {"pitch": 50, "bearing": -15},
        },
        "sources": {"dem": {"type": "raster-dem", "url": "https://x.test/{z}/{x}/{y}.png"}},
        "layers": [],
    }


class TestTriggers:
    def test_no_degradation_when_healthy(self):
        result = degrade_scene(_spec_3d(), terrain_available=True, medium="interactive")
        assert result["mode"] == "3d"
        assert result["degradations"] == []

    def test_static_medium_degrades_extrusion_only(self):
        result = degrade_scene(_spec_3d(), terrain_available=True, medium="print")
        assert result["mode"] == "2.5d"
        codes = [d["code"] for d in result["degradations"]]
        assert "SCENE_MEDIUM_STATIC" in codes
        # 不可逆损失声明：挤出高度通道不可承载
        assert any(d.get("info_loss") for d in result["degradations"])

    def test_terrain_unavailable_degrades_3d_to_extrusion_only(self):
        result = degrade_scene(_spec_3d(), terrain_available=False, medium="interactive")
        assert result["mode"] == "3d"  # 挤出仍在（height 属性证据），只失地形
        codes = [d["code"] for d in result["degradations"]]
        assert "SCENE_TERRAIN_UNAVAILABLE" in codes
        assert result["scene"]["terrain"] is None

    def test_double_fault_lands_2d(self):
        result = degrade_scene(_spec_3d(), terrain_available=False, medium="print")
        assert result["mode"] == "2d"

    def test_2_5d_loses_only_terrain_on_static_medium(self):
        spec = _spec_3d()
        spec["scene"]["mode"] = "2.5d"
        spec["scene"].pop("camera", None)
        result = degrade_scene(spec, terrain_available=True, medium="export_svg")
        assert result["mode"] == "2.5d"  # 晕渲允许；零透视
        assert result["scene"]["camera"]["pitch"] == 0

    def test_2d_never_degrades_further(self):
        spec = _spec_3d()
        spec["scene"]["mode"] = "2d"
        spec["scene"].pop("terrain", None)
        spec["scene"].pop("camera", None)  # 干净的 2d 场景无相机建议档
        result = degrade_scene(spec, terrain_available=False, medium="print")
        assert result["mode"] == "2d"
        assert result["degradations"] == []


class TestDisclosureShape:
    def test_codes_from_vocabulary(self):
        for code in DEGRADE_CODES:
            assert code.startswith("SCENE_")

    def test_degradation_entries_bounded_shape(self):
        result = degrade_scene(_spec_3d(), terrain_available=False, medium="print")
        for d in result["degradations"]:
            assert set(d) <= {"code", "detail", "info_loss"}
            assert isinstance(d["code"], str)

    def test_spec_not_mutated(self):
        spec = _spec_3d()
        before = {k: v for k, v in spec["scene"].items()}
        degrade_scene(spec, terrain_available=False, medium="print")
        assert spec["scene"] == before

    def test_effective_mode_helper(self):
        assert effective_mode({"scene": {"mode": "3d"}}) == "3d"
        assert effective_mode({}) == "2d"
        assert effective_mode({"scene": {"mode": "bogus"}}) == "2d"

    def test_determinism(self):
        a = degrade_scene(_spec_3d(), terrain_available=False, medium="print")
        b = degrade_scene(_spec_3d(), terrain_available=False, medium="print")
        assert a == b
