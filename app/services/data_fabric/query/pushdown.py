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

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Tuple


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
    filter_split_families: Optional[Tuple[FrozenSet[str], FrozenSet[str]]] = None,
) -> Dict[str, str]:
    """把本计划实际发生的推/不推映射为分级报告（EXPLAIN 证据）。

    ``plan_flags`` 键：pushed_filters(bool), pushed_spatial(bool),
    pushed_temporal(bool), pushed_projection(bool), pushed_aggregation(bool),
    pushed_sort(bool), has_filter/has_spatial/has_temporal/has_aggregate/
    has_sort/has_select（是否用到该族）。未用到的族不出现在报告里；用到
    但未推的族标 ``local``；推了的族标其分级；推了 unsupported 族是
    planner 缺陷 —— 标 ``violation`` 供 parity 测试捕获（绝不静默）。

    V5（Wave 9）：``filter_split_families`` 是 AND 边分解后的
    ``(pushed_families, local_families)`` —— 提供时，标量/IN/范围三个
    过滤族按拆分实况报告（两侧都出现 → ``partial``），布尔旗标不再参与
    这三族的判定。
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

    if filter_split_families is not None:
        out.update(classify_filter_split_families(*filter_split_families, profile=profile))
    else:
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


# ── V5（Wave 9）：AND 边分解的逐子句下推拆分 ─────────────────────────────────
#
# 审计 06 §6.2 缺口 1：谓词下推此前按**整棵过滤 AST** 一个布尔
# （caps.filter_pushdown）决定 —— 混合 AND（一半可推、一半不可推）被整体
# 本地执行，白白拉全量。本节把 AST 沿 AND 边分解为叶子（OR/NOT 子树是
# 原子，绝不拆开），逐叶按 pushdown 分级粒度（scalar_filter/in_filter/
# range_filter 族）判定可推性；可推子合取下推、余项本地执行。
#
# 兼容红线：现存 capability 矩阵（全部布尔派生）下所有叶族同判 ——
# 全可推 → 整体下推（与历史逐位一致）、全不可推 → 整体本地（一致）。
# 混合情形只在源**诚实声明部分可推**（``caps.filter_ops_local`` 非空）
# 时出现；该声明为空时本机制对任何现有源都不改变行为。

#: AST 属性叶子 → pushdown 分级族（predicates.py 的 op 集合是封闭的）。
FILTER_LEAF_OP_FAMILIES = {
    "eq": "scalar_filter",
    "ne": "scalar_filter",
    "like": "scalar_filter",
    "is_null": "scalar_filter",
    "in": "in_filter",
    "not_in": "in_filter",
    "gt": "range_filter",
    "ge": "range_filter",
    "lt": "range_filter",
    "le": "range_filter",
    "between": "range_filter",
}

_FILTER_FAMILIES = ("scalar_filter", "in_filter", "range_filter")


def flatten_conjunction(node: Any) -> List[Any]:
    """沿 AND 边把过滤 AST 展平为叶子列表（嵌套 And 递归展平；源顺序稳定）。

    OR / NOT / 属性叶子都是原子 —— OR 分支永不拆分（语义红线）。
    """
    if getattr(node, "op", None) == "and":
        leaves: List[Any] = []
        for arg in node.args:
            leaves.extend(flatten_conjunction(arg))
        return leaves
    return [node]


def filter_leaf_families(node: Any) -> FrozenSet[str]:
    """子树引用的全部 pushdown 分级族（and/or/not 递归；叶子按 op 映射）。"""
    op = getattr(node, "op", None)
    if op in ("and", "or"):
        out: FrozenSet[str] = frozenset()
        for arg in node.args:
            out = out | filter_leaf_families(arg)
        return out
    if op == "not":
        return filter_leaf_families(node.arg)
    family = FILTER_LEAF_OP_FAMILIES.get(str(op))
    return frozenset({family}) if family else frozenset({"scalar_filter"})


def filter_subtree_pushable(node: Any, caps: Any, profile: Optional[Dict[str, PushdownClass]] = None) -> bool:
    """子树的每个属性叶子对目标源都可下推时才为 True（分级族粒度）。

    两个否定条件（诚实、保守）：
    - 任一引用族的分级是 UNSUPPORTED（能力矩阵说了算）；
    - 任一叶子 op 在 ``caps.filter_ops_local``（源显式声明"这个 op 我推不了，
      必须本地求值"的部分下推契约）。
    """
    prof = profile if profile is not None else pushdown_profile(caps)
    local_ops = set(getattr(caps, "filter_ops_local", None) or [])
    if _subtree_ops(node) & local_ops:
        return False
    return not any(prof.get(f, PushdownClass.UNSUPPORTED) is PushdownClass.UNSUPPORTED
                   for f in filter_leaf_families(node))


def _subtree_ops(node: Any) -> FrozenSet[str]:
    op = getattr(node, "op", None)
    if op in ("and", "or"):
        out: FrozenSet[str] = frozenset()
        for arg in node.args:
            out = out | _subtree_ops(arg)
        return out
    if op == "not":
        return _subtree_ops(node.arg)
    return frozenset({str(op)})


@dataclass(frozen=True)
class FilterPushdownSplit:
    """AND 边分解结果（纯函数产物；叶子顺序 = 源顺序，确定性）。

    ``pushed`` / ``local`` 是重组后的子合取 AST（单叶时不包 And）；
    二者皆非 None 即混合拆分（``partial=True``）。族集合供分级披露。
    """

    pushed: Optional[Any]
    local: Optional[Any]
    partial: bool
    pushed_families: FrozenSet[str]
    local_families: FrozenSet[str]


def _recombine(leaves: List[Any]) -> Optional[Any]:
    if not leaves:
        return None
    if len(leaves) == 1:
        return leaves[0]
    from app.services.data_fabric.query.predicates import And

    return And(args=list(leaves))


def split_filter_pushdown(filter_node: Any, caps: Any) -> FilterPushdownSplit:
    """把过滤 AST 沿 AND 边按逐叶可推性拆成（下推部分, 本地余项）。

    纯函数、确定性（叶子按源顺序稳定分箱）。注意：本函数只回答**能力**
    问题；「要不要拆」的门（无统计保守回退等）由 planner 决定，执行侧
    以 ``QueryPlan.filter_split`` 为单一真相，绝不二次决策。
    """
    profile = pushdown_profile(caps)
    leaves = flatten_conjunction(filter_node)
    pushed_leaves = [leaf for leaf in leaves if filter_subtree_pushable(leaf, caps, profile)]
    local_leaves = [leaf for leaf in leaves if not filter_subtree_pushable(leaf, caps, profile)]
    pushed = _recombine(pushed_leaves)
    local = _recombine(local_leaves)
    return FilterPushdownSplit(
        pushed=pushed,
        local=local,
        partial=pushed is not None and local is not None,
        pushed_families=frozenset().union(*(filter_leaf_families(lf) for lf in pushed_leaves)) if pushed_leaves else frozenset(),
        local_families=frozenset().union(*(filter_leaf_families(lf) for lf in local_leaves)) if local_leaves else frozenset(),
    )


def classify_filter_split_families(
    pushed_families: FrozenSet[str],
    local_families: FrozenSet[str],
    *,
    profile: Dict[str, PushdownClass],
) -> Dict[str, str]:
    """按拆分实况报告过滤三族：两侧都有 → ``partial``；只推 → 其分级；
    只本地 → ``local``；未出现的族不报告。"""
    out: Dict[str, str] = {}
    for family in _FILTER_FAMILIES:
        in_pushed = family in pushed_families
        in_local = family in local_families
        if in_pushed and in_local:
            out[family] = "partial"
        elif in_pushed:
            cls = profile.get(family, PushdownClass.UNSUPPORTED)
            out[family] = "violation" if cls is PushdownClass.UNSUPPORTED else cls.value
        elif in_local:
            out[family] = "local"
    return out


def resolve_plan_filter_split(filter_node: Any, plan: Any) -> Tuple[Optional[Any], Optional[Any]]:
    """执行侧单一真相：按计划取（远端编译谓词, 本地余项谓词）。

    - ``plan.filter_split`` 存在（V5 拆分/守卫路径）→ 直接取计划里的两半
      （计划即执行，adapter 不做第二次能力决策）；
    - 缺席（历史路径）→ 维持既有行为逐位不变：过滤器存在即整体编译下发
      （本地求值型 adapter 本就不走编译路径）。
    """
    fs = getattr(plan, "filter_split", None)
    if not isinstance(fs, dict):
        return filter_node, None
    from app.services.data_fabric.query.predicates import predicate_from_dict

    pushed = fs.get("pushed")
    local = fs.get("local")
    return (
        predicate_from_dict(pushed) if isinstance(pushed, dict) else None,
        predicate_from_dict(local) if isinstance(local, dict) else None,
    )


def local_predicates_present(plan: Any) -> bool:
    """计划是否带有执行期必须本地求值的过滤余项（split 或守卫路径）。"""
    fs = getattr(plan, "filter_split", None)
    return bool(isinstance(fs, dict) and isinstance(fs.get("local"), dict))


__all__ = [
    "PushdownClass",
    "PUSHDOWN_FAMILIES",
    "pushdown_profile",
    "classify_plan_pushdowns",
    "FILTER_LEAF_OP_FAMILIES",
    "flatten_conjunction",
    "filter_leaf_families",
    "filter_subtree_pushable",
    "FilterPushdownSplit",
    "split_filter_pushdown",
    "classify_filter_split_families",
    "resolve_plan_filter_split",
    "local_predicates_present",
]
