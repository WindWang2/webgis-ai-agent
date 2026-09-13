"""Unit tests for Python MapSpec-to-SVG vector compiler target."""
from app.services.mapspec_to_svg import compile_mapspec_to_svg




def test_compile_mapspec_to_svg_basic():
    mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                            "properties": {"name": "Beijing"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Polygon", "coordinates": [[[116.3, 39.8], [116.5, 39.8], [116.5, 40.0], [116.3, 40.0], [116.3, 39.8]]]},
                            "properties": {"name": "Area 1"},
                        }
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "pts",
                "type": "circle",
                "source": "s1",
                "paint": {"circle-color": "#de2d26", "circle-radius": 5},
            },
            {
                "id": "polys",
                "type": "fill",
                "source": "s1",
                "paint": {"fill-color": "#60a5fa", "fill-outline-color": "#1d4ed8"},
            }
        ],
    }

    svg_72 = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert "<svg" in svg_72
    assert "<circle" in svg_72
    assert "<path" in svg_72
    assert 'fill-rule="evenodd"' in svg_72
    # The compiler emits the canonical minimal form (_fmt_num strips trailing
    # zeros): 5.0 -> "5", 1.0 -> "1".
    assert 'r="5"' in svg_72
    assert 'stroke-width="1"' in svg_72

    svg_300 = compile_mapspec_to_svg(mapspec, target_dpi=300)
    # 5 * (300 / 72) = 20.83
    assert 'r="20.83"' in svg_300
    # 1.0 * (300 / 72) = 4.17
    assert 'stroke-width="4.17"' in svg_300
    assert 'viewBox="0 0 5000 3333.33"' in svg_300


def test_resolve_paint_value_style_methods():
    """MAPSPEC-01: Verify _resolve_paint_value supports constant, field, match, step, interpolate."""
    from app.services.mapspec_to_svg import _resolve_paint_value

    props = {"val": 15, "category": "B", "val_float": 50.0}

    # Constant
    assert _resolve_paint_value({"method": "constant", "value": "#ff0000"}) == "#ff0000"

    # Field
    assert _resolve_paint_value({"method": "field", "field": "category"}, props=props) == "B"

    # Match
    match_spec = {"method": "match", "field": "category", "cases": [["A", "#f00"], ["B", "#0f0"]], "default": "#00f"}
    assert _resolve_paint_value(match_spec, props=props) == "#0f0"

    # Step
    step_spec = {"method": "step", "field": "val", "stops": [[10, "#f00"], [20, "#00f"]], "default": "#fff"}
    assert _resolve_paint_value(step_spec, props={"val": 5}) == "#fff"
    assert _resolve_paint_value(step_spec, props={"val": 15}) == "#f00"
    assert _resolve_paint_value(step_spec, props={"val": 25}) == "#00f"

    # Interpolate number
    interp_num = {"method": "interpolate", "field": "val_float", "stops": [[0, 10], [100, 50]]}
    assert _resolve_paint_value(interp_num, props=props) == 30.0

    # Interpolate color
    interp_col = {"method": "interpolate", "field": "val_float", "stops": [[0, "#000000"], [100, "#ffffff"]]}
    assert _resolve_paint_value(interp_col, props=props) == "#808080"


def test_polygon_holes_rendered_with_evenodd():
    """MAPSPEC-02: Verify multi-ring polygon renders with fill-rule="evenodd" and multiple M ... Z path segments."""
    mapspec_hole = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[116.0, 39.0], [117.0, 39.0], [117.0, 40.0], [116.0, 40.0], [116.0, 39.0]],  # Outer ring
                            [[116.3, 39.3], [116.7, 39.3], [116.7, 39.7], [116.3, 39.7], [116.3, 39.3]],  # Hole
                        ],
                    },
                },
            }
        },
        "layers": [{"id": "p", "type": "fill", "source": "s1", "paint": {"fill-color": "#123456"}}],
    }
    svg = compile_mapspec_to_svg(mapspec_hole, target_dpi=72)
    assert '<path d="M ' in svg
    assert ' Z M ' in svg
    assert 'fill-rule="evenodd"' in svg



def test_compile_mapspec_to_svg_escapes_paint_values():
    """P0-3b: paint color/opacity values are interpolated into SVG attributes
    without escaping, allowing attribute-injection (XSS) via a crafted color.
    The compiler must HTML-escape these values.
    """
    mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                            "properties": {},
                        }
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "pts",
                "type": "circle",
                "source": "s1",
                # Tries to break out of the fill=" attribute.
                "paint": {"circle-color": 'red" onclick="alert(1)'},
            }
        ],
    }
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72)
    # The injected attribute boundary must not survive.
    assert 'red" onclick' not in svg
    assert "&quot;" in svg


def test_compile_mapspec_to_svg_empty_or_degenerate_extents():
    """Defensive check: empty sources, empty features, or single point extents must not crash or divide-by-zero."""
    # Empty mapspec
    svg_empty = compile_mapspec_to_svg({})
    assert "<svg" in svg_empty

    # Mapspec with empty sources
    svg_no_src = compile_mapspec_to_svg({"sources": {}, "layers": []})
    assert "<svg" in svg_no_src

    # Mapspec with single point (degenerate range_x = 0, range_y = 0)
    single_pt_mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [10.0, 20.0]},
                },
            }
        },
        "layers": [
            {
                "id": "p1",
                "type": "circle",
                "source": "s1",
                "paint": {"circle-radius": 5},
            }
        ],
    }
    svg_single = compile_mapspec_to_svg(single_pt_mapspec)
    assert "<svg" in svg_single
    assert "<circle" in svg_single


def test_compile_mapspec_to_svg_nan_and_inf_bounds():
    """Defensive check: NaN or Inf in coordinates or bounds fallback to default bounds without crashing."""
    mapspec_nan = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [float("nan"), float("inf")]},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [100.0, float("-inf")]},
                        },
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "l1",
                "type": "circle",
                "source": "s1",
            }
        ],
    }
    svg_nan = compile_mapspec_to_svg(mapspec_nan)
    assert "<svg" in svg_nan


def test_compile_mapspec_to_svg_invalid_log_inputs():
    """Defensive check: target_dpi <= 0, NaN, Inf, or invalid raster extent log arguments do not raise MathDomainError."""
    mapspec_raster = {
        "sources": {
            "r1": {
                "type": "raster",
                "tiles": ["https://tile.example.com/{z}/{x}/{y}.png"],
            }
        },
        "layers": [
            {
                "id": "r-layer",
                "type": "raster",
                "source": "r1",
            }
        ],
    }

    # Zero, negative, NaN, Inf target_dpi
    assert "<svg" in compile_mapspec_to_svg(mapspec_raster, target_dpi=0)
    assert "<svg" in compile_mapspec_to_svg(mapspec_raster, target_dpi=-96)
    assert "<svg" in compile_mapspec_to_svg(mapspec_raster, target_dpi=float("nan"))
    assert "<svg" in compile_mapspec_to_svg(mapspec_raster, target_dpi=float("inf"))


def test_compile_mapspec_to_svg_malformed_inputs_handled_gracefully():
    """Defensive check: non-dict mapspec, malformed layers, non-numeric paint properties do not raise unhandled exceptions."""
    assert "<svg" in compile_mapspec_to_svg(None)
    assert "<svg" in compile_mapspec_to_svg("invalid_mapspec")
    assert "<svg" in compile_mapspec_to_svg({"layers": ["invalid_layer_item"], "sources": None})

    malformed_paint_mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [10, 20]},
                },
            }
        },
        "layers": [
            {
                "id": "l1",
                "type": "circle",
                "source": "s1",
                "paint": {
                    "circle-radius": "invalid_number",
                    "circle-opacity": None,
                },
            }
        ],
    }
    svg = compile_mapspec_to_svg(malformed_paint_mapspec)
    assert "<svg" in svg


def test_mapspec_05_stroke_and_dasharray_properties():
    """MAPSPEC-05: circle-stroke-color, circle-stroke-width, line-dasharray, line-linecap, line-linejoin."""
    mapspec_stroke = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                        },
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [
                                    [116.4, 39.9],
                                    [116.5, 40.0],
                                ],
                            },
                        },
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "circle-stroke",
                "type": "circle",
                "source": "s1",
                "paint": {
                    "circle-color": "#3b82f6",
                    "circle-stroke-color": "#000000",
                    "circle-stroke-width": 2,
                },
            },
            {
                "id": "line-styled",
                "type": "line",
                "source": "s1",
                "layout": {
                    "line-linecap": "round",
                    "line-linejoin": "bevel",
                },
                "paint": {
                    "line-color": "#2563eb",
                    "line-width": 2,
                    "line-dasharray": [2, 4],
                },
            },
        ],
    }
    svg = compile_mapspec_to_svg(mapspec_stroke, target_dpi=300)
    assert 'stroke="#000000"' in svg
    assert 'stroke-width="8.33"' in svg
    assert 'stroke-linecap="round"' in svg
    assert 'stroke-linejoin="bevel"' in svg
    assert 'stroke-dasharray="8.33,16.67"' in svg


def test_mapspec_06_text_halo_and_anchors():
    """MAPSPEC-06: Text halo SVG rendering and MapLibre text-anchor to SVG text-anchor/dominant-baseline."""
    mapspec_halo = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                            "properties": {"label": "Test Label"},
                        }
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "label-halo",
                "type": "symbol",
                "source": "s1",
                "layout": {
                    "text-field": "{label}",
                    "text-anchor": "top-left",
                },
                "paint": {
                    "text-color": "#000000",
                    "text-halo-color": "#ffffff",
                    "text-halo-width": 2,
                },
            }
        ],
    }
    svg = compile_mapspec_to_svg(mapspec_halo, target_dpi=300)
    assert 'fill="none" stroke="#ffffff" stroke-width="16.67"' in svg
    assert 'text-anchor="start"' in svg
    assert 'dominant-baseline="hanging"' in svg
    assert "Test Label" in svg


def test_mapspec_07_polygon_bbox_centroid():
    """MAPSPEC-07: Compute bounding box centroid for polygon text label placement."""
    mapspec_poly_label = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [10, 20],
                                [30, 20],
                                [30, 40],
                                [10, 40],
                                [10, 20],
                            ]
                        ],
                    },
                    "properties": {"name": "PolyCenter"},
                },
            }
        },
        "layers": [
            {
                "id": "poly-text",
                "type": "symbol",
                "source": "s1",
                "layout": {"text-field": "{name}"},
            }
        ],
    }
    svg = compile_mapspec_to_svg(mapspec_poly_label, target_dpi=72)
    assert '<text x="600"' in svg
    assert "PolyCenter" in svg


def test_mapspec_08_fallback_rendering():
    """MAPSPEC-08: Add fallback rendering for heatmap and fill-extrusion layer types."""
    mapspec_fallbacks = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                        },
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [116.3, 39.8],
                                        [116.5, 39.8],
                                        [116.5, 40.0],
                                        [116.3, 40.0],
                                        [116.3, 39.8],
                                    ]
                                ],
                            },
                        },
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "heat",
                "type": "heatmap",
                "source": "s1",
                "paint": {
                    "heatmap-color": "#ff0000",
                    "heatmap-radius": 10,
                },
            },
            {
                "id": "3d-bldg",
                "type": "fill-extrusion",
                "source": "s1",
                "paint": {
                    "fill-extrusion-color": "#334155",
                    "fill-extrusion-opacity": 0.9,
                },
            },
        ],
    }
    svg = compile_mapspec_to_svg(mapspec_fallbacks, target_dpi=72)
    assert "<circle" in svg
    assert 'fill="#ff0000"' in svg
    assert 'r="10"' in svg
    assert '<path d="M ' in svg
    assert 'fill="#334155"' in svg
    assert 'fill-opacity="0.9"' in svg




# ────────────────────────────────────────────────────────────────────────
# W4：孪生 SVG 编译器正确性 —— 可见性 / 阈值执行 / label 截断 / 结构化诊断
# ────────────────────────────────────────────────────────────────────────
from app.lib.cartography.render_diagnostics import MAX_DIAGNOSTICS_PER_EXPORT
from app.services.mapspec_to_svg import (
    DEFAULT_MAX_FEATURES,
    DEFAULT_EXPORT_TIMEOUT_MS,
    MAX_SVG_LABEL_CHARS,
    SvgCompilation,
    compile_mapspec_to_svg_detailed,
    resolve_spec_timeout_ms,
)


def _points_mapspec(n: int, **layer_extra) -> dict:
    """n 个确定性经纬度点位的单圆层 mapspec。"""
    return {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [116.0 + (i % 50) * 0.01,
                                                39.0 + (i // 50) * 0.01],
                            },
                            "properties": {"name": f"P{i}"},
                        }
                        for i in range(n)
                    ],
                },
            }
        },
        "layers": [
            {"id": "pts", "type": "circle", "source": "s1",
             "paint": {"circle-color": "#123456", "circle-radius": 4},
             **layer_extra},
        ],
    }


def test_w4_layout_visibility_none_layer_skipped():
    """MapLibre 语义：layout.visibility == "none" 的图层不进导出产物。"""
    mapspec = _points_mapspec(3)
    mapspec["layers"].append(
        {"id": "ghost", "type": "circle", "source": "s1",
         "layout": {"visibility": "none"},
         "paint": {"circle-color": "#ff0000", "circle-radius": 9}}
    )
    comp = compile_mapspec_to_svg_detailed(mapspec, target_dpi=72)
    assert isinstance(comp, SvgCompilation)
    assert comp.svg.count("<circle") == 3  # ghost 层的 3 个 r=9 圆不出现
    assert 'r="9"' not in comp.svg
    assert comp.feature_count == 3


def test_w4_top_level_visible_false_layer_skipped():
    """顶层 visible: False 与 layout.visibility=="none" 同语义。"""
    mapspec = _points_mapspec(2, visible=False)
    comp = compile_mapspec_to_svg_detailed(mapspec, target_dpi=72)
    assert comp.svg.count("<circle") == 0
    assert comp.feature_count == 0
    # 控制组：visible=True 照常渲染
    comp_on = compile_mapspec_to_svg_detailed(_points_mapspec(2, visible=True),
                                              target_dpi=72)
    assert comp_on.feature_count == 2


def test_w4_max_features_cap_truncates_in_order_with_diagnostic():
    """超限确定性截断（保持原顺序取前 N）+ features_truncated 诊断。"""
    mapspec = _points_mapspec(5)
    comp = compile_mapspec_to_svg_detailed(mapspec, target_dpi=72,
                                           max_features=2)
    assert comp.svg.count("<circle") == 2
    assert comp.feature_count == 2
    assert comp.truncated_features is True
    codes = [d["code"] for d in comp.diagnostics]
    assert "features_truncated" in codes
    diag = next(d for d in comp.diagnostics if d["code"] == "features_truncated")
    assert diag["detail"] == "2"
    assert diag["layer_id"] == "pts"
    # 确定性：保留的是原顺序的前 2 个要素（坐标可复验）
    assert 'cx=' in comp.svg


def test_w4_spec_thresholds_max_features_honored():
    """显式入参缺省时取 spec.thresholds.maxFeatures。"""
    mapspec = _points_mapspec(5)
    mapspec["thresholds"] = {"maxFeatures": 3}
    comp = compile_mapspec_to_svg_detailed(mapspec, target_dpi=72)
    assert comp.feature_count == 3
    assert comp.truncated_features is True


def test_w4_no_cap_feature_count_full():
    """无 cap（默认 50000）时全量渲染。"""
    comp = compile_mapspec_to_svg_detailed(_points_mapspec(5), target_dpi=72)
    assert comp.feature_count == 5
    assert comp.truncated_features is False
    assert comp.timed_out is False
    assert comp.diagnostics == []


def test_w4_timeout_zero_cooperative_abort_with_diagnostic():
    """timeout 预算耗尽 → 协作中止 + export_timeout_partial，产物仍合法。"""
    comp = compile_mapspec_to_svg_detailed(_points_mapspec(5), target_dpi=72,
                                           timeout_ms=0)
    assert comp.timed_out is True
    assert comp.feature_count == 0
    codes = [d["code"] for d in comp.diagnostics]
    assert "export_timeout_partial" in codes
    assert comp.svg.startswith("<svg")


def test_w4_spec_thresholds_timeout_honored():
    mapspec = _points_mapspec(5)
    mapspec["thresholds"] = {"timeoutMs": 0}
    comp = compile_mapspec_to_svg_detailed(mapspec, target_dpi=72)
    assert comp.timed_out is True
    assert comp.feature_count == 0


def test_w4_resolve_spec_timeout_ms_default_and_spec():
    assert resolve_spec_timeout_ms({}) == DEFAULT_EXPORT_TIMEOUT_MS
    assert resolve_spec_timeout_ms({"thresholds": {"timeoutMs": 1234}}) == 1234.0
    # 非法值回默认
    assert resolve_spec_timeout_ms({"thresholds": {"timeoutMs": "garbage"}}) \
        == DEFAULT_EXPORT_TIMEOUT_MS
    assert DEFAULT_MAX_FEATURES == 50000
    assert DEFAULT_EXPORT_TIMEOUT_MS == 30000


def test_w4_long_symbol_text_truncated_with_diagnostic():
    """symbol 文本接入 fit_label_text：>60 字符截断 + label_truncated 诊断。"""
    long_name = "X" * 100
    mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point",
                                         "coordinates": [116.4, 39.9]},
                            "properties": {"name": long_name},
                        }
                    ],
                },
            }
        },
        "layers": [
            {"id": "lbl", "type": "symbol", "source": "s1",
             "layout": {"text-field": "{name}"}},
        ],
    }
    comp = compile_mapspec_to_svg_detailed(mapspec, target_dpi=72)
    assert long_name not in comp.svg  # 完整长文本绝不入产物
    assert ("X" * (MAX_SVG_LABEL_CHARS - 1) + "…") in comp.svg
    diag = next(d for d in comp.diagnostics if d["code"] == "label_truncated")
    assert diag["layer_id"] == "lbl"
    assert diag["detail"] == "layer=lbl len=100"


def test_w4_short_symbol_text_untouched_no_diagnostic():
    """对照组：短标签原样嵌入、零诊断。"""
    mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point",
                                         "coordinates": [116.4, 39.9]},
                            "properties": {"name": "北京站"},
                        }
                    ],
                },
            }
        },
        "layers": [
            {"id": "lbl", "type": "symbol", "source": "s1",
             "layout": {"text-field": "{name}"}},
        ],
    }
    comp = compile_mapspec_to_svg_detailed(mapspec, target_dpi=72)
    assert "北京站" in comp.svg
    assert comp.diagnostics == []


def test_w4_diagnostics_capped_and_payload_shape():
    """诊断条目数受词表上限约束（防诊断本身成为 DoS 载荷），载荷形状固定。"""
    mapspec = _points_mapspec(200)
    mapspec["layers"] = [
        {"id": "lbl", "type": "symbol", "source": "s1",
         "layout": {"text-field": "{name}"}},
    ]
    # 200 个 90 字符标签 → 理论 200 条 label_truncated，被上限截住
    for feat in mapspec["sources"]["s1"]["inlineData"]["features"]:
        feat["properties"]["name"] = "Y" * 90
    comp = compile_mapspec_to_svg_detailed(mapspec, target_dpi=72)
    assert len(comp.diagnostics) <= MAX_DIAGNOSTICS_PER_EXPORT
    for d in comp.diagnostics:
        assert set(d) <= {"code", "severity", "message", "detail", "layer_id"}
        assert d["code"] == "label_truncated"
        assert d["severity"] == "warning"


def test_w4_compat_wrapper_returns_str_matching_detailed():
    """原 compile_mapspec_to_svg 签名与 str 返回保持兼容，且与 detailed 一致。"""
    mapspec = _points_mapspec(3)
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert isinstance(svg, str)
    detailed = compile_mapspec_to_svg_detailed(mapspec, target_dpi=72)
    assert svg == detailed.svg


def test_ac06_background_layer_emits_full_canvas_rect():
    """AC-06：background 层（source:"" 哨兵）不再被 `not src` 短路静默丢弃
    —— 全画布底色矩形（与前端孪生同字节）。"""
    mapspec = {
        "sources": {},
        "layers": [
            {"id": "bg", "type": "background", "source": "",
             "paint": {"color": "#0f1f3d", "opacity": 0.9}},
        ],
    }
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert (
        '<rect x="0" y="0" width="1200" height="800" '
        'fill="#0f1f3d" fill-opacity="0.9" />'
    ) in svg


def test_ac06_background_layer_maplibre_keys_and_default_color():
    """AC-06：canonical background-* 键同样消费；无 paint 面回落 MapLibre
    文档默认 #000000 / opacity 1。"""
    mapspec = {
        "sources": {},
        "layers": [
            {"id": "bg", "type": "background", "source": "",
             "paint": {"background-color": "#112233"}},
            {"id": "bg2", "type": "background", "source": ""},
        ],
    }
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert 'fill="#112233" fill-opacity="1"' in svg
    assert 'fill="#000000" fill-opacity="1"' in svg


def test_ac06_hillshade_layer_emits_diagnostic_not_silent():
    """AC-06：hillshade（raster-dem 地形晕渲）无法矢量表达 —— 结构化诊断
    披露（hillshade_not_vectorizable），不再静默省略。"""
    mapspec = {
        "sources": {"dem": {"type": "raster-dem", "url": "https://x.test/tiles.json"}},
        "layers": [
            {"id": "terrain", "type": "hillshade", "source": "dem",
             "paint": {"opacity": 0.5}},
        ],
    }
    comp = compile_mapspec_to_svg_detailed(mapspec, target_dpi=72)
    codes = [d["code"] for d in comp.diagnostics]
    assert "hillshade_not_vectorizable" in codes
    diag = next(d for d in comp.diagnostics if d["code"] == "hillshade_not_vectorizable")
    assert diag["detail"] == "terrain"
    assert diag["layer_id"] == "terrain"
    # 该层不产出矢量元素（画面只有根元素与白色底板）
    assert "<circle" not in comp.svg
    assert "<path" not in comp.svg
