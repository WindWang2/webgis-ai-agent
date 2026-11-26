"""Design Series 案例库 —— 五类经 ground truth 锚定的合成场景（WP6）。

纪律（任务书 §4 WP6）：
- **合成数据，绝不虚构真实地理**：全部 fixture 显式 ``_simulated=True``，
  案例元数据 ``simulated: True``（manifest 恒如实传播）；
- **ground truth 在内计算**：密度/断点/净流/差值等真值由本模块用与生产
  同款算法（classify/deterministic 公式）算出并随案例暴露，测试做**值级**
  对账，不做「有返回就算过」；
- 每类案例带**负例控制**（unit 冲突 / 缺分母 / 指漂移等），负例必须被
  系统拒绝或如实披露 —— 不允许假成功。
"""
from __future__ import annotations

from typing import Any, Dict, List

#: 案例库版本。
CASE_LIBRARY_VERSION = "design_series_cases.v1"

_CASES: Dict[str, Dict[str, Any]] = {}


def _fc(features: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _poly(i: int, n: int = 6) -> Dict[str, Any]:
    """确定性小面（网格排布；成都式 bbox 仅空间参考，纯合成）。"""
    cx = 103.95 + (i % 3) * 0.06
    cy = 30.55 + (i // 3) * 0.05
    s = 0.025
    return {"type": "Polygon", "coordinates": [[
        [cx - s, cy - s], [cx + s, cy - s], [cx + s, cy + s], [cx - s, cy + s], [cx - s, cy - s],
    ]]}


def _district_rows(names: List[str], counts: List[int], populations: List[int]) -> List[Dict[str, Any]]:
    return [
        {"name": n, "schools": c, "population_wan": p, "_simulated": True}
        for n, c, p in zip(names, counts, populations)
    ]


# ── 案例 1：设施分布/密度（同分母同断点） ─────────────────────────────

_D1_NAMES = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区", "龙泉驿区"]
_D1_COUNTS = [12, 9, 15, 11, 6, 4]
_D1_POP = [60, 45, 70, 55, 40, 30]  # 万人（同分母口径）

_case1_truth_density = {
    n: round(c / p, 6) for n, c, p in zip(_D1_NAMES, _D1_COUNTS, _D1_POP)
}  # 所/万人

_CASES["facility_density"] = {
    "case_id": "facility_density",
    "title": "设施分布与密度（同分母共享断点）",
    "simulated": True,
    "spec_payload": {
        "spec_version": "design_spec.v1",
        "name": "案例·设施密度规范",
        "color": {"theme_id": "cartographic.light_interactive", "palette_sequential": "YlOrRd"},
        "classification": {"default_method": "quantiles", "k_min": 3, "k_max": 3,
                           "shared_required": True},
        "typography": {"font_resource": "noto-sans-cjk-sc"},
        "components": {"required": [{"component_type": "legend"},
                                    {"component_type": "scale_bar"}]},
        "output": {"targets": ["interactive", "pdf", "svg"], "paper_profile": "a4_landscape"},
        "data_roles": [{"name": "indicator", "value_type": "number", "measurement": "density",
                        "unit": "所/万人", "unit_required": True,
                        "comparable_across_maps": True},
                       {"name": "denominator", "value_type": "number", "measurement": "count",
                        "unit": "万人"}],
        "lineage": {"source": "created"},
    },
    "fixtures": {
        "city_a": _fc([{"type": "Feature", "geometry": _poly(i),
                        "properties": dict(props)} for i, props in
                       enumerate(_district_rows(_D1_NAMES, _D1_COUNTS, _D1_POP))]),
    },
    "ground_truth": {
        "density_per_district": _case1_truth_density,
        "shared_breaks_expectation": {
            "method": "quantiles",
            "k": 3,
            "values_union": sorted(_case1_truth_density.values()),
        },
        "denominator_required": True,
    },
    "negatives": [
        {"kind": "missing_denominator",
         "facts": {"purposes": ["distribution"], "geometry_kind": "polygon",
                   "statistic": "density", "denominator": {"available": False}},
         "expect": {"clarification": "DENOMINATOR_MISSING"}},
    ],
}

# ── 案例 2：行政区专题对比（同指标不同字段名，单位契约） ────────────────

_D2_A = _district_rows(_D1_NAMES[:4], [12, 9, 15, 11], [60, 45, 70, 55])
_D2_B = _district_rows(["B城东", "B城西", "B城南", "B城北"], [5, 4, 9, 6], [25, 20, 45, 30])


def _comparison_fc(rows: List[Dict[str, Any]], *, field: str) -> Dict[str, Any]:
    feats = []
    for i, row in enumerate(rows):
        props = dict(row)
        props[field] = props.pop("schools")
        props["population_wan"] = row["population_wan"]
        feats.append({"type": "Feature", "geometry": _poly(i), "properties": props})
    return _fc(feats)


_case2_truth = {
    "field_a": "primaries",
    "field_b": "schools_total",
    "unit_contract": "所",
    "totals": {
        "city_a": sum(r["schools"] for r in _D2_A),
    },
}

_CASES["district_comparison"] = {
    "case_id": "district_comparison",
    "title": "行政区专题对比（同指标跨地区，字段名不同）",
    "simulated": True,
    "spec_payload": {
        "spec_version": "design_spec.v1",
        "name": "案例·区级对比规范",
        "color": {"theme_id": "cartographic.light_interactive", "palette_sequential": "Blues"},
        "classification": {"default_method": "quantiles", "k_min": 4, "k_max": 6,
                           "shared_required": True},
        "components": {"required": [{"component_type": "legend"}]},
        "output": {"targets": ["interactive", "svg"]},
        "data_roles": [{"name": "indicator", "value_type": "number", "measurement": "count",
                        "unit": "所", "unit_required": True,
                        "comparable_across_maps": True}],
        "lineage": {"source": "created"},
    },
    "fixtures": {
        "city_a": _comparison_fc(_D2_A, field="primaries"),
        "city_b": _comparison_fc(_D2_B, field="schools_total"),
    },
    "ground_truth": _case2_truth,
    "negatives": [
        {"kind": "unit_conflict",
         "expect": {"binding_code": "unit_mismatch"},
         "conflict_unit": "间"},
    ],
}

# ── 案例 3：时间变化（双期共享表达与日期） ────────────────────────────

_D3_2020 = [8, 6, 10, 7, 5, 3]
_D3_2025 = [12, 9, 15, 11, 6, 4]


def _temporal_fc(year: int, counts: List[int]) -> Dict[str, Any]:
    feats = []
    for i, c in enumerate(counts):
        feats.append({"type": "Feature", "geometry": _poly(i),
                      "properties": {"name": _D1_NAMES[i], f"schools_{year}": c,
                                     "_simulated": True}})
    return _fc(feats)


_case3_truth = {
    "periods": [2020, 2025],
    "delta_per_district": {n: b - a for n, a, b in
                           zip(_D1_NAMES, _D3_2020, _D3_2025)},
    "shared_classification": {
        "method": "quantiles",
        "k": 3,
        "union_2020_2025": sorted(set(_D3_2020) | set(_D3_2025)),
    },
}

_CASES["temporal_change"] = {
    "case_id": "temporal_change",
    "title": "时间变化（2020/2025 双期共享表达）",
    "simulated": True,
    "spec_payload": {
        "spec_version": "design_spec.v1",
        "name": "案例·时相对比规范",
        "color": {"theme_id": "cartographic.light_interactive",
                  "palette_diverging": "RdYlGn"},
        "classification": {"default_method": "quantiles", "k_min": 3, "k_max": 3,
                           "shared_required": True},
        "components": {"required": [{"component_type": "legend"},
                                    {"component_type": "annotation"}]},
        "output": {"targets": ["interactive", "svg"]},
        "data_roles": [{"name": "indicator", "value_type": "number", "measurement": "count",
                        "unit": "所", "unit_required": True,
                        "comparable_across_maps": True}],
        "lineage": {"source": "created"},
    },
    "fixtures": {
        "y2020": _temporal_fc(2020, _D3_2020),
        "y2025": _temporal_fc(2025, _D3_2025),
    },
    "ground_truth": _case3_truth,
    "negatives": [
        {"kind": "independent_breaks",
         "expect": {"disclosure": "各图独立分级必须显式披露不可直接比较"}},
    ],
}

# ── 案例 4：流动/交通（OD 方向与净流 —— 引用 G21 语义） ────────────────

_D4_NAMES = _D1_NAMES[:5]


def _od_matrix() -> List[Dict[str, Any]]:
    """确定性 OD 行（origin,destination,flow）；netflow 在内算出。"""
    od = []
    flow_map = {
        (0, 1): 120, (1, 0): 90,
        (0, 2): 60, (2, 3): 140,
        (3, 4): 75, (4, 0): 55,
        (1, 3): 30, (2, 4): 45,
    }
    for (o, d), f in sorted(flow_map.items()):
        od.append({"origin": _D4_NAMES[o], "destination": _D4_NAMES[d],
                   "flow": float(f), "_simulated": True})
    return od


_case4_od = _od_matrix()
_case4_truth = {
    "total_flow": sum(r["flow"] for r in _case4_od),
    "top_flow": max(_case4_od, key=lambda r: r["flow"]),
    "netflow": {
        _D4_NAMES[i]: sum(r["flow"] for r in _case4_od if r["origin"] == _D4_NAMES[i])
        - sum(r["flow"] for r in _case4_od if r["destination"] == _D4_NAMES[i])
        for i in range(5)
    },
}

_CASES["flow_od"] = {
    "case_id": "flow_od",
    "title": "流动/交通 OD（方向与净流；引用 G21 语义不重做）",
    "simulated": True,
    "spec_payload": {
        "spec_version": "design_spec.v1",
        "name": "案例·OD 流向规范",
        "color": {"theme_id": "cartographic.light_interactive"},
        "classification": {"default_method": "quantiles", "k_min": 3, "k_max": 5,
                           "shared_required": False},
        "components": {"required": [{"component_type": "legend"}]},
        "output": {"targets": ["interactive", "svg"]},
        "data_roles": [{"name": "flow_value", "value_type": "number",
                        "measurement": "count", "unit": "人次", "unit_required": True,
                        "comparable_across_maps": False}],
        "lineage": {"source": "created"},
    },
    "fixtures": {
        "od_table": _fc([
            {"type": "Feature",
             "geometry": {"type": "LineString",
                          "coordinates": [[103.95 + 0.02 * hash(r["origin"]) % 3 * 0.01,
                                           30.55],
                                          [103.95 + 0.03, 30.60]]},
             "properties": dict(r)}
            for r in _case4_od
        ]),
    },
    "ground_truth": _case4_truth,
    "negatives": [],
}

# ── 案例 5：多图综合专题成果（composition/布局） ──────────────────────

_case5_truth = {
    "composition_requirements": ["title", "legend", "scale_bar", "attribution"],
    "print_layout_required": True,
    "paper_profile": "a4_landscape",
}

_CASES["composition_sheet"] = {
    "case_id": "composition_sheet",
    "title": "多图综合专题成果（图组版面）",
    "simulated": True,
    "spec_payload": {
        "spec_version": "design_spec.v1",
        "name": "案例·综合版面规范",
        "color": {"theme_id": "cartographic.publication"},
        "classification": {"default_method": "quantiles", "k_min": 4, "k_max": 6,
                           "shared_required": True},
        "typography": {"font_resource": "noto-serif-cjk-sc", "title_size_pt": 20,
                       "base_size_pt": 10},
        "components": {"required": [{"component_type": "title"},
                                    {"component_type": "legend"},
                                    {"component_type": "scale_bar"},
                                    {"component_type": "attribution"}],
                       "forbidden": ["north_arrow"]},
        "output": {"targets": ["pdf", "svg"], "paper_profile": "a4_landscape", "dpi": 300},
        "data_roles": [{"name": "indicator", "value_type": "number",
                        "measurement": "density", "unit": "所/万人",
                        "unit_required": True, "comparable_across_maps": True}],
        "lineage": {"source": "created"},
    },
    "fixtures": {
        "sheet": _fc([{"type": "Feature", "geometry": _poly(i),
                       "properties": {"name": n, "schools": c, "population_wan": p}}
                      for i, (n, c, p) in enumerate(
                          zip(_D1_NAMES, _D1_COUNTS, _D1_POP))]),
    },
    "ground_truth": _case5_truth,
    "negatives": [
        {"kind": "forbidden_component_conflict",
         "expect": {"conflict_kind": "forbidden_component_present"}},
    ],
}


def all_cases() -> Dict[str, Dict[str, Any]]:
    return dict(_CASES)


def get_case(case_id: str) -> Dict[str, Any]:
    if case_id not in _CASES:
        raise KeyError(f"unknown case: {case_id}")
    return _CASES[case_id]


def case_ids() -> List[str]:
    return sorted(_CASES.keys())


__all__ = [
    "CASE_LIBRARY_VERSION",
    "all_cases",
    "get_case",
    "case_ids",
]
