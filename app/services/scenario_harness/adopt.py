"""选择性采纳 / 回退事务（G05 WP4）。

职责边界（契约见 ``contracts.py`` 的 AdoptionReceipt / AdoptSelection）：
隔离机制只有 whatif 分支一套，本模块只做"分支产物 → 父会话权威态"的
确定性事务，不造第二套合并/隔离实现。

- **三方合并裁决（逐条逐字段）**：``base_values``（提案时观察到的父会话
  基准）vs 父会话当前值 —— 相等才重放；未观察基线（base 无记录）保守
  拒绝（``agent_conflict``/``base_value_missing``），绝不静默覆盖用户或
  其他 Agent 在分叉后对同字段的改动（诚实优先于便利）。
- **用户锁定优先**：``find_blocking_lock`` 命中的条目整条拒
  （``lock_blocked``，detail 携带 lock_id）。
- **事务原子性**：applied 条目先落采纳前 checkpoint（``CheckpointIntent``，
  兼作回退锚），再逐条经 ``apply_gis_mutation`` 链式 CAS（每条
  ``expected_revision`` = 上一条结果 revision）重放；任一条失败或
  superseded 立即 ``RollbackIntent`` 回滚到锚点 —— 要么全落，要么父
  spec 回到采纳前，绝不留半采纳。批通道 ``apply_gis_mutation_batch``
  只接受 presentation 意图（引擎现状），混合意图的原子性由
  "checkpoint + 链式 CAS + 失败回滚"承载（净效果等价单事务）。
- **幂等**：idempotency_key = sha256(candidate|mode|groups|seqs|CAS
  revision)[:24]，键绑定采纳锚 revision；adopted 候选同选集重放命中既有
  回执（不重放 mutation）；revert 后 revision 已推进 → 重采纳得新键新回执。
- **回执零掩盖**：逐条 verdict（applied / lock_blocked / agent_conflict /
  target_missing / not_selected）+ 计数落账；全拒绝也是合法决议（回执照
  落、候选状态不动、父 spec 零副作用）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_world_state import apply_gis_mutation
from app.services.mapspec.intents import CheckpointIntent, RollbackIntent
from app.services.mapspec.store import mapspec_store_instance
from app.services.scenario_harness.contracts import (
    MAX_REASON,
    REGISTRY_ADOPTIONS,
    REGISTRY_BASELINES,
    REGISTRY_CANDIDATES,
    AdoptSelection,
    AdoptionItem,
    AdoptionItemVerdict,
    AdoptionReceipt,
    DeltaIntent,
    ScenarioCandidate,
)
from app.services.scenario_harness.candidates import base_values_for_kind
from app.services.scenario_harness.intents_codec import (
    DeltaCodecError,
    deserialize_intents,
)
from app.services.scenario_harness.locks import find_blocking_lock
from app.services.scenario_harness.store import (
    RegistryError,
    load_registry,
    locked_registry_update,
    mint_id,
    upsert_registry_entry,
    utc_now_iso,
)
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

#: base_values 缺记录哨兵（区别于显式记录的 None = "观察到缺席"）。
_ABSENT = object()

#: 可采纳状态词表（旁路 stale 可恢复后采纳）。
_ADOPTABLE_STATES = ("comparable", "stale")


# ── 值比较辅助（确定性；缺席归一）────────────────────────────────────


def _norm_absent(value: Any) -> Any:
    """缺席归一：None / 空dict / 空list → None（与"观察到缺席"同判）。"""
    if value is None:
        return None
    if isinstance(value, (dict, list)) and not value:
        return None
    return value


def _values_equal(base: Any, parent: Any) -> bool:
    """base vs parent 确定性相等（结构值走 canonical JSON，标量走 ==）。"""
    b = _norm_absent(base)
    p = _norm_absent(parent)
    if b is None or p is None:
        return b is None and p is None
    if isinstance(b, (dict, list)) or isinstance(p, (dict, list)):
        try:
            return json.dumps(b, ensure_ascii=False, sort_keys=True) == json.dumps(
                p, ensure_ascii=False, sort_keys=True
            )
        except (TypeError, ValueError):
            return False
    return b == p


def _layer_digest(layer: Dict[str, Any]) -> str:
    """单层 digest（与 branch_manager 指纹同口径；惰性导入避免几何链）。"""
    from app.services.simulation.whatif.branch_manager import fingerprint_mapspec

    return fingerprint_mapspec(layer)[:16]


# ── 三方字段判定（统一双读：同一观察函数 × 提案/采纳两个时刻）────────

#: 层/组件/数据源目标意图（absent↔digest 翻转 → target_missing）。
_TARGET_KINDS = {
    "PatchLayerStyleIntent": "layer",
    "PatchLayerLabelAuthoringIntent": "layer",
    "UpsertLayerIntent": "layer",
    "PatchComponentIntent": "component",
    "RemoveComponentIntent": "component",
    "UpsertSourceIntent": "source",
}


def _adjudicate_fields(
    spec: Dict[str, Any], delta: DeltaIntent
) -> Tuple[Optional[AdoptionItemVerdict], str]:
    """三方字段判定（纯函数）→ (阻断 verdict | None, detail)。

    口径（review P1-1 统一）：``base_values``（提案时刻父态观察）vs
    ``observe``（采纳时刻父态观察，**同一函数** ``base_values_for_kind``）。
    逐键比对 —— 值不同或键集合翻转（absent↔digest）即冲突：

    - base 无记录 → ``agent_conflict``/``base_value_missing``（未观察基线
      不可自动合并，诚实优先）；
    - absent→digest 翻转且目标是层/组件/数据源 → ``target_missing``（分叉
      后目标被他人删除）；
    - digest→absent 翻转 → ``agent_conflict``（目标被他人删除，采纳语义
      不明 —— 对 upsert 类即"重建被删目标"，保守拒绝）。
    """
    kind = delta.kind
    base = delta.base_values or {}
    if not base:
        return "agent_conflict", "base_value_missing:unobserved"
    current = base_values_for_kind(kind, delta.payload, spec)

    diffs = sorted(
        key
        for key in set(base) | set(current)
        if not _values_equal(base.get(key), current.get(key))
    )
    if not diffs:
        return None, ""

    # 目标缺席翻转的精确披露（base 见过目标、现在没了）
    target_kind = _TARGET_KINDS.get(kind)
    if target_kind:
        absent_key = f"{target_kind}.absent"
        digest_key = f"{target_kind}.digest"
        if base.get(digest_key) and current.get(absent_key) is True:
            return "target_missing", f"{target_kind}={delta.target_id} 不在父会话"

    shown = ",".join(diffs[:4])
    more = f"(+{len(diffs) - 4})" if len(diffs) > 4 else ""
    return "agent_conflict", f"conflict_fields={shown}{more}"


async def _adjudicate_item(
    session_id: str,
    spec: Dict[str, Any],
    delta: DeltaIntent,
    *,
    store: Any = None,
) -> AdoptionItem:
    """单条干预裁决：先锁（用户显式决策优先），再三方字段判定。"""
    lock = await find_blocking_lock(
        session_id, delta.target_id, delta.field_paths, store=store
    )
    if lock is not None:
        return AdoptionItem(
            seq=delta.seq,
            kind=delta.kind,
            group=delta.group,
            target_id=delta.target_id,
            verdict="lock_blocked",
            detail=f"lock_id={lock.lock_id}"[:MAX_REASON],
        )
    verdict, detail = _adjudicate_fields(spec, delta)
    return AdoptionItem(
        seq=delta.seq,
        kind=delta.kind,
        group=delta.group,
        target_id=delta.target_id,
        verdict=verdict or "applied",
        detail=detail[:MAX_REASON],
    )


# ── 幂等键 / 回执检索 ────────────────────────────────────────────────


def _idempotency_key(candidate_id: str, selection: AdoptSelection, cas_revision: int) -> str:
    """sha256(candidate|mode|groups|seqs|CAS revision)[:24]（键绑定采纳锚）。"""
    raw = (
        f"{candidate_id}|{selection.mode}|{sorted(selection.groups)}"
        f"|{sorted(selection.seqs)}|{int(cas_revision)}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


async def _receipt_by_id(
    session_id: str, adoption_id: str, *, store: Any = None
) -> Optional[AdoptionReceipt]:
    entries = await load_registry(session_id, REGISTRY_ADOPTIONS, store=store)
    return entries.get(adoption_id)


async def _adopted_receipt_hit(
    session_id: str,
    cand: ScenarioCandidate,
    selection: AdoptSelection,
    *,
    store: Any = None,
) -> Optional[AdoptionReceipt]:
    """adopted 候选的幂等命中：既有回执的 base_revision 重算键比对。"""
    if not cand.adoption_id:
        return None
    receipt = await _receipt_by_id(session_id, cand.adoption_id, store=store)
    if receipt is None or receipt.reverted_by:
        return None
    if receipt.candidate_id != cand.candidate_id:
        return None
    key = _idempotency_key(cand.candidate_id, selection, receipt.base_revision)
    return receipt if key == receipt.idempotency_key else None


async def _scan_idempotent_receipt(
    session_id: str, key: str, *, store: Any = None
) -> Optional[AdoptionReceipt]:
    """落账前扫描：同键且未回退的回执已存在 → 复用（防竞态双写）。"""
    entries = await load_registry(session_id, REGISTRY_ADOPTIONS, store=store)
    for receipt in entries.values():
        if receipt.idempotency_key == key and not receipt.reverted_by:
            return receipt
    return None


# ── revision / 回滚辅助 ──────────────────────────────────────────────


async def _parent_revision(session_id: str) -> int:
    """父会话当前 mutation revision（CAS 令牌；与引擎单调源一致）。"""
    state = await session_data_manager.get_map_state(session_id)
    try:
        return int(state.get("_cartographic_mutation_revision", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _partial_adopt_error(
    candidate_id: str, checkpoint_id: str, rollback_err: str
) -> RegistryError:
    """半采纳态的诚实错误（review P1-3）：携带恢复路径，绝不谎报已回滚。"""
    return RegistryError(
        "adopt_rollback_failed",
        f"采纳中途失败且回滚也失败 —— 父会话可能处于部分采纳态。"
        f"恢复：对父会话执行 RollbackIntent(checkpoint_id={checkpoint_id!r}) "
        f"或人工核对（candidate={candidate_id}, rollback_err={rollback_err}）",
    )


async def _rollback_to_anchor(

    session_id: str, checkpoint_id: str, candidate_id: str, turn_id: str = ""
) -> Tuple[bool, str]:
    """失败回滚：父 spec 回到采纳前锚点。返回 (是否成功, 失败原因)。

    review P1-3：失败**必须如实上抛**（携带 checkpoint_id 供人工恢复），
    不得被"已整笔回滚"话术掩盖 —— 半采纳态要可发现。
    """
    try:
        res = await apply_gis_mutation(
            session_id,
            RollbackIntent(checkpoint_id=checkpoint_id),
            origin="agent",
            actor="scenario_adopt",
            turn_id=turn_id or None,
            reason=f"adopt-rollback {candidate_id}",
        )
        if res.is_error:
            logger.error(
                "[scenario_adopt] rollback failed for %s: %s",
                candidate_id,
                res.error_msg,
            )
            return False, res.error_msg[:120]
        return True, ""
    except Exception as exc:  # noqa: BLE001 — 回滚失败必须留痕，但不掩盖原错误
        logger.exception("[scenario_adopt] rollback crashed for %s", candidate_id)
        return False, f"{type(exc).__name__}"


# ── 主入口：采纳 ─────────────────────────────────────────────────────


async def adopt_candidate(
    session_id: str,
    candidate_id: str,
    selection: AdoptSelection,
    *,
    origin: str = "user",
    actor: str = "user",
    turn_id: str = "",
    store: Any = None,
) -> AdoptionReceipt:
    """选择性采纳候选到父会话（三方裁决 + 锚点事务 + 幂等回执）。

    确定性算法：候选状态闸 → 基准存在闸 → 选集过滤 → 逐条裁决 →
    applied=0 走"全拒绝决议"（回执照落、零 mutation）；applied≥1 先落
    采纳前 checkpoint 锚，再链式 CAS 重放（失败整笔回滚），落回执并把
    候选推到 adopted。
    """
    candidates = await load_registry(session_id, REGISTRY_CANDIDATES, store=store)
    cand = candidates.get(candidate_id)
    if cand is None:
        raise RegistryError(
            "candidate_not_found",
            f"候选 {candidate_id!r} 不存在（会话 {session_id}）",
        )
    if cand.state == "adopted":
        hit = await _adopted_receipt_hit(session_id, cand, selection, store=store)
        if hit is not None:
            return hit
        raise RegistryError(
            "adopt_already_adopted",
            f"候选 {candidate_id} 已采纳（回执 {cand.adoption_id or '未知'}），"
            "同选集幂等未命中（如需部分重采纳请先 revert）",
        )
    if cand.state not in _ADOPTABLE_STATES:
        raise RegistryError(
            "adopt_not_comparable",
            f"候选 {candidate_id} 当前状态 {cand.state}，"
            f"仅 {'/'.join(_ADOPTABLE_STATES)} 可采纳",
        )

    baselines = await load_registry(session_id, REGISTRY_BASELINES, store=store)
    if cand.baseline_id not in baselines:
        raise RegistryError(
            "baseline_missing",
            f"候选 {candidate_id} 绑定的基准 {cand.baseline_id!r} 不存在"
            f"（会话 {session_id}）",
        )

    # review P2-6：数据版本推进的候选不可采纳（contracts.CandidateImpact
    # "unresolvable：旧结果不可采纳"在此强制 —— 旧版本产物不得混入权威态）。
    from app.services.scenario_harness.baseline import observe_baseline_move
    from app.services.scenario_harness.staleness import decide_impact_level

    move = await observe_baseline_move(session_id, cand.baseline_id, store=store)
    level, reasons = decide_impact_level(move, cand)
    if level == "unresolvable":
        raise RegistryError(
            "adopt_unresolvable",
            f"候选 {candidate_id} 的基准数据版本已推进，旧结果不可采纳"
            f"（原因: {';'.join(reasons[:3])}）。请在候选上局部重算后再采纳。",
        )

    deltas = sorted(cand.delta, key=lambda d: d.seq)
    if selection.mode == "groups":
        if not selection.groups:
            raise RegistryError("adopt_empty_selection", "groups 模式需要至少选择一个分组")
        wanted = set(selection.groups)
        selected = [d for d in deltas if d.group in wanted]
    elif selection.mode == "seqs":
        if not selection.seqs:
            raise RegistryError("adopt_empty_selection", "seqs 模式需要至少选择一个序号")
        wanted = set(selection.seqs)
        selected = [d for d in deltas if d.seq in wanted]
    else:
        selected = list(deltas)
    if selection.mode in ("groups", "seqs") and not selected:
        raise RegistryError(
            "adopt_empty_selection",
            f"选集与候选 {candidate_id} 的干预无交集（groups/seqs 拼写核对）",
        )
    selected_seqs = {d.seq for d in selected}

    spec = await mapspec_store_instance.get_mapspec(session_id)
    if not isinstance(spec, dict) or not spec:
        raise RegistryError(
            "parent_spec_missing",
            f"父会话 {session_id} 无权威 MapSpec 可采纳",
        )

    items: List[AdoptionItem] = []
    for d in deltas:
        if d.seq not in selected_seqs:
            items.append(
                AdoptionItem(
                    seq=d.seq,
                    kind=d.kind,
                    group=d.group,
                    target_id=d.target_id,
                    verdict="not_selected",
                )
            )
        else:
            items.append(await _adjudicate_item(session_id, spec, d, store=store))
    item_by_seq = {item.seq: item for item in items}
    applied_deltas = [
        d for d in selected if item_by_seq[d.seq].verdict == "applied"
    ]

    current_rev = await _parent_revision(session_id)
    if (
        selection.expected_revision is not None
        and int(selection.expected_revision) != current_rev
    ):
        raise RegistryError(
            "adopt_superseded",
            f"父会话已推进（expected_revision={selection.expected_revision}，"
            f"当前 {current_rev}），请重新观察后重试",
        )

    selected_groups = sorted({str(d.group) for d in selected})
    reason = f"adopt {candidate_id} groups={','.join(selected_groups)}"[:MAX_REASON]

    if not applied_deltas:
        # 全拒绝决议：合法 —— 回执照常落账；不执行 mutation、候选状态不变。
        key = _idempotency_key(candidate_id, selection, current_rev)
        existing = await _scan_idempotent_receipt(session_id, key, store=store)
        if existing is not None:
            return existing
        receipt = AdoptionReceipt(
            adoption_id=mint_id("ad", session_id),
            session_id=session_id,
            candidate_id=candidate_id,
            base_revision=current_rev,
            final_revision=current_rev,
            selected_groups=selected_groups,
            items=items,
            applied_count=0,
            skipped_count=len(items),
            adopted_at=utc_now_iso(),
            turn_id=turn_id,
            idempotency_key=key,
            pre_adopt_checkpoint_id="",
        )
        await upsert_registry_entry(session_id, REGISTRY_ADOPTIONS, receipt, store=store)
        return receipt

    # 采纳前锚：checkpoint 本身是 mutation（revision+1），锚后重读 revision
    # 作为链式 CAS 基准（锚点不动 spec，失败/superseded 时无 spec 副作用）。
    # review P1-2：锚点带 CAS（expected=裁决所读 revision）—— spec 读 →
    # 锚点窗口内任何并发落账都会让锚点 superseded，裁决口径不可能过期生效。
    ck_res = await apply_gis_mutation(
        session_id,
        CheckpointIntent(),
        origin="agent",
        actor="scenario_adopt",
        expected_revision=current_rev,
        turn_id=turn_id or None,
        reason=f"pre-adopt {candidate_id}",
    )
    if ck_res.superseded:
        raise RegistryError(
            "adopt_superseded", "父会话并发推进，请重新观察后重试"
        )
    if ck_res.is_error:
        raise RegistryError(
            "adopt_apply_failed", f"采纳前锚点创建失败: {ck_res.error_msg}"
        )
    checkpoint_id = str(ck_res.checkpoint_id or "")
    base_revision = await _parent_revision(session_id)
    if base_revision != current_rev + 1:
        # 防御性复核（正常路径由上面的 CAS 短路）。
        raise RegistryError(
            "adopt_superseded", "父会话并发推进，请重新观察后重试"
        )

    try:
        intents = deserialize_intents(applied_deltas)
    except DeltaCodecError as exc:
        raise RegistryError(
            "adopt_apply_failed", f"候选干预反序列化失败: {exc.message}"
        ) from exc

    # 链式 CAS 重放（单事务语义：任一条失败/superseded 立即整笔回滚锚点）。
    # review P2-3：user-wins 守卫命中（MapSpecResult.locked_*_ids 指名目标）
    # → 该条降级 user_wins_skip 并继续其余条（工具承诺与实现一致：用户锁定
    # 的字段逐项跳过，不是整笔失败）。
    running = base_revision
    downgraded_seqs: List[int] = []
    for delta, intent in zip(applied_deltas, intents):
        res = await apply_gis_mutation(
            session_id,
            intent,
            origin=origin,  # type: ignore[arg-type]
            actor=actor,
            expected_revision=running,
            mutation_id=mint_id("adopt", session_id),
            turn_id=turn_id or None,
            reason=reason,
        )
        if res.superseded:
            ok, rb_err = await _rollback_to_anchor(
                session_id, checkpoint_id, candidate_id, turn_id
            )
            if not ok:
                raise _partial_adopt_error(candidate_id, checkpoint_id, rb_err)
            raise RegistryError(
                "adopt_superseded", "父会话并发推进，请重新观察后重试"
            )
        if res.is_error:
            locked_ids = set(res.locked_layer_ids) | set(res.locked_component_ids)
            guard_hit = delta.target_id in locked_ids if locked_ids else False
            # presentation 守卫用单码契约标记（不填 locked_*_ids）
            if res.error_code == "user_wins_presentation":
                guard_hit = True
            if guard_hit:
                item_by_seq[delta.seq].verdict = "user_wins_skip"
                item_by_seq[delta.seq].detail = (
                    f"user_wins:{res.error_code or 'locked'}({delta.target_id})"
                )[:MAX_REASON]
                downgraded_seqs.append(delta.seq)
                continue
            ok, rb_err = await _rollback_to_anchor(
                session_id, checkpoint_id, candidate_id, turn_id
            )
            if not ok:
                raise _partial_adopt_error(candidate_id, checkpoint_id, rb_err)
            raise RegistryError(
                "adopt_apply_failed",
                f"采纳重放失败（已整笔回滚到采纳前锚点）: {res.error_msg}",
            )
        running = int(res.mutation_revision or running + 1)
    applied_count = len(applied_deltas) - len(downgraded_seqs)
    if applied_count == 0:
        # 全部在链上被 user-wins 拦截：无 spec 副作用（锚点不动物），回执如实
        # 记录逐项裁决；候选状态保持（仍是 comparable）。
        receipt = AdoptionReceipt(
            adoption_id=mint_id("ad", session_id),
            session_id=session_id,
            candidate_id=candidate_id,
            base_revision=base_revision,
            final_revision=running,
            selected_groups=selected_groups,
            items=items,
            applied_count=0,
            skipped_count=len(items),
            adopted_at=utc_now_iso(),
            turn_id=turn_id,
            idempotency_key=_idempotency_key(candidate_id, selection, base_revision),
            pre_adopt_checkpoint_id=checkpoint_id,
        )
        await upsert_registry_entry(session_id, REGISTRY_ADOPTIONS, receipt, store=store)
        return receipt

    key = _idempotency_key(candidate_id, selection, base_revision)
    existing = await _scan_idempotent_receipt(session_id, key, store=store)
    if existing is not None:
        # revision 单调 → 理论不可达；防御同刻竞态双写（不重放 mutation）。
        return existing

    receipt = AdoptionReceipt(
        adoption_id=mint_id("ad", session_id),
        session_id=session_id,
        candidate_id=candidate_id,
        base_revision=base_revision,
        final_revision=running,
        selected_groups=selected_groups,
        items=items,
        applied_count=applied_count,
        skipped_count=len(items) - applied_count,
        adopted_at=utc_now_iso(),
        turn_id=turn_id,
        idempotency_key=key,
        pre_adopt_checkpoint_id=checkpoint_id,
    )
    await upsert_registry_entry(session_id, REGISTRY_ADOPTIONS, receipt, store=store)

    def _mark_adopted(entry: ScenarioCandidate) -> ScenarioCandidate:
        entry.state = "adopted"
        entry.adoption_id = receipt.adoption_id
        entry.updated_at = utc_now_iso()
        return entry

    await locked_registry_update(
        session_id, REGISTRY_CANDIDATES, candidate_id, _mark_adopted, store=store
    )
    return receipt


# ── 主入口：回退 ─────────────────────────────────────────────────────


async def revert_adoption(
    session_id: str,
    adoption_id: str,
    *,
    origin: str = "user",
    actor: str = "user",
    turn_id: str = "",
    allow_drift: bool = False,
    store: Any = None,
) -> AdoptionReceipt:
    """回退一次采纳：父会话回滚到采纳前锚点，候选回 comparable。

    - 回执不存在 / 已回退 → ``revert_not_available``；
    - 无采纳前锚（全拒绝决议或旧回执）→ ``revert_anchor_missing``；
    - **采纳后有漂移**（他人 revision 推进，review P2-2）→ 默认拒绝
      ``revert_drift``（回滚是整 spec 替换，会静默清掉采纳后所有人的改动）；
      ``allow_drift=True`` 显式放行并把丢弃区间记入回执
      ``discarded_revisions``；
    - 回滚经 ``RollbackIntent``（CAS 读-比对-执行，superseded 短重试）；
    - 候选 state → comparable（分支仍在）、adoption_id 保留审计；
      回退后重采纳允许（revision 已推进 → 新幂等键 → 新回执）。
    """
    receipt = await _receipt_by_id(session_id, adoption_id, store=store)
    if receipt is None or receipt.reverted_by:
        raise RegistryError(
            "revert_not_available",
            f"采纳回执 {adoption_id!r} 不存在或已回退（会话 {session_id}）",
        )
    if not receipt.pre_adopt_checkpoint_id:
        raise RegistryError(
            "revert_anchor_missing",
            f"回执 {adoption_id} 无采纳前锚点（全拒绝决议无 mutation 可回退）",
        )
    current = await _parent_revision(session_id)
    discarded = max(0, current - int(receipt.final_revision or 0))
    if discarded > 0 and not allow_drift:
        raise RegistryError(
            "revert_drift",
            f"采纳后父会话又推进了 {discarded} 个 revision"
            f"（回滚会一并丢弃这些改动）。确认丢弃请带 allow_drift=true 重试。",
        )

    rb = None
    for _ in range(3):
        expected = await _parent_revision(session_id)
        rb = await apply_gis_mutation(
            session_id,
            RollbackIntent(checkpoint_id=receipt.pre_adopt_checkpoint_id),
            origin=origin,  # type: ignore[arg-type]
            actor=actor,
            expected_revision=expected,
            turn_id=turn_id or None,
            reason=f"revert {adoption_id}",
        )
        if not rb.superseded:
            break
    assert rb is not None  # 循环至少执行一次
    if rb.is_error:
        raise RegistryError("revert_failed", f"回退执行失败: {rb.error_msg}")
    if rb.superseded:
        raise RegistryError(
            "revert_failed", "父会话并发推进，回退未执行（已重试），请稍后重试"
        )

    reverted_by = str(rb.mutation_id or f"rev:{rb.mutation_revision}")

    def _mark_reverted(entry: AdoptionReceipt) -> AdoptionReceipt:
        entry.reverted_by = reverted_by
        entry.discarded_revisions = discarded
        return entry

    updated = await locked_registry_update(
        session_id, REGISTRY_ADOPTIONS, adoption_id, _mark_reverted, store=store
    )
    assert updated is not None  # must_exist=True 保证

    def _back_to_comparable(entry: ScenarioCandidate) -> ScenarioCandidate:
        if entry.state == "adopted":
            entry.state = "comparable"
            entry.updated_at = utc_now_iso()
        return entry

    try:
        await locked_registry_update(
            session_id,
            REGISTRY_CANDIDATES,
            receipt.candidate_id,
            _back_to_comparable,
            store=store,
        )
    except RegistryError:
        # 候选已删除/归档：回退本身已完成，不阻断回执更新（审计链保留）。
        logger.info(
            "[scenario_adopt] revert %s: candidate %s missing, state skip",
            adoption_id,
            receipt.candidate_id,
        )
    return updated


__all__ = [
    "adopt_candidate",
    "revert_adoption",
]
