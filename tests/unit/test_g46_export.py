"""G46 导出投影回归：chart.aggregation 尊重 + 行截断/other 桶披露。

oracle：聚合期望值由 fixture 手算（不调用导出函数反推）。
"""
from __future__ import annotations

import pytest

from app.services.linked_exploration import contract
from app.services.linked_exploration.buckets import category_buckets
from app.services.linked_exploration.export import (
    _MAX_EXPORT_ROWS,
    _chart_payload_for,
    _inject_chart_components,
)


def _feat(props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [0, 0]},
        "properties": props,
    }


def _chart(**kw):
    defaults = dict(
        chart_id="ch1", kind="category", field="cat", aggregation="count")
    defaults.update(kw)
    return contract.ChartSpec(**defaults)


AGG_FEATURES = [
    _feat({"cat": "a", "pop": 10}),
    _feat({"cat": "a", "pop": 20}),
    _feat({"cat": "b", "pop": 5}),
]


class TestChartPayloadAggregation:
    @pytest.mark.parametrize("agg, expected", [
        ("sum", {"a": 30.0, "b": 5.0}),
        ("mean", {"a": 15.0, "b": 5.0}),
        ("min", {"a": 10.0, "b": 5.0}),
        ("max", {"a": 20.0, "b": 5.0}),
    ])
    def test_uses_declared_aggregation_not_count(self, agg, expected):
        chart = _chart(aggregation=agg, value_field="pop")
        stats = category_buckets(AGG_FEATURES, chart, where=None)
        payload = _chart_payload_for(stats, chart)
        assert payload["y_label"] == agg
        assert {p["name"]: p["value"] for p in payload["data"]} == expected
        # count 口径为 a=2, b=1 —— 聚合图不得退化成计数条形图
        assert expected != {"a": 2.0, "b": 1.0}

    def test_count_chart_stays_count(self):
        chart = _chart()
        stats = category_buckets(AGG_FEATURES, chart, where=None)
        payload = _chart_payload_for(stats, chart)
        assert payload["y_label"] == "count"
        assert {p["name"]: p["value"] for p in payload["data"]} == {
            "a": 2, "b": 1}


class TestChartRowTruncationDisclosure:
    @staticmethod
    def _stats():
        # 65 个类别 → 64 kept + other（第 65 个并入溢出桶）
        feats = [
            _feat({"cat": f"c{i:02d}", "pop": 100 - i}) for i in range(65)]
        chart = _chart()
        return chart, category_buckets(feats, chart, where=None)

    def test_other_bucket_retained_and_title_discloses(self):
        chart, stats = self._stats()
        payload = _chart_payload_for(stats, chart)
        assert len(payload["data"]) == _MAX_EXPORT_ROWS
        assert payload["data"][-1]["name"].startswith("other")
        assert payload["data"][-1]["value"] == 1
        assert "截断" in payload["title"]
        assert "65" in payload["title"] and "32" in payload["title"]

    def test_manifest_disclosure(self):
        chart, stats = self._stats()
        doc = {"layout": {"components": []}}
        preview = {"charts": [{
            "chart_id": chart.chart_id,
            "facet": {"statistics": stats},
        }]}
        disclosures = _inject_chart_components(doc, preview, [chart], "")
        assert len(disclosures) == 1
        d = disclosures[0]
        assert d["chart_id"] == chart.chart_id
        assert d["rows_total"] == 65
        assert d["rows_exported"] == _MAX_EXPORT_ROWS
        assert d["rows_dropped"] == 65 - _MAX_EXPORT_ROWS
        assert d["other_exported"] is True
        assert d["other_value_count"] == 1
        chart_opt = doc["layout"]["components"][0]["options"]["chart"]
        assert chart_opt["data"][-1]["name"].startswith("other")
