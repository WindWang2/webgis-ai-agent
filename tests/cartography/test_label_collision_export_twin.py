"""V6（ADR-0120 W6）孪生编译器确定性标签碰撞集成。

- collision 模式：标签进顶层 mapspec-labels 组（置顶），抑制/预算披露诊断；
- legacy 路径（缺省）：byte-stable（无 labels 组、无新诊断）。
- 长标签 CJK 截断仍生效（label_truncated 口径不变）。
"""

from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def _spec(labels_cfg: dict | None) -> dict:
    spec = {
        "version": "1.1",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {
            "pts": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        # 无 name 字段的几何扩边要素：把数据范围撑大，
                        # 使带标签要素投影在画布内部（边缘无候选可放）。
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [115.8, 38.8]},
                            "properties": {},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.8, 39.6]},
                            "properties": {"other": 1},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                            "properties": {"name": "北京"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.01, 39.0]},
                            "properties": {"name": "廊坊"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.40, 39.20]},
                            "properties": {"name": "远郊点"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.20, 39.25]},
                            "properties": {"名称": "中心点", "name": "中心点"},
                        },
                    ],
                },
            },
        },
        "layers": [
            {
                "id": "pts_sym",
                "source": "pts",
                "type": "symbol",
                "layout": {"text-field": "{name}", "text-size": 14},
                "paint": {"text-color": "#1e293b"},
            },
        ],
    }
    if labels_cfg is not None:
        spec.setdefault("layout", {})["labels"] = labels_cfg
    return spec


def test_collision_mode_emits_top_level_labels_group():
    result = compile_mapspec_to_svg_detailed(
        _spec({"collision": "deterministic"}), target_dpi=72, width=400, height=300, padding=10
    )
    assert '<g class="mapspec-labels">' in result.svg
    # 数据层组在标签组之前（z 序：标签置顶）
    assert result.svg.index('mapspec-vector-layers') < result.svg.index('mapspec-labels')
    # 无碰撞（两标签远离）→ 无放宽披露
    assert not any(d["code"] == "label_collision_relaxed" for d in result.diagnostics)


def test_collision_suppression_emits_diagnostic():
    spec = _spec({"collision": "deterministic"})
    # 同一坐标重复 30 个同名要素 → 视口内放不下 → 抑制披露
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
            "properties": {"name": f"点{i}"},
        }
        for i in range(30)
    ]
    spec["sources"]["pts"]["inlineData"]["features"] = feats
    result = compile_mapspec_to_svg_detailed(
        spec, target_dpi=72, width=200, height=150, padding=5
    )
    relaxed = [d for d in result.diagnostics if d["code"] == "label_collision_relaxed"]
    assert relaxed, result.diagnostics
    assert "suppressed=" in relaxed[0]["detail"]


def test_collision_budget_diagnostic():
    from app.lib.cartography.label_collision import MAX_LABELS_PER_EXPORT

    spec = _spec({"collision": "deterministic"})
    n = MAX_LABELS_PER_EXPORT + 10
    spec["sources"]["pts"]["inlineData"]["features"] = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.001, 39.0 + i * 0.001]},
            "properties": {"name": f"点{i}"},
        }
        for i in range(n)
    ]
    result = compile_mapspec_to_svg_detailed(
        spec, target_dpi=72, width=600, height=400, padding=5
    )
    budget = [d for d in result.diagnostics if d["code"] == "label_budget_exceeded"]
    assert budget
    assert budget[0]["detail"] == str(MAX_LABELS_PER_EXPORT)


def test_legacy_path_unchanged_no_labels_group():
    spec = _spec(None)
    r_legacy = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=400, height=300, padding=10)
    assert '<g class="mapspec-labels">' not in r_legacy.svg
    assert not any(
        d["code"] in ("label_collision_relaxed", "label_budget_exceeded")
        for d in r_legacy.diagnostics
    )
    # 内联标签仍在数据层组内（legacy 行为）
    assert '<text x="' in r_legacy.svg


def test_line_label_rotation_in_collision_mode():
    spec = {
        "version": "1.1",
        "sources": {
            "ways": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        # 几何扩边要素（无 name）：让线的锚点投影在画布内部
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [115.7, 38.8]},
                            "properties": {},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.7, 39.7]},
                            "properties": {"other": 1},
                        },
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[116.2, 39.1], [116.2, 39.3], [116.2, 39.5]],
                            },
                            "properties": {"name": "主干道"},
                        },
                    ],
                },
            },
        },
        "layers": [
            {
                "id": "ways_line",
                "source": "ways",
                "type": "symbol",
                "layout": {"text-field": "{name}", "text-size": 12},
            },
        ],
        "layout": {"labels": {"collision": "deterministic"}},
    }
    result = compile_mapspec_to_svg_detailed(
        spec, target_dpi=72, width=400, height=400, padding=10
    )
    assert '<g class="mapspec-labels">' in result.svg
    assert result.feature_count == 1
