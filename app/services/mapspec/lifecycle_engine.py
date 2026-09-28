"""MapSpecLifecycleEngine - 核心 MapSpec 意图声明与生命周期引擎。

深入封装 MapSpec 意图变迁 (InitProject, SetView, UpsertLayer, RemoveLayer, SetLayout)、
自动 Spatial Profiling、Pre-compile Structure Validation、Redis map_state 双写与 Checkpoint 物理快照。

可靠性契约（REL-06 / REL-07）：
- 事务语义：先在内存构建 candidate，校验通过后才落盘。引入新的 blocking 校验
  错误的 mutation 被拒绝，last-known-good 不被污染。
- 落盘顺序：checkpoint → save_mapspec(disk+redis mapspec) → 同步 redis layers。
  任一步失败 → rollback 恢复旧 mapspec + layers，返回 is_error。杜绝半提交。
- process_layer_ingestion（GeoJSON profiling / raster PNG）经 asyncio.to_thread
  卸载，不阻塞 event loop（大 inline GeoJSON 不再冻结所有 session 的 I/O）。
"""
import copy
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple


from app.services.session_data import session_data_manager
# §8.1.1 阈值单点归 V11 data_tiers（本线自建单点已删）。
from app.lib.cartography.data_tiers import (
    TIER_EXPORT_FEATURES as MAPSPEC_MAX_FEATURES,
)
from app.services.mapspec.store import mapspec_store_instance, _should_remove_layer
from app.services.mapspec.coordinator import validate as validate_mapspec
from app.lib.cartography.quality_loop import (
    cartographic_fingerprint,
    review_and_repair_cartography,
)
from app.lib.cartography.grammar_propagation import grammar_auditor_for_mapspec
from app.services.mapspec.checkpoint import (
    snapshot as create_checkpoint,
    discard_checkpoint,
)
from app.services.distributed_lock import session_lock_registry
# ADR-0186 视觉自愈编译器：纯函数层，无 service 依赖（无环）。锁内重规划 +
# 事务应用见 ApplyVisualHealPatchIntent 分支与 apply_visual_heal_patch 入口。
from app.services.mapspec.visual_healer import (
    MAX_VISUAL_HEAL_ITERATIONS,
    SelfHealConvergenceExhausted,
    VisualCritiqueItem,  # noqa: F401 — 兼容 re-export（mapspec/__init__ 公开面）
    VisualHealStrategyPlanner,
    defect_fingerprint as heal_defect_fingerprint,
    normalize_visual_report,
)

# H02 解巨石：mutation 纯原语与质量门禁钩子迁至 leaf 模块
# （mutation_primitives.py / quality_gate_hook.py），原样 re-export。
from app.services.mapspec.mutation_primitives import (  # noqa: E402
    BLOCKING_VALIDATION_CODES,  # noqa: F401
    LOCK_CONFLICT_CODE,  # noqa: F401
    OVERRIDE_PRESENTATION,  # noqa: F401
    OVERRIDE_SEMANTIC,  # noqa: F401
    OVERRIDE_SOURCES,  # noqa: F401
    OVERRIDE_TEMPORARY_UI,  # noqa: F401
    STATE_PRESENTATION,  # noqa: F401
    STATE_SEMANTIC,  # noqa: F401
    STATE_TRANSIENT,  # noqa: F401
    TRANSIENT_INTERACTION_KEYS,  # noqa: F401
    LockGuardResult,  # noqa: F401
    _MAX_COMPONENT_BYTES,  # noqa: F401
    _MAX_LOCK_IDS,  # noqa: F401
    _MAX_WORKBENCH_DOC_BYTES,  # noqa: F401
    _MUTATION_DEDUP_KEY,  # noqa: F401
    _MUTATION_DEDUP_LIMIT,  # noqa: F401
    _OPACITY_PAINT_KEYS,  # noqa: F401
    _WORKBENCH_GROUP_MAX_DEPTH,  # noqa: F401
    _WORKBENCH_MODES,  # noqa: F401
    _dedup_commit_fields,  # noqa: F401
    _dedup_hit,  # noqa: F401
    _dedup_index_of,  # noqa: F401
    _dedup_strip,  # noqa: F401
    _estimate_component_bytes,  # noqa: F401
    _lock_matches,  # noqa: F401
    _patch_layer_presentation,  # noqa: F401
    _preserve_durable_presentation,  # noqa: F401
    _project_cartographic_intent,  # noqa: F401
    _workbench_doc_error,  # noqa: F401
    classify_override,  # noqa: F401
    guard_intent_locks,  # noqa: F401
    guard_locked_partitions,  # noqa: F401
    intent_lock_targets,  # noqa: F401
    is_entity_locked,  # noqa: F401
    locked_component_ids_of,  # noqa: F401
    locked_layer_ids_of,  # noqa: F401
    strip_transient_state,  # noqa: F401
    user_lock_pin_hit,  # noqa: F401
)
from app.services.mapspec.quality_gate_hook import (  # noqa: E402
    _run_quality_gate_hook,  # noqa: F401
)

# H02 解巨石：契约与 intent 值对象迁至 leaf 模块（mutation_contracts.py /
# intents.py），此处原样 re-export —— 既有 import 面（40+ 消费文件）零破坏。
from app.services.mapspec.mutation_contracts import (  # noqa: E402
    BatchIntentOutcome,  # noqa: F401
    MapSpecBatchResult,  # noqa: F401
    MapSpecResult,  # noqa: F401
    MutationContext,  # noqa: F401
    MutationOrigin,  # noqa: F401
)
from app.services.mapspec.mutation_registry import MUTATION_REGISTRY  # noqa: E402, F401 — 派生消费方（codec/labels）经此引用

# W15 呈现态意图类型（registry effect_class 派生 —— descriptor 单一登记点；
# 原 _PRESENTATION_INTENT_TYPES 9 类元组迁出，兼容 re-export 保留。
# 弃用面：这是 import 时快照，运行期 register 的新 intent 不会出现在
# 此元组 —— 新代码请用 MUTATION_REGISTRY.effect_class / classify_override）。
_PRESENTATION_INTENT_TYPES = tuple(
    d.intent_cls
    for d in MUTATION_REGISTRY.all()
    if d.effect_class == "presentation"
)
from app.services.mapspec.intents import (  # noqa: E402
    ApplyVisualHealPatchIntent,  # noqa: F401
    CheckpointIntent,  # noqa: F401
    DuplicateComponentIntent,  # noqa: F401
    RollbackIntent,  # noqa: F401
    InitProjectIntent,  # noqa: F401
    PatchComponentIntent,  # noqa: F401
    PatchLayerPresentationIntent,  # noqa: F401
    PatchLayerStyleIntent,  # noqa: F401
    PatchWorkbenchDeltaIntent,  # noqa: F401
    RebindComponentIntent,  # noqa: F401
    RemoveComponentIntent,  # noqa: F401
    RemoveLayerIntent,  # noqa: F401
    ReorderLayersIntent,  # noqa: F401
    RestoreStyleIntent,  # noqa: F401
    SetBasemapIntent,  # noqa: F401
    SetLayoutIntent,  # noqa: F401
    SetSceneIntent,  # noqa: F401
    SetScenarioModeIntent,  # noqa: F401
    SetTimeIntent,  # noqa: F401
    SetViewIntent,  # noqa: F401
    SetWorkbenchStateIntent,  # noqa: F401
    UpsertLayerIntent,  # noqa: F401
    UpsertSourceIntent,  # noqa: F401
    MutationIntent,  # noqa: F401
)


logger = logging.getLogger(__name__)

def _spatial_guardrails_enabled() -> bool:
    """ADR-0195 kill switch（懒加载，避免守护引擎在无关突变路径预构建资产）。"""
    from app.services.spatial_guardrails.types import guardrails_enabled

    return guardrails_enabled()


def _get_spatial_guardrails():
    from app.services.spatial_guardrails.guardrail_middleware import (
        get_guardrails,
    )

    return get_guardrails()




class MapSpecLifecycleEngine:
    """深层 MapSpec 意图与生命周期引擎"""

    def __init__(self):
        self.store = mapspec_store_instance
        # Per-session serialization is provided by session_lock_registry
        # (Redis-backed in prod → cross-pod; in-process fallback in tests).
        # The previous in-engine asyncio.Lock table evicted "unlocked" locks,
        # which could hand two concurrent same-session coroutines different lock
        # objects (lost update) — review P1-2. The registry lock is the sole
        # serializer; no in-engine lock table.
        # #1082(F-10): prior spec blocking-codes 的指纹缓存（有界 256）。
        self._prior_blocking_cache: Dict[str, set] = {}
        # ADR-0186 D4：视觉自愈收敛账本（键=缺陷集指纹，有界 FIFO 128）。
        # 进程内状态（多 pod 独立计数，诚实边界已在 ADR 披露）；仅在提交
        # 成功后推进，失败回滚不留账面。
        self._visual_heal_ledger: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _blocking_error_codes(validation: Dict[str, Any]) -> set:
        return {
            e.get("code") for e in validation.get("errors", [])
            if e.get("code") in BLOCKING_VALIDATION_CODES
        }

    @staticmethod
    def _review_failure(mapspec: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
        """Represent an evaluator failure as missing evidence, never as PASS."""
        fingerprint = cartographic_fingerprint(mapspec)
        check = {
            "rule": "CARTOGRAPHIC_REVIEW_EXECUTION",
            "status": "not_evaluated",
            "severity": "error",
            "message": "Cartographic review could not be evaluated.",
            "evidence_class": "deterministic",
            "evidence": {"error_type": type(exc).__name__},
            "repairability": "not_repairable",
            "suggested_fix": None,
        }
        return {
            "stage": "desired_state",
            "status": "not_evaluated",
            "review": {
                "status": "not_evaluated",
                "passed": False,
                "evaluated_count": 0,
                "findings": [],
                "checks": [check],
            },
            "initial_fingerprint": fingerprint,
            "final_fingerprint": fingerprint,
            "attempts": [],
            "repair_count": 0,
            "termination_reason": "review_error",
            "counters": {
                "review_invocations": 1,
                "rule_invocations": 1,
                "metadata_sources": len(mapspec.get("sources") or {}),
                "full_data_loads": 0,
                "repair_attempts": 0,
            },
        }

    async def apply_mutation(
        self,
        session_id: str,
        intent: MutationIntent,
        *,
        origin: MutationOrigin = "agent",
        expected_revision: Optional[int] = None,
        pre_commit_check: Optional[Callable] = None,
        mutation_id: Optional[str] = None,
    ) -> MapSpecResult:
        """原子执行 MapSpec 意图变迁，带 per-session 分布式锁 + 事务 rollback。

        锁：session_lock_registry.lock(session_id) — Redis 跨 pod 互斥（生产），
        in-process asyncio.Lock（单 worker / 测试）。该锁序列化同 session 的并发
        mutation，避免 lost update。

        origin 为 agent|user|system。user 必须带 expected_revision；缺省 origin=agent
        且省略 expected_revision 时仍提交（既有 tool 兼容）。expected_revision 与当前
        revision 不一致则 superseded，MapSpec 不变（ADR-0058）。

        pre_commit_check：锁内、prior spec 载入后调用的异步回调
        ``(session_id, intent, origin, prior_mapspec) -> Optional[MapSpecResult]``；
        返回非 None 即拒绝提交（守卫复检语义）。None 保持既有行为。

        #1071: 持久写路径对锁降级/丢失 fail-closed —— 降级获取（锁 SET
        2s 超时 vs 数据面 5s 的不对称窗口）+ 活数据面 = 两 pod 各持进程
        内锁并发提交，revision 相等的丢更新；TTL 过期丢失（事件循环停顿
        >30s）后本持有者仍会覆盖他 pod 的提交。
        """
        # ADR-0195 空间反幻觉守护网关（锁前管道）：对携带空间几何的意图
        # （SetView/UpsertLayer/InitProject）做 L1-L4 校验。BLOCK → 拒绝
        # 提交（不占锁、不推进 revision）；AUTO_FLIP → 以纠偏后的 intent
        # 进入事务。SPATIAL_GUARDRAILS=0 一键关闭；网关内部 fail-open。
        if _spatial_guardrails_enabled():
            try:
                intent, _guard_verdict = _get_spatial_guardrails().check_intent(
                    intent
                )
                if not _guard_verdict.passed:
                    _first = _guard_verdict.blocking()[0]
                    return MapSpecResult(
                        is_error=True,
                        origin=origin,
                        error_msg=(
                            f"[空间反幻觉拦截] {_first.code}: {_first.message}"
                        ),
                        correction_hint=(
                            "请修正坐标/行政区划码后重新提交；"
                            + str(_first.evidence.get("suggestion") or "")
                        ),
                    )
                if _guard_verdict.mutated:
                    logger.info(
                        "spatial guardrail auto_flip applied: session=%s intent=%s",
                        session_id, type(intent).__name__,
                    )
            except Exception:  # noqa: BLE001 — 守护网关绝不阻断突变面
                pass
        _lock = session_lock_registry.lock(
            session_id, fail_on_degraded=True, fail_on_lost=True,
        )
        async with _lock:
            invalidate = getattr(session_data_manager, "invalidate_local_cache", None)
            if callable(invalidate):
                invalidate(session_id)
            # V3 Performance: copy-on-write candidate to eliminate O(sources) deepcopy
            # for small mutations (SetView/SetLayout). Snapshot is deferred until after
            # intent dispatch so we can capture ONLY what the intent will touch.
            # For Redis backend (returns fresh copies), this is zero-copy. For in-memory
            # backend, shallow copy is enough since we mutate only top-level keys.
            pre_state = await session_data_manager.get_map_state(session_id)
            if pre_state.get("_cartographic_deleted") is True:
                return MapSpecResult(
                    is_error=True,
                    origin=origin,
                    error_msg="Session was deleted; stale MapSpec mutation rejected.",
                )
            # 方向 8：幂等去重（在 CAS 之前 —— 响应丢失后的重试此刻
            # expected_revision 已落后，幂等命中必须优先于 superseded）。
            dedup_hit = _dedup_hit(pre_state, mutation_id)
            if dedup_hit is not None:
                current = await self.store.get_mapspec(session_id, state_hint=pre_state)
                # review C4：回执携带**当前** revision（与返回的权威 spec 同代
                # —— 锁内一致读）。存证 revision 只在 dedup_hit 里留档；回执
                # 用它会让客户端游标落后于已推进的服务端（下次突变 spurious 409）。
                try:
                    current_revision = int(
                        pre_state.get("_cartographic_mutation_revision", 0)
                    )
                except (TypeError, ValueError):
                    current_revision = int(dedup_hit.get("revision") or 0)
                return MapSpecResult(
                    mapspec=current,
                    is_error=False,
                    origin=origin,
                    mutation_revision=current_revision,
                    mutation_id=mutation_id,
                    duplicate=True,
                )
            if origin == "user" and expected_revision is None:
                return MapSpecResult(
                    is_error=True,
                    origin=origin,
                    error_msg="User MapSpec mutations require expected_revision.",
                    correction_hint=(
                        "Re-read MapSpec and retry with the current mutation_revision."
                    ),
                )
            # PERF-F8: defer the layers deepcopy — view/layout/time intents
            # never touch layers, and the COW work already avoids copying the
            # mapspec for them; this unconditional copy was left behind.
            # COW 触层面由 registry 声明驱动（原 8 类 isinstance 元组迁出）。
            _intent_descriptor = MUTATION_REGISTRY.descriptor_for(intent)
            _layers_touching = (
                _intent_descriptor.touches_layers
                if _intent_descriptor is not None else False
            )
            old_layers_snapshot = (
                copy.deepcopy(pre_state.get("layers", []) or [])
                if _layers_touching
                # 669: non-layer intents share no mutation of layers; shallow-copy
                # each layer dict to prove rollback cannot leak via shared refs
                # while keeping cost O(#layers) << payload. Tighten over bare
                # list() which shared dict refs.
                else [dict(layer) if isinstance(layer, dict) else layer for layer in (pre_state.get("layers", []) or [])]
            )
            observation = pre_state.get("_cartographic_observation")
            try:
                runtime_observation_seq = int(
                    observation.get("sequence", 0)
                    if isinstance(observation, dict) else 0
                )
            except (TypeError, ValueError):
                runtime_observation_seq = 0
            # v2(audit F1): 权威载入必须先于 revision 捕获/CAS ——
            # get_mapspec 的磁盘复活路径（Redis 过期、盘上 spec 存活）会把
            # CAS 令牌随 spec 恢复进 Redis 并回写 hint（store.py）。此前
            # prior 从复活前的 pre_state 捕获（stale 0）：commit 以 0+1
            # 覆盖世代 N 破坏单调性，且重放的 expected_revision=0 开世
            # mutation 会通过针对新 spec 的 CAS。superseded 返回也直接复用
            # loaded，省掉旧路径的第二次全量 get_mapspec。
            loaded = await self.store.get_mapspec(session_id, state_hint=pre_state)
            try:
                prior_mutation_revision = int(
                    pre_state.get("_cartographic_mutation_revision", 0)
                )
            except (TypeError, ValueError):
                prior_mutation_revision = 0
            if (
                expected_revision is not None
                and expected_revision != prior_mutation_revision
            ):
                return MapSpecResult(
                    superseded=True,
                    is_error=False,
                    origin=origin,
                    mapspec=loaded,
                    mutation_revision=prior_mutation_revision,
                    error_msg="MapSpec revision has changed.",
                    correction_hint=(
                        "Re-read MapSpec and retry with the current mutation_revision."
                    ),
                )
            # #1074(F-14): except 处理器引用 checkpoint_id_created —— 初始化
            # 必须先于 try（异常发生在原初始化行之前时不得 UnboundLocal）。
            checkpoint_id_created: Optional[str] = None
            ckpt_ref_count = 0
            # review P2-3（H02）：except 处理器同样引用 session_was_fresh /
            # old_mapspec_snapshot —— 此前二者的赋值在 guard/pre-commit 之后，
            # 守卫/复检回调抛异常时 except 内 UnboundLocalError 逸出，违背
            # REL-06「返回 is_error + 事务回滚」承诺。初始化上收到 try 前：
            # snapshot 取当前权威载入 —— 锁内守卫/复检失败 → fresh 会话
            # 丢弃候选（fresh 判定走 discard 分支），存量会话恢复 prior
            #（绝不因回调崩溃丢 last-known-good）。
            session_was_fresh = loaded is None
            old_mapspec_snapshot: Optional[Dict[str, Any]] = loaded
            try:
                prior_mapspec = loaded
                # W15 锁下沉（§33）：统一 guard —— 任何来源的 mutation 先按
                # 目标分区 locked/unlocked；agent/system 意图命中被锁图层/
                # 组件即整笔拒绝（原子意图不可部分提交）+ 机器可读披露
                # （[layer_locked] token 对齐前端 failed/layer_locked）。
                # user 意图不受自有锁约束（用户解锁/操作是唯一 override）。
                lock_refusal = guard_intent_locks(
                    prior_mapspec, intent, origin=origin
                )
                if lock_refusal is not None:
                    return lock_refusal
                # #1070(F-1): 锁内守卫复检 seam —— apply_gis_mutation 的
                # user-wins 检查此前在锁外求值，等锁窗口内落地的用户决策
                # 不可见（TOCTOU）。回调返回非 None 即拒绝（不提交）。
                if pre_commit_check is not None:
                    guard_result = await pre_commit_check(
                        session_id, intent, origin, prior_mapspec
                    )
                    if guard_result is not None:
                        return guard_result
                # #1220（audit3 C-7）：V6 R1-C1 的 agent 层锁守卫已由上方
                # W15 统一 guard（guard_intent_locks，agent/system 意图同拒）
                # 完全覆盖 —— 删除双实现（谓词两份会单边漂移，正是 W15
                # 要消灭的「换 id 拼法绕锁」形态）。
                # CORR-2 companion: whether the session had a persisted spec
                # BEFORE the auto-init skeleton below. Rollback of a first
                # mutation must DISCARD the candidate, not "restore" the
                # in-memory skeleton as a residual spec.
                # （session_was_fresh / old_mapspec_snapshot 初始化已上收
                # try 前 —— review P2-3：守卫/复检异常窗口不得 UnboundLocal）
                mapspec = None  # candidate, assigned per intent type

                # 1. 针对未初始化会话自动构建根框架（仅内存；commit 阶段才落盘 —
                #    Review P2-3: 此前在 reject 前就 save_mapspec，reject 会残留骨架）
                #    审计修正：骨架必须写入 `loaded`。此前写入 `mapspec`，而下方
                #    每个意图分支都用 `mapspec = {**loaded} if loaded else {}` 重建
                #    candidate —— 骨架被静默丢弃，新会话落盘的 spec 丢失
                #    version/layout/thresholds（空 dict 起步）。
                if not loaded and not isinstance(intent, (InitProjectIntent, RollbackIntent)):
                    loaded = {
                        "version": "1.0",
                        "view": {},
                        "sources": {},
                        "layers": [],
                        "layout": {
                            "legend": {"visible": True, "position": "top-right"},
                            "controls": [{"type": "navigation", "position": "top-right"}],
                        },
                        "thresholds": {"maxFeatures": MAPSPEC_MAX_FEATURES, "timeoutMs": 30000},
                    }
                    prior_mapspec = None
                    old_mapspec_snapshot = None

                # 2. Registry dispatch（H02 解巨石）：单一 intent → descriptor
                #    → per-intent handler。candidate 构建语义全部在 handler
                #    （mutation_handlers.py，逐字迁移的分支体）；本层只做
                #    fail-closed 未注册拒绝与事务旗标回收。
                descriptor = _intent_descriptor
                if descriptor is None:
                    # fail-closed：未注册 intent 不再静默落空（原行为是
                    # 掉出 isinstance 链后以 None candidate 进入提交管线，
                    # 在 review 段以隐晦异常失败）。
                    return MapSpecResult(
                        is_error=True,
                        origin=origin,
                        error_code="UNSUPPORTED_MUTATION_INTENT",
                        error_msg=(
                            f"Unsupported MapSpec mutation intent: "
                            f"{type(intent).__name__}."
                        ),
                        correction_hint=(
                            "Use a registered mutation intent "
                            "(see mutation_registry)."
                        ),
                    )
                # 分支体首行的 snapshot 语义上收 orchestration：InitProject
                # 无可回滚基线（None），其余意图 snapshot = 当前权威载入。
                # 异常窗口：guard/pre-commit 阶段由 try 前初始化兜住
                # （snapshot=载入/fresh 判定就绪），handler 阶段为当前权威
                # 载入 —— 与原逐分支首行赋值一致。
                old_mapspec_snapshot = (
                    None if isinstance(intent, InitProjectIntent) else loaded
                )
                _outcome = await descriptor.handler(MutationContext(
                    session_id=session_id,
                    intent=intent,
                    origin=origin,
                    loaded=loaded,
                    pre_state=pre_state,
                    store=self.store,
                    prior_revision=prior_mutation_revision,
                ))
                if isinstance(_outcome, MapSpecResult):
                    # 终局拒绝/门禁/superseded：候选未产生，原样返回。
                    return _outcome
                if _outcome.auto_checkpoint != descriptor.auto_checkpoint:
                    # 声明面与 handler 产出漂移：以 handler 旗标为准（行为
                    # 契约），但必须响亮 —— 新 intent 登记漏配在日志暴露。
                    logger.warning(
                        "auto_checkpoint flag drift for %s: descriptor=%s plan=%s",
                        descriptor.kind, descriptor.auto_checkpoint,
                        _outcome.auto_checkpoint,
                    )
                auto_checkpoint = _outcome.auto_checkpoint
                pending_layer_op = _outcome.pending_layer_op
                is_rollback = _outcome.is_rollback
                restore_notes = _outcome.restore_notes
                checkpoint_id_created = _outcome.checkpoint_id
                ckpt_ref_count = _outcome.ckpt_ref_count
                mapspec = _outcome.mapspec


                # W15 状态三分类（§32）：transient 瞬态交互态永不持久 ——
                # 提交边界剥离（无瞬态键时零拷贝原样返回）。
                mapspec = strip_transient_state(mapspec)
                # 3. Review the immutable desired state and apply only bounded,
                # presentation-only AUTO_SAFE repairs. This is deliberately
                # before structural validation/commit so the persisted MapSpec
                # and runtime layer projection share one fingerprint. Rollback
                # restores an exact historical snapshot and is review-only.
                # V5（ADR-0118 D2）user-wins：legend 显式关闭是用户的 durable
                # 决策（与 ST-P2-2 层可见性 user-wins 同类）—— 只要 merge 后
                # 的 committed 状态 visible=False（无论本次还是先前变异显式
                # 声明），AUTO_SAFE 一律不得翻回；finding 照常进入 review
                # 证据（诚实披露）。agent 要图例必须显式 visible=True。
                suppressed_repairs: set = set()
                merged_legend = (mapspec.get("layout") or {}).get("legend")
                if isinstance(merged_legend, dict) and merged_legend.get("visible") is False:
                    suppressed_repairs.add("set_map_legend_visibility")
                try:
                    # F10（ADR-0205 D7 生产传递）：层携带的 grammar 决策工件
                    # 进入只读对账——缺失/坏工件时 auditor 为 None 或附带
                    # 披露 finding，绝不阻断 review 主线。
                    cartographic_loop = review_and_repair_cartography(
                        mapspec,
                        max_iterations=0 if is_rollback else 2,
                        suppressed_repairs=suppressed_repairs or None,
                        grammar_decision=grammar_auditor_for_mapspec(mapspec),
                    )
                    mapspec = cartographic_loop.mapspec
                    cartographic_review = cartographic_loop.to_dict()
                except Exception as review_exc:  # noqa: BLE001
                    logger.warning(
                        "Cartographic desired-state review unavailable for session %s: %s",
                        session_id,
                        type(review_exc).__name__,
                    )
                    cartographic_review = self._review_failure(mapspec, review_exc)

                # An upsert's deferred runtime write must use the reviewed and
                # possibly repaired layer, not the pre-review object.
                if pending_layer_op is not None and pending_layer_op[0] == "upsert":
                    layer_id = pending_layer_op[1]
                    repaired_layer = next(
                        (
                            layer for layer in mapspec.get("layers", [])
                            if isinstance(layer, dict) and layer.get("id") == layer_id
                        ),
                        pending_layer_op[2],
                    )
                    pending_layer_op = ("upsert", layer_id, repaired_layer)

                # 4. Pre-compile 校验。Rollback 不走拒绝逻辑（恢复的是历史合法 spec）。
                validation = validate_mapspec(mapspec)
                warnings = [e["message"] for e in validation.get("errors", [])] + validation.get("warnings", [])

                if not is_rollback:
                    # #1082(F-10): 旧 spec 的 blocking codes 按指纹缓存 ——
                    # 每次非回滚变更此前都全量 validate 一遍只为 diff codes。
                    prior_blocking = set()
                    if prior_mapspec:
                        prior_fp = None
                        try:
                            from app.lib.cartography.quality_loop import (
                                cartographic_fingerprint,
                            )
                            prior_fp = cartographic_fingerprint(prior_mapspec)
                        except Exception:  # noqa: BLE001 - 指纹失败回退全量校验
                            prior_fp = None
                        if prior_fp is not None and prior_fp in self._prior_blocking_cache:
                            prior_blocking = self._prior_blocking_cache[prior_fp]
                        else:
                            prior_blocking = self._blocking_error_codes(
                                validate_mapspec(prior_mapspec)
                            )
                            if prior_fp is not None:
                                self._prior_blocking_cache[prior_fp] = prior_blocking
                                while len(self._prior_blocking_cache) > 256:
                                    self._prior_blocking_cache.pop(
                                        next(iter(self._prior_blocking_cache))
                                    )
                    new_blocking = self._blocking_error_codes(validation) - prior_blocking
                    if new_blocking:
                        # 引入新的 blocking 错误：拒绝，不落盘，last-known-good 不变。
                        msg = "; ".join(
                            e["message"] for e in validation.get("errors", [])
                            if e.get("code") in new_blocking
                        )
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=f"MapSpec 校验失败: {msg}",
                            correction_hint=(
                                "该意图会引入无效的 source 引用或非法 stops，已拒绝；"
                                "last-known-good MapSpec 保持不变。"
                            ),
                        )

                # 5. 提交（checkpoint → save_mapspec → redis layers），任一失败回滚。
                # #1071: 提交前复检锁所有权 —— TTL 过期丢失（事件循环停顿
                # >30s / Redis 中断后恢复）后本持有者继续写会覆盖他 pod 的
                # 提交；aexit 的 fail_on_lost 只能事后暴露，此处防止发生。
                if getattr(_lock, "lost", False):
                    return MapSpecResult(
                        is_error=True,
                        origin=origin,
                        error_msg="Session lock ownership lost before commit; mutation aborted.",
                        correction_hint=(
                            "锁所有权在提交前丢失（TTL 过期）。请重读 MapSpec 后重试。"
                        ),
                    )
                if auto_checkpoint and not checkpoint_id_created:
                    session_dir = self.store.get_session_dir(session_id)
                    ckpt_res = await create_checkpoint(mapspec, session_dir, session_data_manager)
                    checkpoint_id_created = ckpt_res.get("checkpoint_id")
                    ckpt_ref_count = ckpt_res.get("ref_count", 0)

                mutation_revision = prior_mutation_revision + 1
                # Workbench V6：workbench 分支在落盘前盖 `_rev = 本次
                # mutation_revision`（apply 阶段以 None 占位）—— base_
                # workbench_revision CAS 与前端对账的锚点。
                if isinstance(mapspec, dict) and isinstance(mapspec.get("workbench"), dict) \
                        and mapspec["workbench"].get("_rev", 0) is None:
                    mapspec["workbench"]["_rev"] = mutation_revision
                # #1073: spec 与 CAS 令牌单事务原子落地（crash 窗口不再产生
                # spec=世代 N+1 而令牌=N 的错配）。save 返回未携带时（后端缺
                # set_map_state_fields 的测试替身）退回旧的双写。
                # v2(audit F4): runtime layers 的 upsert/remove/replace 一并
                # 并入同一提交事务（layer_op → commit_mapspec_state 单
                # WATCH/MULTI）—— 此前 spec 落地与 layers 落地是两笔事务，
                # crash 落在中间会留下 spec=世代 N+1 而 layers=世代 N。
                commit_layer_op: Optional[Tuple[str, str, Optional[Any]]] = None
                if pending_layer_op is not None:
                    commit_layer_op = pending_layer_op
                elif is_rollback:
                    # 把运行时 layers 对齐到恢复后的 mapspec.layers。
                    commit_layer_op = ("replace", "", list(mapspec.get("layers", [])))
                # 方向 8：幂等存证随提交单事务落地（crash 窗口不再产生
                # 「已提交但同 id 可重放」的 at-least-once 窗口）。
                dedup_fields = _dedup_commit_fields(
                    pre_state, mutation_id, mutation_revision, origin
                )
                save_res = await self.store.save_mapspec(
                    session_id, mapspec, mutation_revision=mutation_revision,
                    layer_op=commit_layer_op,
                    extra_fields=dedup_fields,
                )
                revision_persisted = bool(
                    save_res.get("revision_persisted")
                ) if isinstance(save_res, dict) else False
                layers_persisted = bool(
                    save_res.get("layers_persisted")
                ) if isinstance(save_res, dict) else False

                if pending_layer_op is not None and not layers_persisted:
                    op, layer_id, layer = pending_layer_op
                    if op == "upsert":
                        persisted = await session_data_manager.update_layer_in_state(
                            session_id, layer_id, layer
                        )
                        if persisted is False:
                            raise RuntimeError("runtime layer persistence rejected")
                    elif op == "remove":
                        persisted = await session_data_manager.remove_layer_from_state(
                            session_id, layer_id
                        )
                        if persisted is False:
                            raise RuntimeError("runtime layer removal rejected")
                elif is_rollback and not layers_persisted:
                    persisted = await session_data_manager.set_map_state(
                        session_id, "layers", list(mapspec.get("layers", []))
                    )
                    if persisted is False:
                        raise RuntimeError("rollback runtime layer persistence rejected")

                if not revision_persisted:
                    persisted = await session_data_manager.set_map_state(
                        session_id,
                        "_cartographic_mutation_revision",
                        mutation_revision,
                    )
                    if persisted is False:
                        raise RuntimeError("cartographic mutation revision persistence rejected")

                cartography_findings = (
                    cartographic_review.get("review", {}).get("findings", [])
                )
                if restore_notes and restore_notes.get("skipped_snapshot_layers"):
                    warnings = list(warnings) + [
                        "restore-style: snapshot layers not present in current "
                        "spec were skipped: "
                        + ",".join(restore_notes["skipped_snapshot_layers"][:8])
                    ]
                return MapSpecResult(
                    mapspec=mapspec,
                    warnings=warnings,
                    is_compiled=validation.get("success", False),
                    checkpoint_id=checkpoint_id_created,
                    ref_count=ckpt_ref_count,
                    is_error=False,
                    cartography_findings=cartography_findings,
                    cartographic_review=cartographic_review,
                    mapspec_fingerprint=cartographic_review.get("final_fingerprint"),
                    runtime_observation_seq=runtime_observation_seq,
                    mutation_revision=mutation_revision,
                    origin=origin,
                    mutation_id=mutation_id,
                    # 方向 8（D2）：user 意图触及 workbench 锁面 → USER_PINNED
                    # （引擎是唯一有锁集廉价视图的位置；门面尊重此印记）。
                    producer_class=(
                        "USER_PINNED"
                        if origin == "user" and user_lock_pin_hit(prior_mapspec, intent)
                        else None
                    ),
                )

            except Exception as e:
                logger.error(f"MapSpec mutation failed for session {session_id}: {e}", exc_info=True)
                # 事务 rollback：恢复 mapspec + redis layers 到 mutation 前。
                # Fresh-session semantics: the intent branches snapshot
                # `loaded`, which the auto-init skeleton replaced — a
                # failed FIRST mutation must DISCARD the candidate, not
                # "restore" the skeleton as a residual spec.
                try:
                    rollback_ok = await self._rollback_to_snapshot(
                        session_id,
                        None if session_was_fresh else old_mapspec_snapshot,
                        old_layers_snapshot,
                        revision=prior_mutation_revision + 1,
                    )
                except Exception as rb_err:  # noqa: BLE001
                    # _rollback_to_snapshot isolates its own failures, but a
                    # raise here must not mask the honest is_error result.
                    logger.error(
                        "MapSpec transaction rollback raised for session %s: %s",
                        session_id, rb_err, exc_info=True,
                    )
                    rollback_ok = False
                # 方向 8：提交未成立 → 剥除幂等存证（best-effort），同 id
                # 重试必须可重新执行。
                if mutation_id:
                    await _dedup_strip(session_id, mutation_id)
                # #1074(F-14): 提交前创建的 auto-checkpoint 描述的是从未
                # commit 的候选世代 —— 孤儿目录会让后续 rollback "恢复"到
                # 未提交状态并占用 20 槽保留额。清理 best-effort。
                if checkpoint_id_created:
                    try:
                        await discard_checkpoint(
                            self.store.get_session_dir(session_id),
                            checkpoint_id_created,
                        )
                    except Exception as ck_err:  # noqa: BLE001
                        logger.warning(
                            "orphan auto-checkpoint cleanup failed for %s/%s: %s",
                            session_id, checkpoint_id_created, ck_err,
                        )
                # #748: never claim a consistency guarantee the rollback did
                # not verify — during a sustained Redis outage commit AND
                # rollback fail together, and the old fixed text told the
                # agent the state was consistent.
                return MapSpecResult(
                    is_error=True,
                    origin=origin,
                    error_msg=f"MapSpec 意图更新失败: {e}",
                    correction_hint=(
                        "事务已回滚，last-known-good MapSpec 与运行时状态保持一致。"
                        if rollback_ok else
                        "回滚尝试失败——状态可能不一致：请先重新读取当前 MapSpec "
                        "（webgis_state_get）再重试，不要假设 last-known-good。"
                    ),
                )

    async def apply_visual_heal_patch(
        self,
        session_id: str,
        defects: Any,
        *,
        origin: MutationOrigin = "system",
        expected_revision: Optional[int] = None,
        mutation_id: Optional[str] = None,
        quality_score: Optional[float] = None,
        on_exhausted: str = "raise",
    ) -> MapSpecResult:
        """ADR-0186：视觉自愈事务入口（VisualJudgeReport 缺陷 → MapSpec 微变异）。

        defects 接受 ``VisualCritiqueItem`` 序列或 ``VisualJudgeReport``
        （鸭子类型 .critiques，经 normalize_visual_report 归一化 + 文本定位）。

        收敛防护（D4，≤2 次迭代）：同一缺陷指纹已提交 MAX_VISUAL_HEAL_ITERATIONS
        次自愈后再次请求 / quality_score 连续 2 次不提升 / 同一补丁签名重放 →
        on_exhausted="raise"（缺省）抛 SelfHealConvergenceExhausted；
        "degrade" 返回 error_code=HEAL_CONVERGENCE_EXHAUSTED 的错误回执，
        MapSpec 与 revision 保持不动。显式 mutation_id 视为同一笔自愈的
        幂等重放（不走重复补丁防护，交由引擎 dedup 回放既有 revision）。

        诚实边界：收敛账本为进程内状态（多 pod 独立计数）；锁内权威规划由
        ApplyVisualHealPatchIntent 分支承担，本入口的预检（账本/签名）读的
        是无锁快照，跨进程竞态窗口已在 ADR 披露。
        """
        if on_exhausted not in ("raise", "degrade"):
            raise ValueError("on_exhausted must be 'raise' or 'degrade'")
        current = await self.store.get_mapspec(session_id)
        known_layer_ids = tuple(
            str(layer.get("id") or "")
            for layer in ((current or {}).get("layers") or [])
            if isinstance(layer, dict) and layer.get("id")
        )
        if hasattr(defects, "critiques"):
            items = tuple(normalize_visual_report(defects, known_layer_ids=known_layer_ids))
        else:
            items = tuple(defects)
        fingerprint = heal_defect_fingerprint(items)
        entry = self._visual_heal_ledger.get(fingerprint) or {
            "attempts": 0,
            "last_score": None,
            "no_improvement": 0,
            "tried_signatures": set(),
        }

        async def exhausted(reason: str) -> MapSpecResult:
            exc = SelfHealConvergenceExhausted(fingerprint, entry["attempts"], reason)
            if on_exhausted != "degrade":
                raise exc
            revision = 0
            try:
                state = await session_data_manager.get_map_state(session_id)
                revision = int(state.get("_cartographic_mutation_revision", 0) or 0)
            except (TypeError, ValueError):
                revision = 0
            return MapSpecResult(
                is_error=True,
                origin=origin,
                error_code="HEAL_CONVERGENCE_EXHAUSTED",
                error_msg=str(exc),
                correction_hint=(
                    "同一缺陷指纹的自愈预算已耗尽（≤2 次迭代）。请人工研判缺陷"
                    "根因，或待上游制图策略变更后以新缺陷指纹重试。"
                ),
                mapspec=current,
                mutation_revision=revision,
            )

        if entry["attempts"] >= MAX_VISUAL_HEAL_ITERATIONS:
            return await exhausted("max_iterations")
        if (
            quality_score is not None
            and entry["last_score"] is not None
            and quality_score <= entry["last_score"]
        ):
            no_improvement = entry["no_improvement"] + 1
        else:
            no_improvement = 0
        if no_improvement >= MAX_VISUAL_HEAL_ITERATIONS:
            return await exhausted("no_improvement")

        plan = VisualHealStrategyPlanner().plan(
            current, items, attempt=entry["attempts"]
        ) if current is not None else None
        if (
            mutation_id is None
            and plan is not None and plan.ops
            and plan.ops_signature in entry["tried_signatures"]
        ):
            return await exhausted("repeated_patch")

        intent = ApplyVisualHealPatchIntent(
            defects=items,
            quality_score=quality_score,
            attempt=entry["attempts"],
        )
        result = await self.apply_mutation(
            session_id,
            intent,
            origin=origin,
            expected_revision=expected_revision,
            mutation_id=mutation_id or f"vheal:{fingerprint}:{entry['attempts']}",
        )
        if result.is_error is False and not result.duplicate and plan is not None:
            tried = set(entry["tried_signatures"])
            if plan.ops:
                tried.add(plan.ops_signature)
            self._visual_heal_ledger[fingerprint] = {
                "attempts": entry["attempts"] + 1,
                "last_score": (
                    quality_score if quality_score is not None else entry["last_score"]
                ),
                "no_improvement": no_improvement,
                "tried_signatures": tried,
            }
            while len(self._visual_heal_ledger) > 128:
                self._visual_heal_ledger.pop(next(iter(self._visual_heal_ledger)))
        return result

    async def apply_presentation_batch(
        self,
        session_id: str,
        intents: List[PatchLayerPresentationIntent],
        *,
        origin: MutationOrigin = "agent",
        expected_revision: Optional[int] = None,
        pre_commit_check: Optional[Callable] = None,
        mutation_id: Optional[str] = None,
    ) -> MapSpecBatchResult:
        """GISMutationBatch：N 个 presentation patch 一个事务。

        单锁 / 单 pre_state 读 / 单 spec 载入（含 F1 复活序）/ 逐 intent
        锁内守卫 / 单次校验 / 单次 review / 单 checkpoint / revision 恰 +1 /
        单次 save。per-intent 裁决：
        - pre_commit_check 返回非 None → refused（守卫拒绝，user-wins）；
        - 层族不命中 → not_found（跳过，不阻断其余）；
        - 其余 applied（_patch_layer_presentation 印记 presentation_owner）。

        全部 refused/not_found → 不落盘不递增（no-op 批）。异常 → 与
        apply_mutation 同款回滚（spec 未变时丢弃候选）。
        """
        _lock = session_lock_registry.lock(
            session_id, fail_on_degraded=True, fail_on_lost=True,
        )
        async with _lock:
            invalidate = getattr(session_data_manager, "invalidate_local_cache", None)
            if callable(invalidate):
                invalidate(session_id)
            pre_state = await session_data_manager.get_map_state(session_id)
            if pre_state.get("_cartographic_deleted") is True:
                return MapSpecBatchResult(
                    is_error=True, origin=origin,
                    error_msg="Session was deleted; stale MapSpec mutation rejected.",
                )
            # 方向 8：幂等去重（同 apply_mutation —— 先于 CAS）。
            dedup_hit = _dedup_hit(pre_state, mutation_id)
            if dedup_hit is not None:
                current = await self.store.get_mapspec(session_id, state_hint=pre_state)
                # review C4：同单笔 —— 回执 revision 与权威 spec 同代。
                try:
                    current_revision = int(
                        pre_state.get("_cartographic_mutation_revision", 0)
                    )
                except (TypeError, ValueError):
                    current_revision = int(dedup_hit.get("revision") or 0)
                return MapSpecBatchResult(
                    mapspec=current,
                    origin=origin,
                    mutation_revision=current_revision,
                    mutation_id=mutation_id,
                    duplicate=True,
                )
            if origin == "user" and expected_revision is None:
                return MapSpecBatchResult(
                    is_error=True, origin=origin,
                    error_msg="User MapSpec mutations require expected_revision.",
                )
            loaded = await self.store.get_mapspec(session_id, state_hint=pre_state)
            try:
                prior_mutation_revision = int(
                    pre_state.get("_cartographic_mutation_revision", 0)
                )
            except (TypeError, ValueError):
                prior_mutation_revision = 0
            if (
                expected_revision is not None
                and expected_revision != prior_mutation_revision
            ):
                return MapSpecBatchResult(
                    superseded=True, origin=origin, mapspec=loaded,
                    mutation_revision=prior_mutation_revision,
                    error_msg="MapSpec revision has changed.",
                    correction_hint=(
                        "Re-read MapSpec and retry with the current mutation_revision."
                    ),
                )
            session_was_fresh = loaded is None
            if not loaded:
                loaded = {
                    "version": "1.0", "view": {}, "sources": {}, "layers": [],
                    "layout": {
                        "legend": {"visible": True, "position": "top-right"},
                        "controls": [{"type": "navigation", "position": "top-right"}],
                    },
                    "thresholds": {"maxFeatures": MAPSPEC_MAX_FEATURES, "timeoutMs": 30000},
                }
            checkpoint_id_created: Optional[str] = None
            outcomes: List[BatchIntentOutcome] = []
            candidate: Optional[Dict[str, Any]] = None
            try:
                # 1. 逐 intent：统一锁 guard 分区 → 锁内守卫 → family 命中 → patch。
                for intent in intents:
                    # W15 锁下沉：被锁 intent refused（披露精确到被锁 id，
                    # 含 layer_locked token），未锁 intent 照常执行。
                    if origin != "user":
                        partition = guard_locked_partitions(
                            loaded, layer_ids=[intent.layer_id]
                        )
                        if partition.has_locked:
                            disclosure = partition.disclosure()
                            outcomes.append(BatchIntentOutcome(
                                layer_id=intent.layer_id, status="refused",
                                visible=intent.visible,
                                error_msg=disclosure["message"],
                                error_code=LOCK_CONFLICT_CODE,
                            ))
                            continue
                    # #1220（C-7）：agent 锁守卫由上方 W15 partition 统一承载。
                    if pre_commit_check is not None:
                        guard_result = await pre_commit_check(
                            session_id, intent, origin, loaded
                        )
                        if guard_result is not None:
                            outcomes.append(BatchIntentOutcome(
                                layer_id=intent.layer_id, status="refused",
                                visible=intent.visible,
                                error_msg=str(getattr(guard_result, "error_msg", "") or ""),
                            ))
                            continue
                    matched = False
                    base = candidate if candidate is not None else loaded
                    patched_layers: List[Any] = []
                    for layer in base.get("layers", []) or []:
                        if not isinstance(layer, dict):
                            patched_layers.append(layer)
                            continue
                        if _should_remove_layer(layer, intent.layer_id):
                            matched = True
                            patched_layers.append(
                                _patch_layer_presentation(
                                    layer, intent.visible, intent.opacity,
                                    origin=str(origin),
                                )
                            )
                        else:
                            patched_layers.append(layer)
                    if not matched:
                        outcomes.append(BatchIntentOutcome(
                            layer_id=intent.layer_id, status="not_found",
                            visible=intent.visible,
                        ))
                        continue
                    if candidate is None:
                        candidate = {**base}
                    candidate["layers"] = patched_layers
                    outcomes.append(BatchIntentOutcome(
                        layer_id=intent.layer_id, status="applied",
                        visible=intent.visible,
                    ))

                applied = sum(1 for o in outcomes if o.status == "applied")
                refused = sum(1 for o in outcomes if o.status == "refused")
                not_found = sum(1 for o in outcomes if o.status == "not_found")
                if candidate is None or applied == 0:
                    # no-op 批：不落盘、不递增 revision、不建 checkpoint。
                    return MapSpecBatchResult(
                        mapspec=loaded, outcomes=outcomes,
                        applied_count=0, refused_count=refused,
                        not_found_count=not_found,
                        mutation_revision=prior_mutation_revision,
                        origin=origin,
                    )

                mapspec = strip_transient_state(candidate)

                # 2. review（AUTO_SAFE ≤2 iter）—— 整批一次。
                cartographic_review: Dict[str, Any] = {}
                try:
                    # F10：层携带 grammar 决策工件 → 只读对账（缺失不阻断）。
                    cartographic_loop = review_and_repair_cartography(
                        mapspec, max_iterations=2,
                        grammar_decision=grammar_auditor_for_mapspec(mapspec),
                    )
                    mapspec = cartographic_loop.mapspec
                    cartographic_review = cartographic_loop.to_dict()
                except Exception as review_exc:  # noqa: BLE001
                    logger.warning(
                        "Cartographic desired-state review unavailable for batch %s: %s",
                        session_id, type(review_exc).__name__,
                    )
                    cartographic_review = self._review_failure(mapspec, review_exc)

                # 3. 校验 + prior-blocking 指纹缓存（与单笔同款）。
                validation = validate_mapspec(mapspec)
                warnings = [e["message"] for e in validation.get("errors", [])] + \
                    validation.get("warnings", [])
                prior_blocking: set = set()
                prior_fp = None
                if loaded:
                    try:
                        from app.lib.cartography.quality_loop import (
                            cartographic_fingerprint,
                        )
                        prior_fp = cartographic_fingerprint(loaded)
                    except Exception:  # noqa: BLE001
                        prior_fp = None
                if prior_fp is not None and prior_fp in self._prior_blocking_cache:
                    prior_blocking = self._prior_blocking_cache[prior_fp]
                elif loaded:
                    prior_blocking = self._blocking_error_codes(validate_mapspec(loaded))
                    if prior_fp is not None:
                        self._prior_blocking_cache[prior_fp] = prior_blocking
                        while len(self._prior_blocking_cache) > 256:
                            # FIFO 驱逐：dict.popitem() 是 LIFO 且不接受参数
                            # （曾误写 popitem(next(iter(...))) → 缓存满即
                            # TypeError，整个 batch 事务回滚）。
                            self._prior_blocking_cache.pop(
                                next(iter(self._prior_blocking_cache))
                            )
                new_blocking = self._blocking_error_codes(validation) - prior_blocking
                if new_blocking:
                    msg = "; ".join(
                        e["message"] for e in validation.get("errors", [])
                        if e.get("code") in new_blocking
                    )
                    return MapSpecBatchResult(
                        is_error=True, origin=origin,
                        error_msg=f"MapSpec 校验失败: {msg}",
                        correction_hint="批量意图会引入无效引用；last-known-good 保持不变。",
                        outcomes=outcomes,
                    )

                # 4. lost 复检 → checkpoint → revision+1 → save（单事务）。
                if getattr(_lock, "lost", False):
                    return MapSpecBatchResult(
                        is_error=True, origin=origin,
                        error_msg="Session lock ownership lost before commit; batch aborted.",
                        correction_hint="锁所有权在提交前丢失（TTL 过期）。请重读 MapSpec 后重试。",
                    )
                session_dir = self.store.get_session_dir(session_id)
                ckpt_res = await create_checkpoint(
                    mapspec, session_dir, session_data_manager,
                )
                checkpoint_id_created = ckpt_res.get("checkpoint_id")
                mutation_revision = prior_mutation_revision + 1
                await self.store.save_mapspec(
                    session_id, mapspec, mutation_revision=mutation_revision,
                    extra_fields=_dedup_commit_fields(
                        pre_state, mutation_id, mutation_revision, origin
                    ),
                )
                return MapSpecBatchResult(
                    mapspec=mapspec, outcomes=outcomes,
                    applied_count=applied, refused_count=refused,
                    not_found_count=not_found,
                    mutation_revision=mutation_revision,
                    origin=origin,
                    checkpoint_id=checkpoint_id_created,
                    mapspec_fingerprint=cartographic_review.get("final_fingerprint"),
                    cartographic_review=cartographic_review,
                    warnings=warnings,
                    mutation_id=mutation_id,
                )
            except Exception as e:
                logger.error(
                    f"MapSpec batch mutation failed for session {session_id}: {e}",
                    exc_info=True,
                )
                try:
                    rollback_ok = await self._rollback_to_snapshot(
                        session_id,
                        None if session_was_fresh else loaded,
                        [dict(layer) if isinstance(layer, dict) else layer
                         for layer in (pre_state.get("layers", []) or [])],
                        revision=prior_mutation_revision + 1,
                    )
                except Exception as rb_err:  # noqa: BLE001
                    logger.error(
                        "MapSpec batch rollback raised for %s: %s",
                        session_id, rb_err, exc_info=True,
                    )
                    rollback_ok = False
                if mutation_id:
                    await _dedup_strip(session_id, mutation_id)
                if checkpoint_id_created:
                    try:
                        await discard_checkpoint(
                            self.store.get_session_dir(session_id),
                            checkpoint_id_created,
                        )
                    except Exception as ck_err:  # noqa: BLE001
                        logger.warning(
                            "orphan batch checkpoint cleanup failed for %s/%s: %s",
                            session_id, checkpoint_id_created, ck_err,
                        )
                return MapSpecBatchResult(
                    is_error=True, origin=origin,
                    error_msg=f"MapSpec 批量意图更新失败: {e}",
                    correction_hint=(
                        "事务已回滚，last-known-good MapSpec 与运行时状态保持一致。"
                        if rollback_ok else
                        "回滚尝试失败——状态可能不一致：请先重新读取当前 MapSpec 再重试。"
                    ),
                    outcomes=outcomes,
                )

    async def _rollback_to_snapshot(
        self,
        session_id: str,
        old_mapspec: Optional[Dict[str, Any]],
        old_layers: List[Any],
        revision: Optional[int] = None,
    ) -> bool:
        """恢复 mutation 前的 mapspec + redis layers，避免半提交。

        ``old_mapspec`` / ``old_layers`` are deep-copied snapshots captured at
        load time (review P1-1): they are independent of the live store state, so
        restoring them is not a silent no-op even under the in-memory backend's
        reference aliasing.

        v2(audit F4): ``revision``（prior+1）随恢复的旧 spec 一并落地 —— 回滚
        绝不把令牌拨回旧值：失败尝试可能已把 N+1 暴露给读者，回退会让持有
        N+1 的客户端在旧 spec 上通过相等 CAS。方向约束：rev ≥ spec 世代是
        安全（客户端被 superseded 后重读），spec 世代 > rev 才是危险方向。
        layers 恢复经 layer_op 并入同一提交事务（后端支持时）。
        """
        try:
            if old_mapspec is not None:
                saved = await self.store.save_mapspec(
                    session_id, old_mapspec,
                    mutation_revision=revision,
                    layer_op=("replace", "", list(old_layers)),
                )
                if not (isinstance(saved, dict) and saved.get("layers_persisted")):
                    # 测试替身缺 commit_mapspec_state —— layers 恢复退回独立写。
                    await session_data_manager.set_map_state(session_id, "layers", old_layers)
            else:
                # First mutation: there is no last-known-good spec. Discard the
                # candidate that may already have been written so rollback does
                # not invent a residual MapSpec.
                discard = getattr(self.store, "discard_mapspec", None)
                if callable(discard):
                    await discard(session_id)
                await session_data_manager.set_map_state(session_id, "layers", old_layers)
        except Exception as rb_err:
            # rollback 自身失败必须大声报错——绝不静默。
            logger.error(
                f"MapSpec transaction rollback FAILED for session {session_id}: {rb_err}",
                exc_info=True,
            )
            return False
        return True


mapspec_lifecycle_engine = MapSpecLifecycleEngine()
