"""基准漂移失效（WP3）：漂移观察 → 逐候选影响分级 + 状态同步 + 恢复。

分级纯函数 ``decide_impact_level``（漂移 × 候选干预面 → level）：

- 基准未动 → 全部 ``current``（不扰动任何状态）；
- 数据集版本推进 且候选干预属 analysis/data 组 → ``unresolvable``
  （旧版本产物不可采纳 —— 版本事实优先于字段冲突裁决）；
- spec 漂移（全局）：候选已 applied 且其目标字段在父会话已变化
  （base_values vs 父当前值，与采纳同口径 ``layer_of`` 读取）→
  ``adopt_blocked``；否则 ``recompute_required``（分支上补干预/局部
  重算后可恢复，不盲目重跑整个任务）；
- 仅 goal digest 变化 → ``recompute_required``。

``stale`` 不是终态：``restore_candidate_after_recompute`` 在基准锚
重判 current 后按 applied 进度恢复（全 applied → comparable）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.mapspec.store import mapspec_store_instance
from app.services.scenario_harness.baseline import (
    latest_baseline,
    observe_baseline_move,
)
from app.services.scenario_harness.contracts import (
    REGISTRY_BASELINES,
    REGISTRY_CANDIDATES,
    BaselineMove,
    CandidateImpact,
    ScenarioCandidate,
)
from app.services.scenario_harness.candidates import (
    get_candidate,
    layer_of,
    list_candidates,
    mark_candidate_state,
)
from app.services.scenario_harness.store import (
    RegistryError,
    get_registry_entry,
    locked_registry_update,
    utc_now_iso,
)
from app.services.simulation.whatif.branch_manager import fingerprint_mapspec

#: 分析/数据组词表（数据版本漂移的失级范围）。
_DATA_DEPENDENT_GROUPS = {"analysis", "data"}


def _target_fields_changed(
    candidate: ScenarioCandidate, parent_spec: Optional[Dict[str, Any]]
) -> bool:
    """候选 base_values vs 父会话当前值（与采纳同口径的确定性读取）。

    base_values 缺席 = 提案时未观察 → 不可判定 → 不算 changed（保守：
    宁可 recompute_required 可恢复，不误判 adopt_blocked 阻断采纳）。
    """
    for delta in candidate.delta:
        observed = delta.base_values
        if not observed:
            continue
        paint_keys = [k for k in observed if str(k).startswith("paint.")]
        if paint_keys:
            layer = layer_of(parent_spec, delta.target_id)
            paint = layer.get("paint") if isinstance(layer, dict) else None
            paint = paint if isinstance(paint, dict) else {}
            for key in paint_keys:
                if paint.get(str(key)[len("paint."):]) != observed[key]:
                    return True
        elif "layer.digest" in observed:
            layer = layer_of(parent_spec, delta.target_id)
            if not isinstance(layer, dict):
                return True  # 层已消失
            if fingerprint_mapspec(layer)[:16] != observed["layer.digest"]:
                return True
        elif observed.get("layer.absent") is True:
            if layer_of(parent_spec, delta.target_id) is not None:
                return True  # 层新出现了
        elif "labels.digest" in observed:
            layer = layer_of(parent_spec, delta.target_id)
            ops = delta.payload.get("ops")
            if ops:
                current = fingerprint_mapspec({"ops": ops})[:16]
            elif isinstance(layer, dict) and layer.get("labels"):
                current = fingerprint_mapspec(layer.get("labels"))[:16]
            else:
                current = ""
            if current != observed.get("labels.digest"):
                return True
        elif observed.get("labels.absent") is True:
            layer = layer_of(parent_spec, delta.target_id)
            if isinstance(layer, dict) and layer.get("labels"):
                return True
    return False


def decide_impact_level(
    move: BaselineMove,
    candidate: ScenarioCandidate,
    parent_spec: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str]]:
    """纯函数：漂移观察 × 候选干预面 → (level, reasons)（reasons ≤8）。"""
    if not move.moved:
        return "current", []
    reasons = move.reasons()  # 词表化 + 有界 ≤8（contracts.BaselineMove）
    groups = {d.group for d in candidate.delta}
    # 数据版本事实最重：旧版本上的产物不可采纳（先于字段冲突裁决）
    if move.dataset_changes and groups & _DATA_DEPENDENT_GROUPS:
        return "unresolvable", reasons
    if move.fingerprint_changed:
        if _target_fields_changed(candidate, parent_spec) and candidate.applied_seqs:
            return "adopt_blocked", reasons
        return "recompute_required", reasons
    if move.goal_changed:
        return "recompute_required", reasons
    return "current", []


async def classify_candidate_impacts(
    session_id: str, *, baseline_id: str = "", store: Any = None
) -> List[CandidateImpact]:
    """逐候选影响分级；漂移时同步把可恢复态的候选标 ``stale``。"""
    if baseline_id:
        baseline = await get_registry_entry(
            session_id, REGISTRY_BASELINES, baseline_id, store=store
        )
        if baseline is None:
            raise RegistryError(
                "baseline_not_found",
                f"基准 {baseline_id!r} 不存在（会话 {session_id}）",
            )
    else:
        baseline = await latest_baseline(session_id, store=store)
        if baseline is None:
            raise RegistryError(
                "baseline_missing",
                f"会话 {session_id} 无共享基准（先 capture_baseline）→ 无法分级候选影响",
            )
    candidates = await list_candidates(session_id, store=store)
    if not candidates:
        return []
    move = await observe_baseline_move(session_id, baseline.baseline_id, store=store)
    if not move.moved:
        return [
            CandidateImpact(candidate_id=c.candidate_id, level="current")
            for c in candidates
        ]
    parent_spec = await mapspec_store_instance.get_mapspec(session_id)
    impacts: List[CandidateImpact] = []
    for candidate in candidates:
        level, reasons = decide_impact_level(
            move,
            candidate,
            parent_spec if isinstance(parent_spec, dict) else None,
        )
        impacts.append(
            CandidateImpact(candidate_id=candidate.candidate_id, level=level, reasons=reasons)
        )
        # 状态同步：只动可恢复态（failed 是独立事实，不重复标 stale）
        if level != "current" and candidate.state in ("draft", "running", "comparable"):
            await mark_candidate_state(
                session_id, candidate.candidate_id, "stale", stale_reasons=reasons, store=store
            )
    return impacts


async def restore_candidate_after_recompute(
    session_id: str, candidate_id: str, *, store: Any = None
) -> ScenarioCandidate:
    """重算完成后的恢复：基准锚重判 current 才放行，否则整笔拒绝。

    恢复态与 applied 进度一致（不谎报 comparable）：全部 seq 已应用 →
    ``comparable``；部分 → ``running``；未应用 → ``draft``。
    """
    candidate = await get_candidate(session_id, candidate_id, store=store)
    move = await observe_baseline_move(session_id, candidate.baseline_id, store=store)
    if move.moved:
        raise RegistryError(
            "still_stale",
            f"基准 {candidate.baseline_id} 仍漂移（先刷新基准/补齐重算）: {'; '.join(move.reasons())}",
        )
    if candidate.state == "archived":
        raise RegistryError(
            "restore_invalid_state",
            f"候选 {candidate_id} 已归档（防采纳），不可恢复",
        )

    def _mutate(entry: ScenarioCandidate) -> ScenarioCandidate:
        all_seqs = {d.seq for d in entry.delta}
        applied = set(entry.applied_seqs)
        if all_seqs and all_seqs <= applied:
            entry.state = "comparable"
        elif applied:
            entry.state = "running"
        else:
            entry.state = "draft"
        entry.stale_reasons = []
        entry.updated_at = utc_now_iso()
        return entry

    updated = await locked_registry_update(
        session_id, REGISTRY_CANDIDATES, candidate_id, _mutate, store=store
    )
    assert updated is not None  # must_exist=True 保证
    return updated


__all__ = [
    "classify_candidate_impacts",
    "decide_impact_level",
    "restore_candidate_after_recompute",
]
