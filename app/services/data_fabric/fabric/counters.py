"""per-execution 结构性计数器（ADR-0119 W13，Epic §16）。

性能成功不能只用 wall-clock —— 计数器随 ``explain_v7.fabric`` 披露：
remote_requests / bytes_fetched / rows_materialized / peak_streaming_bytes /
pushdown 百分比 / cache hit / replans / probe 请求代价。生命周期 =
一次 execute_chain_v6（与 executor/controller 同生共死；线程内单次执行，
不跨线程共享）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class FabricCounters:
    """一次联邦执行的资源计数（结构性；非 wall-clock 门）。"""

    remote_requests: int = 0
    pages_fetched: int = 0
    bytes_fetched: int = 0
    rows_materialized: int = 0
    peak_streaming_bytes: int = 0
    crs_server_placements: int = 0
    crs_fallbacks: int = 0
    aggregate_pushdowns: int = 0
    cache_hit: Optional[bool] = None
    replans: int = 0
    probe_requests: int = 0
    feedback_durable_failures: int = 0
    sources: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def note_source(
        self,
        source_id: str,
        *,
        rows: Optional[int] = None,
        requests: int = 0,
        bytes_fetched: int = 0,
    ) -> None:
        s = self.sources.setdefault(
            source_id, {"rows": 0, "requests": 0, "bytes": 0}
        )
        if rows is not None:
            s["rows"] = rows
        s["requests"] += requests
        s["bytes"] += bytes_fetched

    def observe_streaming_bytes(self, current_bytes: int) -> None:
        if current_bytes > self.peak_streaming_bytes:
            self.peak_streaming_bytes = current_bytes

    def pushdown_ratio(self) -> Optional[float]:
        """下推百分比：执行的下推放置数 / 可下推放置点数（简单口径：
        server placement + aggregate pushdown 占 join+scan 节点的比例）。"""
        total = self.crs_server_placements + self.aggregate_pushdowns
        if total == 0:
            return None
        applied = self.crs_server_placements + self.aggregate_pushdowns
        return round(applied / total, 4) if total else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "remote_requests": self.remote_requests,
            "pages_fetched": self.pages_fetched,
            "bytes_fetched": self.bytes_fetched,
            "rows_materialized": self.rows_materialized,
            "peak_streaming_bytes": self.peak_streaming_bytes,
            "crs_server_placements": self.crs_server_placements,
            "crs_fallbacks": self.crs_fallbacks,
            "aggregate_pushdowns": self.aggregate_pushdowns,
            "cache_hit": self.cache_hit,
            "replans": self.replans,
            "probe_requests": self.probe_requests,
            "feedback_durable_failures": self.feedback_durable_failures,
            "per_source": dict(self.sources),
        }


def collect_from_exec_result(
    counters: FabricCounters, exec_result: Dict[str, Any]
) -> FabricCounters:
    """从 PhysicalExecutor 的执行结果补齐计数（单一数据源，不重复记账）。"""
    counters.pages_fetched = int(exec_result.get("pages_fetched") or 0)
    counters.rows_materialized = sum(
        (exec_result.get("per_source_rows") or {}).values()
    )
    counters.replans = int(exec_result.get("replans_used") or 0)
    counters.crs_fallbacks = len(exec_result.get("crs_fallbacks") or [])
    for hop in exec_result.get("hop_stats") or []:
        if hop.get("aggregate_pushdown"):
            counters.aggregate_pushdowns += 1
    return counters


__all__ = ["FabricCounters", "collect_from_exec_result"]
