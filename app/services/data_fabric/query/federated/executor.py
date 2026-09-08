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
    ):
        self._adapter_factory = adapter_factory
        self._budget = budget
        self._limit = limit
        self._bbox = bbox
        self._ndv_hints = ndv_hints or {}
        self._page_size = page_size
        self.token = CancelToken(
            deadline_s=budget.deadline_s, cancel_event=cancel_event
        )
        self.trace = ExecutionTrace()

    # ── 对外入口 ─────────────────────────────────────────────────────

    def execute(self, plan_tree: LogicalNode) -> Dict[str, Any]:
        from app.services.data_fabric.query.execution import StreamingBudget

        started = time.monotonic()
        self._streaming = StreamingBudget(
            max_rows=self._budget.max_rows,
            max_bytes=self._budget.max_bytes,
            max_vertices=self._budget.max_vertices,
        )
        try:
            rows, joined_total = self._eval(plan_tree, lift_key=None)
        except CancelledError:
            self.trace.cancelled = True
            raise
        final_rows = rows[: self._limit]
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
        from app.services.data_fabric.query.federation import (
            FederatedQueryError,
            _chain_left_features,
            aggregate_join_rows,
            attribute_join_local,
            spatial_join_local,
        )

        # 左 = probe（累积行），右 = build（物化，硬界）。
        my_pos = _left_depth(node)  # 本跳在左深链上的位置（F3 检查语义同 V5）
        left_rows, _ = self._eval(node.left, lift_key=node.join_field_left)
        right_rows, _ = self._eval(
            node.right,
            lift_key=node.join_field_right if node.kind != "spatial_join" else None,
        )
        hop_pos = my_pos
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
        if lift_key:
            self._lift(accumulated, lift_key)
        return accumulated, joined_total

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


def _left_depth(node: LogicalNode) -> int:
    """节点在左深链上的跳位（左脊柱上的 join 数；F3 检查与 V5 src_pos 同义）。"""
    depth = 0
    cur = node
    while isinstance(cur, LogicalJoin):
        depth += 1
        cur = cur.left
    return depth


__all__ = ["PhysicalExecutor", "ExecutionTrace"]
