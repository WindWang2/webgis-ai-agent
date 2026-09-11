"""V7（Goal 08 Phase E）类型化 style token 契约测试.

覆盖：输出预设解析（output→preset 映射/主题 profile 收紧/确定性）、
token 结构有界性、组件样式投影（按 token 消费面裁剪/未登记兜底）、
调色板 profile 迁移规划（色带识别/语义位置对齐/用户色不猜改/不可解
诚实披露）。
"""

from app.lib.cartography.style_tokens import (
    STYLE_PRESET_IDS,
    plan_palette_profile_migration,
    resolve_component_style,
    resolve_style_tokens,
)


class TestStyleTokenPresets:
    def test_output_target_mapping(self):
        assert resolve_style_tokens("interactive").preset == "screen"
        assert resolve_style_tokens("png").preset == "screen"
        assert resolve_style_tokens("pdf").preset == "publication"
        assert resolve_style_tokens("svg").preset == "publication"
        assert resolve_style_tokens("print").preset == "publication"

    def test_theme_profile_tightens_screen(self):
        assert resolve_style_tokens("interactive", "print").preset == "publication"
        assert resolve_style_tokens("png", "high_contrast").preset == "publication"
        # publication 输出不受 light/dark 主题影响
        assert resolve_style_tokens("pdf", "light").preset == "publication"

    def test_explicit_preset_wins(self):
        assert resolve_style_tokens("pdf", preset="screen").preset == "screen"
        # 非法 preset 回退 output 映射
        assert resolve_style_tokens("pdf", preset="cyber").preset == "publication"

    def test_screen_lines_thicker_than_publication(self):
        screen_tokens = resolve_style_tokens("interactive")
        pub_tokens = resolve_style_tokens("pdf")
        assert screen_tokens.line.minor > pub_tokens.line.minor
        assert screen_tokens.line.emphasis > pub_tokens.line.emphasis
        assert screen_tokens.symbol.point_max_px > pub_tokens.symbol.point_max_px

    def test_deterministic_and_bounded(self):
        a = resolve_style_tokens("interactive", "light").to_bounded_dict()
        b = resolve_style_tokens("interactive", "light").to_bounded_dict()
        assert a == b
        assert set(a) == {"preset", "themeProfile", "line", "symbol",
                          "hierarchy", "background", "borderWidth", "haloWidth"}
        assert resolve_style_tokens("interactive").preset in STYLE_PRESET_IDS

    def test_unknown_output_falls_back_to_screen(self):
        assert resolve_style_tokens("hologram").preset == "screen"


class TestComponentStyleProjection:
    def test_title_consumes_hierarchy(self):
        style = resolve_component_style("title", "interactive")
        assert style["fontSizeToken"] == "text-title"
        assert style["fontWeight"] == 600

    def test_publication_title_weight_heavier(self):
        screen = resolve_component_style("title", "interactive")
        pub = resolve_component_style("title", "pdf")
        assert pub["fontWeight"] > screen["fontWeight"]

    def test_scale_bar_consumes_line_and_annotation(self):
        style = resolve_component_style("scale_bar", "pdf")
        assert style["lineWidth"] == 0.6
        assert style["fontSizeToken"] == "text-micro"

    def test_border_consumers(self):
        assert "borderWidth" in resolve_component_style("legend", "interactive")
        assert "borderWidth" in resolve_component_style("map_border", "pdf")

    def test_unknown_component_gets_legend_fallback(self):
        style = resolve_component_style("woozle_panel", "interactive")
        assert style  # 层级档兜底，不空手

    def test_keys_bounded(self):
        allowed = {"fontSizeToken", "fontWeight", "lineWidth", "borderWidth",
                   "haloWidth"}
        for ctype in ("title", "legend", "north_arrow", "graticule",
                      "chart_panel", "woozle"):
            assert set(resolve_component_style(ctype, "pdf")) <= allowed


class TestPaletteMigration:
    def test_native_interpolate_ramp_recognized(self):
        paint = {"circle-color": {"type": "interpolate", "stops": [
            [0, "#ffffb2"], [50, "#fd8d3c"], [100, "#bd0026"]]}}
        plan = plan_palette_profile_migration(paint, target_profile="print")
        assert plan["applicable"]
        assert plan["source_palette"] == "YlOrRd"
        assert plan["target_palette"]
        assert plan["target_palette"] != plan["source_palette"]

    def test_semantic_positions_preserved(self):
        paint = {"circle-color": {"type": "interpolate", "stops": [
            [0, "#ffffb2"], [50, "#fd8d3c"], [100, "#bd0026"]]}}
        plan = plan_palette_profile_migration(paint, target_profile="print")
        stops = plan["patch"]["circle-color"]["stops"]
        # 端点映射端点（低→低、高→高），结构原样保留
        assert stops[0][0] == 0 and stops[-1][0] == 100
        target_hexes = [s[1] for s in stops]
        assert len(set(target_hexes)) == 3
        assert "#eff3ff" == stops[0][1]  # YlOrRd 端点 → 替代带低值端

    def test_step_method_recognized(self):
        paint = {"fill-color": {"method": "step", "field": "v", "stops": [
            [0, "#eff3ff"], [10, "#bdd7e7"], [100, "#08519c"]]}}
        plan = plan_palette_profile_migration(paint, target_profile="print")
        assert plan["applicable"]
        assert plan["source_palette"] == "Blues"

    def test_user_custom_color_not_rewritten(self):
        plan = plan_palette_profile_migration(
            {"circle-color": "#ff00aa"}, target_profile="print")
        assert plan["applicable"] is False
        assert plan["patch"] is None
        assert plan["disclosures"]
        assert "不猜测" in plan["disclosures"][0] or "保留" in plan["disclosures"][0]

    def test_mixed_ramp_and_custom_rejected(self):
        paint = {"circle-color": {"type": "interpolate", "stops": [
            [0, "#ffffb2"], [50, "#ff00aa"], [100, "#bd0026"]]}}
        plan = plan_palette_profile_migration(paint, target_profile="print")
        assert plan["applicable"] is False

    def test_print_target_requires_print_safe(self):
        # Set1（qualitative）print 不安全（相邻灰度差极小）→ print 目标必须换
        paint = {"circle-color": {"type": "match", "stops": [
            ["a", "#e41a1c"], ["b", "#377eb8"], ["c", "#4daf4a"]],
            "fallback": "#984ea3"}}
        plan = plan_palette_profile_migration(paint, target_profile="print")
        if plan["applicable"]:
            assert plan["source_palette"] == "Set1"
            assert plan["target_palette"] != "Set1"

    def test_screen_target_keeps_palette_or_honest(self):
        # print_safe 源色带在 screen 目标下无需迁移（无强制义务）
        paint = {"circle-color": {"type": "interpolate", "stops": [
            [0, "#440154"], [50, "#21908c"], [100, "#fde725"]]}}  # Viridis
        plan = plan_palette_profile_migration(paint, target_profile="screen")
        if plan["applicable"]:
            assert plan["target_palette"]
        else:
            assert plan["disclosures"]

    def test_empty_paint_disclosed(self):
        plan = plan_palette_profile_migration({}, target_profile="print")
        assert plan["applicable"] is False
        assert plan["disclosures"]

    def test_patch_structure_preserved(self):
        paint = {
            "circle-color": {"type": "interpolate", "stops": [
                [0, "#eff3ff"], [100, "#08519c"]]},
            "circle-stroke-width": 2,
            "circle-opacity": 0.8,
        }
        plan = plan_palette_profile_migration(paint, target_profile="print")
        patch = plan["patch"]
        assert patch["circle-stroke-width"] == 2
        assert patch["circle-opacity"] == 0.8
        assert patch["circle-color"]["type"] == "interpolate"
        # 原 paint 不被改写（只建议，不执行）
        assert paint["circle-color"]["stops"][0][1] == "#eff3ff"
