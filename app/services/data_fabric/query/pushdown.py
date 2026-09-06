"""下推能力分级模型（ADR-0101 D7，V4 §14）。

adapter 的 ``AdapterCapabilitiesV2`` 是布尔矩阵；planner 据此决定推/不推。
V4 在其上增加**分级语义**（不改变布尔决策）：

- ``exact``：远端语义与本地完全一致（PostGIS SQL 谓词、ST_ 空间谓词）；
- ``equivalent``：经等价变换下推（CQL2-text / ArcGIS where / FES XML；
  时间区间重映射）—— 语义一致但形式不同；
- ``coarse``：只能做粗过滤（如 bbox 包络预过滤），**必须**本地精确复算；
- ``unsupported``：远端不支持，本地执行。

planner 绝不推送 unsupported 语义（布尔矩阵已保证）；本模块负责把
「推了什么、以何种精度推的」写进 QueryPlan（EXPLAIN 诚实披露）。
纯函数、无 IO。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class PushdownClass(str, Enum):
    EXACT = "exact"
    EQUIVALENT = "equivalent"
    COARSE = "coarse"
    UNSUPPORTED = "unsupported"


#: 分级覆盖的谓词/操作族（planner 报告口径）。
PUSHDOWN_FAMILIES = (
    "projection", "scalar_filter", "in_filter", "range_filter", "temporal",
    "bbox", "spatial_predicate", "aggregate", "group_by", "sort", "limit",
    "join", "spatial_join", "cql",
)

#: 以「等价变换」下推属性谓词的源（编译到远端方言，非原生 SQL）。
_EQUIVALENT_FILTER_SOURCES = frozenset({"ogc_api", "arcgis", "wfs"})
#: 以「等价变换」下推时间谓词的源（区间语义重映射到 datetime 参数）。
_EQUIVALENT_TEMPORAL_SOURCES = frozenset({"stac", "ogc_api", "arcgis"})


def pushdown_profile(caps: Any) -> Dict[str, PushdownClass]:
    """由能力矩阵 + 源类型知识派生每个族的下推分级（诚实、确定性）。"""
    st = str(getattr(caps, "source_type", "") or "").lower()
    filt = bool(getattr(caps, "filter_pushdown", False))
    filt_class = (
        PushdownClass.EQUIVALENT if (filt and st in _EQUIVALENT_FILTER_SOURCES)
        else PushdownClass.EXACT if filt else PushdownClass.UNSUPPORTED
    )
    temporal = bool(getattr(caps, "temporal_filter", False))
    temporal_class = (
        PushdownClass.EQUIVALENT if (temporal and st in _EQUIVALENT_TEMPORAL_SOURCES)
        else PushdownClass.EXACT if temporal else PushdownClass.UNSUPPORTED
    )
    bbox = bool(getattr(caps, "bbox_pushdown", False))
    spatial_preds = set(getattr(caps, "spatial_predicates", None) or [])
    exact_spatial = bool(spatial_preds - {"bbox"})
    spatial_class = (
        PushdownClass.EXACT if exact_spatial
        else PushdownClass.COARSE if bbox
        else PushdownClass.UNSUPPORTED
    )
    agg = bool(getattr(caps, "aggregation", False))
    group_by = bool(getattr(caps, "group_by", False))
    sort = bool(getattr(caps, "sort_pushdown", False))
    projection = bool(getattr(caps, "projection_pushdown", False))
    server_spatial_join = bool(getattr(caps, "server_side_spatial_join", False))
    cql_class = (
        PushdownClass.EQUIVALENT if (st == "ogc_api" and filt) else PushdownClass.UNSUPPORTED
    )
    return {
        "projection": PushdownClass.EXACT if projection else PushdownClass.UNSUPPORTED,
        "scalar_filter": filt_class,
        "in_filter": filt_class,
        "range_filter": filt_class,
        "temporal": temporal_class,
        "bbox": PushdownClass.EXACT if bbox else PushdownClass.UNSUPPORTED,
        "spatial_predicate": spatial_class,
        "aggregate": PushdownClass.EXACT if agg else PushdownClass.UNSUPPORTED,
        "group_by": PushdownClass.EXACT if group_by else PushdownClass.UNSUPPORTED,
        "sort": PushdownClass.EXACT if sort else PushdownClass.UNSUPPORTED,
        "limit": PushdownClass.EXACT,   # 页大小永远作用于远端拉取窗口
        "join": PushdownClass.UNSUPPORTED,  # 跨源 join 只在本地（V4 边界）
        "spatial_join": (
            PushdownClass.EXACT if server_spatial_join else PushdownClass.UNSUPPORTED
        ),
        "cql": cql_class,
    }


def classify_plan_pushdowns(
    plan_flags: Dict[str, Any], caps: Any, *, spatial_op: Optional[str] = None,
) -> Dict[str, str]:
    """把本计划实际发生的推/不推映射为分级报告（EXPLAIN 证据）。

    ``plan_flags`` 键：pushed_filters(bool), pushed_spatial(bool),
    pushed_temporal(bool), pushed_projection(bool), pushed_aggregation(bool),
    pushed_sort(bool), has_filter/has_spatial/has_temporal/has_aggregate/
    has_sort/has_select（是否用到该族）。未用到的族不出现在报告里；用到
    但未推的族标 ``local``；推了的族标其分级；推了 unsupported 族是
    planner 缺陷 —— 标 ``violation`` 供 parity 测试捕获（绝不静默）。
    """
    profile = pushdown_profile(caps)
    out: Dict[str, str] = {}

    def _report(family: str, used: bool, pushed: bool) -> None:
        if not used:
            return
        if not pushed:
            out[family] = "local"
            return
        cls = profile.get(family, PushdownClass.UNSUPPORTED)
        out[family] = "violation" if cls is PushdownClass.UNSUPPORTED else cls.value

    _report("scalar_filter", bool(plan_flags.get("has_filter")), bool(plan_flags.get("pushed_filters")))
    if plan_flags.get("has_spatial"):
        family = "bbox" if (spatial_op or "bbox") == "bbox" else "spatial_predicate"
        _report(family, True, bool(plan_flags.get("pushed_spatial")))
        if family == "spatial_predicate" and not plan_flags.get("pushed_spatial") \
                and profile["spatial_predicate"] is PushdownClass.COARSE:
            out["spatial_predicate_note"] = "coarse bbox prefilter available; exact predicate evaluated locally"
    _report("temporal", bool(plan_flags.get("has_temporal")), bool(plan_flags.get("pushed_temporal")))
    _report("projection", bool(plan_flags.get("has_select")), bool(plan_flags.get("pushed_projection")))
    _report("aggregate", bool(plan_flags.get("has_aggregate")), bool(plan_flags.get("pushed_aggregation")))
    _report("sort", bool(plan_flags.get("has_sort")), bool(plan_flags.get("pushed_sort")))
    return out
