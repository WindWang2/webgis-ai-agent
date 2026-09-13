"""Goal Satisfaction 投影面（ADR-0183 G5/G8 接线辅助）。

单一职责：把持久化在 ``map_product["goal_satisfaction"]`` 块上的评估
结论投影成两类有界面 —— LLM 投影行（``[GIS Goal]`` 单行，进
[GIS Plan] 块）与 SSE/task_complete 载荷（additive 键，旧读者忽略）。

红线：本模块**只读存储块，不重算**（评估只在 finalizer 触发点发生，
与 runtime_state_machine 投影同纪律）；块缺席 = 诚实缺席（空串/None），
绝不在这里补算或伪造。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def bounded_payload(block: Any) -> Optional[Dict[str, Any]]:
    """存储块 → SSE/task_complete 有界载荷（verdict/signal/counts/缺失面）。

    非 dict / 空块 → None（调用方省略该键，零漂移）。
    """
    gs = _dict(block)
    if not gs or not gs.get("verdict"):
        return None
    counts = gs.get("counts") if isinstance(gs.get("counts"), dict) else {}
    return {
        "verdict": str(gs.get("verdict") or "")[:24],
        "signal": str(gs.get("signal") or "")[:32],
        "counts": {str(k): int(v) for k, v in list(counts.items())[:8]},
        "missing_summary": [str(m)[:96]
                            for m in (gs.get("missing_summary") or [])[:6]],
        "summary_line": str(gs.get("summary_line") or "")[:240],
    }


def goal_line_from_block(block: Any) -> str:
    """存储块 → [GIS Goal] 单行（LLM 投影面；块缺席 → 空串）。

    行内只披露 verdict / signal / 缺失码摘要 —— 不暴露内部敏感 trace
    （G8 纪律：诊断细节走 evidence 行，不进 prompt 面）。
    """
    payload = bounded_payload(block)
    if payload is None:
        return ""
    line = (
        f"[GIS Goal] task={payload['verdict']} next={payload['signal']}"
    )
    missing = payload["missing_summary"]
    if missing:
        codes = [m.split(":")[-1] for m in missing[:3]]
        line += " missing:" + ",".join(codes)
    return line[:240]


__all__ = [
    "bounded_payload",
    "goal_line_from_block",
]
