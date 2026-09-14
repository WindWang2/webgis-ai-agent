"""离线重放的确定性视觉 judge（``CARTO_VISUAL_JUDGE`` 注入缝的 fixture 实现）。

生产 judge 是 VLM（record-only）；离线重放/语料需要**无 LLM、无网络**的
确定性替代：批评直接从 snapshot 的 ``deterministic_summary`` 推导 ——
L4 评审已知的失败规则 → error 级视觉批评；否则零批评（L5 pass）。
这不是新的质量语义，只是把既有确定性事实翻译成 judge 报文形状。
"""
from __future__ import annotations

from typing import Any, Dict, List


def deterministic_judge(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按 snapshot 中已确定的 L4 失败规则产出批评（无 LLM、无随机性）。"""
    summary = snapshot.get("deterministic_summary") or {}
    failed_rules = [
        str(rule) for rule in (summary.get("failed_rules") or []) if rule
    ]
    if failed_rules:
        return [{
            "dimension": "readability",
            "severity": "error",
            "suggestion": f"deterministic L4 failures: {', '.join(failed_rules[:3])}",
            "evidence": "replay_judge:deterministic_summary",
        }]
    return []
