"""V6 join 枚举（ADR-0118 W4）：join graph 上的子集 DP，left-deep + bushy。

ADR-0101 D7 明示 deferred 的 "bushy join-tree search" 在此落地：

- **输入是纯事实**（SourceFacts/JoinEdge），无 adapter IO —— 解析/探测由
  V6 planner（W7）完成；
- **结构性有界**：n ≤ 4 → 子集 ≤16，每子集保留 top-K 候选，替代披露 ≤8；
  绝不指数；
- **确定性**：子集按 (popcount, bitmask) 序枚举；并列成本用计划哈希
  tie-break；同输入必同输出；
- **边方向语义**：spatial/aggregate 跳是方向敏感的（within 的左点右面、
  aggregate 的右分组）—— 只允许与声明一致的子树方位；attribute 跳是
  对称内连接，两侧方位都合法（键随方位归一）；
- **位置寻址 joins**（无 id 对）无法安全重排 → 恒等序链 + warning
  （V5 parity）。

成本口径公开：传输字节（投影感知）+ 本地 join CPU 行 + CRS 变换行 +
远端请求；权重数值与 optimizer.py 既有权重系同源（不建第二权重真相）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_fabric.query.federated.logical import (
    LogicalJoin,
    LogicalNode,
    LogicalReproject,
    LogicalScan,
)
from app.services.data_fabric.query.federated.spatial_stats import (
    SpatialGridHistogram,
    estimate_bbox_selectivity,
)
from app.services.data_fabric.query.selectivity import estimate_predicate_selectivity
from app.services.data_fabric.query.statistics import DatasetStatistics

#: 与 V5 链式联邦同一条红线（单一数值源在 federation.MAX_FEDERATED_SOURCES；
#: 本处镜像并在校验时惰性对账，避免模块级反向依赖）。
MAX_FEDERATED_SOURCES = 4

#: 每子集保留的最优候选数（剪枝；结构性有界）。
TOP_K_PER_SUBSET = 3
#: EXPLAIN 替代披露上限（与 optimizer.MAX_ALTERNATIVES 同口径）。
MAX_ALTERNATIVES = 8

# 成本权重（与 optimizer.py 既有权重系一致的数值语义）
_W_BYTES_TRANSFERRED = 1.0
_W_LOCAL_CPU_PER_ROW = 0.5
_W_BUILD_PER_ROW = 0.05  # 右侧（build/物化侧）每行材料化成本
_W_REMOTE_REQUEST = 50.0
_W_CRS_TRANSFORM_PER_ROW = 1.0
_BYTE_PER_FEATURE_GEO = 1_800
_BYTE_PER_FEATURE_PROJECTED = 700
_UNESTIMATED_ROWS = 1_000_000
_SPATIAL_SHRINK_WITHIN = 0.4
_SPATIAL_SHRINK_INTERSECTS = 0.6


@dataclass
class SourceFacts:
    """单源规划事实（V6 planner 从 request + adapter 探测解析而来）。"""

    source_id: str
    dataset_id: str
    estimated_rows: Optional[int] = None
    row_count: Optional[int] = None
    column_ndv: Dict[str, int] = field(default_factory=dict)
    crs: Optional[str] = None
    server_reprojection: bool = False
    extent: Optional[List[float]] = None
    spatial_histogram: Optional[Dict[str, Any]] = None
    where: Optional[Any] = None  # Predicate 实例（已解析）
    where_raw: Optional[str] = None  # 不可解析的原始过滤（诚实保留）
    fields: Optional[List[str]] = None
    stats: Optional[DatasetStatistics] = None  # 完整统计（可选；selectivity 消费）
    caps: Optional[Any] = None  # AdapterCapabilitiesV2（W10 探测注入；下推边界解释）
    # ── V7（ADR-0119 additive）──
    #: 被动观测的限流提示（probing；None = 未知 → 无惩罚，绝不虚构）。
    rate_limit: Optional[Any] = None
    #: measured 级唯一键字段（stats_hints 显式声明或 pg_index 探测；
    #: 估计 NDV 不作数 —— R-C1 证明条件 3）。
    unique_keys: List[str] = field(default_factory=list)


@dataclass
class JoinEdge:
    """join graph 的边（id 寻址；positional_index 标记 V3 位置语义）。"""

    left_source_id: Optional[str]
    right_source_id: Optional[str]
    kind: str  # attribute_join | spatial_join | aggregate_join
    join_field_left: Optional[str] = None
    join_field_right: Optional[str] = None
    spatial_op: Optional[str] = None
    group_by_right: Optional[List[str]] = None
    aggregates: Optional[List[Dict[str, Any]]] = None
    positional_index: Optional[int] = None  # 非 None = 位置寻址（不可重排）

    @property
    def id_addressed(self) -> bool:
        return bool(self.left_source_id and self.right_source_id)

    def swapped(self) -> "JoinEdge":
        """方向归一的边（仅 attribute join 允许；键随方位互换）。"""
        return JoinEdge(
            left_source_id=self.right_source_id,
            right_source_id=self.left_source_id,
            kind=self.kind,
            join_field_left=self.join_field_right,
            join_field_right=self.join_field_left,
            spatial_op=self.spatial_op,
            group_by_right=self.group_by_right,
            aggregates=self.aggregates,
            positional_index=self.positional_index,
        )


@dataclass
class EnumerationContext:
    """枚举输入（纯数据；planner 组装）。"""

    sources: List[SourceFacts]
    joins: List[JoinEdge]
    limit: int = 10_000
    bbox: Optional[List[float]] = None
    order_strategy: str = "cost"  # cost | given | cost_stats
    # ── V8（ADR-0130 additive）：治理面富集披露（source_id → basis）。
    # 纯数据传递；EXPLAIN 如实渲染，None = 无富集（输出与 V7 逐位一致）。
    estimate_basis: Optional[Dict[str, Dict[str, Any]]] = None


@dataclass
class EnumeratedPlan:
    """枚举产出：树 + 成本分解 + 披露。"""

    tree: LogicalNode
    cost: float
    components: Dict[str, Any]
    order: List[str]  # 左深链形状下的源序（展示/兼容用）
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    crs_transforms: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def plan_hash(self) -> str:
        return self.tree.plan_hash()


@dataclass
class _Candidate:
    tree: LogicalNode
    sources_in: Tuple[str, ...]
    card: int
    cost: float
    components: Dict[str, Any]
    crs_transforms: List[Dict[str, Any]]
    key: str  # 确定性 tie-break
    #: 子树输出的**累积几何 CRS**（首个空间跳左源经变换后的 srid）。
    #: C1（评审 R1）：join 的 CRS 决策必须以两侧子树的输出 CRS 为输入，
    #: 不能用边声明源的 CRS 代表整个子树 —— 否则混合 CRS 多跳链会静默错位。
    out_srid: Optional[int] = None


# ── 成本核（纯函数）──────────────────────────────────────────────────────


def _parse_srid(crs: Optional[str]) -> Optional[int]:
    if not crs:
        return None
    s = str(crs).strip().upper()
    if s.endswith("CRS84") or s == "OGC:CRS84":
        return 4326
    if s.startswith("EPSG:"):
        body = s[5:]
        return int(body) if body.isdigit() else None
    return int(s) if s.isdigit() else None


def _crs_transform_meta(
    edge: JoinEdge,
    left: SourceFacts,
    right: SourceFacts,
    card_left: int,
    card_right: int,
    left_srid: Optional[int] = None,
    right_srid: Optional[int] = None,
    *,
    server_capable: Tuple[bool, bool] = (False, False),
) -> Tuple[float, Optional[Dict[str, Any]]]:
    """CRS 对齐成本（单一决策 API：costing.decide_crs_transform）。

    V7（ADR-0119 W8）：``server_capable`` 标记（left, right）两侧是否为
    **scan-like 且 caps 声明 output_crs_pushdown** —— 此时允许 server
    placement（ADR-0118 KL#2 兑现）；子树侧维持本地（无法回溯重扫）。
    """
    from app.services.data_fabric.query.federated.costing import decide_crs_transform

    # C1（评审 R1）：CRS 输入用**子树输出 srid**（调用方传入）；缺省回落
    # 边声明源（单跳链两者一致）。
    ls = left_srid if left_srid is not None else _parse_srid(left.crs)
    rs = right_srid if right_srid is not None else _parse_srid(right.crs)
    decision = decide_crs_transform(
        left_crs_srid=ls,
        right_crs_srid=rs,
        join_kind=edge.kind,
        caps_left=left,
        caps_right=right,
        est_left_rows=card_left,
        est_right_rows=card_right,
        allow_server=bool(server_capable[0] or server_capable[1]),
        # 单侧可行性（scan-like + 已验证通道）进决策 —— 成本与可选 placement
        # 一致，绝无「选中后降级」的不诚实估价。
        server_feasible=server_capable,
    )
    if decision.placement == "none":
        return 0.0, {
            "placement": "none",
            "transform_side": None,
            "reason": decision.reason,
            **(
                {"correctness_note": decision.correctness_note}
                if decision.correctness_note
                else {}
            ),
        }
    cost = (
        decision.per_row_cost
        * (card_left if decision.transform_side == "left" else card_right)
        * _W_CRS_TRANSFORM_PER_ROW
    )
    return cost, {
        "placement": decision.placement,
        "transform_side": decision.transform_side,
        "reason": decision.reason,
        "from_crs": f"EPSG:{src_of(decision, ls, rs)}",
        "to_crs": f"EPSG:{dst_of(decision, ls, rs)}",
        "estimated_rows": (
            card_left if decision.transform_side == "left" else card_right
        ),
    }


def src_of(decision: Any, ls: Optional[int], rs: Optional[int]) -> Optional[int]:
    """变换侧的源 SRID（decision transform_side → srid）。"""
    if decision.transform_side == "left":
        return ls
    return rs


def dst_of(decision: Any, ls: Optional[int], rs: Optional[int]) -> Optional[int]:
    if decision.transform_side == "left":
        return rs
    return ls


def _extent_overlap_ratio(left: SourceFacts, right: SourceFacts) -> Optional[float]:
    """两侧 extent 的重叠积占左 extent 比（无 hist 时的空间跳基数输入）。"""
    le, re_ = left.extent, right.extent
    if not le or not re_ or len(le) != 4 or len(re_) != 4:
        return None
    ox = min(le[2], re_[2]) - max(le[0], re_[0])
    oy = min(le[3], re_[3]) - max(le[1], re_[1])
    if ox <= 0 or oy <= 0:
        return 0.0
    area_l = max(1e-12, (le[2] - le[0]) * (le[3] - le[1]))
    return max(0.0, min(1.0, (ox * oy) / area_l))


def _spatial_hop_factor(
    left: SourceFacts, right: SourceFacts, spatial_op: Optional[str]
) -> float:
    """空间跳每左行命中因子：hist 重叠 → extent 重叠 → 诚实上界 1.0。"""
    hist = (
        SpatialGridHistogram.from_meta(left.spatial_histogram)
        if left.spatial_histogram
        else None
    )
    ratio: Optional[float] = None
    if hist is not None and right.extent:
        ratio = estimate_bbox_selectivity(hist, right.extent)
    if ratio is None:
        ratio = _extent_overlap_ratio(left, right)
    if ratio is None:
        return 1.0  # 无任何空间证据：不虚构（最坏方向）
    shrink = (
        _SPATIAL_SHRINK_WITHIN
        if (spatial_op or "within") == "within"
        else _SPATIAL_SHRINK_INTERSECTS
    )
    return max(0.0, min(1.0, ratio)) * shrink


def _scan_transfer_rows(src: SourceFacts) -> int:
    base = src.estimated_rows if src.estimated_rows is not None else _UNESTIMATED_ROWS
    sel = 1.0
    where = src.where
    if where is not None and not isinstance(where, str) and getattr(where, "op", None):
        sel = estimate_predicate_selectivity(where, src.stats).value
    return max(1, int(base * max(sel, 1e-9)))


def _per_feature_bytes(fields: Optional[List[str]]) -> int:
    return _BYTE_PER_FEATURE_PROJECTED if fields else _BYTE_PER_FEATURE_GEO


def _scan_cost(src: SourceFacts, *, bytes_cost: float) -> "tuple[float, Dict[str, Any]]":
    """V7 扫描成本（W7）：传输字节 × 不确定性乘子 + 请求惩罚（rate-limit）。

    返回 (cost, disclosure) —— disclosure 进 components 供 EXPLAIN 复述；
    确定性：同输入同乘子（stats.confidence 与 hints 均为规划期事实）。
    """
    from app.services.data_fabric.query.federated.costing import (
        estimate_uncertainty_multiplier,
        rate_limit_request_penalty,
    )

    unc, basis = estimate_uncertainty_multiplier(src.stats)
    rl_penalty = rate_limit_request_penalty(src.rate_limit)
    disclosure: Dict[str, Any] = {"uncertainty": unc, "confidence": basis}
    if rl_penalty:
        disclosure["rate_limit_penalty"] = round(rl_penalty, 4)
    return bytes_cost * unc + rl_penalty, disclosure


def _scan_tree(src: SourceFacts, ctx: EnumerationContext) -> LogicalScan:
    return LogicalScan(
        source_id=src.source_id,
        dataset_id=src.dataset_id,
        where=src.where if getattr(src.where, "op", None) else None,
        where_raw=src.where_raw,
        bbox=list(ctx.bbox) if ctx.bbox else None,
        fields=list(src.fields) if src.fields else None,
        fetch_limit=ctx.limit,
        crs=src.crs,
        estimated_rows=src.estimated_rows,
    )


def _fed_error(msg: str, **details: Any) -> Exception:
    """typed FederatedQueryError（惰性导入避免 federation ↔ federated 环）。"""
    from app.services.data_fabric.query.federation import FederatedQueryError

    return FederatedQueryError(msg, details=details or None)


def _ndv_of(
    sources: Tuple[str, ...], field_name: Optional[str], by_id: Dict[str, SourceFacts]
) -> Optional[int]:
    if not field_name:
        return None
    best: Optional[int] = None
    for sid in sources:
        s = by_id.get(sid)
        if s is None:
            continue
        v = s.column_ndv.get(field_name)
        if v and (best is None or v > best):
            best = v
    return best


# ── 枚举主体 ────────────────────────────────────────────────────────────────


def enumerate_federation(ctx: EnumerationContext) -> EnumeratedPlan:
    """join graph 上的有界 DP：返回成本最优连通树（纯函数）。"""
    n = len(ctx.sources)
    if n < 2:
        raise _fed_error("chain federation requires at least 2 sources")
    from app.services.data_fabric.query.federation import (
        MAX_FEDERATED_SOURCES as V5_MAX,
    )

    if n > V5_MAX or n > MAX_FEDERATED_SOURCES:
        raise _fed_error(
            f"chain federation supports at most {V5_MAX} sources (got {n}); "
            "bounded planning is a red line"
        )
    if len(ctx.joins) != n - 1:
        raise _fed_error(
            f"chain requires exactly len(sources)-1 joins "
            f"({len(ctx.joins)} given for {n} sources)"
        )
    if ctx.limit <= 0:
        raise _fed_error("limit must be positive")

    by_id = {s.source_id: s for s in ctx.sources}
    if len(by_id) != n:
        raise _fed_error("duplicate source_id in chain")
    id_order = [s.source_id for s in ctx.sources]

    if any(not e.id_addressed for e in ctx.joins):
        return _enumerate_fixed_chain(ctx, by_id, id_order, positional=True)
    if all(s.estimated_rows is None for s in ctx.sources):
        # 无任何成本信号 → 保持 given 序（V5 默认行为逐位一致：重排会翻转
        # __right__ 的归属侧，属用户可见形状变化 —— 没有测量背书不做）。
        plan = _enumerate_fixed_chain(ctx, by_id, id_order, positional=False)
        plan.warnings.insert(
            0,
            "no estimated_rows hints available; order follows the given "
            "sequence (estimates are assumptions)",
        )
        return plan

    id_edges: Dict[str, JoinEdge] = {}
    for e in ctx.joins:
        if e.left_source_id not in by_id or e.right_source_id not in by_id:
            raise _fed_error(
                f"join references unknown source ({e.left_source_id}>{e.right_source_id})"
            )
        if e.left_source_id == e.right_source_id:
            raise _fed_error(f"self join on {e.left_source_id!r} is not supported")
        key = f"{e.left_source_id}>{e.right_source_id}"
        rev = f"{e.right_source_id}>{e.left_source_id}"
        if key in id_edges or rev in id_edges:
            raise _fed_error(f"duplicate join edge between {key} / {rev}")
        id_edges[key] = e

    idx_of = {sid: i for i, sid in enumerate(id_order)}
    full = (1 << n) - 1
    dp: Dict[int, List[_Candidate]] = {}

    def edge_between(mask_a: int, mask_b: int) -> Optional[JoinEdge]:
        """连接两子集的边（确定性：按 id 对字典序取第一条）。"""
        found: List[Tuple[str, JoinEdge]] = []
        for key in sorted(id_edges):
            e = id_edges[key]
            a, b = idx_of[e.left_source_id], idx_of[e.right_source_id]
            if ((mask_a >> a) & 1 and (mask_b >> b) & 1) or (
                (mask_a >> b) & 1 and (mask_b >> a) & 1
            ):
                found.append((key, e))
        return found[0][1] if found else None

    for i, sid in enumerate(id_order):
        src = by_id[sid]
        rows = _scan_transfer_rows(src)
        bytes_ = rows * _per_feature_bytes(src.fields)
        scan_cost, scan_disc = _scan_cost(
            src, bytes_cost=bytes_ * _W_BYTES_TRANSFERRED
        )
        scan_cost += _W_REMOTE_REQUEST
        dp[1 << i] = [
            _Candidate(
                tree=_scan_tree(src, ctx),
                sources_in=(sid,),
                card=rows,
                cost=scan_cost,
                components={"scans": {sid: {"rows": rows, "bytes": bytes_, **scan_disc}}},
                crs_transforms=[],
                key=f"scan:{sid}",
                out_srid=_parse_srid(src.crs),
            )
        ]

    for mask in sorted(range(1, full + 1), key=lambda m: (bin(m).count("1"), m)):
        if bin(mask).count("1") < 2:
            continue
        candidates: List[_Candidate] = []
        sub = (mask - 1) & mask
        while sub > 0:
            rest = mask & ~sub
            if rest:
                # 两侧角色交换是**不同计划**（build 侧不同）—— 两个方向都枚举；
                # 方向合法性（spatial/aggregate 语义）由 _join_candidate 守卫。
                candidates.extend(_combine(dp, sub, rest, edge_between, by_id, ctx))
                candidates.extend(_combine(dp, rest, sub, edge_between, by_id, ctx))
            sub = (sub - 1) & mask
        if candidates:
            candidates.sort(key=lambda c: (c.cost, c.key))
            dp[mask] = candidates[:TOP_K_PER_SUBSET]

    best_list = dp.get(full)
    if not best_list:
        raise _fed_error(
            "join graph does not connect all sources into a tree; declare "
            "id-addressed joins covering every source pair path"
        )

    best = best_list[0]
    order = (
        _chain_order_hint(best.tree)
        if _is_chain_shape(best.tree)
        else list(best.sources_in)
    )

    alternatives: List[Dict[str, Any]] = []
    for cand in best_list[1:]:
        alternatives.append(
            {
                "name": f"order[{','.join(cand.sources_in)}]",
                "description": "left-deep" if _is_chain_shape(cand.tree) else "bushy",
                "feasible": True,
                "rejected_reason": f"higher cost ({cand.cost:.0f} > {best.cost:.0f})",
                "cost": round(cand.cost, 2),
            }
        )
    for mask in sorted(dp.keys()):
        if mask == full or bin(mask).count("1") < 2:
            continue
        for cand in dp[mask][1:]:
            if len(alternatives) >= MAX_ALTERNATIVES:
                break
            alternatives.append(
                {
                    "name": f"subplan[{','.join(cand.sources_in)}]",
                    "description": "bushy"
                    if not _is_chain_shape(cand.tree)
                    else "left-deep",
                    "feasible": True,
                    "rejected_reason": "subplan pruned (not cheapest in subset)",
                    "cost": round(cand.cost, 2),
                }
            )
            break
        if len(alternatives) >= MAX_ALTERNATIVES:
            break

    return EnumeratedPlan(
        tree=best.tree,
        cost=best.cost,
        components=best.components,
        order=order,
        alternatives=alternatives[:MAX_ALTERNATIVES],
        warnings=[],
        crs_transforms=best.crs_transforms,
    )


def _enumerate_fixed_chain(
    ctx: EnumerationContext,
    by_id: Dict[str, SourceFacts],
    id_order: List[str],
    *,
    positional: bool = True,
) -> EnumeratedPlan:
    """给定序左深链逐跳估价（无重排 —— V5 parity）。

    ``positional``：仅当请求确实使用位置寻址 joins 时披露该 warning；
    无统计提示的 id 寻址链同样走本路径，但语义是「保持 given 序」，
    不应误导用户去声明已有的 id 对（m-1，评审 R2）。
    """
    warnings = []
    if positional:
        warnings.append(
            "positional joins detected; order is the given sequence "
            "(declare left/right_source_id to enable cost-based enumeration)"
        )
    tree: LogicalNode = _scan_tree(by_id[id_order[0]], ctx)
    card = _scan_transfer_rows(by_id[id_order[0]])
    first_bytes = card * _per_feature_bytes(by_id[id_order[0]].fields)
    first_cost, first_disc = _scan_cost(
        by_id[id_order[0]], bytes_cost=first_bytes * _W_BYTES_TRANSFERRED
    )
    cost = first_cost + _W_REMOTE_REQUEST
    scans = {
        "scans": {
            id_order[0]: {
                "rows": card,
                "bytes": first_bytes,
                **first_disc,
            }
        }
    }
    transforms: List[Dict[str, Any]] = []
    acc_out_srid: Optional[int] = _parse_srid(by_id[id_order[0]].crs)
    sources_in: Tuple[str, ...] = (id_order[0],)
    for i, edge in enumerate(ctx.joins):
        right_sid = id_order[i + 1]
        right_src = by_id[right_sid]
        right_rows = _scan_transfer_rows(right_src)
        right_bytes = right_rows * _per_feature_bytes(right_src.fields)
        left_src = by_id[sources_in[0]]
        left_srid = acc_out_srid if acc_out_srid is not None else _parse_srid(left_src.crs)
        right_srid = _parse_srid(right_src.crs)
        crs_cost, crs_meta = _crs_transform_meta(
            edge, left_src, right_src, card, right_rows,
            left_srid=left_srid, right_srid=right_srid,
            server_capable=(
                _caps_output_crs(left_src),  # 累积侧恒为子树：server 不可行
                _caps_output_crs(right_src),
            ),
        )
        if crs_meta:
            transforms.append(crs_meta)
        right_tree: LogicalNode = _scan_tree(right_src, ctx)
        if crs_meta and crs_meta.get("placement") == "server" and right_tree is not None:
            # server placement 必然落在右 scan（累积左子树无法回溯重扫）。
            right_tree = _with_output_crs(right_tree, crs_meta["to_crs"])
        right_cand = _Candidate(
            tree=right_tree,
            sources_in=(right_sid,),
            card=right_rows,
            cost=right_bytes,
            components={},
            crs_transforms=[],
            key=f"scan:{right_sid}",
        )
        left_cand = _Candidate(
            tree=tree,
            sources_in=sources_in,
            card=card,
            cost=cost,
            components=scans,
            crs_transforms=[],
            key="chain",
        )
        new_card = _join_cardinality(edge, left_cand, right_cand, by_id)
        cpu = new_card * _W_LOCAL_CPU_PER_ROW
        build = right_rows * _W_BUILD_PER_ROW
        cost = cost + right_bytes + cpu + build + crs_cost
        left_tree: LogicalNode = tree
        if (
            crs_meta
            and crs_meta.get("placement") == "local"
            and crs_meta.get("transform_side")
        ):
            acc_out_srid = _parse_srid(crs_meta["to_crs"])
            reproj_kwargs = {
                "from_crs": crs_meta["from_crs"],
                "to_crs": crs_meta["to_crs"],
                "placement": "local",
            }
            if crs_meta["transform_side"] == "left":
                left_tree = LogicalReproject(input=left_tree, **reproj_kwargs)
            else:
                right_tree = LogicalReproject(input=right_tree, **reproj_kwargs)
        elif crs_meta and crs_meta.get("placement") in ("local", "server") and crs_meta.get("transform_side"):
            # server placement：交付即目标 CRS（scan 已带 output_crs）。
            acc_out_srid = _parse_srid(crs_meta["to_crs"])
        elif left_srid is not None:
            acc_out_srid = left_srid
        tree = LogicalJoin(
            join_kind=edge.kind,
            left=left_tree,
            right=right_tree,
            join_field_left=edge.join_field_left,
            join_field_right=edge.join_field_right,
            spatial_op=edge.spatial_op if edge.kind == "spatial_join" else None,
            group_by_right=list(edge.group_by_right) if edge.group_by_right else None,
            aggregates=list(edge.aggregates) if edge.aggregates else None,
        )
        card = new_card
        sources_in = sources_in + (right_sid,)
    return EnumeratedPlan(
        tree=tree,
        cost=cost,
        components=scans,
        order=list(id_order),
        alternatives=[],
        warnings=warnings,
        crs_transforms=transforms,
    )


def _combine(
    dp: Dict[int, List[_Candidate]],
    mask_a: int,
    mask_b: int,
    edge_between,
    by_id: Dict[str, SourceFacts],
    ctx: EnumerationContext,
) -> List[_Candidate]:
    """两个子计划经连接边合成候选（bushy 在此自然涌现；方向语义守卫）。"""
    edge = edge_between(mask_a, mask_b)
    if edge is None:
        return []
    out: List[_Candidate] = []
    for left in dp.get(mask_a, []):
        for right in dp.get(mask_b, []):
            cand = _join_candidate(left, right, edge, by_id, ctx)
            if cand is not None:
                out.append(cand)
    return out


def _join_candidate(
    left: _Candidate,
    right: _Candidate,
    edge: JoinEdge,
    by_id: Dict[str, SourceFacts],
    ctx: EnumerationContext,
) -> Optional[_Candidate]:
    """方向语义守卫下的合成候选；方位不合法返回 None。"""
    if (
        edge.left_source_id in left.sources_in
        and edge.right_source_id in right.sources_in
    ):
        eff = edge
    else:
        # 方向语义守卫：边方位决定行形状（__right__ 归属）与 spatial/aggregate
        # 语义 —— 绝不换位（attribute 内连接虽逻辑对称，翻转属于用户可见的
        # 输出形状变化，必须由声明的边方向决定）。
        return None
    # 空间跳的 build 侧必须是**原始 scan**（或其 Reproject 包装）：join 子树
    # 的累积行几何存于 __right_geometry__/__left_geometry__，spatial_join_local
    # 读 "geometry" 字段 —— 子树右孩子会静默空结果（评审 R1 对 C1 探针的推广）。
    if eff.kind == "spatial_join" and not _is_scan_like(right.tree):
        return None
    lsrc = by_id.get(eff.left_source_id) or _first_src(left, by_id)
    rsrc = by_id.get(eff.right_source_id) or _first_src(right, by_id)
    # C1：左侧输入 CRS = 左子树**输出** CRS（累积几何的真实坐标系），
    # 绝不用边声明源 CRS 代表整个子树（混合 CRS 多跳链会静默错位）。
    left_srid = left.out_srid if left.out_srid is not None else _parse_srid(lsrc.crs)
    right_srid = right.out_srid if right.out_srid is not None else _parse_srid(rsrc.crs)
    crs_cost, crs_meta = _crs_transform_meta(
        eff, lsrc, rsrc, left.card, right.card,
        left_srid=left_srid, right_srid=right_srid,
        server_capable=(
            _is_scan_like(left.tree) and _caps_output_crs(lsrc),
            _is_scan_like(right.tree) and _caps_output_crs(rsrc),
        ),
    )
    card = _join_cardinality(eff, left, right, by_id)
    cpu = card * _W_LOCAL_CPU_PER_ROW
    build = right.card * _W_BUILD_PER_ROW  # 右侧物化进哈希/空间索引
    total = left.cost + right.cost + cpu + build + crs_cost
    comps: Dict[str, Any] = {
        "join": {
            "kind": eff.kind,
            "card": card,
            "cpu": round(cpu, 2),
            "build_rows": right.card,
        },
        # 嵌套子树成分（extract_hop_estimates 的 post-order 链序提取依赖它）
        "left": left.components,
        "right": right.components,
    }
    transforms = list(left.crs_transforms) + list(right.crs_transforms)
    left_tree, right_tree = left.tree, right.tree
    if crs_meta:
        transforms.append(crs_meta)
        # 本地变换成为显式计划节点：executor 在 build/探针缓存构建前
        # 一次性变换该侧几何（physical.transform_rows_geometry）。
        if crs_meta.get("placement") == "local" and crs_meta.get("transform_side"):
            reproj_kwargs = {
                "from_crs": crs_meta["from_crs"],
                "to_crs": crs_meta["to_crs"],
                "placement": "local",
            }
            if crs_meta["transform_side"] == "left":
                left_tree = LogicalReproject(input=left_tree, **reproj_kwargs)
            else:
                right_tree = LogicalReproject(input=right_tree, **reproj_kwargs)
        # V7（ADR-0119 W8）：server placement → scan 携带 output_crs（服务端
        # 变换交付）；无 Reproject 节点，out_srid 账本记交付 CRS。
        elif crs_meta.get("placement") == "server" and crs_meta.get("transform_side"):
            if crs_meta["transform_side"] == "left":
                left_tree = _with_output_crs(left_tree, crs_meta["to_crs"])
            else:
                right_tree = _with_output_crs(right_tree, crs_meta["to_crs"])
    tree = LogicalJoin(
        join_kind=eff.kind,
        left=left_tree,
        right=right_tree,
        join_field_left=eff.join_field_left,
        join_field_right=eff.join_field_right,
        spatial_op=eff.spatial_op if eff.kind == "spatial_join" else None,
        group_by_right=list(eff.group_by_right) if eff.group_by_right else None,
        aggregates=list(eff.aggregates) if eff.aggregates else None,
    )
    # 输出几何 CRS：变换（local 或 server）后 = 目标 CRS；否则沿左子树
    # （V5 链累积几何来自左侧）。
    if crs_meta and crs_meta.get("placement") in ("local", "server") and crs_meta.get("transform_side"):
        out_srid = _parse_srid(crs_meta["to_crs"])
    else:
        out_srid = left_srid if left_srid is not None else right_srid
    return _Candidate(
        tree=tree,
        sources_in=left.sources_in + right.sources_in,
        card=card,
        cost=total,
        components=comps,
        crs_transforms=transforms,
        key=tree.plan_hash(),
        out_srid=out_srid,
    )


def _is_scan_like(tree: LogicalNode) -> bool:
    """scan 或 Reproject(scan)（空间跳 build 侧的合法形状）。"""
    from app.services.data_fabric.query.federated.logical import LogicalReproject

    while isinstance(tree, LogicalReproject):
        tree = tree.input
    return isinstance(tree, LogicalScan)


def _caps_output_crs(src: "SourceFacts") -> bool:
    """该源是否具备已验证的 output_crs 扫描通道（V7 W8）。"""
    caps = getattr(src, "caps", None)
    return bool(caps is not None and getattr(caps, "output_crs_pushdown", False))


def _with_output_crs(tree: LogicalNode, output_crs: str):
    """scan-like 树根携带 output_crs（server placement 语义；返回新树）。

    仅 LogicalScan / LogicalReproject(LogicalScan) 形态受支持
    （调用方以 _is_scan_like 守卫）；其余形态抛错 —— 绝不静默忽略。
    """
    if isinstance(tree, LogicalScan):
        return tree.model_copy(update={"output_crs": output_crs})
    if isinstance(tree, LogicalReproject) and isinstance(tree.input, LogicalScan):
        return tree.model_copy(
            update={"input": tree.input.model_copy(update={"output_crs": output_crs})}
        )
    raise AssertionError("server placement requires a scan-like tree")




def _first_src(cand: _Candidate, by_id: Dict[str, SourceFacts]) -> SourceFacts:
    for sid in cand.sources_in:
        s = by_id.get(sid)
        if s is not None:
            return s
    raise _fed_error("candidate references unknown source")


def _join_cardinality(
    edge: JoinEdge,
    left: _Candidate,
    right: _Candidate,
    by_id: Dict[str, SourceFacts],
) -> int:
    """跳基数：属性 NDV 模型（V5 同式）/ 空间重叠因子 / 聚合组界。"""
    if edge.kind == "spatial_join":
        lsrc = by_id.get(edge.left_source_id) if edge.id_addressed else None
        rsrc = by_id.get(edge.right_source_id) if edge.id_addressed else None
        if lsrc is None or rsrc is None:
            return max(1, min(left.card, right.card))
        factor = _spatial_hop_factor(lsrc, rsrc, edge.spatial_op)
        return max(1, int(left.card * factor))
    if edge.kind == "aggregate_join":
        groups = 5000
        ndv_product = 1.0
        has_ndv = False
        for f in edge.group_by_right or []:
            ndv = _ndv_of(right.sources_in, f, by_id)
            if ndv:
                ndv_product *= min(ndv, 10_000)
                has_ndv = True
        if has_ndv and ndv_product > 0:
            groups = int(min(ndv_product, 1_000_000))
        # 聚合输出行数以**左侧行数**为上界（每左行归属恰一组；评审 R1 m2：
        # 旧式 left×right 高估一整个量级，扭曲 DP 排序）。
        return max(1, min(left.card, groups))
    # attribute：|A|·|B| / max(ndv)（V5 _chain_join_cardinality 同式）
    ndv = max(
        _ndv_of(left.sources_in, edge.join_field_left, by_id) or 1,
        _ndv_of(right.sources_in, edge.join_field_right, by_id) or 1,
    )
    return max(1, (left.card * right.card) // ndv)


def _is_chain_shape(tree: LogicalNode) -> bool:
    """树是否左深链（每跳右侧都是 scan）—— 展示序提取用。"""
    while isinstance(tree, LogicalJoin):
        if not isinstance(tree.right, LogicalScan):
            return False
        tree = tree.left
    return isinstance(tree, LogicalScan)


def _chain_order_hint(tree: LogicalNode) -> List[str]:
    ids: List[str] = []
    node: LogicalNode = tree
    while isinstance(node, LogicalJoin):
        right = node.right
        if isinstance(right, LogicalScan):
            ids.append(right.source_id)
        node = node.left
    if isinstance(node, LogicalScan):
        ids.append(node.source_id)
    return list(reversed(ids))


__all__ = [
    "SourceFacts",
    "JoinEdge",
    "EnumerationContext",
    "EnumeratedPlan",
    "enumerate_federation",
    "MAX_FEDERATED_SOURCES",
    "TOP_K_PER_SUBSET",
    "MAX_ALTERNATIVES",
]


# ── V7（ADR-0119 W9）：安全聚合下推证明（R-C1 五条件）─────────────────────

#: 可安全下推的聚合函数（可合并内核；stddev 等需合并状态的不在列）。
_SAFE_PUSH_AGG_FUNCS = frozenset({"count", "sum", "min", "max", "avg"})


def aggregate_pushdown_proof(
    edge: JoinEdge,
    left_sources: Tuple[str, ...],
    by_id: Dict[str, SourceFacts],
) -> Optional[Dict[str, Any]]:
    """aggregate_join → 源侧 GROUP BY 的**语义等价证明**（不满足返回 None）。

    R-C1 五条件（缺一不可；全部计划期可证）：
    1. 纯属性等值 join（join_field_left/right 均存在；spatial aggregate join
       显式排除）；
    2. ``group_by_right`` **包含** ``join_field_right`` —— 组内右行要么全部
       被连接命中、要么全部未命中（未命中组被最终 join 丢弃）；
    3. 左 join 键唯一性为 **measured 级证明**（``SourceFacts.unique_keys``：
       stats_hints 显式声明或 pg_index 探测）—— 每右行至多匹配一左行，
       杜绝 join-后聚合的右行重复计数；
    4. 聚合函数 ⊆ {count, sum, min, max, avg}（可合并内核，组值可直接迁移）
       且聚合字段**不出现在左侧顶层字段**（R1-M5：内核左优先解析 —— 左侧
       未投影（字段集未知）或含同名列 → 无法证明，不下推）；
    5. 右源 caps.aggregation=True（源真的能做 GROUP BY）。

    等价性论证：条件 2+3 ⇒ 存活组与命中组一致且组内聚合不重复；条件 4 ⇒
    聚合值可由源侧组值直接承载 ⇒ 源侧 GROUP BY ≡ join-后聚合（逐位）。
    """
    if edge.kind != "aggregate_join":
        return None
    if not edge.join_field_left or not edge.join_field_right:
        return None
    if not edge.group_by_right or edge.join_field_right not in edge.group_by_right:
        return None
    if edge.left_source_id not in left_sources:
        return None
    left_src = by_id.get(edge.left_source_id)
    right_src = by_id.get(edge.right_source_id)
    if left_src is None or right_src is None:
        return None
    if edge.join_field_left not in (left_src.unique_keys or []):
        return None
    right_caps = getattr(right_src, "caps", None)
    if right_caps is None or not getattr(right_caps, "aggregation", False):
        return None
    for a in edge.aggregates or []:
        func = str(a.get("func") if isinstance(a, dict) else getattr(a, "func", ""))
        if func not in _SAFE_PUSH_AGG_FUNCS:
            return None
        # R1-M5：内核聚合字段解析是**左优先右回退**（_AggregateState.update）
        # —— 左侧顶层存在同名列时本地读左值、下推算右值，不等价。左字段集
        # 未知（未投影）→ 无法证明 → 不下推（保守诚实）。
        field = a.get("field") if isinstance(a, dict) else getattr(a, "field")
        if field is not None:
            if left_src.fields is None:
                return None
            if field in left_src.fields:
                return None
    return {
        "left_source_id": edge.left_source_id,
        "right_source_id": edge.right_source_id,
        "join_field_left": edge.join_field_left,
        "join_field_right": edge.join_field_right,
        "group_by_right": list(edge.group_by_right),
        "basis": "unique_key_declaration" if (left_src.unique_keys) else "pg_index",
        "aggregates": list(edge.aggregates or []),
    }
