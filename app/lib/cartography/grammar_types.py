"""Grammar 契约常量单点（ADR-0204）.

grammar_solver / visual_variables / scale_rules 共享的有界阈值与映射表。
独立成模块是为了避免 solver ↔ 子模块互相 import（词表在
visual_variables，动作在 scale_rules，常量在这里——依赖单向）。

全部为冻结字面量：改动即契约变化，须过版本化（GRAMMAR_VERSION bump）
与 golden/契约测试。
"""
from __future__ import annotations

from typing import Dict, Tuple

#: 语法契约版本（进指纹；任何裁决语义变化必须 bump）。
GRAMMAR_VERSION = "1.0.0"

#: 定性色带容量上限（Set2/Dark2=8、Set1/Pastel1=9 的库存口径下限）；
#: 超过 → 收纳 top N-1 + Other（GRAMMAR.REP.TOO_MANY_CATEGORIES）。
MAX_CATEGORICAL_CLASSES = 8

#: 收纳后保留的类数（= MAX_CATEGORICAL_CLASSES - 1，留 1 格给 Other）。
COLLAPSE_KEEP_CLASSES = MAX_CATEGORICAL_CLASSES - 1

#: 单图绑定的专题字段上限（有界请求；超出 fail-closed。
#: 语义下限 = semantic_checks 的 visualvar fail 阈值 4，契约测试锁定）。
MAX_THEMATIC_FIELDS = 4

#: data_kind → 合法 legend_spec.type（thematic_spec 的 DIVERGENT/GRADUATED/
#: CONTINUOUS/CATEGORICAL 形态词表；cyclic 无专用色带，降级 graduated 并
#: 在 legend.reason_codes 披露——与 resolve_symbology 的 cyclic 降级同口径）。
LEGEND_FORM_BY_DATA_KIND: Dict[str, str] = {
    "qualitative": "categorical",
    "diverging": "divergent",
    "sequential": "graduated",
    "cyclic": "graduated",
}

#: 表达选择会用到的 MapModel id 词表（全部必须是 model_library 注册 id，
#: 契约测试锁定可解析；grammar 不另立表达词表）。
REPRESENTATION_MODEL_IDS: Tuple[str, ...] = (
    "visual_heatmap",
    "aggregate_grid",
    "proportional_symbol",
    "point_cluster",
    "point_overlay",
    "simple_point_map",
    "categorical_thematic",
    "administrative_choropleth",
    "raster_surface",
)

__all__ = [
    "GRAMMAR_VERSION",
    "MAX_CATEGORICAL_CLASSES",
    "COLLAPSE_KEEP_CLASSES",
    "MAX_THEMATIC_FIELDS",
    "LEGEND_FORM_BY_DATA_KIND",
    "REPRESENTATION_MODEL_IDS",
]
