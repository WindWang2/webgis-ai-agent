"""证据链完整性评测门（V4 Wave 8 — ADR-0104 决策 9）。

任务书验收：关键 GIS 场景 trace completeness ≥95%。Phase-0 审计 07：
18 阶段中 13 个无生产发射点，且链无持久化 —— 指标既不可达也不可测。

本模块把指标变成**离线可回归门**：

- ``load_session_chains(session_id)``：读会话 JSONL（trace_store）；
- ``evaluate_chain_gate(records, min_completeness=0.95, na_stages=...)``：
  逐链 completeness + 门裁决。``na_stages`` 是**显式声明**的场景级
  不适用阶段（如纯脚本评测无 LLM 调用 → MODEL_ROUTING 不适用）——
  分母扣除并逐条披露，绝不把「缺发射」伪装成「不适用」。

纯函数 + 文件读取，零 LLM、零网络。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app.lib.runtime.gis_trace import ALL_STAGES, STAGE_IDS, Stage

DEFAULT_MIN_COMPLETENESS = 0.95


def load_session_chains(session_id: str) -> List[Dict[str, Any]]:
    """会话持久化链（trace_store.read_chains 的评测侧别名）。"""
    from app.services.gis_harness.trace_store import read_chains

    return read_chains(session_id)


def evaluate_chain_gate(
    records: List[Dict[str, Any]],
    *,
    min_completeness: float = DEFAULT_MIN_COMPLETENESS,
    na_stages: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """门裁决（纯函数）。

    records: ``chain.as_dict()`` 形状列表（含 ``stages`` / ``completeness`` /
    ``turn_id``）。返回 {passed, min_required, per_chain[], n/a 披露}。
    """
    na = {str(s) for s in (na_stages or set())}
    unknown_na = na - {Stage(int(i)).name for i in STAGE_IDS}
    per_chain: List[Dict[str, Any]] = []
    passed = bool(records)
    for rec in records[:64]:
        stages = {
            str(s.get("stage"))
            for s in rec.get("stages") or []
            if isinstance(s, dict) and s.get("stage")
        }
        effective_total = len(ALL_STAGES) - len(na & set(
            Stage(int(i)).name for i in STAGE_IDS))
        if effective_total <= 0:
            effective_total = len(ALL_STAGES)
        covered = len({s for s in stages if s not in na})
        ratio = covered / effective_total
        chain_pass = ratio >= min_completeness
        passed = passed and chain_pass
        per_chain.append({
            "turn_id": str(rec.get("turn_id") or "")[:64],
            "covered_stages": sorted(stages),
            "covered_count": covered,
            "effective_total": effective_total,
            "completeness": round(ratio, 4),
            "passed": chain_pass,
        })
    return {
        "passed": passed,
        "min_required": min_completeness,
        "na_stages": sorted(na),
        "unknown_na_stages": sorted(unknown_na),
        "chain_count": len(per_chain),
        "per_chain": per_chain,
    }


def run_chain_gate_for_session(
    session_id: str,
    *,
    min_completeness: float = DEFAULT_MIN_COMPLETENESS,
    na_stages: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """会话级门：读持久化链 + 裁决（评测/CI 入口）。"""
    return evaluate_chain_gate(
        load_session_chains(session_id),
        min_completeness=min_completeness,
        na_stages=na_stages,
    )


__all__ = [
    "DEFAULT_MIN_COMPLETENESS",
    "evaluate_chain_gate",
    "load_session_chains",
    "run_chain_gate_for_session",
]
