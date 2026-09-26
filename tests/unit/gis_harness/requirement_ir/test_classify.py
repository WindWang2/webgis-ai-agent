"""F02 五类意图分类单测（F02 DoD #1：query-only / analysis / map / edit /
export 五类互不误判）。

负例矩阵：每一类的代表话语必须在其余四类上不误判。双语、显式语气护栏
（只分析不画图）、时序 stages（先分析后制图）。全部确定性。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.requirement_ir import classify_task_kind
from app.services.gis_harness.requirement_ir.classify import classification_from_core
from app.services.gis_harness.intent import resolve_map_request_intent

# 五类的代表性话语 → 期望 kind
POSITIVE_CASES = [
    # query_only
    ("看看成都的大学", "query_only"),
    ("给我看一下这片区域", "query_only"),
    # analysis
    ("统计成都各区医院数量，不要图", "analysis"),
    ("分析一下这个片区的可达性", "analysis"),
    # map
    ("成都的小学分布情况，做一张图", "map"),
    ("做个热力图看看各区人口", "map"),
    # export
    ("把地图导出成PDF", "export"),
    ("export the map as png", "export"),
]

# 负例矩阵：query 不得被判成表中列出的错误 kind
MISJUDGE_GUARDS = [
    # (query, 不得误判为)
    ("只分析不画图，统计各区医院数量", "map"),
    ("只要分析结果，不需要图", "map"),
    ("改成各区统计", "export"),
    ("隐藏道路图层", "analysis"),
    ("先分析各区小学密度，然后再画图", "analysis"),   # stages 语义：最终 kind=map
    ("统计成都各区小学数量", "export"),
]


@pytest.mark.parametrize("query,expected", POSITIVE_CASES)
def test_positive_classification(query, expected):
    core = resolve_map_request_intent(query)
    result = classification_from_core(query, core)
    assert result.kind == expected, (
        f"{query!r} → {result.kind}（期望 {expected}）；"
        f"reasons={result.reason_codes} cues={result.cues}")


@pytest.mark.parametrize("query,forbidden", MISJUDGE_GUARDS)
def test_misjudge_guards(query, forbidden):
    core = resolve_map_request_intent(query)
    result = classification_from_core(query, core)
    assert result.kind != forbidden, (
        f"{query!r} 误判为 {forbidden}；reasons={result.reason_codes}")


class TestExplicitGuards:
    def test_analysis_only_suppresses_map(self):
        result = classify_task_kind(
            "只分析不画图，统计各区医院数量",
            core_task="administrative_statistic",
            analysis_intents=("administrative_summary",),
            output_intents=("map", "statistics"),
        )
        assert result.kind == "analysis"
        assert "no_map_suppression" in result.reason_codes

    def test_english_no_map(self):
        result = classify_task_kind(
            "statistics only, no map please",
            core_task="administrative_statistic",
            analysis_intents=("administrative_summary",),
            output_intents=("map",),
        )
        assert result.kind == "analysis"

    def test_stages_sequential(self):
        result = classify_task_kind(
            "先分析成都各区小学密度，然后再画图",
            core_task="administrative_statistic",
            analysis_intents=("analytical_density",),
            cartography_intents=(),
            output_intents=("map",),
        )
        assert result.stages == ("analysis", "map")
        assert result.kind == "map"


class TestEditRouting:
    def test_edit_requires_document_or_continuation(self):
        # 无文档且无续接语气：编辑 cue 不成立（回落语义分类）
        result = classify_task_kind("隐藏道路图层", has_document=False)
        assert result.kind != "edit"
        # 有文档 → edit
        result = classify_task_kind("隐藏道路图层", has_document=True)
        assert result.kind == "edit"

    def test_continuation_prefix_enables_edit(self):
        result = classify_task_kind("再导出PDF", has_document=False)
        assert result.kind == "edit"

    def test_edit_beats_export(self):
        # 「再导出PDF」是增量修订（往既有需求加导出义务），不是全新 export 任务
        result = classify_task_kind("再导出PDF", has_document=True)
        assert result.kind == "edit"
        assert "edit_route" in result.reason_codes

    def test_fresh_export_utterance(self):
        result = classify_task_kind(
            "把当前的图导出成出版用的PDF", has_document=False)
        assert result.kind == "export"
        assert any("publish" in code for code in result.reason_codes)


class TestDeterminism:
    def test_same_input_same_output(self):
        for query, _ in POSITIVE_CASES:
            core = resolve_map_request_intent(query)
            a = classification_from_core(query, core)
            b = classification_from_core(query, core)
            assert a == b
