"""F14 D7：live vs export 结构语义 golden corpus 闭环测试。

四组用例（设计文档 docs/dev/publication-export-parity-design.md §D7）：

1. **语料闭环**：逐 case 加载 JSON → ``compile_mapspec_to_svg_detailed``
   （include_chrome=True）→ ``extract_semantics`` → ``compare_semantics``
   → 断言无 ``missing`` / ``extra``（``ok=True``）。expected 面只收
   ``PUBLICATION_COMPONENT_TYPES`` 内的族（ABI 驱动）——colorbar/annotation/
   panel/披露族当前不在矩阵内，不进 expected，测试现在全绿；主 agent
   落地新族渲染器并矩阵置位后自动纳入。
2. **降级路径**：结构化 omitted 回执 → ``degraded`` 且 ``ok=True``；
   无回执 → ``missing`` 且 ``ok=False``。
3. **矩阵一致性**：``PUBLICATION_COMPONENT_TYPES`` ∪ 待落地新族共 18 个
   chrome 组件类型，逐类型最小可编译 spec → 产物出现对应 marker class。
   marker 尚未落地的类型记 ``xfail(strict, reason="publication renderer
   landing in F14 WP2")``——主 agent 落地后 XPASS 即报错，摘除标记转正。
4. **boundedness / 确定性**：双次抽取 deep-equal；病态 SVG →
   ``parse_ok=False`` 不抛异常。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.lib.cartography.component_renderers import PUBLICATION_COMPONENT_TYPES
from app.lib.cartography.export_semantic_corpus import (
    compare_semantics,
    expected_semantics,
    extract_semantics,
)
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "export_semantic_corpus"

# 空件 SVG（可解析、无任何 chrome marker）——降级/缺席路径的 actual。
EMPTY_SVG = '<svg xmlns="http://www.w3.org/2000/svg"></svg>'


def _case_paths() -> List[Path]:
    paths = sorted(FIXTURE_DIR.glob("case_*.json"))
    assert paths, "export_semantic_corpus 语料不得为空"
    return paths


def _load_case(path: Path) -> Dict[str, Any]:
    case = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(case.get("name"), str) and case["name"]
    assert isinstance(case.get("mapspec"), dict) and case["mapspec"]
    return case


def _compile_chrome_svg(mapspec: Dict[str, Any]) -> str:
    """确定性编译（固定 dpi/画幅；语料自带真实地理范围，免退化范围兜底）。"""
    return compile_mapspec_to_svg_detailed(
        mapspec, target_dpi=96, width=1200, height=800, padding=40,
        include_chrome=True,
    ).svg


# ── 1) 语料闭环 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("case_path", _case_paths(), ids=lambda p: p.stem)
def test_corpus_closed_loop_no_missing_no_extra(case_path: Path) -> None:
    """编译 → 抽取 → 比较闭环：expected 各族 verdict 不得为 missing/extra。"""
    case = _load_case(case_path)
    expected = expected_semantics(case["mapspec"])
    actual = extract_semantics(_compile_chrome_svg(case["mapspec"]))

    assert actual["parse_ok"] is True
    report = compare_semantics(expected, actual)

    assert report["parse_ok"] is True
    assert report["ok"] is True, f"parity report: {json.dumps(report, ensure_ascii=False)}"
    assert report["summary"]["missing"] == 0
    assert report["summary"]["extra"] == 0
    for entry in report["families"]:
        assert entry["verdict"] not in ("missing", "extra"), entry


def test_hidden_layer_never_leaks_into_export() -> None:
    """case_hidden_layer：隐藏层的几何/颜色出现在导出件 = extra = fail。

    当前编译器本就跳过 ``visible:False`` 图层 —— 本断言锁该回归。
    """
    case = _load_case(FIXTURE_DIR / "case_hidden_layer.json")
    expected = expected_semantics(case["mapspec"])
    actual = extract_semantics(_compile_chrome_svg(case["mapspec"]))
    report = compare_semantics(expected, actual)

    assert expected["hidden_layers"] == ["l2"]
    assert expected["hidden_layer_colors"] == ["#ff00ff"]
    assert "#ff00ff" not in actual["vector_layer_colors"]
    assert "l2" not in actual["layer_groups"]
    hidden = next(f for f in report["families"] if f["family"] == "hidden_layers")
    assert hidden["verdict"] == "match", hidden


def test_disabled_component_absent_from_expected_and_export() -> None:
    """case_disabled_component：disabled 组件不进 expected，产物亦无 marker。"""
    case = _load_case(FIXTURE_DIR / "case_disabled_component.json")
    mapspec = case["mapspec"]
    expected = expected_semantics(mapspec)
    actual = extract_semantics(_compile_chrome_svg(mapspec))

    assert "north_arrow" not in expected["chrome_families"]
    assert "north_arrow" not in actual["chrome_families"]
    assert "chrome-north-arrow" not in _compile_chrome_svg(mapspec)
    # 其余 enabled 族仍须在 expected（对照非空，防止静默吞族）
    assert "title" in expected["chrome_families"]
    assert "legend" in expected["chrome_families"]


def test_non_publication_families_never_enter_expected() -> None:
    """publication 真值外的族不得进 expected（ABI 驱动的核心不变量）。

    F14 WP2 集成后 colorbar/annotation/panel/披露族已入 publication 真值
    （渲染器落地 + 矩阵置位），本测试改锁仍然真值外的族（canvas-only/
    绑定面组件）—— 不变量本体不变：expected 面严格随矩阵派生。
    """
    panels = expected_semantics(_load_case(FIXTURE_DIR / "case_panels.json")["mapspec"])
    disclosures = expected_semantics(
        _load_case(FIXTURE_DIR / "case_disclosures.json")["mapspec"])
    colorbar = expected_semantics(_load_case(FIXTURE_DIR / "case_colorbar.json")["mapspec"])
    annotations = expected_semantics(
        _load_case(FIXTURE_DIR / "case_annotations.json")["mapspec"])

    for family in ("basemap", "export_layout", "label_layer"):
        assert family not in panels["chrome_families"]
        assert family not in disclosures["chrome_families"]
        assert family not in colorbar["chrome_families"]
        assert family not in annotations["chrome_families"]
        assert family not in PUBLICATION_COMPONENT_TYPES

    # WP2 转正面：panel/披露/色带/注记族现在进 expected（矩阵置位的存在性断言）
    for family in (
        "statistics_panel", "chart_panel", "table_panel",
        "methodology_note", "uncertainty_panel", "decision_panel",
        "continuous_colorbar", "annotation",
    ):
        assert family in PUBLICATION_COMPONENT_TYPES

    # panel 摘要仍诚实记录内联数据在场（kind / 行数期望 / has_data）
    by_kind = {p["kind"]: p for p in panels["panel_specs"]}
    assert by_kind["statistics"]["expected_rows"] == 3 and by_kind["statistics"]["has_data"]
    assert by_kind["chart"]["expected_rows"] == 3 and by_kind["chart"]["has_data"]
    assert by_kind["table"]["expected_rows"] == 3 and by_kind["table"]["has_data"]
    disc_by_kind = {p["kind"]: p for p in disclosures["panel_specs"]}
    assert disc_by_kind["methodology"]["expected_rows"] == 2
    assert disc_by_kind["decision"]["expected_rows"] == 3
    assert disc_by_kind["uncertainty"]["has_data"]


def test_no_chrome_case_has_empty_semantics() -> None:
    """case_no_chrome：components 缺席 → expected 空面、产物无 chrome 组、不炸。"""
    case = _load_case(FIXTURE_DIR / "case_no_chrome.json")
    expected = expected_semantics(case["mapspec"])
    svg = _compile_chrome_svg(case["mapspec"])

    assert expected["chrome_families"] == []
    assert expected["legend_entries"] == 0
    assert expected["hidden_layers"] == []
    assert "mapspec-chrome" not in svg
    actual = extract_semantics(svg)
    assert actual["parse_ok"] is True
    assert actual["chrome_families"] == []
    assert compare_semantics(expected, actual)["ok"] is True


def test_basic_chrome_expected_text_and_legend_counts() -> None:
    """case_basic_chrome：应然面文本/图例条目与实然面逐项对上。"""
    case = _load_case(FIXTURE_DIR / "case_basic_chrome.json")
    expected = expected_semantics(case["mapspec"])
    actual = extract_semantics(_compile_chrome_svg(case["mapspec"]))

    assert expected["title_text"] == "研究区专题图"
    assert expected["subtitle_text"] == "F14 语义语料 · 基础 chrome"
    assert expected["attribution_text"] == "© WebGIS AI Agent 语料"
    assert expected["legend_entries"] == 3
    # F14 WP2 集成：map_border 组件按 README 契约加回语料
    assert set(expected["chrome_families"]) == {
        "title", "subtitle", "north_arrow", "scale_bar",
        "legend", "attribution", "graticule", "map_border",
    }
    assert actual["title_text"] == expected["title_text"]
    assert actual["subtitle_text"] == expected["subtitle_text"]
    assert actual["attribution_text"] == expected["attribution_text"]
    assert actual["legend_entry_count"] == 3


# ── 2) 降级路径（诚实降级 vs 静默丢失）───────────────────────────────────


def _minimal_expected() -> Dict[str, Any]:
    return {
        "chrome_families": ["north_arrow", "legend"],
        "legend_entries": 3,
        "hidden_layers": [],
        "hidden_layer_colors": [],
        "family_components": {
            "north_arrow": ["c-north"],
            "legend": ["c-legend"],
        },
    }


def test_omitted_receipt_yields_degraded_and_ok() -> None:
    """expected 有、actual 缺 + 结构化回执 → degraded 且 ok=True（诚实降级）。"""
    report = compare_semantics(
        _minimal_expected(),
        extract_semantics(EMPTY_SVG),
        omitted_codes=[
            {"component_id": "c-north", "type": "north_arrow",
             "code": "publication_chrome_omitted"},
            {"component_id": "c-legend", "type": "categorical_legend",
             "code": "legend_spec_unavailable"},
        ],
    )
    verdicts = {f["family"]: f["verdict"] for f in report["families"]}
    assert verdicts["north_arrow"] == "degraded"
    assert verdicts["legend"] == "degraded"  # 图例族归并：categorical_legend 回执认领
    assert report["summary"]["degraded"] == 2
    assert report["summary"]["missing"] == 0
    assert report["ok"] is True


def test_missing_without_receipt_fails() -> None:
    """expected 有、actual 缺、无回执 → missing 且 ok=False（静默丢失 = fail）。"""
    report = compare_semantics(_minimal_expected(), extract_semantics(EMPTY_SVG))
    verdicts = {f["family"]: f["verdict"] for f in report["families"]}
    assert verdicts["north_arrow"] == "missing"
    assert verdicts["legend"] == "missing"
    assert report["ok"] is False


def test_receipt_type_mismatch_does_not_degrade() -> None:
    """回执 type 对不上族 → 不认领，仍 missing；component_id 不认领同理。"""
    report = compare_semantics(
        _minimal_expected(),
        extract_semantics(EMPTY_SVG),
        omitted_codes=[
            {"component_id": "c-north", "type": "scale_bar", "code": "wrong_family"},
            {"component_id": "unknown-id", "type": "legend", "code": "wrong_component"},
        ],
    )
    verdicts = {f["family"]: f["verdict"] for f in report["families"]}
    assert verdicts["north_arrow"] == "missing"
    assert verdicts["legend"] == "missing"
    assert report["ok"] is False


def test_legend_entry_count_drift_is_missing() -> None:
    """族在场但条目数漂移 = 静默丢失（missing），不是 match。"""
    actual = extract_semantics(_compile_chrome_svg(
        _load_case(FIXTURE_DIR / "case_basic_chrome.json")["mapspec"]))
    tampered = dict(_minimal_expected())
    tampered["legend_entries"] = 7  # 实然为 3
    report = compare_semantics(tampered, actual)
    verdicts = {f["family"]: f["verdict"] for f in report["families"]}
    assert verdicts["legend"] == "missing"
    entry = next(f for f in report["families"] if f["family"] == "legend")
    assert "legend_entry_count_mismatch" in entry["detail"]
    assert report["ok"] is False


def test_parse_failure_treats_all_expected_families_missing() -> None:
    """actual 解析失败 = 空抽取面：全部 expected 族 missing（除非有回执）。"""
    report = compare_semantics(_minimal_expected(), {"parse_ok": False})
    assert report["parse_ok"] is False
    assert all(f["verdict"] == "missing" for f in report["families"])
    assert report["ok"] is False


# ── 3) 矩阵一致性（18 chrome 组件类型 → marker class）────────────────────


def _matrix_mapspec(component_type: str) -> Dict[str, Any]:
    """单组件最小可编译 spec（组件 + 必要数据面）。

    基座：geojson 双点源 + circle 层（legend 型组件消费其 legend_spec；
    continuous_colorbar 额外绑 continuous legend_spec 双通道取数）。
    """
    legend_spec: Dict[str, Any] = {
        "type": "categorical",
        "title": "矩阵图例",
        "categories": [
            {"key": "a", "label": "甲", "color": "#b35806"},
            {"key": "b", "label": "乙", "color": "#2b70ab"},
        ],
    }
    options: Dict[str, Any] = {}
    if component_type == "title":
        options = {"text": "矩阵标题"}
    elif component_type == "subtitle":
        options = {"text": "矩阵副标题"}
    elif component_type == "legend":
        options = {"layerId": "l1"}
    elif component_type == "categorical_legend":
        options = {"layerId": "l1"}
    elif component_type == "continuous_colorbar":
        options = {
            "layerId": "l1",
            "min": 0,
            "max": 100,
            "unit": "m",
            "palette_colors": ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"],
        }
        legend_spec = {
            "type": "continuous",
            "title": "矩阵色带",
            "field": "v",
            "min": 0,
            "max": 100,
            "unit": "m",
            "palette_colors": ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"],
        }
    elif component_type == "annotation":
        options = {"text": "矩阵注记第一行\n矩阵注记第二行"}
    elif component_type == "attribution":
        options = {"text": "© 矩阵语料"}
    elif component_type == "statistics_panel":
        options = {"stats": {"items": [{"label": "均值", "value": 1.5}]}}
    elif component_type == "chart_panel":
        options = {"chart": {"kind": "bar", "points": [{"x": "甲", "y": 1.0}]}}
    elif component_type == "table_panel":
        options = {"table": {"columns": ["k"], "rows": [["a"]]}}
    elif component_type == "methodology_note":
        options = {"warnings": ["矩阵语料警告"]}
    elif component_type == "uncertainty_panel":
        options = {"uncertainty": {"confidence": 0.95}}
    elif component_type == "decision_panel":
        options = {"rows": [{"rank": 1, "name": "方案A", "score": 0.9, "vetoed": False}]}

    component: Dict[str, Any] = {
        "id": f"m-{component_type.replace('_', '-')}",
        "type": component_type,
        "enabled": True,
    }
    if options:
        component["options"] = options

    return {
        "version": "1.1",
        "sources": {
            "s1": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                         "properties": {"name": "a", "v": 8.0}},
                        {"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [116.44, 39.94]},
                         "properties": {"name": "b", "v": 92.0}},
                    ],
                },
            }
        },
        "layers": [
            {"id": "l1", "source": "s1", "type": "circle",
             "paint": {"circle-radius": 6, "circle-color": "#2171b5"},
             "legend_spec": legend_spec},
        ],
        "layout": {"components": [component]},
    }


#: (组件 type, 期望 chrome 族键, 期望 panel data-kind or None)
_MATRIX_CASES: List[Tuple[str, str, Optional[str]]] = [
    ("title", "title", None),
    ("subtitle", "subtitle", None),
    ("north_arrow", "north_arrow", None),
    ("scale_bar", "scale_bar", None),
    ("legend", "legend", None),
    ("categorical_legend", "legend", None),
    ("continuous_colorbar", "continuous_colorbar", None),
    ("annotation", "annotation", None),
    ("attribution", "attribution", None),
    ("statistics_panel", "statistics_panel", "statistics"),
    ("chart_panel", "chart_panel", "chart"),
    ("table_panel", "table_panel", "table"),
    ("methodology_note", "methodology_note", "methodology"),
    ("uncertainty_panel", "uncertainty_panel", "uncertainty"),
    ("decision_panel", "decision_panel", "decision"),
    ("graticule", "graticule", None),
    ("map_border", "map_border", None),
    ("inset_map", "inset_map", None),
]

#: 当前 publication 渲染分支**尚未落地**的族（marker class 缺席）：
#: F14 WP2 已全部落地（colorbar/annotation/panel/披露族渲染器 +
#: ``chrome-map-border`` 组包装）—— 集成时摘除全部 xfail 标记转正。
_NOT_YET_LANDED: set = set()


def _matrix_params() -> List[Any]:
    params: List[Any] = []
    for comp_type, _family, _kind in _MATRIX_CASES:
        if comp_type in _NOT_YET_LANDED:
            params.append(pytest.param(
                comp_type,
                marks=pytest.mark.xfail(
                    reason="publication renderer landing in F14 WP2", strict=True),
                id=f"matrix-{comp_type}",
            ))
        else:
            params.append(pytest.param(comp_type, id=f"matrix-{comp_type}"))
    return params


@pytest.mark.parametrize("component_type", _matrix_params())
def test_publication_matrix_marker_present(component_type: str) -> None:
    """逐类型最小 spec 编译后，产物必须出现该族 marker class。

    已落地 9 族（title/subtitle/north_arrow/scale_bar/legend/
    categorical_legend/attribution/graticule/inset_map）必须直接通过；
    其余 9 族 xfail(strict)（WP2 落地后自动转正）。
    """
    family = next(f for t, f, _k in _MATRIX_CASES if t == component_type)
    kind = next(k for t, _f, k in _MATRIX_CASES if t == component_type)

    mapspec = _matrix_mapspec(component_type)
    assert component_type in (
        PUBLICATION_COMPONENT_TYPES | set(_NOT_YET_LANDED)
    ), "矩阵用例必须覆盖 publication 真值或声明中的待落地族"

    svg = _compile_chrome_svg(mapspec)
    actual = extract_semantics(svg)
    assert actual["parse_ok"] is True
    assert family in actual["chrome_families"], (
        f"{component_type} 的 chrome marker 缺席: {actual['chrome_families']}"
    )
    if kind is not None:
        assert kind in actual["panel_kinds"], actual["panel_kinds"]

    # ABI 对账：已置 publication=True 的类型必须在 expected 面出现该族；
    # expected 与 actual 一致（同 spec 双面自洽）。
    expected = expected_semantics(mapspec)
    if component_type in PUBLICATION_COMPONENT_TYPES:
        assert family in expected["chrome_families"]
        assert compare_semantics(expected, actual)["ok"] is True


def test_matrix_covers_all_chrome_component_types() -> None:
    """矩阵用例必须覆盖 schema 全部 chrome 族（21 型 − 3 非 chrome 型 = 18）。"""
    from app.lib.cartography.mapspec_schema import COMPONENT_TYPES

    non_chrome = {"basemap", "export_layout", "label_layer"}
    covered = {t for t, _f, _k in _MATRIX_CASES}
    assert covered == set(COMPONENT_TYPES) - non_chrome
    assert len(_MATRIX_CASES) == 18


# ── 4) boundedness / 确定性 ─────────────────────────────────────────────


def test_extraction_is_deterministic() -> None:
    """同 SVG 双次抽取 deep-equal；比较亦 deterministic。"""
    svg = _compile_chrome_svg(
        _load_case(FIXTURE_DIR / "case_basic_chrome.json")["mapspec"])
    first = extract_semantics(svg)
    second = extract_semantics(svg)
    assert first == second
    expected = expected_semantics(_load_case(FIXTURE_DIR / "case_basic_chrome.json")["mapspec"])
    assert compare_semantics(expected, first) == compare_semantics(expected, second)
    # expected 面同样确定性
    assert expected == expected_semantics(_load_case(FIXTURE_DIR / "case_basic_chrome.json")["mapspec"])


def test_expected_semantics_is_json_serializable_and_bounded() -> None:
    """expected/extract/compare 输出全部可 JSON 序列化且 bounded。"""
    from app.lib.cartography.export_semantic_corpus import (
        MAX_FAMILIES,
        MAX_PANELS,
    )

    for name in ("case_basic_chrome", "case_panels", "case_disclosures",
                 "case_hidden_layer", "case_no_chrome"):
        case = _load_case(FIXTURE_DIR / f"{name}.json")
        expected = expected_semantics(case["mapspec"])
        assert len(expected["chrome_families"]) <= MAX_FAMILIES
        assert len(expected["panel_specs"]) <= MAX_PANELS
        json.dumps(expected, ensure_ascii=False)  # 不得抛

        actual = extract_semantics(_compile_chrome_svg(case["mapspec"]))
        json.dumps(actual, ensure_ascii=False)
        report = compare_semantics(expected, actual)
        json.dumps(report, ensure_ascii=False)
        assert len(report["families"]) <= MAX_FAMILIES


@pytest.mark.parametrize("bad_svg", ["<svg", "", None, 123, "<svg><unclosed></svg>"])
def test_malformed_svg_returns_parse_ok_false(bad_svg: Any) -> None:
    """病态输入 → parse_ok=False，绝不抛异常。"""
    result = extract_semantics(bad_svg)
    assert result == {"parse_ok": False}


def test_compare_against_parse_failure_is_bounded_honest() -> None:
    """解析失败的 actual 不产生 extra/崩坏；hidden 层同样按缺席面裁决。"""
    expected = expected_semantics(
        _load_case(FIXTURE_DIR / "case_hidden_layer.json")["mapspec"])
    report = compare_semantics(expected, {"parse_ok": False})
    assert report["parse_ok"] is False
    hidden = next(f for f in report["families"] if f["family"] == "hidden_layers")
    assert hidden["verdict"] == "match"  # 无产物 → 无泄漏（但 chrome 族全 missing）
    assert report["summary"]["extra"] == 0
    assert report["ok"] is False
