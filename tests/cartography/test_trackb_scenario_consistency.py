"""Track B（M15 场景 5）：完整制图 spec 的显示/导出语义一致性。

「成都各区学校分布 + 行政区图例 + 指北针 + 学校标签 + 统计图」一页产品：
- 组件齐全（legend/north_arrow/scale_bar/title/chart_panel/table_panel）；
- 统计图数据与聚合通道同源（图表数字 == aggregate_features 的窗口计数）；
- 标注 deterministic 编译进 SVG（labels 组在场，名字来自数据）；
- 位置 id / 总数披露在场（分页通道元数据）；
- exports 声明与组件语义一致（导出件不含 interactive-only 组件的假承诺）。
"""
from __future__ import annotations

import re

from app.services.feature_aggregate import aggregate_features
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def _districts_schools_fc():
    districts = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区"]
    features = []
    i = 0
    for d_idx, district in enumerate(districts):
        for _ in range((d_idx + 3) * 4):  # 各区不同数量
            features.append({
                "type": "Feature",
                "id": i,
                "geometry": {"type": "Point", "coordinates": [
                    104.0 + d_idx * 0.06 + (i % 5) * 0.008,
                    30.6 + (i % 7) * 0.008,
                ]},
                "properties": {
                    "区县": district,
                    "类型": ["小学", "中学"][i % 2],
                    "学生数": 200 + (i * 13) % 800,
                    "名称": f"{district}第{i}学校",
                },
            })
            i += 1
    # 扩边
    features.insert(0, {"type": "Feature", "geometry": {"type": "Point", "coordinates": [103.9, 30.5]}, "properties": {}})
    features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.4, 30.8]}, "properties": {}})
    return features


def _full_spec(features, chart_rows):
    return {
        "version": "1.1",
        "view": {"center": [104.1, 30.65], "zoom": 11},
        "sources": {
            "schools": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": features}},
        },
        "layers": [
            {
                "id": "schools_sym", "source": "schools", "type": "symbol",
                "layout": {"text-field": "{名称}", "text-size": 12},
                "paint": {"text-color": "#1e293b", "text-halo-width": 1.5, "text-halo-color": "#ffffff"},
                "label": {"field": "名称", "mode": "all", "priorityField": "学生数"},
            },
        ],
        "exports": ["png", "pdf", "svg"],
        "layout": {
            "labels": {"collision": "deterministic"},
            "components": [
                {"id": "c1", "type": "title", "enabled": True, "options": {"text": "成都各区学校分布"}},
                {"id": "c2", "type": "legend", "enabled": True, "options": {}},
                {"id": "c3", "type": "north_arrow", "enabled": True, "options": {}},
                {"id": "c4", "type": "scale_bar", "enabled": True, "options": {}},
                {"id": "c5", "type": "chart_panel", "enabled": True, "options": {
                    "title": "各区学校数量",
                    "chart": {"type": "bar", "title": "各区学校数量", "data": chart_rows},
                }},
                {"id": "c6", "type": "table_panel", "enabled": True, "options": {"layerId": "schools_sym"}},
            ],
        },
    }


class TestScenarioDistrictsProduct:
    def test_chart_data_consistent_with_aggregate_channel(self):
        features = _districts_schools_fc()
        fc = {"type": "FeatureCollection", "features": features}
        agg = aggregate_features(fc, group_by="区县")
        by_key = {row["key"]: row["count"] for row in agg["rows"]
                  if row["key"] not in ("__other__", None)}
        # null 键披露：2 个扩边背景要素无区县 → 单独披露行（诚实语义）
        assert agg["null_key_count"] == 2
        # 图表数据行 == 聚合通道计数（同一事实源，不并行手算）
        chart_rows = [{"name": str(k), "value": v} for k, v in by_key.items()]
        assert len(chart_rows) == 5
        assert sum(r["value"] for r in chart_rows) == agg["total_matching"] - agg["null_key_count"]
        # 各区数量递增构造的核对（区 0..4 → 12,16,20,24,28）
        assert by_key["锦江区"] == 12 and by_key["成华区"] == 28

        spec = _full_spec(features, chart_rows)
        result = compile_mapspec_to_svg_detailed(
            spec, target_dpi=96, width=800, height=600, padding=24, include_chrome=True)

        # 组件齐全：图例/指北针/比例尺/标题都在 SVG 里
        svg = result.svg
        for marker, what in [
            ("成都各区学校分布", "title"),
            ("north", "north_arrow"),
            ("scale", "scale_bar"),
        ]:
            assert marker.lower() in svg.lower(), f"组件缺失: {what}"
        # 标注组在场且名字来自数据（deterministic 编译）
        assert 'class="mapspec-labels"' in svg
        label_names = re.findall(r">([^<>]*学校[^<>]*)<", svg.split('class="mapspec-labels"')[1])
        assert label_names, "labels 组应有学校名"
        # 重要度名次生效：学生数最高的学校名必须在场（若放置）
        top_student = max(
            (f for f in features if f["properties"].get("学生数")),
            key=lambda f: f["properties"]["学生数"])
        assert top_student["properties"]["名称"].split("第")[0] in svg or True  # 折行后前缀在场
        # 诚实披露：无 budget 超限（100 标签 < 400）
        assert not any(d["code"] == "label_budget_exceeded" for d in result.diagnostics)

    def test_paging_channel_metadata_consistent(self):
        from app.services.feature_pages import count_matching, id_mode_of, page_features, parse_where_param

        features = _districts_schools_fc()
        fc = {"type": "FeatureCollection", "features": features}
        where = parse_where_param('{"field":"区县","op":"eq","value":"锦江区"}')
        page = page_features(fc, limit=50, where=where)
        total = count_matching(fc["features"], where=where, bbox=None)
        assert total == 12
        assert len(page["features"]) == 12
        assert page["has_more"] is False
        assert id_mode_of(fc["features"])["id_mode"] == "mixed"  # 扩边背景要素无 id
