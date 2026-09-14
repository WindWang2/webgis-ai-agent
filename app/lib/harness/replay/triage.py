"""失败 triage 六分类（B11，ADR-0183 决策六）。

禁止裸 "snapshot changed"：每次 red 必须给出机器可读类别 + 证据路径：

- ``semantic_regression``        语义裁决漂移（gate/goal 期望失配）；
- ``cartography_visual_regression`` 制图质量面专项劣化（CQ/goal 链）；
- ``data_fixture_drift``         fixture 引用失效 / 注入故障未按契约生效；
- ``generated_artifact_drift``   期望仍绿但 replay 指纹相对基线漂移；
- ``platform_limitation``        平台/环境限制（如 T3 未实现、子模块缺席）；
- ``nondeterministic_text_only`` 仅自然语言长度带差异（永不作为语义失败）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

CATEGORIES = (
    "semantic_regression",
    "cartography_visual_regression",
    "data_fixture_drift",
    "generated_artifact_drift",
    "platform_limitation",
    "nondeterministic_text_only",
)


def classify(
    scenario,
    result,
    *,
    baseline_digest: Optional[str] = None,
    platform_note: Optional[str] = None,
) -> Dict[str, Any]:
    """单场景 triage。scenario/result 为离线重放对象。"""
    diffs: List[Dict[str, Any]] = [
        dict(d, turn=t.turn_index)
        for t in result.turns for d in t.exact_diffs
    ]
    text_diffs = [
        dict(d, turn=t.turn_index)
        for t in result.turns for d in getattr(t, "text_diffs", [])
    ]
    evidence: Dict[str, Any] = {"scenario_id": scenario.scenario_id,
                                "diffs": diffs[:12],
                                "text_diffs": text_diffs[:8]}
    paths = [str(d.get("path") or "") for d in diffs]

    if not diffs and text_diffs:
        return {"category": "nondeterministic_text_only", "evidence": evidence}
    if not diffs:
        if platform_note:
            return {"category": "platform_limitation", "evidence":
                    {**evidence, "note": platform_note}}
        if baseline_digest and baseline_digest != result.replay_digest:
            return {"category": "generated_artifact_drift", "evidence":
                    {**evidence, "baseline": baseline_digest[:16],
                     "current": result.replay_digest[:16]}}
        return {"category": "generated_artifact_drift" if result.not_run
                else "semantic_regression",
                "evidence": {**evidence,
                             "note": "green but flagged by caller"}}

    if paths and all(p.startswith("text:") for p in paths):
        return {"category": "nondeterministic_text_only", "evidence": evidence}

    cq_paths = [p for p in paths if "CartographicQuality" in p
                or p.startswith("goal")]
    semantic_paths = [p for p in paths if p not in cq_paths
                      and not p.startswith("text:")]

    if scenario.faults:
        evidence["faults"] = scenario.faults
        if semantic_paths:
            return {"category": "data_fixture_drift", "evidence": evidence}

    if cq_paths and not semantic_paths:
        return {"category": "cartography_visual_regression",
                "evidence": evidence}
    return {"category": "semantic_regression", "evidence": evidence}


def summarize(classifications: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {name: 0 for name in CATEGORIES}
    for item in classifications:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return counts
