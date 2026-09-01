"""DAG 终态门 validator（execution facet）— ADR-0081 / ADR-0091。"""
from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import (
    F_EXECUTION_BLOCKED,
    F_NEEDS_EXECUTION,
    MapCompletionFinding,
)


def validate_execution(chapter: Dict[str, Any]) -> List[MapCompletionFinding]:
    """DAG 终态门（复用 plan_graph 投影 —— 单一计算源，不重推行状态）。

    mandatory 节点未全部 complete/skipped → pending（needs_execution 披露）。
    failed 行是可重试的执行缺口、unavailable 是能力缺口 —— finalizer 不
    自己重跑算法，交还 DAG/Harness 的重试/降级语义。
    """
    rows = [
        r
        for r in list(chapter.get("data_requirements") or [])
        + list(chapter.get("analysis_steps") or [])
        if isinstance(r, dict)
    ]
    if not rows:
        return []
    try:
        from app.services.gis_harness.plan_graph import build_plan_graph

        graph = build_plan_graph(chapter)
        nodes = graph.nodes
    except Exception:  # noqa: BLE001 — 图构建失败退回行状态判别
        nodes = []
    findings: List[MapCompletionFinding] = []
    if nodes:
        mandatory = [n for n in nodes if not n.optional]
        open_nodes = [
            n for n in mandatory if n.status.value in ("pending", "ready", "running")
        ]
        blocked = [n for n in mandatory if n.status.value in ("failed", "unavailable")]
        if open_nodes:
            caps = ",".join(n.capability for n in open_nodes[:4])
            findings.append(
                MapCompletionFinding(
                    code=F_NEEDS_EXECUTION,
                    severity="error",
                    target=caps,
                    detail=f"{len(open_nodes)} mandatory nodes not terminal",
                )
            )
        if blocked:
            caps = ",".join(n.capability for n in blocked[:4])
            findings.append(
                MapCompletionFinding(
                    code=F_EXECUTION_BLOCKED,
                    severity="error",
                    target=caps,
                    detail=(
                        f"{len(blocked)} mandatory nodes "
                        + ",".join(sorted({n.status.value for n in blocked}))
                        + " — retry or replan owed"
                    ),
                )
            )
        return findings
    # 兜底（无图）：行状态直读（unavailable 与 failed 同为阻塞态）
    open_rows = [r for r in rows if str(r.get("status") or "") == "pending"]
    failed_rows = [
        r for r in rows if str(r.get("status") or "") in ("failed", "unavailable")
    ]
    if open_rows:
        caps = ",".join(str(r.get("capability") or "?") for r in open_rows[:4])
        findings.append(
            MapCompletionFinding(
                code=F_NEEDS_EXECUTION,
                severity="error",
                target=caps,
                detail=f"{len(open_rows)} capability rows pending",
            )
        )
    if failed_rows:
        caps = ",".join(str(r.get("capability") or "?") for r in failed_rows[:4])
        findings.append(
            MapCompletionFinding(
                code=F_EXECUTION_BLOCKED,
                severity="error",
                target=caps,
                detail=f"{len(failed_rows)} failed rows await retry",
            )
        )
    return findings
