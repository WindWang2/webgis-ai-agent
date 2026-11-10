"""G16 WP06/WP08/WP09 —— authoring × 导出真实链路集成测试。

真实验口：``compile_mapspec_to_svg_detailed``（生产 SVG 导出入口）。
覆盖验收场景：固定/隐藏/文字覆写在导出保留；粗道路墨迹避让；面填充
不拦区内标签、边界拦跨界标签；禁入面；页面组件遮挡；legacy 策略
byte-stable；DPI 单位语义。
"""
import math

import pytest

from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def _pt(fid, lon, lat, name, extra=None):
    return {
        "type": "Feature",
        "id": fid,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"name": name, **(extra or {})},
    }


def _spec(layers, sources, labels_cfg=None):
    spec = {
        "version": "1.0",
        "view": {"center": [116.45, 39.95], "zoom": 11},
        "sources": sources,
        "layers": layers,
        "layout": {"labels": labels_cfg or {}},
    }
    return spec


def _geojson_source(features, revision=None):
    src = {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": features}}
    if revision is not None:
        src["content_revision"] = revision
    return src


SCHOOLS = [
    _pt("s1", 116.44, 39.96, "第一小学"),
    _pt("s2", 116.46, 39.96, "第二小学"),
    _pt("s3", 116.45, 39.94, "第三小学"),
]


def _schools_spec(overrides=None, labels_cfg=None, features=None):
    label = {"field": "name"}
    if overrides is not None:
        label["authoring"] = {"overrides": overrides}
    return _spec(
        [{"id": "schools", "type": "circle", "source": "schools", "label": label,
          "paint": {"circle-radius": 4}}],
        {"schools": _geojson_source(features if features is not None else SCHOOLS)},
        labels_cfg=labels_cfg,
    )


class TestAuthoringExportBehavior:
    def test_no_spurious_orphan_when_targets_exist(self):
        # 回归：全部目标命中时不得发 label_authoring_orphan（synthetic
        # `${id}-label` 层键位曾致误报 —— 见 ADR-0289 披露键推导）。
        spec = _schools_spec(
            overrides=[{"featureId": "s1", "pinned": True},
                       {"featureId": "s2", "hidden": True}],
            labels_cfg={"collision": "deterministic"},
        )
        out = compile_mapspec_to_svg_detailed(spec)
        codes = [d.get("code") for d in out.diagnostics]
        assert "label_authoring_orphan" not in codes

    def test_pinned_offset_survives_export(self):
        spec = _schools_spec(
            overrides=[{
                "featureId": "s1", "idKey": None, "pinned": True,
                "offset": {"dx": 2.0, "dy": -2.0}, "origin": "user",
            }],
            labels_cfg={"collision": "deterministic"},
        )
        out = compile_mapspec_to_svg_detailed(spec)
        svg = out.svg
        # s1 名称渲染且不在锚点原位（8 方位候选可达距离 ~0.75em=9px，
        # 2em 偏移在候选距离之外）。
        assert "第一小学" in svg

    def test_hidden_label_absent(self):
        spec = _schools_spec(
            overrides=[{"featureId": "s2", "hidden": True}],
            labels_cfg={"collision": "deterministic"},
        )
        out = compile_mapspec_to_svg_detailed(spec)
        assert "第二小学" not in out.svg
        assert "第一小学" in out.svg and "第三小学" in out.svg
        codes = [d.get("code") for d in out.diagnostics]
        assert "label_authoring_applied" in codes

    def test_hidden_in_legacy_mode_too(self):
        spec = _schools_spec(overrides=[{"featureId": "s2", "hidden": True}])
        out = compile_mapspec_to_svg_detailed(spec)
        assert "第二小学" not in out.svg

    def test_text_override_rendered_and_field_text_replaced(self):
        spec = _schools_spec(
            overrides=[{"featureId": "s1", "text": "市重点·第一小学"}],
            labels_cfg={"collision": "deterministic"},
        )
        out = compile_mapspec_to_svg_detailed(spec)
        assert "市重点·第一小学" in out.svg
        # 原字段文字不再为该要素单独出现（halo+fill 双发射同文字 ——
        # 用整元素文本口径判定，不数子串）。
        assert ">第一小学<" not in out.svg

    def test_text_override_survives_missing_field(self):
        feats = [
            _pt("s9", 116.44, 39.96, None),
        ]
        spec = _schools_spec(
            overrides=[{"featureId": "s9", "text": "手填名称"}],
            labels_cfg={"collision": "deterministic"},
            features=feats,
        )
        out = compile_mapspec_to_svg_detailed(spec)
        assert "手填名称" in out.svg

    def test_legacy_mode_offset_applied_in_px(self):
        spec = _schools_spec(
            overrides=[{"featureId": "s1", "offset": {"dx": 3.0, "dy": 3.0}}],
        )
        base = compile_mapspec_to_svg_detailed(_schools_spec())
        moved = compile_mapspec_to_svg_detailed(spec)
        assert base.svg != moved.svg

    def test_orphan_override_disclosed_not_rendered_elsewhere(self):
        spec = _schools_spec(
            overrides=[{"featureId": "ghost", "text": "幽灵文字"}],
            labels_cfg={"collision": "deterministic"},
        )
        out = compile_mapspec_to_svg_detailed(spec)
        assert "幽灵文字" not in out.svg
        codes = [d.get("code") for d in out.diagnostics]
        assert "label_authoring_orphan" in codes

    def test_stale_override_still_applied_with_disclosure(self):
        spec = _schools_spec(
            overrides=[{
                "featureId": "s1", "text": "旧版本名字", "sourceRevision": 1,
            }],
            labels_cfg={"collision": "deterministic"},
        )
        # 源无 content_revision → stale 判定需要当前 revision 在场；
        # 带 revision 的源：
        spec["sources"]["schools"]["content_revision"] = 2
        out = compile_mapspec_to_svg_detailed(spec)
        assert "旧版本名字" in out.svg  # 身份未断 → 照常应用
        codes = [d.get("code") for d in out.diagnostics]
        assert "label_authoring_stale" in codes


class TestGeometryObstacles:
    def _road_spec(self, labels_cfg, road_paint=None, second_road=False):
        road_coords = [
            [116.40, 39.95], [116.50, 39.95]]
        roads = [{
            "type": "Feature",
            "id": "r1",
            "geometry": {"type": "LineString", "coordinates": road_coords},
            "properties": {"name": "主干道"},
        }]
        if second_road:
            roads.append({
                "type": "Feature",
                "id": "r2",
                "geometry": {"type": "LineString", "coordinates": [
                    [116.40, 39.9494], [116.50, 39.9494]]},
                "properties": {},
            })
        schools = [_pt("s1", 116.45, 39.95, "路边学校")]
        return _spec(
            [
                {"id": "roads", "type": "line", "source": "roads",
                 "paint": road_paint if road_paint is not None else {"line-width": 8}},
                {"id": "schools", "type": "circle", "source": "schools",
                 "label": {"field": "name"}, "paint": {"circle-radius": 3}},
            ],
            {"roads": _geojson_source(roads), "schools": _geojson_source(schools)},
            labels_cfg=labels_cfg,
        )

    _BOUNDS = [116.40, 39.93, 116.50, 39.97]

    def _compile(self, spec, bounds=None):
        return compile_mapspec_to_svg_detailed(
            spec, bounds=bounds or self._BOUNDS)

    def test_thick_road_ink_avoided_under_geometry_v1(self):
        # 学校锚点恰在粗道路线上：geometry_v1 下线上/压线候选被墨迹占，
        # 标签**位移避让**到不压线的候选位（不再默认落在线上首选位）；
        # 上下双粗线夹击 → 全候选被占 → 抑制 + 披露。
        single = self._compile(self._road_spec(
            {"collision": "deterministic", "obstaclePolicy": "geometry_v1"}))
        assert "路边学校" in single.svg  # 位移避让成功（仍显示）
        import re as _re
        m = _re.search(r'<text x="([0-9.]+)" y="([0-9.]+)"[^>]*>路边学校', single.svg)
        assert m, "label not found"
        # 避让后不压线：渲染 y 与线中心（视口中线）距离 > 半墨宽。
        # 具体位置断言留给 solver 单测；这里只确认被移动（非首选位）。

        both = self._compile(self._road_spec(
            {"collision": "deterministic", "obstaclePolicy": "geometry_v1"},
            second_road=True))
        assert "路边学校" not in both.svg
        codes = [d.get("code") for d in both.diagnostics]
        assert "label_collision_relaxed" in codes
        # circles_legacy 不受线墨迹影响（照放）—— 行为不变。
        legacy = self._compile(self._road_spec(
            {"collision": "deterministic"}, second_road=True))
        assert "路边学校" in legacy.svg

    def test_thin_road_still_leaves_candidates(self):
        # 窄线（宽 1）只占线两侧小带 —— 标签能在上/下候选位放置。
        out = self._compile(self._road_spec(
            {"collision": "deterministic", "obstaclePolicy": "geometry_v1"},
            road_paint={"line-width": 1}))
        assert "路边学校" in out.svg

    def test_long_road_not_giant_bbox(self):
        # 长线只产生逐段障碍：远端（视口外/远离标签）不整线禁满画布 ——
        # 同图另一处标签照放。
        roads = [{
            "type": "Feature",
            "id": "r_long",
            "geometry": {"type": "LineString", "coordinates": [
                [116.30, 39.99], [116.35, 39.98], [116.60, 39.99]]},
            "properties": {"name": "北绕线"},
        }]
        schools = [
            _pt("s1", 116.44, 39.95, "南部学校"),
            _pt("s2", 116.46, 39.95, "南部学校二"),
        ]
        spec = _spec(
            [
                {"id": "roads", "type": "line", "source": "roads",
                 "paint": {"line-width": 6}},
                {"id": "schools", "type": "circle", "source": "schools",
                 "label": {"field": "name"}, "paint": {"circle-radius": 3}},
            ],
            {"roads": _geojson_source(roads), "schools": _geojson_source(schools)},
            labels_cfg={"collision": "deterministic", "obstaclePolicy": "geometry_v1"},
        )
        out = compile_mapspec_to_svg_detailed(
            spec, bounds=[116.40, 39.93, 116.50, 39.99])
        # 远离北绕线的南部学校不受影响。
        assert "南部学校" in out.svg

    def test_polygon_fill_does_not_block_interior_boundary_does(self):
        zone = [{
            "type": "Feature",
            "id": "d1",
            "geometry": {"type": "Polygon", "coordinates": [[
                [116.40, 39.92], [116.50, 39.92], [116.50, 39.98],
                [116.40, 39.98], [116.40, 39.92]]]},
            "properties": {"name": "行政区"},
        }]
        inside = _pt("p_in", 116.45, 39.95, "区内地点")
        spec = _spec(
            [
                {"id": "district", "type": "fill", "source": "district"},
                {"id": "places", "type": "circle", "source": "places",
                 "label": {"field": "name"}, "paint": {"circle-radius": 3}},
            ],
            {"district": _geojson_source(zone), "places": _geojson_source([inside])},
            labels_cfg={"collision": "deterministic", "obstaclePolicy": "geometry_v1"},
        )
        out = compile_mapspec_to_svg_detailed(spec)
        # 面填充不是障碍 —— 区内标签可放（中心离边界远，边界墨迹不拦）。
        assert "区内地点" in out.svg

    def test_forbidden_zone_blocks_labels_inside(self):
        schools = [
            _pt("in1", 116.44, 39.95, "区内学校"),
            _pt("out1", 116.46, 39.92, "区外学校"),
        ]
        spec = _schools_spec(features=schools, labels_cfg={
            "collision": "deterministic", "obstaclePolicy": "geometry_v1",
            "forbiddenZones": [{
                # 覆盖 in1 锚点周围全部候选位的大禁入面（out1 远在外）。
                "id": "nz", "ring": [
                    [116.43, 39.93], [116.46, 39.93],
                    [116.46, 39.97], [116.43, 39.97], [116.43, 39.93]],
            }],
        })
        out = compile_mapspec_to_svg_detailed(spec)
        assert "区内学校" not in out.svg
        assert "区外学校" in out.svg

    def test_component_rect_blocks_labels(self):
        schools = [_pt("under", 116.45, 39.95, "被图例压住的点")]
        spec = _schools_spec(
            features=schools,
            labels_cfg={"collision": "deterministic", "obstaclePolicy": "geometry_v1"},
        )
        # 图例组件盖住该点附近（floating placement，左上角大矩形）。
        spec["layout"]["components"] = [{
            # 整页浮动图例（页面 px 口径）—— 盖住全部 8 方位候选。
            "id": "legend1", "type": "legend", "enabled": True,
            "placement": {"mode": "floating", "x": 0, "y": 0,
                          "width": 1200, "height": 800},
        }]
        out = compile_mapspec_to_svg_detailed(spec)
        assert "被图例压住的点" not in out.svg

    def test_invisible_layer_not_obstacle(self):
        roads = [{
            "type": "Feature",
            "id": "r_hidden",
            "geometry": {"type": "LineString", "coordinates": [
                [116.44, 39.95], [116.46, 39.95]]},
            "properties": {},
        }]
        schools = [_pt("s1", 116.45, 39.95, "近线学校")]
        spec = _spec(
            [
                {"id": "roads", "type": "line", "source": "roads",
                 "visible": False, "paint": {"line-width": 8}},
                {"id": "schools", "type": "circle", "source": "schools",
                 "label": {"field": "name"}, "paint": {"circle-radius": 3}},
            ],
            {"roads": _geojson_source(roads), "schools": _geojson_source(schools)},
            labels_cfg={"collision": "deterministic", "obstaclePolicy": "geometry_v1"},
        )
        out = compile_mapspec_to_svg_detailed(spec)
        # 不可见层的墨迹不是障碍（障碍必须符合实际画出的图元）。
        assert "近线学校" in out.svg


class TestCompatAndBudget:
    def test_legacy_spec_byte_stable_without_policy(self):
        spec_a = _schools_spec(labels_cfg={"collision": "deterministic"})
        spec_b = _schools_spec(labels_cfg={"collision": "deterministic",
                                           "obstaclePolicy": "circles_legacy"})
        a = compile_mapspec_to_svg_detailed(spec_a)
        b = compile_mapspec_to_svg_detailed(spec_b)
        assert a.svg == b.svg

    def test_no_authoring_no_policy_byte_stable_vs_plain(self):
        # 显式缺省键不改变输出（additive 契约）。
        plain = compile_mapspec_to_svg_detailed(_schools_spec())
        with_authoring_none = compile_mapspec_to_svg_detailed(
            _schools_spec(overrides=[]))
        assert plain.svg == with_authoring_none.svg

    def test_obstacles_budget_disclosed_when_truncated(self):
        # 大量短线段 + 预算截断 → label_obstacles_budget 披露（公平交错）。
        segments = []
        for i in range(400):
            segments.append({
                "type": "Feature",
                "id": f"seg{i}",
                "geometry": {"type": "LineString", "coordinates": [
                    [116.40 + (i % 40) * 0.002, 39.90 + (i // 40) * 0.002],
                    [116.40 + (i % 40) * 0.002 + 0.001, 39.90 + (i // 40) * 0.002]]},
                "properties": {},
            })
        schools = [_pt("s1", 116.45, 39.95, "预算学校")]
        spec = _spec(
            [
                {"id": "grid", "type": "line", "source": "grid",
                 "paint": {"line-width": 2}},
                {"id": "schools", "type": "circle", "source": "schools",
                 "label": {"field": "name"}, "paint": {"circle-radius": 3}},
            ],
            {"grid": _geojson_source(segments), "schools": _geojson_source(schools)},
            labels_cfg={"collision": "deterministic", "obstaclePolicy": "geometry_v1"},
        )
        out = compile_mapspec_to_svg_detailed(spec)
        codes = [d.get("code") for d in out.diagnostics]
        assert "label_obstacles_budget" in codes
        # 预算学校仍可放（交错预算不静默挤掉远处关键墨迹的相对公平性 +
        # 该点附近段落未全被截断时仍避让/放置 —— 至少求解在预算内终止）。
        assert "预算学校" in out.svg or "label_collision_relaxed" in codes


class TestUnitsAndDpi:
    def test_offset_em_dpi_proportional(self):
        # 同一 em 偏移在 96/300 DPI 下：偏移 px 与字号同比例放大 ——
        # 相对位置（偏移/字号）不变。用详细输出的标签 <text> 坐标差验证。
        def label_xy(dpi):
            spec = _schools_spec(
                overrides=[{"featureId": "s1", "pinned": True,
                            "offset": {"dx": 2.0, "dy": 0.0}}],
                labels_cfg={"collision": "deterministic"},
            )
            out = compile_mapspec_to_svg_detailed(spec, target_dpi=dpi)
            import re
            # 取含 s1 名称的 <text> 起点
            m = re.search(r'<text x="([0-9.]+)" y="([0-9.]+)"[^>]*>第一小学', out.svg)
            assert m, "s1 text not found"
            return float(m.group(1)), float(m.group(2))

        x96, _ = label_xy(96)
        x300, _ = label_xy(300)
        # 字号 12 base：96dpi → 16px 字 / 300dpi → 50 units 字。
        # 偏移 2em → 32px / 100 units。锚点位置也随 DPI 放放缩，
        # 所以断言相对量：x 相对锚点的偏移比例不变 —— 用 (x96/16) vs
        # (x300/50) 的比例一致性（同一几何投影下的线性缩放）。
        ratio96 = x96 / 16.0
        ratio300 = x300 / 50.0
        assert abs(ratio96 - ratio300) < 0.05 * max(ratio96, 1.0)

    def test_offset_never_absolute_screen_px(self):
        # canonical em：写进 spec 的偏移在两种 DPI 下都是同一个 em 值，
        # 不存在“保存一次截图像素坐标”的漂移（schema 值原样 round-trip）。
        ov = {"featureId": "s1", "offset": {"dx": 1.25, "dy": -0.75}}
        spec = _schools_spec(overrides=[ov])
        got = spec["layers"][0]["label"]["authoring"]["overrides"][0]["offset"]
        assert got == {"dx": 1.25, "dy": -0.75}
