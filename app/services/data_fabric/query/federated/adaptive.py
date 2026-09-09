"""V6 自适应执行（ADR-0118 W8）：执行期基数观测 + 一次性受护栏重排。

护栏（绝不失控）：
- **最多 1 次 replan**；``order_strategy="given"`` 或位置寻址 joins 禁用；
- 观测偏差超过 ``DEVIATION_THRESHOLD``（actual vs estimated）才触发；
- 重排候选只在**可连通**的剩余尾序里选（join graph 约束，与 V4 枚举同一
  红线）；新序的估计剩余成本**必须严格更优**才切换，否则保留原序
  （确定性 fallback）；
- 只对**链形计划**生效（bushy 子树的中途重排是显式 follow-up）；
- 观测记录进执行证据（explain），不写跨进程存储。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 偏差阈值：actual/estimated 比值超出 [1/T, T] 记为显著偏差。
DEVIATION_THRESHOLD = 4.0
#: 最多 replan 次数（结构性护栏）。
MAX_REPLANS = 1


@dataclass
class AdaptiveController:
    """一次执行的自适应状态（不可复用并发）。"""

    enabled: bool = True
    replans_used: int = 0
    observations: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def observe(
        self, *, hop: int, estimated_rows: Optional[int], actual_rows: int
    ) -> Optional[Dict[str, Any]]:
        """记录一跳的 est vs actual；显著偏差时返回观测（否则 None）。"""
        if estimated_rows is None or estimated_rows <= 0:
            return None
        ratio = actual_rows / float(estimated_rows)
        obs = {
            "hop": hop,
            "estimated_rows": estimated_rows,
            "actual_rows": actual_rows,
            "ratio": round(ratio, 4),
        }
        self.observations.append(obs)
        if ratio >= DEVIATION_THRESHOLD or ratio <= 1.0 / DEVIATION_THRESHOLD:
            self.notes.append(
                f"hop {hop}: cardinality deviated x{ratio:.2f} "
                f"(est {estimated_rows} vs actual {actual_rows})"
            )
            return obs
        return None

    def can_replan(self) -> bool:
        return self.enabled and self.replans_used < MAX_REPLANS

    def mark_replan(self, *, adopted: bool, reason: str) -> None:
        self.replans_used += 1
        self.notes.append(f"replan {'adopted' if adopted else 'rejected'}: {reason}")


def _spec_field(spec: Any, name: str) -> Any:
    """join 语义字段访问（dict 或 ChainJoin 形状兼容）。"""
    if isinstance(spec, dict):
        return spec.get(name)
    return getattr(spec, name, None)


def evaluate_tail_orders(
    *,
    accumulated_card: int,
    tail_sources: Sequence[Tuple[str, Optional[int]]],  # (source_id, estimated_rows)
    tail_edges: Dict[
        Tuple[str, str], Dict[str, Any]
    ],  # (left_id, right_id) → join 语义
    ndv_by_source: Dict[str, Dict[str, int]],
    entry_sources: Optional[set] = None,  # 累积侧可达的首尾跳源（M3）
) -> List[Tuple[int, List[str]]]:
    """剩余尾序的成本排序（确定性；只计跳基数 CPU + build 行）。

    返回按 (cost, order) 升序的全部连通候选；不可连通的序不出现。
    ``accumulated_card`` 是已累积左侧的观测行数（作为首跳 left card）。
    """
    from itertools import permutations

    from app.services.data_fabric.query.federated.enumerator import (
        _W_BUILD_PER_ROW,
        _W_LOCAL_CPU_PER_ROW,
    )

    ids = [sid for sid, _ in tail_sources]
    est = {sid: rows for sid, rows in tail_sources}
    out: List[Tuple[int, List[str]]] = []
    for perm in permutations(ids):
        # M3：首尾跳的左侧是累积行 —— 该源必须从已消费集合有向可达。
        if entry_sources is not None and perm[0] not in entry_sources:
            continue
        # 连通性：首源须有边来自已累积侧之外的首跳语义 —— 保守要求
        # 尾部自身成链（边存在于相邻两源之间）。
        ok = True
        card = accumulated_card
        cost = 0
        for i in range(len(perm) - 1):
            # 严格方向查找：边键与执行器的 edge_index 同一方向语义，
            # spatial/aggregate 的方向敏感性绝不放松。
            edge = tail_edges.get((perm[i], perm[i + 1]))
            if edge is None:
                ok = False
                break
            left_card = card
            right_card = est[perm[i + 1]] or 0
            if right_card <= 0:
                right_card = 1
            # 左侧 NDV：首跳的左是累积侧（NDV 未知 → None，退到右 NDV 除数）；
            # 后续跳的左含前一尾源的字段 → 取 perm[i-1]。
            left_ndv_sid = perm[i - 1] if i >= 1 else None
            ndv_l = (
                ndv_by_source.get(left_ndv_sid, {}).get(
                    str(_spec_field(edge, "join_field_left") or "")
                )
                if left_ndv_sid
                else None
            )
            ndv_r = ndv_by_source.get(perm[i + 1], {}).get(
                str(_spec_field(edge, "join_field_right") or "")
            )
            ndv = max(ndv_l or 1, ndv_r or 1)
            card = max(1, (left_card * right_card) // ndv)
            cost += card * _W_LOCAL_CPU_PER_ROW + right_card * _W_BUILD_PER_ROW
        if ok:
            out.append((int(cost), list(perm)))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def pick_tail_order(
    *,
    controller: AdaptiveController,
    original_tail: List[str],
    observed_first_card: int,
    tail_sources: Sequence[Tuple[str, Optional[int]]],
    tail_edges: Dict[Tuple[str, str], Dict[str, Any]],
    ndv_by_source: Dict[str, Dict[str, int]],
    entry_sources: Optional[set] = None,
) -> Tuple[List[str], bool]:
    """受护栏重排：更优才切换（严格小于），最多一次。"""
    if not controller.can_replan():
        return original_tail, False
    if len(original_tail) < 2:
        return original_tail, False
    ranked = evaluate_tail_orders(
        accumulated_card=observed_first_card,
        tail_sources=tail_sources,
        tail_edges=tail_edges,
        ndv_by_source=ndv_by_source,
    )
    if not ranked:
        controller.mark_replan(adopted=False, reason="no connected tail order")
        return original_tail, False
    best_cost, best_order = ranked[0]
    orig_cost, _ = next(((c, o) for c, o in ranked if o == original_tail), (None, None))
    if orig_cost is None:
        controller.mark_replan(
            adopted=False, reason="original tail not connected under observed graph"
        )
        return original_tail, False
    if best_cost < orig_cost and best_order != original_tail:
        controller.mark_replan(
            adopted=True,
            reason=f"tail reordered {original_tail}→{best_order} "
            f"(cost {orig_cost}→{best_cost}) from observed cardinality",
        )
        return best_order, True
    controller.mark_replan(
        adopted=False, reason=f"observed-order tail already optimal (cost {orig_cost})"
    )
    return original_tail, False


__all__ = [
    "AdaptiveController",
    "DEVIATION_THRESHOLD",
    "MAX_REPLANS",
    "evaluate_tail_orders",
    "pick_tail_order",
]
