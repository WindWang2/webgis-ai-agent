"""候选生命周期（WP2）：可重放提案 + whatif 分支隔离 + 锁拦截 + 状态机。

分层（防重复施工）：
- 隔离只有一套：``ScenarioBranchManager``（派生会话 + ADR-0257 事务机器），
  本模块只补"候选注册表条目 + 可重放 delta + base_values 观察"；
- 提案序列化先行（``serialize_intents``）—— DeltaCodecError 原样上抛，
  失败路径不留孤儿分支；
- ``base_values`` 在 propose 时对父会话当前权威 spec 做确定性观察
  （三方合并的 base 边；大对象只存 digest，缺席键诚实 None）。

状态机：``draft → running → comparable``；任一条干预 is_error →
``failed``（保留 applied_seqs，可增量补齐后回到 comparable）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.mapspec.store import mapspec_store_instance
from app.services.scenario_harness.baseline import get_or_create_baseline
from app.services.scenario_harness.contracts import (
    MAX_CANDIDATES,
    MAX_HYPOTHESIS,
    MAX_TITLE,
    REGISTRY_CANDIDATES,
    CandidateState,
    DeltaIntent,
    ScenarioCandidate,
)
from app.services.scenario_harness.intents_codec import (
    DeltaCodecError,
    deserialize_intent,
    serialize_intents,
)
from app.services.scenario_harness.locks import find_blocking_lock
from app.services.scenario_harness.store import (
    RegistryError,
    get_registry_entry,
    load_registry,
    locked_registry_update,
    mint_id,
    remove_registry_entry,
    upsert_registry_entry,
    utc_now_iso,
)
from app.services.simulation.whatif.branch_manager import (
    REGISTRY_KEY as WHATIF_REGISTRY_KEY,
    ScenarioBranchManager,
    fingerprint_mapspec,
)
from typing import get_args

#: 候选合法状态词表（与 contracts.CandidateState 字面量同源派生）。
_VALID_STATES = set(get_args(CandidateState))

#: 干预被终态拒绝的候选状态（archived/adopted/cancelled 不可再施加）。
_TERMINAL_APPLY_STATES = {"archived", "adopted", "cancelled"}


async def _session_state(session_id: str, store: Any = None) -> Dict[str, Any]:
    if store is None:
        from app.services.session_data import session_data_manager as store  # noqa: E702

    state = await store.get_map_state(session_id)
    return state if isinstance(state, dict) else {}


# ── 层读取与基准值观察（staleness/compare 复用的公共辅助）───────────


def layer_of(spec: Optional[Dict[str, Any]], layer_id: str) -> Optional[Dict[str, Any]]:
    """确定性层读取：spec["layers"] 是 list of dict，首个 id 精确匹配。"""
    if not isinstance(spec, dict):
        return None
    wanted = str(layer_id or "")
    for layer in spec.get("layers") or []:
        if isinstance(layer, dict) and str(layer.get("id") or "") == wanted:
            return layer
    return None


def base_values_for_kind(
    kind: str,
    payload: Dict[str, Any],
    parent_spec: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """确定性基准值观察（propose 时逐条 delta 填 ``base_values``）。

    - PatchLayerStyleIntent：``paint.<k>`` → 父层 paint[k]（层/键缺失 → None）；
    - UpsertLayerIntent：同 id 层在场 → ``layer.digest``（整层指纹前 16）；
      缺席 → ``layer.absent: True``；
    - PatchLayerLabelAuthoringIntent：``labels.digest``（ops 或父层 labels 键）；
    - 其他（全局意图）：``{}`` —— 三方合并按全局字段处理。
    """
    if kind == "PatchLayerStyleIntent":
        layer = layer_of(parent_spec, str(payload.get("layer_id") or ""))
        paint = layer.get("paint") if isinstance(layer, dict) else None
        paint = paint if isinstance(paint, dict) else {}
        patch = payload.get("paint")
        keys = list(patch.keys()) if isinstance(patch, dict) else []
        return {f"paint.{k}": paint.get(k) for k in keys}
    if kind == "UpsertLayerIntent":
        layer = payload.get("layer")
        layer_id = str(layer.get("id") or "") if isinstance(layer, dict) else ""
        existing = layer_of(parent_spec, layer_id)
        if isinstance(existing, dict):
            return {"layer.digest": fingerprint_mapspec(existing)[:16]}
        return {"layer.absent": True}
    if kind == "PatchLayerLabelAuthoringIntent":
        ops = payload.get("ops")
        if ops:
            return {"labels.digest": fingerprint_mapspec({"ops": ops})[:16]}
        layer = layer_of(parent_spec, str(payload.get("layer_id") or ""))
        labels = layer.get("labels") if isinstance(layer, dict) else None
        if labels:
            return {"labels.digest": fingerprint_mapspec(labels)[:16]}
        return {"labels.absent": True}
    return {}


# ── propose ──────────────────────────────────────────────────────────


async def _next_branch_id(
    session_id: str, existing: Dict[str, Any], store: Any
) -> str:
    """分支命名 ``scc{N}``：scenario + whatif 两注册表防撞车。

    branch_session_id 派生命名空间是共享的（``{parent}__wif_{branch}``），
    用户直连 whatif 分支可能已占用 scc 前缀 id —— 命名必须跨表查重。
    """
    state = await _session_state(session_id, store)
    whatif_ids = state.get(WHATIF_REGISTRY_KEY)
    whatif = set(whatif_ids) if isinstance(whatif_ids, dict) else set()
    n = len(existing) + 1
    while f"scc{n}" in existing or f"scc{n}" in whatif:
        n += 1
    return f"scc{n}"


async def propose_candidate(
    session_id: str,
    *,
    title: str = "",
    hypothesis: str = "",
    intents: Optional[List[Any]] = None,
    created_by: str = "agent",
    turn_id: str = "",
    store: Any = None,
) -> ScenarioCandidate:
    """提议一个候选：fork 隔离分支 + 序列化 delta + 基准值观察。"""
    title = str(title or "")[:MAX_TITLE]
    hypothesis = str(hypothesis or "")[:MAX_HYPOTHESIS]
    # 1) 序列化先行（白名单/容量校验；失败整笔拒绝，不留孤儿分支）
    deltas = serialize_intents(list(intents or []))
    # 2) 容量预检（避免 create_branch 成功后注册表超限的孤儿分支；
    #    upsert 仍是权威检查，这里只是把常见失败前移）
    entries = await load_registry(session_id, REGISTRY_CANDIDATES, store=store)
    if len(entries) >= MAX_CANDIDATES:
        raise RegistryError(
            f"{REGISTRY_CANDIDATES}_limit",
            f"{REGISTRY_CANDIDATES} 已达上限 {MAX_CANDIDATES}（先归档/释放闲置条目）",
        )
    # 3) 共享基准（最新基准 revision+指纹一致 → 复用，不新增）
    baseline = await get_or_create_baseline(session_id, turn_id=turn_id, store=store)
    # 4) 分支命名（跨表防撞车）+ fork
    branch_id = await _next_branch_id(session_id, entries, store)
    parent_spec = await mapspec_store_instance.get_mapspec(session_id)
    manager = ScenarioBranchManager()
    meta = await manager.create_branch(session_id, branch_id, title, hypothesis)
    # 5) 基准值观察（父会话当前权威 spec，确定性；逐条 delta 填 base 边）
    for delta in deltas:
        delta.base_values = base_values_for_kind(delta.kind, delta.payload, parent_spec)
    candidate = ScenarioCandidate(
        candidate_id=mint_id("scn", session_id),
        title=title,
        hypothesis=hypothesis,
        session_id=session_id,
        branch_session_id=meta.branch_session_id,
        branch_id=branch_id,
        baseline_id=baseline.baseline_id,
        fork_revision=meta.fork_revision,
        state="draft",
        delta=deltas,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        created_by="user" if created_by == "user" else "agent",
        turn_id=str(turn_id or "")[:64],
    )
    await upsert_registry_entry(session_id, REGISTRY_CANDIDATES, candidate, store=store)
    return candidate


# ── apply / 状态机 ───────────────────────────────────────────────────


async def apply_candidate_interventions(
    session_id: str,
    candidate_id: str,
    *,
    deltas: Optional[List[DeltaIntent]] = None,
    turn_id: str = "",
    actor: str = "scenario",
    store: Any = None,
) -> List[Any]:
    """施加候选干预（逐条锁检查 → 分支全事务），返回逐条回执。

    - ``deltas=None``：重放候选全部 seq；否则以传入为准（增量补干预，
      合并进 candidate.delta —— seq 冲突整笔拒绝，seq 是选择键）；
    - 回执：``{"seq", "status": applied|lock_blocked|error, "error"}``，
      单条失败不回滚已成功项（与 whatif 干预语义一致）。
    """
    candidate = await get_candidate(session_id, candidate_id, store=store)
    if candidate.state in _TERMINAL_APPLY_STATES:
        raise RegistryError(
            "candidate_state_invalid",
            f"候选 {candidate_id} 状态为 {candidate.state}，不可再施加干预",
        )
    existing_seqs = {d.seq for d in candidate.delta}
    if deltas is not None:
        for delta in deltas:
            if delta.seq in existing_seqs:
                raise RegistryError(
                    "delta_seq_conflict",
                    f"delta seq {delta.seq} 已存在（seq 是选择键，不可覆写）",
                )
    to_apply: List[DeltaIntent] = (
        list(candidate.delta) if deltas is None else list(deltas)
    )

    results_by_seq: Dict[int, Dict[str, Any]] = {}
    decoded: List[Any] = []
    applied: List[int] = []
    errored: List[int] = []
    for delta in to_apply:
        lock = await find_blocking_lock(
            session_id, delta.target_id, delta.field_paths, store=store
        )
        if lock is not None:
            results_by_seq[delta.seq] = {
                "seq": delta.seq,
                "status": "lock_blocked",
                "error": f"lock:{lock.lock_id}",
            }
            continue
        try:
            decoded.append((delta, deserialize_intent(delta)))
        except DeltaCodecError as exc:
            results_by_seq[delta.seq] = {
                "seq": delta.seq,
                "status": "error",
                "error": exc.code,
            }
            errored.append(delta.seq)
    branch_results: List[Any] = []
    if decoded:
        branch_results = await ScenarioBranchManager().apply_intervention(
            session_id,
            candidate.branch_id,
            [intent for _, intent in decoded],
            actor=actor,
            reason=f"scenario candidate {candidate_id} apply",
        )
    for (delta, _intent), result in zip(decoded, branch_results):
        if result.is_error or result.superseded:
            results_by_seq[delta.seq] = {
                "seq": delta.seq,
                "status": "error",
                "error": (
                    result.error_code
                    or ("superseded" if result.superseded else "mutation_failed")
                ),
            }
            errored.append(delta.seq)
        else:
            results_by_seq[delta.seq] = {"seq": delta.seq, "status": "applied"}
            applied.append(delta.seq)

    def _mutate(entry: ScenarioCandidate) -> ScenarioCandidate:
        if deltas is not None:
            merged = {d.seq: d for d in entry.delta}
            for delta in deltas:
                merged[delta.seq] = delta
            entry.delta = [merged[k] for k in sorted(merged)]
        entry.applied_seqs = sorted(set(entry.applied_seqs) | set(applied))
        all_seqs = {d.seq for d in entry.delta}
        if errored:
            entry.state = "failed"  # 部分成功保留在 applied_seqs（可补齐重试）
        elif all_seqs and all_seqs <= set(entry.applied_seqs):
            entry.state = "comparable"
        elif entry.state == "draft":
            entry.state = "running"  # 首次 apply 且未全部完成
        entry.updated_at = utc_now_iso()
        return entry

    await locked_registry_update(
        session_id, REGISTRY_CANDIDATES, candidate_id, _mutate, store=store
    )
    # 回执按 to_apply 原序输出（确定性；重复 seq 以首条分类为准）
    seen: set[int] = set()
    out: List[Any] = []
    for delta in to_apply:
        if delta.seq in seen:
            continue
        seen.add(delta.seq)
        out.append(results_by_seq[delta.seq])
    return out


# ── 查询 / 退役 / 状态推进 ───────────────────────────────────────────


async def list_candidates(
    session_id: str, *, store: Any = None
) -> List[ScenarioCandidate]:
    """候选清单（(created_at, candidate_id) 稳定序）。"""
    entries = await load_registry(session_id, REGISTRY_CANDIDATES, store=store)
    return sorted(entries.values(), key=lambda c: (c.created_at, c.candidate_id))


async def get_candidate(
    session_id: str, candidate_id: str, *, store: Any = None
) -> ScenarioCandidate:
    entry = await get_registry_entry(
        session_id, REGISTRY_CANDIDATES, candidate_id, store=store
    )
    if entry is None:
        raise RegistryError(
            "candidate_not_found",
            f"候选 {candidate_id!r} 不存在（会话 {session_id}）",
        )
    return entry


async def abandon_candidate(
    session_id: str,
    candidate_id: str,
    *,
    reason: str = "",
    hard_delete: bool = True,
    store: Any = None,
) -> bool:
    """退役候选。hard_delete=True → 分支会话整体回收 + 注册表移除；
    hard_delete=False → state=archived（保留分支，防采纳）。
    幂等：候选不存在返回 False。``reason`` 仅调用方审计日志承担
    （候选条目无退役备注字段，不挪用 stale_reasons 语义）。"""
    del reason  # 签名冻结；条目无对应字段（见 docstring）
    entries = await load_registry(session_id, REGISTRY_CANDIDATES, store=store)
    candidate = entries.get(candidate_id)
    if candidate is None:
        return False
    if hard_delete:
        # 分支回收幂等（分支不存在返回 False，不影响候选移除结果）
        await ScenarioBranchManager().delete_branch(session_id, candidate.branch_id)
        return await remove_registry_entry(
            session_id, REGISTRY_CANDIDATES, candidate_id, store=store
        )

    def _mutate(entry: ScenarioCandidate) -> ScenarioCandidate:
        entry.state = "archived"
        entry.updated_at = utc_now_iso()
        return entry

    await locked_registry_update(
        session_id, REGISTRY_CANDIDATES, candidate_id, _mutate, store=store
    )
    return True


async def mark_candidate_state(
    session_id: str,
    candidate_id: str,
    state: str,
    *,
    stale_reasons: Optional[List[str]] = None,
    store: Any = None,
) -> ScenarioCandidate:
    """状态推进/恢复（staleness 标脏与 restore 共用；词表外整笔拒绝）。"""
    if state not in _VALID_STATES:
        raise RegistryError(
            "candidate_state_invalid",
            f"候选状态 {state!r} 不在词表 {sorted(_VALID_STATES)} 内",
        )

    def _mutate(entry: ScenarioCandidate) -> ScenarioCandidate:
        entry.state = state  # type: ignore[assignment]
        entry.stale_reasons = [str(r)[:120] for r in (stale_reasons or [])][:8]
        entry.updated_at = utc_now_iso()
        return entry

    updated = await locked_registry_update(
        session_id, REGISTRY_CANDIDATES, candidate_id, _mutate, store=store
    )
    assert updated is not None  # must_exist=True 保证（不存在时上面已抛）
    return updated


__all__ = [
    "abandon_candidate",
    "apply_candidate_interventions",
    "base_values_for_kind",
    "get_candidate",
    "layer_of",
    "list_candidates",
    "mark_candidate_state",
    "propose_candidate",
]
