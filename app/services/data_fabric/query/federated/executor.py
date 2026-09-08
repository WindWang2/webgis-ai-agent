"""V6 物理执行器（ADR-0118 W6）：计划树 → 有界批执行。

语义红线：与 V5 链式联邦**逐位一致**的行形状与预算行为 ——
- 行形状复用 ``federation.attribute_join_local / spatial_join_local /
  _AggregateState``（单一真相；W11 差分锁定）；
- build 侧硬界 ``MAX_JOIN_CANDIDATES``、逐跳 ``budget.max_rows`` fail-fast、
  ``StreamingBudget`` 累计、F1 键提升 —— 全部镜像 V5 链执行器；
- V6 增量：源按**页**拉取（逐页取消/超时检查，probe 不全量物化）、
  Bloom 盈利预滤、聚合 O(组数) 增量内核、``LogicalReproject`` 节点的
  一次性 CRS 变换、取消 token。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_fabric.errors import QueryBudgetExceededError
from app.services.data_fabric.query.federated.adaptive import (
    AdaptiveController,
    pick_tail_order,
)
from app.services.data_fabric.query.federated.bloom import (
    build_bloom_from_rows,
    semi_join_plan,
)
from app.services.data_fabric.query.federated.logical import (
    LogicalJoin,
    LogicalLimit,
    LogicalNode,
    LogicalReproject,
    LogicalScan,
)
from app.services.data_fabric.query.federated.physical import (
    DEFAULT_PAGE_SIZE,
    CancelToken,
    CancelledError,
    bloom_prefilter,
    iter_scan_pages,
    transform_rows_geometry,
)
from app.services.data_fabric.query.predicates import predicate_to_canonical_dict

logger = logging.getLogger(__name__)


@dataclass
class ExecutionTrace:
    """执行证据（explain_v6 的 actual 侧）。"""

    per_source_rows: Dict[str, int] = field(default_factory=dict)
    pages_fetched: int = 0
    hop_stats: List[Dict[str, Any]] = field(default_factory=list)
    adaptive_notes: List[str] = field(default_factory=list)
    bloom_stats: List[Dict[str, Any]] = field(default_factory=list)
    crs_transforms_applied: List[Dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False


class PhysicalExecutor:
    """计划树的有界批执行（一次执行一个计划；不可复用并发）。"""

    def __init__(
        self,
        *,
        adapter_factory: Any,
        budget: Any,
        limit: int,
        bbox: Optional[List[float]] = None,
        cancel_event: Optional[threading.Event] = None,
        ndv_hints: Optional[Dict[str, Dict[str, int]]] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        adaptive: bool = True,
        order_strategy: str = "cost",
    ):
        self._adapter_factory = adapter_factory
        self._budget = budget
        self._limit = limit
        self._bbox = bbox
        self._ndv_hints = ndv_hints or {}
        self._page_size = page_size
        self._order_strategy = order_strategy
        self.adaptive = AdaptiveController(
            enabled=adaptive and order_strategy != "given"
        )
        self.token = CancelToken(
            deadline_s=budget.deadline_s, cancel_event=cancel_event
        )
        self.trace = ExecutionTrace()

    # ── 对外入口 ─────────────────────────────────────────────────────

    def execute(
        self,
        plan_tree: LogicalNode,
        hop_estimates: Optional[List[int]] = None,
        edge_specs: Optional[Dict[Tuple[str, str], Any]] = None,
    ) -> Dict[str, Any]:
        from app.services.data_fabric.query.execution import StreamingBudget

        started = time.monotonic()
        self._streaming = StreamingBudget(
            max_rows=self._budget.max_rows,
            max_bytes=self._budget.max_bytes,
            max_vertices=self._budget.max_vertices,
        )
        self.trace = ExecutionTrace()  # m3（评审 R1）：复用实例时证据不串台
        try:
            chain = _flatten_chain(plan_tree)
            if chain is not None and len(chain[1]) >= 2 and self.adaptive.enabled:
                rows, joined_total = self._execute_chain_adaptive(
                    chain[0],
                    chain[1],
                    hop_estimates=hop_estimates or [],
                    edge_specs=edge_specs or {},
                )
            else:
                rows, joined_total = self._eval(plan_tree, lift_key=None)
        except CancelledError:
            self.trace.cancelled = True
            raise
        final_rows = rows[: self._limit]
        self.trace.adaptive_notes = list(self.adaptive.notes)
        return {
            "rows": final_rows,
            "row_count": len(final_rows),
            "joined_row_count": joined_total,
            "per_source_rows": self.trace.per_source_rows,
            "execution_duration_s": round(time.monotonic() - started, 4),
            "pages_fetched": self.trace.pages_fetched,
            "hop_stats": self.trace.hop_stats,
            "bloom_stats": self.trace.bloom_stats,
            "crs_transforms_applied": self.trace.crs_transforms_applied,
            "adaptive_observations": self.trace.adaptive_notes,
            "replans_used": self.adaptive.replans_used,
            "cancelled": self.trace.cancelled,
        }

    # ── 树求值 ───────────────────────────────────────────────────────

    def _eval(
        self, node: LogicalNode, *, lift_key: Optional[str]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """求值子树 → (rows, joined_total)。``lift_key``：父跳需要的键
        （F1 提升语义的树形推广 —— 左孩子需要父的左键，右孩子需要父的右键）。"""
        if isinstance(node, LogicalScan):
            return self._scan(node, lift_key)
        if isinstance(node, LogicalReproject):
            return self._reproject(node, lift_key=lift_key)
        if isinstance(node, LogicalLimit):
            rows, total = self._eval(node.input, lift_key=lift_key)
            return rows[: node.limit], min(total, node.limit)
        if isinstance(node, LogicalJoin):
            return self._join(node, lift_key=lift_key)
        from app.services.data_fabric.query.federation import FederatedQueryError

        raise FederatedQueryError(
            f"V6 executor cannot evaluate plan node kind={getattr(node, 'kind', '?')}"
        )

    def _scan(
        self, node: LogicalScan, lift_key: Optional[str]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """分页拉取一个源：逐页消费，每页是取消/超时/预算检查点。

        fetch 窗口由计划约束（``fetch_limit`` × budget 硬界）—— 窗口内
        仍会物化（build 侧契约与 V5 一致），但页间检查让超时/取消即时
        生效，而非等整源拉完。
        """
        from app.services.data_fabric.query.federation import FederatedQueryError

        adapter = self._adapter_factory(node.source_id)
        if adapter is None:
            raise FederatedQueryError(
                f"chain source '{node.source_id}' is not connected",
                details={"source_id": node.source_id},
            )
        fetch_window = min(node.fetch_limit or self._limit, self._budget.max_rows)
        where = None
        if node.where is not None:
            where = predicate_to_canonical_dict(node.where)
        elif node.where_raw:
            where = node.where_raw
        rows: List[Dict[str, Any]] = []
        for page in iter_scan_pages(
            adapter,
            node.dataset_id,
            where=where,
            fields=node.fields,
            bbox=node.bbox or self._bbox,
            fetch_limit=fetch_window,
            budget=self._budget,
            token=self.token,
            page_size=self._page_size,
        ):
            rows.extend(page)
            self.trace.pages_fetched += 1
        self.trace.per_source_rows[node.source_id] = len(rows)
        if lift_key:
            self._lift(rows, lift_key)
        return rows, len(rows)

    def _reproject(
        self, node: LogicalReproject, *, lift_key: Optional[str]
    ) -> Tuple[List[Dict[str, Any]], int]:
        from app.services.data_fabric.query.federation import FederatedQueryError
        from app.services.data_fabric.query.planner import parse_epsg

        rows, total = self._eval(node.input, lift_key=lift_key)
        from_srid = parse_epsg(node.from_crs)
        to_srid = parse_epsg(node.to_crs)
        if from_srid is None or to_srid is None:
            raise FederatedQueryError(
                f"invalid CRS in reproject node: {node.from_crs!r}→{node.to_crs!r}"
            )
        if node.placement != "local":
            # 计划/执行一致性红线：本 PR 只执行 local placement。
            raise FederatedQueryError(
                f"reproject placement {node.placement!r} is not executable "
                "(output.crs pushdown plumbing deferred)"
            )
        rows = transform_rows_geometry(rows, from_srid, to_srid)
        self.trace.crs_transforms_applied.append(
            {
                "from_crs": node.from_crs,
                "to_crs": node.to_crs,
                "rows": len(rows),
                "placement": node.placement,
            }
        )
        return rows, total

    def _join(
        self, node: LogicalJoin, *, lift_key: Optional[str]
    ) -> Tuple[List[Dict[str, Any]], int]:

        # 左 = probe（累积行），右 = build（物化，硬界）。
        # 0-based 跳位（F3 检查语义同 V5：首跳不检查几何）。
        my_pos = _left_depth(node) - 1
        left_rows, _ = self._eval(node.left, lift_key=node.join_field_left)
        right_rows, _ = self._eval(
            node.right,
            lift_key=node.join_field_right if node.join_kind != "spatial_join" else None,
        )
        rows = self._apply_hop(node, left_rows, right_rows, hop_pos=my_pos)
        if lift_key:
            self._lift(rows, lift_key)
        return rows, len(rows)

    def _apply_hop(
        self,
        node: LogicalJoin,
        left_rows: List[Dict[str, Any]],
        right_rows: List[Dict[str, Any]],
        *,
        hop_pos: int,
    ) -> List[Dict[str, Any]]:
        """单跳应用（连接语义；_join 与自适应链循环共用）。"""
        from app.services.data_fabric.query.federation import (
            FederatedQueryError,
            _chain_left_features,
            aggregate_join_rows,
            attribute_join_local,
            spatial_join_local,
        )

        max_output = self._budget.max_rows

        if node.join_kind == "attribute_join":
            right_rows, semi_stats = self._reduce_right(
                accumulated=left_rows, right_rows=right_rows, node=node
            )
            index = self._build_index(right_rows, node.join_field_right or "")
            accumulated = attribute_join_local(
                left_rows,
                right_rows,
                join_field_left=str(node.join_field_left),
                join_field_right=str(node.join_field_right),
                budget=self._streaming,
                max_output=max_output,
                left_key_resolver=self._chain_row_key,
                right_index=index,
            )
            self.trace.hop_stats.append(
                {
                    "hop": hop_pos,
                    "kind": node.join_kind,
                    "output_rows": len(accumulated),
                    **semi_stats,
                }
            )
        elif node.join_kind == "spatial_join":
            left_features = _chain_left_features(left_rows)
            if hop_pos > 0 and not any(
                isinstance(f.get("geometry"), dict) for f in left_features
            ):
                raise FederatedQueryError(
                    f"chain spatial join {hop_pos} has no left geometry: the "
                    "preceding hop did not carry one",
                    details={"hint": "make the preceding hop a spatial join"},
                )
            accumulated = spatial_join_local(
                left_features,
                right_rows,
                spatial_op=node.spatial_op or "within",
                budget=self._streaming,
                max_output=max_output,
                carry_left_geometry=True,
            )
            self.trace.hop_stats.append(
                {
                    "hop": hop_pos,
                    "kind": node.join_kind,
                    "output_rows": len(accumulated),
                }
            )
        else:  # aggregate_join：F2 —— 先连接后按右字段分组聚合
            right_rows, semi_stats = self._reduce_right(
                accumulated=left_rows, right_rows=right_rows, node=node
            )
            index = self._build_index(right_rows, node.join_field_right or "")
            joined = attribute_join_local(
                left_rows,
                right_rows,
                join_field_left=str(node.join_field_left or ""),
                join_field_right=str(node.join_field_right or ""),
                budget=self._streaming,
                max_output=max_output,
                left_key_resolver=self._chain_row_key,
                right_index=index,
            )
            # O(组数) 增量聚合（_AggregateState 单一语义真相）
            accumulated = aggregate_join_rows(
                joined,
                node.aggregates or [],
                node.group_by_right or [],
            )
            self.trace.hop_stats.append(
                {
                    "hop": hop_pos,
                    "kind": node.join_kind,
                    "joined_rows": len(joined),
                    "output_rows": len(accumulated),
                    **semi_stats,
                }
            )

        joined_total = len(accumulated)
        if joined_total > self._budget.max_rows:
            raise QueryBudgetExceededError(
                f"chain join {hop_pos} produced {joined_total} rows "
                f"(budget {self._budget.max_rows}); fail-fast stops the chain",
                details={
                    "hint": "filter sources harder, or aggregate before joining",
                    "per_source_rows": self.trace.per_source_rows,
                },
            )
        return accumulated

    def _execute_chain_adaptive(
        self,
        base: LogicalScan,
        hops: List[LogicalJoin],
        *,
        hop_estimates: List[int],
        edge_specs: Dict[Tuple[str, str], Any],
    ) -> Tuple[List[Dict[str, Any]], int]:
        """链形计划的迭代执行 + 自适应观测（W8）。

        每跳后对比估计与实际基数；显著偏差触发**一次性**受护栏尾重排
        （可连通 + 更优才切换）。方向语义边（spatial/aggregate）按原方向
        键索引 —— 缺边即不可连通，绝不猜测。
        """
        accumulated, _ = self._eval(base, lift_key=None)
        last_consumed = base.source_id
        scan_by_id: Dict[str, LogicalScan] = {base.source_id: base}
        for h in hops:
            scan_by_id[h.right.source_id] = h.right
        edge_index: Dict[Tuple[str, str], LogicalJoin] = {}
        for j, h in enumerate(hops):
            prev = base.source_id if j == 0 else hops[j - 1].right.source_id
            edge_index[(prev, h.right.source_id)] = h
        i = 0
        total = len(accumulated)
        while i < len(hops):
            hop = hops[i]
            right_rows, _ = self._eval(
                hop.right,
                lift_key=hop.join_field_right if hop.join_kind != "spatial_join" else None,
            )
            accumulated = self._apply_hop(hop, accumulated, right_rows, hop_pos=i)
            total = len(accumulated)
            # F1：跳后提升下一跳左键（V5 链不变量；_chain_lift_next_join_key
            # 自身对 spatial 跳早退）。
            if i + 1 < len(hops) and hops[i + 1].join_field_left:
                self._lift(accumulated, hops[i + 1].join_field_left)
            est = hop_estimates[i] if i < len(hop_estimates) else None
            obs = self.adaptive.observe(hop=i, estimated_rows=est, actual_rows=total)
            if obs and self.adaptive.can_replan() and i + 1 < len(hops):
                remaining = hops[i + 1 :]
                tail_ids = [h.right.source_id for h in remaining]
                tail_sources = [
                    (
                        sid,
                        self.trace.per_source_rows.get(sid)
                        or getattr(scan_by_id.get(sid), "estimated_rows", None),
                    )
                    for sid in tail_ids
                ]
                # M3（评审 R1）：尾重排的连通图来自**完整 join graph**——
                # 首跳左侧是累积行（携带全部已消费源的字段，_chain_row_key 可
                # 穿透 __right__ 取键），因此任何 (已消费源 → 尾源) 的有向边
                # 都可执行；仅用链邻接边会让重排结构性不可达。
                consumed = set(self.trace.per_source_rows.keys())
                tail_set = set(tail_ids)
                tail_edges: Dict[Tuple[str, str], Any] = {}
                entry_sources = set()
                for (x, y), spec in edge_specs.items():
                    if y not in tail_set:
                        continue
                    if x in tail_set:
                        tail_edges[(x, y)] = spec
                    elif x in consumed:
                        # 累积侧 → 尾源：首尾跳的合法入口（累积行携带全部
                        # 已消费源字段，_chain_row_key 穿透 __right__ 取键）。
                        entry_sources.add(y)
                ndv = {sid: self._ndv_hints.get(sid, {}) for sid in tail_ids}
                new_tail, adopted = pick_tail_order(
                    controller=self.adaptive,
                    original_tail=tail_ids,
                    observed_first_card=total,
                    tail_sources=tail_sources,
                    tail_edges=tail_edges,
                    ndv_by_source=ndv,
                    entry_sources=entry_sources,
                )
                if adopted:
                    # 新首尾跳的左侧可以是**任意已消费源**（累积行携带其字段）；
                    # 确定性：按 per_source_rows 插入序取第一个有入口边者。
                    seq0 = None
                    for x in self.trace.per_source_rows:
                        if (x, new_tail[0]) in edge_specs:
                            seq0 = x
                            break
                    seq = ([seq0] if seq0 is not None else [last_consumed]) + new_tail
                    rebuilt = []
                    feasible = True
                    for k in range(len(seq) - 1):
                        spec = edge_specs.get((seq[k], seq[k + 1]))
                        if spec is None:
                            feasible = False
                            break
                        rebuilt.append(
                            self._synthesize_hop(spec, scan_by_id, seq[k + 1])
                        )
                    if feasible:
                        hops = hops[: i + 1] + rebuilt
                        # 修复评审 R1 双执行：推进到**新尾序的第一跳**（重建
                        # 列表已保留旧 hops[:i+1]，hops[i] 不再重跑）。
                        i += 1
                        continue
                    # 未成链：退还一次性预算，退回原序继续（hops 未被改动）
                    self.adaptive.replans_used -= 1
                    self.adaptive.notes.append(
                        "replan aborted: rebuilt tail not connected under edge specs")
            last_consumed = hop.right.source_id
            i += 1
        return accumulated, total

    def _synthesize_hop(
        self,
        spec: Any,
        scan_by_id: Dict[str, LogicalScan],
        right_sid: str,
    ) -> LogicalJoin:
        """从 join 语义 spec（ChainJoin 形状）合成单跳 LogicalJoin。"""
        return LogicalJoin(
            join_kind=spec.kind,
            left=scan_by_id.get(right_sid)
            or LogicalScan(source_id=right_sid, dataset_id=right_sid),
            right=scan_by_id[right_sid],
            join_field_left=spec.join_field_left,
            join_field_right=spec.join_field_right,
            spatial_op=spec.spatial_op if spec.kind == "spatial_join" else None,
            group_by_right=list(spec.group_by_right) if spec.group_by_right else None,
            aggregates=list(spec.aggregates) if spec.aggregates else None,
        )

    # ── 辅助 ─────────────────────────────────────────────────────────

    def _reduce_right(
        self,
        *,
        accumulated: List[Dict[str, Any]],
        right_rows: List[Dict[str, Any]],
        node: LogicalJoin,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """build 侧预滤：Bloom（盈利时）→ V5 键集 semi-join 兜底。

        两者都只是键过滤 —— 内连接语义不变（假阳性由精确 join 兜底）。
        """
        from app.services.data_fabric.query.federation import (
            _semi_join_reduce_right,
        )

        semi_stats: Dict[str, Any] = {}
        ndv_left = self._ndv_hints.get("__left__", {}).get(node.join_field_left or "")
        ndv_right = self._ndv_hints.get("__right__", {}).get(
            node.join_field_right or ""
        )
        plan = semi_join_plan(
            left_card=len(accumulated),
            right_card=len(right_rows),
            ndv_left=ndv_left,
            ndv_right=ndv_right,
            applicable=node.join_kind == "attribute_join",
        )
        if plan.enabled:
            bloom, stats = build_bloom_from_rows(
                accumulated, node.join_field_left or ""
            )
            if bloom is not None and not bloom.saturated:
                right_rows, pre = bloom_prefilter(
                    right_rows, bloom, node.join_field_right or ""
                )
                self.trace.bloom_stats.append(
                    {
                        "hop_kind": node.join_kind,
                        **stats,
                        "reason": plan.reason,
                    }
                )
                semi_stats["bloom_filtered"] = pre["filtered"]
        # V5 键集 semi-join（历史路径；Bloom 未启用/饱和时的兜底）
        reduced, original = _semi_join_reduce_right(
            accumulated,
            right_rows,
            _ChainJoinShim(node.join_field_left, node.join_field_right),
        )
        if reduced is not right_rows:
            semi_stats["keyset_reduction"] = {
                "right_rows_before": original,
                "right_rows_after": len(reduced),
            }
            right_rows = reduced
        return right_rows, semi_stats

    def _build_index(self, right_rows: List[Dict[str, Any]], field: str):
        from app.services.data_fabric.query.federation import build_attribute_index

        return build_attribute_index(right_rows, field) if field else {}

    @staticmethod
    def _chain_row_key(row: Dict[str, Any], field: str) -> Any:
        from app.services.data_fabric.query.federation import _chain_row_key

        return _chain_row_key(row, field)

    @staticmethod
    def _lift(rows: List[Dict[str, Any]], key: str) -> None:
        """F1 键提升（树形推广：左键或右键，由调用方传入）。"""
        from app.services.data_fabric.query.federation import (
            ChainJoin,
            _chain_lift_next_join_key,
        )

        _chain_lift_next_join_key(
            rows, ChainJoin(kind="attribute_join", join_field_left=key)
        )


class _ChainJoinShim:
    """``_semi_join_reduce_right`` 需要的最小 join 形状（避免构造完整对象）。"""

    def __init__(self, join_field_left: Optional[str], join_field_right: Optional[str]):
        self.join_field_left = join_field_left
        self.join_field_right = join_field_right
        self.kind = "attribute_join"


def extract_hop_estimates(plan) -> List[int]:
    """从 EnumeratedPlan.components 按**链跳序**提取 join 基数估计
    （post-order：左子树 → 本跳 → 右子树；与 _flatten_chain 的 hop 序一致；
    自适应观测的 estimated 侧输入）。"""
    comps = getattr(plan, "components", None) or {}
    out: List[int] = []

    def walk(c: Any) -> None:
        if not isinstance(c, dict):
            return
        if "join" in c and isinstance(c["join"], dict):
            walk(c.get("left") or {})
            card = c["join"].get("card")
            if isinstance(card, (int, float)) and card > 0:
                out.append(int(card))
            walk(c.get("right") or {})

    walk(comps)
    return out


def _flatten_chain(
    node: LogicalNode,
) -> Optional[Tuple[LogicalScan, List[LogicalJoin]]]:
    """纯 scan/join 左深链 → (基表, 有序跳)；含包装节点/bushy → None
    （自适应只对纯链形计划生效；其余走静态递归求值）。"""
    hops: List[LogicalJoin] = []
    cur = node
    while isinstance(cur, LogicalJoin):
        if not isinstance(cur.right, LogicalScan):
            return None
        hops.append(cur)
        cur = cur.left
    if not isinstance(cur, LogicalScan):
        return None
    return cur, list(reversed(hops))


def _left_depth(node: LogicalNode) -> int:
    """节点在左深链上的跳位（左脊柱上的 join 数；F3 检查与 V5 src_pos 同义）。"""
    depth = 0
    cur = node
    while isinstance(cur, LogicalJoin):
        depth += 1
        cur = cur.left
    return depth


__all__ = ["PhysicalExecutor", "ExecutionTrace", "extract_hop_estimates"]
