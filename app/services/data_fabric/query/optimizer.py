"""成本模型与有界计划备选（Data Fabric V3，ADR-0096 D3）。

原则：
- **相对成本，不假精度**：可解释的量（扫描行、传输字节、远端请求数、
  join 候选）× 常数权重 → 排序用分数；权重是公开常数，EXPLAIN 可复述。
- **备选有界**：硬上限 8 个，绝不组合爆炸。
- **计划即执行**：选中的执行决策仍由 planner 单一产出；备选是 EXPLAIN
  的「如果……会怎样」投影与预算失败的可行动建议，不产生第二执行真相。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

# 相对成本权重（公开、确定性、无量纲；仅为排序服务）
_W_BYTES_TRANSFERRED = 1.0
_W_ROWS_SCANNED = 0.2
_W_REMOTE_REQUEST = 50.0
_W_JOIN_CANDIDATE = 0.05
_W_LOCAL_CPU_PER_ROW = 0.5
MAX_ALTERNATIVES = 8


class PlanCost(BaseModel):
    """选中计划/备选计划的相对成本分解（全部可解释）。

    口径诚实声明：``score()`` 是**逐页（per-page）**口径的排序分数，不做
    数据集规模归一 —— 不同规模数据集之间的 score 不可直接比较，仅用于
    同一查询内计划/备选的相对排序。
    """

    rows_scanned: int = 0
    bytes_transferred: int = 0
    rows_emitted: int = 0
    remote_requests: int = 1
    join_candidates: int = 0
    local_cpu_rows: int = 0
    memory_class: int = Field(default=1, ge=1, le=5)   # 1 低 … 5 高
    latency_class: int = Field(default=2, ge=1, le=3)  # 1 快 … 3 慢

    def score(self) -> float:
        return (
            self.rows_scanned * _W_ROWS_SCANNED
            + self.bytes_transferred * _W_BYTES_TRANSFERRED
            + self.remote_requests * _W_REMOTE_REQUEST
            + self.join_candidates * _W_JOIN_CANDIDATE
            + self.local_cpu_rows * _W_LOCAL_CPU_PER_ROW
            + self.memory_class * 100.0
            + self.latency_class * 25.0
        )

    def explain_lines(self) -> List[str]:
        return [
            f"cost(rows_scanned={self.rows_scanned}, bytes={self.bytes_transferred}, "
            f"emitted={self.rows_emitted}, requests={self.remote_requests}, "
            f"join_cand={self.join_candidates}, mem_class={self.memory_class}, "
            f"lat_class={self.latency_class}) score={self.score():.0f}"
        ]


class PlanAlternative(BaseModel):
    """一个被考虑/否决的备选方案（EXPLAIN 投影）。"""

    name: str
    description: str
    feasible: bool = True
    rejected_reason: Optional[str] = None
    estimated_cost: Optional[PlanCost] = None

    def summary(self) -> str:
        tag = "feasible" if self.feasible else "rejected"
        line = f"{tag}: {self.name} — {self.description}"
        if self.rejected_reason:
            line += f" ({self.rejected_reason})"
        if self.estimated_cost is not None and self.feasible:
            line += f" {self.estimated_cost.explain_lines()[0]}"
        return line


def _per_feature_bytes(has_select: bool) -> int:
    return 700 if has_select else 1800


def cost_of_chosen(
    *,
    estimated_rows: Optional[int],
    estimated_bytes: Optional[int],
    pushed_any: bool,
    local_rows: Optional[int],
    remote_requests: int = 1,
    join_candidates: int = 0,
) -> PlanCost:
    """从 planner 已确定的估算推导选中计划的成本分解。"""
    rows = estimated_rows or 0
    return PlanCost(
        rows_scanned=rows if pushed_any else 0,
        bytes_transferred=estimated_bytes or 0,
        rows_emitted=rows,
        remote_requests=remote_requests,
        join_candidates=join_candidates,
        local_cpu_rows=int(local_rows or 0),
        memory_class=_memory_class(estimated_bytes),
        latency_class=1 if pushed_any else 3,
    )


def _memory_class(estimated_bytes: Optional[int]) -> int:
    if not estimated_bytes:
        return 1
    mb = estimated_bytes / (1024 * 1024)
    if mb <= 1:
        return 1
    if mb <= 16:
        return 2
    if mb <= 128:
        return 3
    if mb <= 512:
        return 4
    return 5


def generate_alternatives(
    *,
    source_type: str,
    estimated_rows: Optional[int],
    page_window: int,
    budget_max_rows: int,
    budget_max_bytes: int,
    filter_pushed: bool,
    spatial_pushed: bool,
    aggregation_pushed: bool,
    aggregate_requested: bool,
    projection_pushed: bool,
    has_select: bool,
    order_by: bool,
    sort_pushed: bool,
    vector_tiles: bool,
    result_mode: str,
    tile_source_types: tuple = ("wms", "wmts", "pmtiles", "stac"),
) -> List[PlanAlternative]:
    """有界备选生成（≤MAX_ALTERNATIVES）。全部是纯函数投影，无 IO。"""
    alts: List[PlanAlternative] = []
    rows = estimated_rows
    base_bytes = rows and rows * _per_feature_bytes(has_select) or 0

    if not filter_pushed:
        alts.append(PlanAlternative(
            name="pushdown_attribute_filter",
            description="server-side attribute filter would cut transferred rows",
            feasible=False,
            rejected_reason="source capability lacks filter pushdown",
        ))
    if not spatial_pushed:
        alts.append(PlanAlternative(
            name="pushdown_spatial_filter",
            description="server-side bbox/spatial predicate would shrink scans",
            feasible=False,
            rejected_reason="source capability lacks spatial pushdown",
        ))
    if aggregate_requested and not aggregation_pushed and rows:
        alts.append(PlanAlternative(
            name="aggregate_before_transfer",
            description="server-side aggregation would reduce transfer to group rows",
            feasible=False,
            rejected_reason="source capability lacks aggregation pushdown",
        ))
    if rows is not None and rows > budget_max_rows and result_mode == "features":
        sample_rows = min(rows, page_window)
        alts.append(PlanAlternative(
            name="sample_result_mode",
            description="deterministic SAMPLE mode bounded to the page window",
            feasible=True,
            estimated_cost=PlanCost(
                rows_scanned=rows,
                bytes_transferred=sample_rows * _per_feature_bytes(has_select),
                rows_emitted=sample_rows,
                remote_requests=1,
                memory_class=2,
                latency_class=1,
            ),
        ))
        alts.append(PlanAlternative(
            name="materialize_bounded_subset",
            description="bounded MATERIALIZE then tile/stream from the materialized subset",
            feasible=True,
            estimated_cost=PlanCost(
                rows_scanned=rows,
                bytes_transferred=min(base_bytes, budget_max_bytes),
                rows_emitted=rows,
                remote_requests=2,
                memory_class=4,
                latency_class=2,
            ),
        ))
    if vector_tiles and result_mode == "features" and rows is not None and rows > 10_000:
        alts.append(PlanAlternative(
            name="vector_tile_path",
            description="VECTOR_TILE result mode (server MVT) instead of feature transfer",
            feasible=True,
            estimated_cost=PlanCost(
                rows_scanned=rows,
                bytes_transferred=min(base_bytes // 10, budget_max_bytes),
                rows_emitted=0,
                remote_requests=4,
                memory_class=2,
                latency_class=1,
            ),
        ))
    if not projection_pushed and has_select is False:
        alts.append(PlanAlternative(
            name="project_before_transfer",
            description="explicit field selection reduces per-feature bytes",
            feasible=False,
            rejected_reason="no select list provided by caller",
        ))
    if order_by and not sort_pushed:
        alts.append(PlanAlternative(
            name="sort_pushdown",
            description="server-side sort avoids local sort over the full page set",
            feasible=False,
            rejected_reason="source capability lacks sort pushdown",
        ))
    if source_type in tile_source_types:
        alts.append(PlanAlternative(
            name="overview_or_tile_path",
            description="tile/overview path avoids feature-level transfer for visual use",
            feasible=True,
            estimated_cost=PlanCost(
                rows_scanned=0,
                bytes_transferred=256 * 1024,
                rows_emitted=0,
                remote_requests=4,
                memory_class=1,
                latency_class=1,
            ),
        ))
    return alts[:MAX_ALTERNATIVES]


def budget_failure_suggestions(alternatives: List[PlanAlternative]) -> List[str]:
    """预算失败时的可行动建议：可行的备选名 + 常规降本路径。"""
    suggestions = [
        "narrow the query extent (bbox)",
        "add attribute filters before transfer",
        "reduce the page limit or use keyset pagination",
    ]
    for alt in alternatives:
        if alt.feasible:
            suggestions.append(f"consider alternative '{alt.name}': {alt.description}")
    return suggestions[:8]


def cost_summary_line(cost: Optional[PlanCost]) -> Optional[str]:
    if cost is None:
        return None
    return cost.explain_lines()[0]
