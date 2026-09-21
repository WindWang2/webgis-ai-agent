"""计划级统一估算聚合（R2/R6，ADR-0204 D2）。

:func:`~app.services.governor.estimation.sum_estimates` 的**结构化替代**：
朴素求和把 wall_time 与内存也累加，既高估串行内存复用、又低估并行峰值。
本模块按计划形状分维聚合：

- ``SEQUENTIAL`` 链：wall_time 求和（critical path）；live 内存峰值取成员
  max（前序已释放后序才驻留）；
- ``PARALLEL`` 组：wall_time 取成员 max；live 内存峰值成员求和（同时在驻）；
- 累计维（tokens / LLM cost / network bytes / external calls / render work /
  storage / feature / pixel）：全路径求和；
- retry 乘数：wall 与累计维 × ``expected_attempts``（内存不乘 —— 重试串行，
  峰值不叠乘）；
- cache 复用：可缓存维 × ``(1 - cache_read_probability)``；
- unknown 维：按保守地板计入数值（``source=conservative_floor`` 留痕 +
  ``floor_charged_dims`` 披露），聚合结果绝不把 unknown 当 0。

OPTIONAL / FALLBACK 节点不进主聚合（single列披露池）：optional 是"执行才追加"
的增量，fallback 是"触发才计费"的分支（其费用走 retry/replan 预算，R6）。

纯函数、确定性、bounded（MAX_PLAN_NODES 截断 + 披露）。governor 的执行准入
权不受影响 —— 本模块只回答"这个计划值多少资源"，不回答"准不准跑"。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.governor.contract import (
    CONSERVATIVE_FLOORS,
    DegradationSemantics,
    Dimension,
    DimValue,
    ResourceClass,
    ResourceEstimate,
    Subsystem,
)

#: 聚合契约版本（进 estimate.source，演进时 bump）
AGGREGATE_VERSION = "plan_aggregate.v1"

#: 上界（bounded everything；截断必须披露）
MAX_PLAN_NODES = 64
MAX_EXPECTED_ATTEMPTS = 8

#: ResourceClass 严重度序（review P1-1：字符串字典序与资源档无关 ——
#: "heavy" < "light" 字典序为 False，会使全 heavy 计划聚合出 LIGHT）。
_RCLASS_SEVERITY = {
    ResourceClass.LIGHT: 0,
    ResourceClass.MEDIUM: 1,
    ResourceClass.HEAVY: 2,
    ResourceClass.RASTER: 3,
    ResourceClass.BROWSER: 3,
    ResourceClass.EXPORT: 3,
    ResourceClass.LLM: 3,
}

#: cache 复用可折扣的维（封闭词表：重复执行真的不重做的部分）。
#: memory 不在其中 —— 缓存命中仍要把产物载入内存。
CACHED_DIMS: Tuple[Dimension, ...] = (
    Dimension.WALL_TIME_S,
    Dimension.NETWORK_BYTES,
    Dimension.RENDER_WORK_UNITS,
    Dimension.ESTIMATED_LLM_COST,
    Dimension.EXTERNAL_SERVICE_CALLS,
)


class PlanNodeKind(str, Enum):
    """计划节点形状（封闭词表）。"""

    SEQUENTIAL = "sequential"   # 串行步（critical path 累加）
    PARALLEL = "parallel"       # 并行步（峰值叠加、wall 取 max）
    OPTIONAL = "optional"       # 可选步（不进主聚合；披露池）
    FALLBACK = "fallback"       # 回退分支（触发才计费；披露池）


class PlanNode(BaseModel):
    """计划中一个可估算节点（typed / serializable / bounded）。"""

    key: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=128)
    kind: PlanNodeKind = PlanNodeKind.SEQUENTIAL
    estimate: ResourceEstimate
    expected_attempts: int = Field(default=1, ge=1, le=MAX_EXPECTED_ATTEMPTS)
    cache_read_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    #: 本节点路径的科学语义标注（降级候选被选中时由 planner 填；R3 披露面）
    semantics: Optional[DegradationSemantics] = None

    def summary(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind.value,
            "attempts": self.expected_attempts,
            "cache_hit": self.cache_read_probability,
            "semantics": (self.semantics.value if self.semantics else None),
            "confidence": round(self.estimate.overall_confidence(), 2),
        }


class PlanAggregate(BaseModel):
    """计划级聚合产物（estimate + 解释性披露）。"""

    schema_version: str = AGGREGATE_VERSION
    estimate: ResourceEstimate
    critical_path: List[str] = Field(default_factory=list)
    retry_tail_s: float = 0.0
    #: 单节点最大 cache 命中声明（非整计划有效折扣 —— 折扣是逐节点乘性）
    max_node_cache_hit: float = 0.0
    parallel_peak_memory_bytes: float = 0.0
    optional_pool: List[Dict[str, Any]] = Field(default_factory=list)
    fallback_pool: List[Dict[str, Any]] = Field(default_factory=list)
    #: 计划内被地板计费的维（unknown≠0 的聚合面披露）
    floor_charged_dims: List[str] = Field(default_factory=list)
    #: 全计划最差科学语义承诺（None = 全部 comparable/未标注）
    worst_semantics: Optional[str] = None
    truncated: bool = False


def _range_of(dim: Dimension, dv: DimValue) -> Tuple[float, float, float]:
    """DimValue → 数值三元组（unknown 按该维保守地板计；unavailable 不进来）。"""
    if dv.certainty.value == "unknown":
        floor = float(CONSERVATIVE_FLOORS.get(dim, 0.0))
        return floor, floor, floor
    lo = dv.min if dv.min is not None else (dv.expected or 0.0)
    exp = dv.expected if dv.expected is not None else (dv.max or lo)
    hi = dv.max if dv.max is not None else exp
    return float(lo), float(exp), float(hi)


def _add(a: Tuple[float, float, float],
         b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _maxr(a: Tuple[float, float, float],
          b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]))


def _scale(r: Tuple[float, float, float], f: float) -> Tuple[float, float, float]:
    return (r[0] * f, r[1] * f, r[2] * f)


def _effective(node: PlanNode) -> Tuple[
        Dict[Dimension, Tuple[float, float, float]], Dict[Dimension, DimValue]]:
    """节点 → 聚合用有效数值（retry × cache × 地板）+ 原始 DimValue 留档。

    - WALL_TIME_S：× attempts × (1-p)（重试重做整步；缓存命中免重做）；
    - 其余 CACHED_DIMS：× attempts × (1-p)；
    - 非缓存累计维：× attempts；
    - MEMORY_BYTES：原值（重试串行不叠乘；缓存命中仍要载入）；
    - unknown 维：地板三元组参与数值计算。
    """
    eff: Dict[Dimension, Tuple[float, float, float]] = {}
    raw: Dict[Dimension, DimValue] = {}
    cache_keep = 1.0 - node.cache_read_probability
    for dim in Dimension:
        dv = node.estimate.dim(dim)
        if not dv.is_meaningful():
            continue
        raw[dim] = dv
        r = _range_of(dim, dv)
        if dim is Dimension.MEMORY_BYTES:
            eff[dim] = r
        elif dim is Dimension.WALL_TIME_S:
            eff[dim] = _scale(_scale(r, node.expected_attempts), cache_keep)
        elif dim in CACHED_DIMS:
            eff[dim] = _scale(_scale(r, node.expected_attempts), cache_keep)
        else:
            eff[dim] = _scale(r, node.expected_attempts)
    return eff, raw


def _pool_entry(node: PlanNode) -> Dict[str, Any]:
    eff, raw = _effective(node)
    dims_view: Dict[str, float] = {}
    for dim, r in sorted(eff.items(), key=lambda kv: kv[0].value):
        dims_view[dim.value] = round(r[1], 2)
    entry = node.summary()
    entry["expected_dims"] = dims_view
    unknown = [d.value for d, dv in raw.items()
               if dv.certainty.value == "unknown"]
    if unknown:
        entry["unknown_dims"] = unknown
    return entry


def aggregate_plan(
    nodes: List[PlanNode],
    *,
    subsystem: Subsystem = Subsystem.TOOL_DISPATCH,
) -> PlanAggregate:
    """计划节点序列 → 结构化聚合（确定性；同输入必同输出）。"""
    truncated = len(nodes) > MAX_PLAN_NODES
    nodes = list(nodes[:MAX_PLAN_NODES])

    main = [n for n in nodes
            if n.kind in (PlanNodeKind.SEQUENTIAL, PlanNodeKind.PARALLEL)]
    optional = [n for n in nodes if n.kind is PlanNodeKind.OPTIONAL]
    fallback = [n for n in nodes if n.kind is PlanNodeKind.FALLBACK]

    # 连续同类主路径节点成 run（声明序；跨 run 形状切换即分段）
    runs: List[Tuple[PlanNodeKind, List[PlanNode]]] = []
    for n in main:
        if runs and runs[-1][0] is n.kind:
            runs[-1][1].append(n)
        else:
            runs.append((n.kind, [n]))

    wall_path = (0.0, 0.0, 0.0)
    mem_peak = (0.0, 0.0, 0.0)
    cumulative: Dict[Dimension, Tuple[float, float, float]] = {}
    critical_path: List[str] = []
    retry_tail_s = 0.0
    max_node_cache_hit = 0.0
    floor_dims: set = set()
    worst_sem: Optional[DegradationSemantics] = None
    conf_shortboard: Optional[float] = None
    rclass_max = ResourceClass.LIGHT

    _SEM_ORDER = {
        DegradationSemantics.COMPARABLE: 0,
        DegradationSemantics.APPROXIMATE: 1,
        DegradationSemantics.NON_COMPARABLE: 2,
    }

    for kind, members in runs:
        run_wall = (0.0, 0.0, 0.0)
        run_mem = (0.0, 0.0, 0.0)
        run_tail = 0.0
        path_keys: List[str] = []
        argmax_key = ""
        argmax_tail = 0.0
        argmax_wall = (-1.0, -1.0, -1.0)
        for n in members:
            eff, raw = _effective(n)
            w = eff.get(Dimension.WALL_TIME_S, (0.0, 0.0, 0.0))
            m = eff.get(Dimension.MEMORY_BYTES, (0.0, 0.0, 0.0))
            # 单步 tail（期望语义）：effective − 去掉 retry 乘数的单次值
            step_tail = w[1] - (w[1] / n.expected_attempts
                                if n.expected_attempts else w[1])
            for dim, dv in raw.items():
                if dv.certainty.value == "unknown":
                    # unknown≠0 留痕：wall/mem 的地板进路径数值，其余进累计
                    floor_dims.add(dim.value)
            for dim, r in eff.items():
                if dim in (Dimension.WALL_TIME_S, Dimension.MEMORY_BYTES):
                    continue
                cumulative[dim] = _add(cumulative.get(dim, (0.0, 0.0, 0.0)), r)
            if (_RCLASS_SEVERITY.get(n.estimate.resource_class, 0)
                    > _RCLASS_SEVERITY.get(rclass_max, 0)):
                rclass_max = n.estimate.resource_class
            nc = n.estimate.overall_confidence()
            if conf_shortboard is None or nc < conf_shortboard:
                conf_shortboard = nc
            if n.semantics is not None and (
                worst_sem is None
                or _SEM_ORDER[n.semantics] > _SEM_ORDER[worst_sem]
            ):
                worst_sem = n.semantics
            if n.cache_read_probability > max_node_cache_hit:
                max_node_cache_hit = n.cache_read_probability
            if kind is PlanNodeKind.SEQUENTIAL:
                run_wall = _add(run_wall, w)
                run_mem = _maxr(run_mem, m)
                run_tail += step_tail
                path_keys.append(n.key)
            else:  # PARALLEL：wall/memory 走 max/sum，tail 只留 argmax 成员
                run_wall = _maxr(run_wall, w)
                run_mem = _add(run_mem, m)
                if w > argmax_wall:
                    argmax_wall = w
                    argmax_key = n.key
                    argmax_tail = step_tail
        if kind is PlanNodeKind.SEQUENTIAL:
            retry_tail_s += run_tail
            critical_path.extend(path_keys)
        else:
            retry_tail_s += argmax_tail
            if argmax_key:
                critical_path.append(argmax_key)
        wall_path = _add(wall_path, run_wall)
        mem_peak = _maxr(mem_peak, run_mem)

    dims: Dict[Dimension, DimValue] = {}
    if wall_path[2] > 0:
        dims[Dimension.WALL_TIME_S] = DimValue.estimated(
            wall_path[0], wall_path[1], wall_path[2],
            confidence=conf_shortboard if conf_shortboard is not None else 0.4,
            source=AGGREGATE_VERSION,
            reason=f"critical path over {len(critical_path)} nodes",
        )
    if mem_peak[1] > 0 or mem_peak[2] > 0:
        dims[Dimension.MEMORY_BYTES] = DimValue.estimated(
            mem_peak[0], mem_peak[1], mem_peak[2],
            confidence=conf_shortboard if conf_shortboard is not None else 0.4,
            source=AGGREGATE_VERSION,
            reason="live peak (sequential max / parallel sum)",
        )
    for dim, r in cumulative.items():
        dims[dim] = DimValue.estimated(
            r[0], r[1], r[2],
            confidence=conf_shortboard if conf_shortboard is not None else 0.4,
            source=AGGREGATE_VERSION,
            reason="cumulative sum × retry × cache",
        )

    parallel_peak_runs = [
        sum(_effective(m)[0].get(Dimension.MEMORY_BYTES, (0.0, 0.0, 0.0))[1]
            for m in members)
        for kind, members in runs if kind is PlanNodeKind.PARALLEL
    ]

    estimate = ResourceEstimate(
        subsystem=subsystem,
        resource_class=rclass_max,
        dims=dims,
        confidence=conf_shortboard if conf_shortboard is not None else 0.4,
        source=AGGREGATE_VERSION,
        reason=(
            f"runs={len(runs)} optional={len(optional)} "
            f"fallback={len(fallback)} floors={sorted(floor_dims)}"
        ),
    )
    return PlanAggregate(
        estimate=estimate,
        critical_path=critical_path,
        retry_tail_s=round(retry_tail_s, 3),
        max_node_cache_hit=round(max_node_cache_hit, 3),
        parallel_peak_memory_bytes=(
            max(parallel_peak_runs) if parallel_peak_runs else 0.0),
        optional_pool=[_pool_entry(n) for n in optional],
        fallback_pool=[_pool_entry(n) for n in fallback],
        floor_charged_dims=sorted(floor_dims),
        worst_semantics=(worst_sem.value if worst_sem else None),
        truncated=truncated,
    )


def budget_violations(
    estimate: ResourceEstimate,
    limits: Dict[Dimension, float],
    *,
    conservative_floors: Optional[Dict[Dimension, float]] = None,
) -> List[str]:
    """聚合估算 vs 预算上限的违规清单（feasibility 输入；确定性）。

    ``conservative_floors`` 允许预算方按 scope 覆盖 unknown 地板（与
    DimValue.adjudged 同参数语义）—— 接 feasibility 判定时应传预算侧
    覆盖表，避免与准入口径分叉。
    """
    out: List[str] = []
    for dim in Dimension:
        cap = limits.get(dim)
        if cap is None:
            continue
        value = estimate.dim(dim).adjudged(
            conservative_floors=conservative_floors, dim=dim)
        if value > float(cap):
            out.append(f"{dim.value}:{value:.4g}>{float(cap):.4g}")
    return out


__all__ = [
    "AGGREGATE_VERSION",
    "MAX_PLAN_NODES",
    "MAX_EXPECTED_ATTEMPTS",
    "CACHED_DIMS",
    "PlanNodeKind",
    "PlanNode",
    "PlanAggregate",
    "aggregate_plan",
    "budget_violations",
]
