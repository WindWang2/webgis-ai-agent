"""Cartographic Golden Corpus — grammar 决策矩阵（F10 M6，design D7）.

方向任务书要求的验收面：signed change / rate vs count / nominal 类别 /
密集点 / 低 N / 多尺度 / 双变量。每个用例 = ``GrammarRequest`` + 关联的
``resolve_symbology`` 输入 + **断言面**（不止 palette 名：reason codes、
diverging center、legend 配对、collapse spec、尺度带候选、确定性）。

声明式语料（与 ``app/evaluation/quality_corpus.py`` 同风格）：用例表在本
模块定义，harness 在 ``test_grammar_golden_corpus_v1.py`` 驱动。断言随
``GRAMMAR_VERSION`` 版本化——裁决语义变化必须显式 bump 并审阅本语料的
预期面。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.lib.cartography.grammar_solver import (
    FieldEvidence,
    GrammarRequest,
)

#: SIGNED 值场（净迁移形：双符号、负侧 ~33%）。
_SIGNED_VALUES = [-500.0, -300.0, -100.0, -50.0, 10.0, 80.0,
                  200.0, 400.0, 600.0, 900.0, -800.0, -200.0]
_RATE_SIGNED_VALUES = [-0.10, 0.20, 0.30, -0.40, 0.10, -0.05,
                       0.25, 0.05, -0.30, 0.15, -0.02, 0.08]

#: 12 个名义类别（超定性色带容量 8）。
_NOMINAL_MANY = [f"lut_{i:02d}" for i in range(12)]
_NOMINAL_FEW = ["residential", "industrial", "green", "water"]

_POSITIVE = [100.0, 300.0, 500.0, 800.0, 1200.0, 2000.0,
             3000.0, 500.0, 700.0, 900.0, 1500.0, 2500.0]


def _field(name: str, **kw: Any) -> FieldEvidence:
    return FieldEvidence(name=name, **kw)


#: 用例矩阵（键 = 场景 id；断言面 = 期望的 reason codes / 状态）。
GRAMMAR_CORPUS: Dict[str, Dict[str, Any]] = {
    # 1. 带符号变化：diverging 族保序（正负语义不丢）。
    "signed_change": {
        "request": GrammarRequest(
            geometry="polygon", feature_count=12,
            fields=[_field("net_migration", dtype="float",
                           values=_SIGNED_VALUES)]),
        "expect": {
            "data_kind": "diverging",
            "measurement_kind": "signed_change",
            "representation_selected": "administrative_choropleth",
            "reason_codes_contain": ["GRAMMAR.REP.SIGNED_DIVERGING"],
            "legend_form": "divergent",
            "symbology": {
                "input": {"data_kind": "diverging",
                          "measurement_kind": "signed_change",
                          "values": _SIGNED_VALUES},
                "palette_family_diverging": True,
                "diverging_center": 0.0,
            },
        },
    },
    # 2a. 率：归一化口径披露（count_vs_rate 语义族）。
    "rate_normalized": {
        "request": GrammarRequest(
            geometry="polygon", feature_count=12,
            fields=[_field("gdp_per_capita", dtype="float",
                           values=_POSITIVE)]),
        "expect": {
            "measurement_kind": "rate",
            "reason_codes_contain": ["GRAMMAR.REP.RATE_NORMALIZED"],
            "legend_form": "graduated",
            "symbology": {
                "input": {"data_kind": "sequential",
                          "measurement_kind": "rate",
                          "values": _POSITIVE},
                "palette_family_diverging": False,
            },
        },
    },
    # 2b. 计数：count-vs-rate advisory（区域大小偏置提示）。
    "count_advisory": {
        "request": GrammarRequest(
            geometry="polygon", feature_count=12,
            fields=[_field("school_total", dtype="float",
                           values=_POSITIVE)]),
        "expect": {
            "measurement_kind": "ratio",
            "reason_codes_contain": ["GRAMMAR.REP.COUNT_VS_RATE_ADVISORY"],
        },
    },
    # 2c. 带符号率（率名 × 双符号样本）：值证据 signed_change → diverging
    # （#1480 判定序：值证据先于名称词素；画像在场的升级路径单测覆盖）。
    "signed_rate": {
        "request": GrammarRequest(
            geometry="polygon", feature_count=12,
            fields=[_field("population_growth_rate", dtype="float",
                           values=_RATE_SIGNED_VALUES)]),
        "expect": {
            "data_kind": "diverging",
            "reason_codes_contain": ["GRAMMAR.MEAS.VALUE_SIGNED"],
            "legend_form": "divergent",
        },
    },
    # 3a. 名义类别过多：收纳声明（top N-1 + Other）。
    "nominal_many": {
        "request": GrammarRequest(
            geometry="polygon", feature_count=12,
            fields=[_field("landuse", dtype="string",
                           unique_count=len(_NOMINAL_MANY))]),
        "expect": {
            "data_kind": "qualitative",
            "representation_selected": "categorical_thematic",
            "reason_codes_contain": ["GRAMMAR.REP.TOO_MANY_CATEGORIES"],
            "collapse": {"keep_classes": 7, "other_label": "Other",
                         "observed_classes": 12},
            "legend_form": "categorical",
        },
    },
    # 3b. 名义类别少量：无收纳。
    "nominal_few": {
        "request": GrammarRequest(
            geometry="polygon", feature_count=12,
            fields=[_field("zone_type", dtype="string",
                           unique_count=len(_NOMINAL_FEW))]),
        "expect": {
            "data_kind": "qualitative",
            "representation_selected": "categorical_thematic",
            "collapse_absent": True,
            "legend_form": "categorical",
        },
    },
    # 4. 密集点：聚合族候选 + heatmap×nominal 拒绝。
    "dense_points": {
        "request": GrammarRequest(
            geometry="point", feature_count=9000, zoom=11.0,
            fields=[_field("sensor_id", dtype="string",
                           unique_count=9000)]),
        "expect": {
            "scale_tier": "city",
            "is_dense_points": True,
            "reason_codes_contain": ["GRAMMAR.SCALE.DENSE_POINTS_AGGREGATE"],
            "rejected_representations": {
                "visual_heatmap": "GRAMMAR.REP.HEATMAP_NOMINAL"},
            "point_candidates_first": "aggregate_grid",
        },
    },
    # 5. 稀疏点：原始符号面。
    "sparse_points": {
        "request": GrammarRequest(
            geometry="point", feature_count=40, zoom=14.0,
            fields=[_field("station_value", dtype="float",
                           values=_POSITIVE)]),
        "expect": {
            "is_dense_points": False,
            "reason_codes_contain": ["GRAMMAR.SCALE.SPARSE_POINTS_RAW"],
            "representation_selected": "proportional_symbol",
        },
    },
    # 6. 低 N：统计证据不足披露（resolve_symbology 将降置信）。
    "low_n": {
        "request": GrammarRequest(
            geometry="polygon", feature_count=5,
            fields=[_field("index_v", dtype="float",
                           values=[1.0, 2.0, 3.0, 4.0, 9.0])]),
        "expect": {
            "disclosures_contain": ["n=5"],
        },
    },
    # 9. 双变量（数量 + 不确定度）：次通道披露；不虚构 bivariate 模型。
    "bivariate_with_uncertainty": {
        "request": GrammarRequest(
            geometry="polygon", feature_count=12,
            fields=[
                _field("population", dtype="float", values=_POSITIVE),
                _field("margin_of_error", dtype="float", is_secondary=True,
                       values=[0.1, 0.2, 0.3, 0.1, 0.2, 0.3, 0.1, 0.2,
                               0.3, 0.1, 0.2, 0.3]),
            ]),
        "expect": {
            "secondary_binding_measurement": "uncertainty",
            "disclosures_contain": ["次通道字段测量语义"],
            "representations_in_registry_vocab": True,
        },
    },
    # 10. 用户 pin 与矩阵冲突：user-wins + 冲突披露（不覆盖）。
    "pin_channel_conflict": {
        "request": GrammarRequest(
            geometry="polygon", feature_count=12,
            fields=[_field("net_migration", dtype="float",
                           values=_SIGNED_VALUES)],
            pinned_channels={"net_migration": "size"}),
        "expect": {
            "user_wins_kinds": ["channel"],
            "binding_variable": "size",
            "reason_codes_contain": ["GRAMMAR.PIN.CHANNEL_CONFLICT"],
        },
    },
}

#: 多尺度矩阵（同一请求 × 4 zoom 带 → 分带动作；与 label_plan 分界同界）。
MULTISCALE_CASES: List[Dict[str, Any]] = [
    {"zoom": 4.0, "tier": "world",
     "dense_at": 1300, "sparse_at": 900},
    {"zoom": 9.5, "tier": "province",
     "dense_at": 2100, "sparse_at": 1900},
    {"zoom": 12.0, "tier": "city",
     "dense_at": 4100, "sparse_at": 3900},
    {"zoom": 16.0, "tier": "street",
     "dense_at": 8100, "sparse_at": 5000},
]


def build_request_for_multiscale(zoom: float, feature_count: int) -> GrammarRequest:
    return GrammarRequest(
        geometry="point", feature_count=feature_count, zoom=zoom,
        fields=[_field("poi_value", dtype="float", values=_POSITIVE)])
