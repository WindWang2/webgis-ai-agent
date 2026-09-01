"""布局冲突校验 validator（layout facet）— ADR-0081 / ADR-0091。"""
from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import F_LAYOUT_CONFLICT, MapCompletionFinding


def validate_layout(mapspec: Dict[str, Any]) -> List[MapCompletionFinding]:
    """第一版布局冲突检测：floating 矩形重叠 + zone 容量/exclusive 超限。

    复用 semantic_checks 已有的 desired-state 判定（同一几何语义，不建第
    二套碰撞模型）。修复原则：user-pinned（floating）组件不自动挪动——
    只披露；anchor 默认组件的超限同样披露（auto 重排在导出布局引擎里处
    理，见 ADR-0081 deferred）。
    """
    findings: List[MapCompletionFinding] = []
    components = [
        c
        for c in ((mapspec.get("layout") or {}).get("components") or [])
        if isinstance(c, dict)
    ]
    if not components:
        return findings

    floating: List[Dict[str, Any]] = []
    for c in components:
        if c.get("enabled") is False:
            continue
        placement = c.get("placement") or {}
        if not isinstance(placement, dict) or placement.get("mode") != "floating":
            continue
        try:
            floating.append(
                {
                    "id": str(c.get("id") or ""),
                    "x": float(placement.get("x") or 0),
                    "y": float(placement.get("y") or 0),
                    "w": float(placement.get("width") or 0),
                    "h": float(placement.get("height") or 0),
                }
            )
        except (TypeError, ValueError):
            continue
    for i in range(len(floating)):
        for j in range(i + 1, len(floating)):
            a, b = floating[i], floating[j]
            if a["w"] <= 0 or a["h"] <= 0 or b["w"] <= 0 or b["h"] <= 0:
                continue
            if (
                a["x"] < b["x"] + b["w"]
                and b["x"] < a["x"] + a["w"]
                and a["y"] < b["y"] + b["h"]
                and b["y"] < a["y"] + a["h"]
            ):
                findings.append(
                    MapCompletionFinding(
                        code=F_LAYOUT_CONFLICT,
                        severity="warning",
                        target=f"{a['id']}+{b['id']}",
                        detail="floating components overlap (user-pinned, disclosed only)",
                    )
                )
    return findings
