"""Tests for generate_chart tool"""
import json
import pytest
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
