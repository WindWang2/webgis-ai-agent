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
import logging

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


logger = logging.getLogger(__name__)


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


def component_of(
    spec: Optional[Dict[str, Any]], component_id: str
) -> Optional[Dict[str, Any]]:
    """确定性组件读取：spec["layout"]["components"] 内 id 精确匹配。"""
    layout = (spec or {}).get("layout")
    components = layout.get("components") if isinstance(layout, dict) else None
    wanted = str(component_id or "")
    for component in components or []:
        if isinstance(component, dict) and str(component.get("id") or "") == wanted:
            return component
    return None


def source_of(
    spec: Optional[Dict[str, Any]], source_id: str
) -> Optional[Dict[str, Any]]:
    """确定性数据源读取：spec["sources"][source_id]（dict 键）。"""
    sources = (spec or {}).get("sources")
    if not isinstance(sources, dict):
        return None
    entry = sources.get(str(source_id or ""))
    return entry if isinstance(entry, dict) else None


#: 全局意图 kind → spec 顶层分支键（基准观察与采纳共用一份口径）。
GLOBAL_BRANCH_OF_KIND = {
    "SetLayoutIntent": "layout",
    "SetBasemapIntent": "basemap",
    "SetTimeIntent": "time",
    "SetTemporalViewIntent": "temporal",
    "SetScenarioModeIntent": "scenario_mode",
}

#: SetLayoutIntent 的字段键（field closure 与 base 观察共用）。
LAYOUT_KEYS = ("legend", "controls", "margins", "components",
               "component_links", "composition")


def base_values_for_kind(
    kind: str,
    payload: Dict[str, Any],
    parent_spec: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """确定性基准值观察（propose 时逐条 delta 填 ``base_values``）。

    **单一口径**：采纳时（adopt）对同一函数传入采纳时刻的父 spec 再算一次，
    逐键比对 —— 键集合或值任一不同即冲突。约定：
    - 小标量/键值内联（``paint.<k>`` / ``scenario_mode``）；
    - 大对象一律 digest（``*.digest`` = 指纹前 16）；缺席记 ``*.absent: True``。
    键形状是 adopt 侧 target_missing/agent_conflict 判定的依据（absent↔digest
    翻转 = 分叉后目标被他人创建/删除）。
    """
    spec = parent_spec if isinstance(parent_spec, dict) else {}
    if kind == "PatchLayerStyleIntent":
        layer = layer_of(spec, str(payload.get("layer_id") or ""))
        paint = layer.get("paint") if isinstance(layer, dict) else None
        paint = paint if isinstance(paint, dict) else {}
        patch = payload.get("paint")
        keys = list(patch.keys()) if isinstance(patch, dict) else []
        return {f"paint.{k}": paint.get(k) for k in keys}
    if kind == "PatchLayerPresentationIntent":
        # presentation 观察：与 mutation_primitives._patch_layer_presentation
        # 的真实落字段同源 —— 可见性 = layout.visibility（"visible"/"none"），
        # 透明度 = paint.opacity。
        layer = layer_of(spec, str(payload.get("layer_id") or ""))
        if not isinstance(layer, dict):
            return {"presentation.absent": True}
        out: Dict[str, Any] = {}
        if payload.get("visible") is not None:
            layout = layer.get("layout")
            visibility = (
                layout.get("visibility") if isinstance(layout, dict) else None
            )
            out["presentation.visible"] = (
                None if visibility is None else visibility != "none"
            )
        if payload.get("opacity") is not None:
            paint = layer.get("paint")
            out["presentation.opacity"] = (
                paint.get("opacity") if isinstance(paint, dict) else None
            )
        return out
    if kind == "UpsertLayerIntent":
        layer = payload.get("layer")
        layer_id = str(layer.get("id") or "") if isinstance(layer, dict) else ""
        existing = layer_of(spec, layer_id)
        if isinstance(existing, dict):
            return {"layer.digest": fingerprint_mapspec(existing)[:16]}
        return {"layer.absent": True}
    if kind == "PatchLayerLabelAuthoringIntent":
        # 基准 = 父层当前标注状态（候选 ops 不参与基准 —— review P1-1 修正）
        layer = layer_of(spec, str(payload.get("layer_id") or ""))
        if isinstance(layer, dict):
            labels_state = {
                k: layer[k]
                for k in ("labels", "labels_config")
                if layer.get(k) is not None
            }
            if labels_state:
                return {"labels.digest": fingerprint_mapspec(labels_state)[:16]}
        return {"labels.absent": True}
    if kind in ("PatchComponentIntent", "RemoveComponentIntent"):
        comp = component_of(spec, str(payload.get("component_id") or ""))
        if isinstance(comp, dict):
            return {"component.digest": fingerprint_mapspec(comp)[:16]}
        return {"component.absent": True}
    if kind == "UpsertSourceIntent":
        src = source_of(spec, str(payload.get("source_id") or ""))
        if isinstance(src, dict):
            return {"source.digest": fingerprint_mapspec(src)[:16]}
        return {"source.absent": True}
    if kind == "SetLayoutIntent":
        layout = spec.get("layout")
        layout = layout if isinstance(layout, dict) else {}
        out: Dict[str, Any] = {}
        for key in LAYOUT_KEYS:
            if payload.get(key) is not None:
                value = layout.get(key)
                if value is None or (isinstance(value, (dict, list)) and not value):
                    out[f"layout.{key}.absent"] = True
                else:
                    out[f"layout.{key}.digest"] = fingerprint_mapspec(value)[:16]
        return out
    branch = GLOBAL_BRANCH_OF_KIND.get(kind)
    if branch == "layout":  # pragma: no cover — SetLayoutIntent 已在上方处理
        return {}
    if branch:
        value = spec.get(branch)
        if value is None or (isinstance(value, (dict, list)) and not value):
            return {f"{branch}.absent": True}
        return {f"{branch}.digest": fingerprint_mapspec(value)[:16]}
    return {}


def candidate_summary(cand: ScenarioCandidate) -> Dict[str, Any]:
    """有界摘要行（工具/路由消费）：**不含** delta payload/base_values ——
    LLM/UI 面只拿身份、状态、分组与失效披露（review P2-4，token 不整包复制）。
    """
    return {
        "candidate_id": cand.candidate_id,
        "title": cand.title,
        "hypothesis": cand.hypothesis,
        "state": cand.state,
        "baseline_id": cand.baseline_id,
        "fork_revision": cand.fork_revision,
        "branch_session_id": cand.branch_session_id,
        "delta": [
            {
                "seq": d.seq, "kind": d.kind, "group": d.group,
                "target_id": d.target_id, "field_paths": d.field_paths,
                "summary": d.summary,
            }
            for d in cand.delta
        ],
        "applied_seqs": list(cand.applied_seqs),
        "created_by": cand.created_by,
        "adoption_id": cand.adoption_id,
        "stale_reasons": list(cand.stale_reasons),
        "compare_digest": cand.compare_digest,
    }


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
    try:
        await upsert_registry_entry(session_id, REGISTRY_CANDIDATES, candidate, store=store)
    except RegistryError:
        # review P2-8：注册表写入失败（并发容量竞争）→ 回收已物化的分支，
        # 不留孤儿派生会话；容量错误原样上抛。
        try:
            await ScenarioBranchManager().delete_branch(session_id, branch_id)
        except Exception:  # noqa: BLE001 — 回收失败不掩盖原错误
            logger.warning(
                "[scenario] propose orphan branch cleanup failed: %s", branch_id
            )
        raise
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
        # review P3-6：追加干预的 seq 自动接续已有 delta（调用方不感知内部
        # 序号）；调用方内部重复 seq 也拒绝（seq 是选择键）。
        seen: set = set()
        next_seq = (max(existing_seqs) + 1) if existing_seqs else 1
        for delta in deltas:
            if delta.seq in existing_seqs or delta.seq in seen:
                raise RegistryError(
                    "delta_seq_conflict",
                    f"delta seq {delta.seq} 已存在（seq 是选择键，不可覆写）",
                )
            seen.add(delta.seq)
            delta.seq = next_seq
            next_seq += 1
    to_apply: List[DeltaIntent] = (
        list(candidate.delta) if deltas is None else list(deltas)
    )

    results_by_seq: Dict[int, Dict[str, Any]] = {}
    decoded: List[Any] = []
    applied: List[int] = []
    errored: List[int] = []
    blocked: List[int] = []
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
    # review P3-4：返回长度漂移防护（API 边异常）—— 严格配对，
    # 未覆盖的 seq 如实记 error，不静默丢、不 KeyError。
    if len(branch_results) != len(decoded):
        logger.warning(
            "[scenario] apply_intervention returned %d results for %d intents"
            " (candidate %s)", len(branch_results), len(decoded), candidate_id,
        )
    for idx, (delta, _intent) in enumerate(decoded):
        result = branch_results[idx] if idx < len(branch_results) else None
        if result is None:
            results_by_seq[delta.seq] = {
                "seq": delta.seq,
                "status": "error",
                "error": "result_missing",
            }
            errored.append(delta.seq)
            continue
        if result.is_error or result.superseded:
            error_code = str(result.error_code or "")
            # review P2-3 语义：presentation 守卫命中 = 该条被**用户所有权**
            # 拦截（候选照常 comparable，其余条已应用）——不是候选失败。
            # 采纳链上同条会再被拦并降级 user_wins_skip 逐项披露。
            if result.is_error and error_code == "user_wins_presentation":
                results_by_seq[delta.seq] = {
                    "seq": delta.seq,
                    "status": "user_wins_blocked",
                    "error": error_code,
                }
                blocked.append(delta.seq)
                continue
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
        entry.blocked_seqs = sorted(set(entry.blocked_seqs) | set(blocked))
        all_seqs = {d.seq for d in entry.delta}
        if errored:
            entry.state = "failed"  # 部分成功保留在 applied_seqs（可补齐重试）
        elif all_seqs and all_seqs <= set(entry.applied_seqs) | set(entry.blocked_seqs):
            # blocked = 被 user-wins 拦截（用户所有权，agent 永不可试验/采纳
            # 该条）——不阻断候选进入 comparable。
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
