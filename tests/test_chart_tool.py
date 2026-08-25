"""Tests for generate_chart tool"""
import json
from app.tools.chart import generate_chart


def test_bar_chart():
    data = json.dumps([{"name": "海淀", "value": 45}, {"name": "朝阳", "value": 38}])
    result = generate_chart(chart_type="bar", title="学校数量", data=data)
    assert "chart" in result
    chart = result["chart"]
    assert chart["type"] == "bar"
    assert chart["title"] == "学校数量"
    assert len(chart["data"]) == 2
    assert chart["data"][0]["name"] == "海淀"
    assert chart["data"][0]["value"] == 45


def test_pie_chart():
    data = json.dumps([{"name": "学校", "value": 30}, {"name": "医院", "value": 20}])
    result = generate_chart(chart_type="pie", title="POI分布", data=data)
    assert result["chart"]["type"] == "pie"
    assert len(result["chart"]["data"]) == 2


def test_scatter_chart():
    data = json.dumps([{"name": "A", "x": 1.5, "y": 3.2}, {"name": "B", "x": 2.1, "y": 4.8}])
    result = generate_chart(chart_type="scatter", title="分布", data=data)
    assert result["chart"]["type"] == "scatter"
    assert result["chart"]["data"][0]["x"] == 1.5


def test_line_chart():
    data = json.dumps([{"name": "1月", "value": 10}, {"name": "2月", "value": 20}])
    result = generate_chart(chart_type="line", title="趋势", data=data)
    assert result["chart"]["type"] == "line"


def test_optional_labels():
    data = json.dumps([{"name": "A", "value": 1}])
    result = generate_chart(chart_type="bar", title="T", data=data, x_label="X轴", y_label="Y轴")
    assert result["chart"]["x_label"] == "X轴"
    assert result["chart"]["y_label"] == "Y轴"


def test_invalid_chart_type():
    data = json.dumps([{"name": "A", "value": 1}])
    result = generate_chart(chart_type="radar", title="T", data=data)
    assert "error" in result


def test_invalid_data_json():
    result = generate_chart(chart_type="bar", title="T", data="not json")
    assert "error" in result


def test_empty_data():
    result = generate_chart(chart_type="bar", title="T", data="[]")
    assert "error" in result


# ─── GeoJSON / ref / 字段映射契约（2026-08-25 会话：LLM 传 ref + x_field/y_field 被旧契约拒绝）───

def _fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"ct_name": "武侯区", "primary_school_count": 88}},
            {"type": "Feature", "properties": {"ct_name": "锦江区", "primary_school_count": 62}},
        ],
    }


def test_geojson_fc_with_field_mapping():
    result = generate_chart(chart_type="bar", title="成都市各区县小学数量分布",
                            data=_fc(), x_field="ct_name", y_field="primary_school_count")
    assert "chart" in result, result
    assert result["chart"]["data"] == [
        {"name": "武侯区", "value": 88.0},
        {"name": "锦江区", "value": 62.0},
    ]


def test_geojson_scatter_mapping():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": "a", "pop": 100, "area": 5}},
            {"type": "Feature", "properties": {"name": "b", "pop": 300, "area": 9}},
        ],
    }
    result = generate_chart(chart_type="scatter", title="相关性", data=fc,
                            x_field="area", y_field="pop", name_field="name")
    assert "chart" in result, result
    assert result["chart"]["data"][0] == {"name": "a", "x": 5.0, "y": 100.0}


def test_type_alias():
    data = json.dumps([{"name": "A", "value": 1}])
    result = generate_chart(type="bar", title="T", data=data)
    assert "chart" in result
    assert result["chart"]["type"] == "bar"


def test_geojson_without_y_field_errors():
    result = generate_chart(chart_type="bar", title="T", data=_fc())
    assert "error" in result
    assert "y_field" in result["error"]


def test_geojson_non_numeric_value_errors():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "a", "n": "abc"}},
    ]}
    result = generate_chart(chart_type="bar", title="T", data=fc, y_field="n")
    assert "error" in result


def test_numeric_string_values_coerced():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "a", "n": "42"}},
    ]}
    result = generate_chart(chart_type="bar", title="T", data=fc, y_field="n")
    assert result["chart"]["data"][0]["value"] == 42.0


def test_unresolved_ref_string_errors():
    result = generate_chart(chart_type="bar", title="T", data="ref:geojson-doesnotexist")
    assert "error" in result
    assert "解引用" in result["error"]


def test_plain_records_with_field_mapping():
    """会话实录形态：裸记录数组（无 Feature 包装）+ 字段映射。"""
    records = [
        {"ct_name": "武侯区", "primary_school_count": 88},
        {"ct_name": "锦江区", "primary_school_count": 62},
    ]
    result = generate_chart(chart_type="bar", title="成都市各区县小学数量分布",
                            data=records, x_field="ct_name", y_field="primary_school_count")
    assert "chart" in result, result
    assert result["chart"]["data"] == [
        {"name": "武侯区", "value": 88.0},
        {"name": "锦江区", "value": 62.0},
    ]


def test_records_json_string_with_fields():
    result = generate_chart(chart_type="bar", title="T",
                            data='[{"name":"a","n":1},{"name":"b","n":2}]',
                            y_field="n")
    assert result["chart"]["data"][0] == {"name": "a", "value": 1.0}


def test_name_value_points_without_fields_unchanged():
    """不带映射字段的既有 {name,value} 形态维持原校验路径。"""
    result = generate_chart(chart_type="bar", title="T",
                            data=[{"name": "A", "value": 3}])
    assert result["chart"]["data"] == [{"name": "A", "value": 3}]
