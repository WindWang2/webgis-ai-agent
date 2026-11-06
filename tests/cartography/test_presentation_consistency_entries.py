"""W2.3（ADR-0283 D3）：跨入口 presentation（图例推导）一致性测试族。

同一数据 + 同一分类方法 + 同一 palette，走三条生产入口产出图例推导：

- E1 lib 单源：``thematic_spec.build_graduated_spec``（canonical legend_spec，
  paint 与 legend 的共同上游）；
- E2 legacy service 对：``CartographyService.build_thematic_style`` →
  ``build_legend_spec``（classify 经 ``CartographyService.classify``）；
- E3 生产工具：``create_thematic_map``（app/tools/cartography.py —— "classify
  exactly once" 不变量：style_def 从 legend_spec 合成，无双分类）。

最终统一经 ``render_scene.derive_legend_items``（legend 单源，本文件不重复
test_legend_model_parity 的 golden 语义锁定，也不重复
test_symbology_consistency_matrix 的决策一致性矩阵）断言：区间数/断点值/
色值跨入口一致；nodata/unit 的既有入口差异**显式锁定**（差异源头有文档，
漂移即红灯），不被静默容忍。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.palettes import resolve_palette_colors
from app.lib.cartography.render_scene import derive_legend_items
from app.lib.cartography.thematic_spec import (
    NODATA_DEFAULT,
    build_categorical_spec,
    build_graduated_spec,
)
from app.services.cartography_service import CartographyService

pytestmark = pytest.mark.cartography

FIELD = "pop"
CAT_FIELD = "kind"
METHOD = "quantiles"
K = 4
PALETTE = "YlOrRd"
UNIT = "人"

# 中等偏斜、无重尾（不触发 clip_p99 裁剪路径 —— 裁剪差异属
# test_symbology_consistency_matrix 的决策域，不在本文件重复）。
VALUES = [10.0, 12.0, 14.0, 11.0, 13.0, 15.0, 12.0, 14.0, 16.0, 13.0,
          11.0, 15.0, 14.0, 12.0, 17.0, 16.0]
CATEGORIES = ["school", "hospital", "school", "park", "hospital", "school"]


def _fc() -> dict:
    features = [
        {"type": "Feature", "properties": {FIELD: v, CAT_FIELD: CATEGORIES[i % len(CATEGORIES)]},
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}}
        for i, v in enumerate(VALUES)
    ]
    return {"type": "FeatureCollection", "features": features}


def _entry_e1(fc) -> dict:
    return build_graduated_spec(fc, FIELD, method=METHOD, k=K, palette=PALETTE, unit=UNIT)


def _entry_e2(fc):
    style = CartographyService.build_thematic_style(fc, FIELD, method=METHOD, k=K, palette=PALETTE)
    assert style is not None
    legend = CartographyService.build_legend_spec(style, palette=PALETTE)
    assert legend is not None
    return style, legend


def _entry_e3(fc) -> dict:
    from app.tools.cartography import register_cartography_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_cartography_tools(reg)
    res = reg._tools["create_thematic_map"](
        geojson=fc, field=FIELD, method=METHOD, k=K, palette=PALETTE, unit=UNIT,
    )
    assert "error" not in res, res
    legend = res.get("legend_spec")
    assert isinstance(legend, dict), res.keys()
    return res, legend


class TestGraduatedCrossEntryConsistency:
    def test_breaks_interval_count_and_colors_identical(self):
        fc = _fc()
        e1 = _entry_e1(fc)
        _style2, e2 = _entry_e2(fc)
        _res3, e3 = _entry_e3(fc)

        # 断点值 / 区间数 / 类色：三入口逐值一致（同一 classify_values +
        # resolve_thematic_colors 单源；build_thematic_style 与
        # build_graduated_spec 的值收集口径同为 finite-number 过滤）
        assert e1["breaks"] == e2["breaks"] == e3["breaks"]
        assert len(e1["breaks"]) - 1 == K
        assert e1["palette_colors"] == e2["palette_colors"] == e3["palette_colors"]
        assert len(e1["palette_colors"]) == K

    def test_tool_style_view_synthesized_from_legend_spec_no_double_classify(self):
        fc = _fc()
        res3, e3 = _entry_e3(fc)
        style = res3["style"]
        # app/tools/cartography.py:575-582 "classify exactly once"：legacy
        # style 视图从 legend_spec 合成 —— breaks/labels 必须逐值同源。
        assert style["type"] == "choropleth"
        assert style["breaks"] == e3["breaks"]
        assert style["colors"] == e3["palette_colors"]
        assert style["legend_labels"] == e3["labels"]

    def test_label_format_shared_by_both_builders(self):
        fc = _fc()
        e1 = _entry_e1(fc)
        style2, e2 = _entry_e2(fc)
        # thematic_spec._graduated_label 与 build_thematic_style 的
        # legend_labels 同式（f"{lo:.2f} - {hi:.2f}"）—— 标签文本一致
        assert e1["labels"] == style2["legend_labels"]

    def test_derived_models_colors_and_counts_consistent(self):
        fc = _fc()
        e1 = _entry_e1(fc)
        _style2, e2 = _entry_e2(fc)
        _res3, e3 = _entry_e3(fc)

        m1, m2, m3 = (derive_legend_items(s) for s in (e1, e2, e3))
        assert m1 and m2 and m3
        # 类条目（排除 nodata 尾条）色值跨入口一致
        c1 = [e["color"] for e in m1["entries"] if e["kind"] == "class"]
        c2 = [e["color"] for e in m2["entries"] if e["kind"] == "class"]
        c3 = [e["color"] for e in m3["entries"] if e["kind"] == "class"]
        assert c1 == c2 == c3 == e1["palette_colors"]
        # E1/E3 携带 labels 覆盖（_graduated_label 同式）→ 条目文本一致；
        # E2 无 labels 键 → derive 按断点现算（format_legend_value 分组口径），
        # 区间端点同源但数字格式不同 —— 见 test_label_format_divergence_locked
        assert [e["label"] for e in m1["entries"] if e["kind"] == "class"] == [
            e["label"] for e in m3["entries"] if e["kind"] == "class"
        ] == e1["labels"]

    def test_label_format_divergence_locked(self):
        """E2 无 labels 键 → derive 用 format_legend_value（zh-CN 分组、无
        定点两位）；E1/E3 用 _graduated_label（.2f 定点）。差异源头：
        thematic_spec.py:786 与 render_scene.format_legend_value —— 显式锁定
        （格式差异，断点同源），漂移（任一侧改格式）即红灯。"""
        fc = _fc()
        e1 = _entry_e1(fc)
        _style2, e2 = _entry_e2(fc)
        m1, m2 = derive_legend_items(e1), derive_legend_items(e2)
        l1 = [e["label"] for e in m1["entries"] if e["kind"] == "class"]
        l2 = [e["label"] for e in m2["entries"] if e["kind"] == "class"]
        assert l1 != l2  # "10.00 - 12.00" vs "10 – 12"（分组 + 定点差异）
        # 区间数一致（同一断点集的不同格式化）
        assert len(l1) == len(l2) == K


class TestNodataAndUnitDivergenceLocked:
    """nodata / unit 的入口差异**显式锁定**（非静默容忍）：

    - E1/E3（canonical）：默认携带 nodata（ADR-0078 —— null/missing 转
      无数据色，不落最低类；thematic_spec.py:277-280）+ unit 透传；
    - E2（legacy service 对）：build_legend_spec 的映射契约
      （cartography_service.py:269-288 "choropleth → graduated"）不携带
      nodata / unit —— 旧形状，锁定防漂移。
    """

    def test_canonical_entries_carry_default_nodata(self):
        fc = _fc()
        e1 = _entry_e1(fc)
        _res3, e3 = _entry_e3(fc)
        assert e1["nodata"] == dict(NODATA_DEFAULT)
        assert e3["nodata"] == dict(NODATA_DEFAULT)
        m1, m3 = derive_legend_items(e1), derive_legend_items(e3)
        assert m1["hasNodata"] is True
        assert m3["hasNodata"] is True
        assert m1["entries"][-1]["kind"] == "nodata"
        assert m1["entries"][-1]["color"] == NODATA_DEFAULT["color"]

    def test_legacy_entry_has_no_nodata_locked(self):
        fc = _fc()
        _style2, e2 = _entry_e2(fc)
        assert "nodata" not in e2
        assert derive_legend_items(e2)["hasNodata"] is False

    def test_unit_carried_by_canonical_only_locked(self):
        fc = _fc()
        e1 = _entry_e1(fc)
        _style2, e2 = _entry_e2(fc)
        _res3, e3 = _entry_e3(fc)
        assert e1.get("unit") == UNIT
        assert e3.get("unit") == UNIT
        assert "unit" not in e2


class TestCategoricalFieldCrossEntry:
    def test_service_categorial_legend_matches_canonical_normalizer(self):
        """分类字段：legacy service（build_thematic_style categorical →
        build_legend_spec）与 canonical 规整器（build_categorical_spec 吃同
        一 categories）→ derive 模型逐字段一致（收敛于同一 presentation）。"""
        fc = _fc()
        style = CartographyService.build_thematic_style(
            fc, CAT_FIELD, method="categorical", k=4, palette="Set2")
        assert style is not None and style["type"] == "categorical"
        legacy = CartographyService.build_legend_spec(style, palette="Set2")
        assert legacy is not None

        canonical = build_categorical_spec(CAT_FIELD, style["categories"], palette="Set2")
        assert canonical is not None

        m_legacy = derive_legend_items(legacy)
        m_canonical = derive_legend_items(canonical)
        assert m_legacy is not None and m_canonical is not None
        assert m_legacy == m_canonical

        # 类别来自数据去重（保持出现序，上限 k）；颜色按 Set2 顺序循环
        expected_keys = []
        for f in fc["features"]:
            v = f["properties"][CAT_FIELD]
            if v not in expected_keys:
                expected_keys.append(v)
        n_cats = min(len(expected_keys), 4)
        assert [str(c["key"]) for c in legacy["categories"]] == expected_keys[:4]
        set2 = resolve_palette_colors("Set2")
        assert [c["color"] for c in legacy["categories"]] == [
            set2[i % len(set2)] for i in range(n_cats)
        ]

    def test_categorical_both_entries_no_nodata_consistent(self):
        fc = _fc()
        style = CartographyService.build_thematic_style(
            fc, CAT_FIELD, method="categorical", k=4, palette="Set2")
        legacy = CartographyService.build_legend_spec(style, palette="Set2")
        canonical = build_categorical_spec(CAT_FIELD, style["categories"], palette="Set2")
        assert derive_legend_items(legacy)["hasNodata"] is False
        assert derive_legend_items(canonical)["hasNodata"] is False
