"""Harness Kernel settle coordinator（ADR-0223 / ADR-0180 K9 清偿）。

Host-neutral 的 turn 结算编排：一个 turn 的通用 GIS 结算（工具/步骤证据
之外的 finalize / workflow / runtime 投影 / checkpoint / 证据链 / kernel
终态 / parity 观测）只经由 ``settle_turn`` 这一个生产入口完成，Pi bridge 与
legacy engine 均以薄适配接入（host 只负责把自身信号归一化为
``SettleCause`` 并投影 ``HarnessSettleOutcome``）。

边界裁决（ADR-0223 D1——本模块是编排者，不是事实源）：

- turn 终态台账的唯一写者仍是 ``GISSessionRuntime.end_turn``（本模块只是
  它的一个编排调用方；重复/迟到结算的持久判定永远以 kernel 台账为准）；
- MapSpec / map_product 裁决、WorkflowInstance、RuntimeState、context
  checkpoint、trace 链的权威仍在各自模块——这里只按固定顺序调用并如实
  记收据（receipt）；
- 进程内 receipt cache 是**编排幂等**（同键重放同一收据），不是第二真相源；
- effect 函数全部惰性 import（kernel 包不得在模块顶层依赖 gis_harness 域，
  与 runtime.py 的既有 import 纪律一致）。

幂等与 stale 语义（D3）：

- 同一 ``(session, turn)`` 的重复 ``settle_turn`` 重放缓存的收据
  （disposition=duplicate），不重跑任何效果；per-turn asyncio 锁串行化
  进程内并发结算；
- 台账已终态且 status 与本次 cause 应落值不同 → ``stale``（迟到结算，
  如 turn N 在 turn N+1 begin_turn 终态化之后才到达）：零效果，不污染
  在飞的新 turn；
- legacy 的锁内预终态化（``pre_terminated=True``）是正规首次调用：效果
  照跑、kernel end_turn 腿诚实记 ``already``（引擎已在锁内写终态，S1）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Closed vocabularies（fail-closed：词表外的值一律按失败族收口）─────────

#: 归一化结算起因（host 信号 → cause 的归一化在 host adapter；cause 之下
#: 不再有 host 私有维度）。词表语义：
#: - success：vendor/引擎正常收口；
#: - tool_error / model_error：失败族细分（工具执行错误 / 模型输出异常）；
#: - user_cancel：用户/客户端取消（含 abort source=user）；
#: - system_abort / policy_abort：系统（会话删除等）/ 策略（预算/看门狗）中止；
#: - timeout：stall / 总预算 / 轮数预算；
#: - send_error：下发/流式传输失败；
#: - host_process_death：宿主运行时进程死亡；
#: - unclassified_error：未分类异常（绝不伪装 completed）；
#: - host_interrupted：宿主未给出终态（重启后由 begin_turn 惰性终态化路径
#:   使用；settle 路径收到它按 interrupted 收口）。
SETTLE_CAUSES: Tuple[str, ...] = (
    "success",
    "tool_error",
    "model_error",
    "user_cancel",
    "system_abort",
    "policy_abort",
    "timeout",
    "send_error",
    "host_process_death",
    "unclassified_error",
    "host_interrupted",
)

#: cause → kernel ``TurnStatus`` 单表（ADR-0223 D2；与 F03 ``_hk_turn_status``
#: 的优先级结果逐位一致，词表权威从此在 kernel）。
CAUSE_STATUS: Dict[str, str] = {
    "success": "completed",
    "tool_error": "failed",
    "model_error": "failed",
    "user_cancel": "cancelled",
    "system_abort": "aborted",
    "policy_abort": "aborted",
    "timeout": "failed",
    "send_error": "failed",
    "host_process_death": "failed",
    "unclassified_error": "failed",
    "host_interrupted": "interrupted",
}

#: kernel status → 结算类别（clean 才有完成度奖励；F03 D2 reduced settle）。
STATUS_SETTLE_CLASS: Dict[str, str] = {
    "completed": "clean",
    "refused": "clean",
    "cancelled": "cancelled",
    "aborted": "aborted",
    "failed": "failed",
    "interrupted": "failed",
}

#: settle 效果词表（顺序即权威序，ADR-0223 D2；测试锁定）。
EFFECT_FINAL_GATE = "final_gate"
EFFECT_WORKFLOW_PROJECTION = "workflow_projection"
EFFECT_RUNTIME_STATE_PROJECTION = "runtime_state_projection"
EFFECT_CONTEXT_CHECKPOINT = "context_checkpoint"
EFFECT_MAP_PRODUCT_DISCLOSURE = "map_product_disclosure"
EFFECT_EVIDENCE_CHAIN = "evidence_chain"
EFFECT_KERNEL_END_TURN = "kernel_end_turn"
EFFECT_LIFECYCLE_PARITY = "lifecycle_parity"

#: settle 主路径的披露效果（E1..E6）；K/P 两腿由 ``settle_turn`` 自身编排。
DISCLOSURE_EFFECTS: Tuple[str, ...] = (
    EFFECT_FINAL_GATE,
    EFFECT_WORKFLOW_PROJECTION,
    EFFECT_RUNTIME_STATE_PROJECTION,
    EFFECT_CONTEXT_CHECKPOINT,
    EFFECT_MAP_PRODUCT_DISCLOSURE,
    EFFECT_EVIDENCE_CHAIN,
)

_RECEIPT_OK = ("applied", "skipped", "failed", "already")
_DISPOSITIONS = ("settled", "duplicate", "stale", "unknown_turn")

#: kill switch（ADR-0223 D6；stable，默认 ON）。OFF 只摘除披露效果序，
#: kernel end_turn（authority 腿）不受开关影响。
_SETTLE_FLAG = "GIS_HOST_NEUTRAL_SETTLE"

#: 效果腿/收据的进程内界（有界，防泄漏）。
_RECEIPT_CACHE_MAX = 256
_TURN_LOCKS_MAX = 256
_END_TURN_BUDGET_S = 5.0

_FAILURE_DETAIL_BOUND = 200
_FAILURE_CLASS_BOUND = 64
_RECEIPT_DETAIL_BOUND = 120


def settle_effects_enabled() -> bool:
    """Kill switch consult（ADR-0223 D6；OFF = 只保留 authority 腿）。"""
    return os.getenv(_SETTLE_FLAG, "1") not in ("0", "false", "False")


def status_for_cause(cause: str) -> str:
    """cause → kernel TurnStatus（fail-closed：未知 cause 按 failed 收口）。"""
    return CAUSE_STATUS.get(str(cause or ""), "failed")


def settle_class_for_status(status: str) -> str:
    """kernel status → settle class（clean/cancelled/aborted/failed）。"""
    return STATUS_SETTLE_CLASS.get(str(status or ""), "failed")


def cause_from_host_signals(
    *,
    cancelled: bool,
    timed_out: bool,
    send_failed: bool,
    process_died: bool,
    error: bool = False,
    abort_source: str = "",
) -> str:
    """Pi bridge 的 settle 信号 → 归一化 cause（优先级与 F03 ``_hk_turn_status``
    逐位一致：显式取消 > abort 来源 > 失败族 > success）。纯函数。"""
    if cancelled:
        return "user_cancel"
    if abort_source:
        if abort_source == "user":
            return "user_cancel"
        # system/policy（以及防御性的未知来源）→ aborted 语义。
        return "system_abort" if abort_source == "system" else "policy_abort"
    if timed_out:
        return "timeout"
    if send_failed:
        return "send_error"
    if process_died:
        return "host_process_death"
    if error:
        return "unclassified_error"
    return "success"


#: legacy ``TurnEvidence`` failure_class → cause 细分（status 恒 failed；这里
#: 只是把 host taxonomy 归一到 kernel 词表，便于收据/指标 join）。
_LEGACY_FAILURE_CAUSE: Dict[str, str] = {
    "turn_timeout": "timeout",
    "max_rounds": "timeout",
    "empty_result": "model_error",
    "no_progress": "tool_error",
    "chat_stream_exception": "send_error",
}


def cause_from_legacy_outcome(rt_ev: Any) -> str:
    """legacy ``TurnEvidence`` → cause（未结算 → host_interrupted）。纯函数。"""
    try:
        recorder = rt_ev.outcome
        outcome = recorder.outcome
    except Exception:  # noqa: BLE001 — 诊断面缺席按未结算
        return "host_interrupted"
    name = str(getattr(outcome, "name", "") or "")
    if name == "SUCCEEDED":
        return "success"
    if name == "CANCELLED":
        return "user_cancel"
    if name == "FAILED":
        failure_class = str(getattr(recorder, "failure_class", "") or "")
        return _LEGACY_FAILURE_CAUSE.get(failure_class, "unclassified_error")
    return "host_interrupted"


# ── Contract（typed、frozen、bounded）─────────────────────────────────────


@dataclass(frozen=True)
class HarnessSettleInput:
    """一次 turn 结算的编排输入（引用/摘要，不含大 payload）。

    ``projections_pre_settled``：host 已在 try 内预跑过披露效果序（Pi clean
    路径的 in-try 预跑），coordinator 跳过 E1..E6 并记 ``already`` 收据。
    ``pre_terminated``：legacy 在引擎会话锁内已完成 kernel 终态化（S1），
    锁释放后的本调用是正规首次结算——效果照跑，K 腿记 ``already``。
    """

    session_id: str
    turn_id: str
    host: str = "pi"
    cause: str = "success"
    failure_class: str = ""
    failure_detail: str = ""
    reason: str = "turn_settled"
    state_trigger: str = "execution_settled"
    idempotency_key: str = ""
    projections_pre_settled: bool = False
    pre_terminated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "failure_detail", self.failure_detail[:_FAILURE_DETAIL_BOUND]
        )
        object.__setattr__(self, "failure_class", self.failure_class[:_FAILURE_CLASS_BOUND])


@dataclass(frozen=True)
class SettleEffectReceipt:
    """单条效果的诚实收据（有界；detail 不含 payload/secrets）。"""

    effect: str
    status: str                     # applied | skipped | failed | already
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", self.detail[:_RECEIPT_DETAIL_BOUND])


@dataclass(frozen=True)
class HarnessSettleOutcome:
    """结算编排的结果（host adapter 据此投影 tracker/SSE/诊断面）。"""

    kernel_status: str              # 最终落账的 TurnStatus（含 refusal 降级）
    settle_class: str               # clean | cancelled | aborted | failed
    disposition: str                # settled | duplicate | stale | unknown_turn
    receipts: Tuple[SettleEffectReceipt, ...] = ()
    map_product: Optional[dict] = None   # clean 结算的披露负载（bounded）
    refusal: Optional[dict] = None       # refused 降级事实（question 等）
    failure_class: str = ""
    failure_detail: str = ""

    def receipt_line(self) -> str:
        return ",".join(f"{r.effect}:{r.status}" for r in self.receipts) or "-"


# ── Process-local orchestration state（有界；非真相源）────────────────────

_RECEIPTS: "OrderedDict[str, HarnessSettleOutcome]" = OrderedDict()
_TURN_LOCKS: "OrderedDict[str, asyncio.Lock]" = OrderedDict()


def _lock_for(key: str) -> asyncio.Lock:
    lock = _TURN_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _TURN_LOCKS[key] = lock
        # 只驱逐未被持有的锁（驱逐持锁中的锁会让同 key 后到者拿到新锁，
        # 破坏进程内互斥；>256 并发不同 turn 结算本身已是异常态）。
        if len(_TURN_LOCKS) > _TURN_LOCKS_MAX:
            for stale_key in list(_TURN_LOCKS):
                if len(_TURN_LOCKS) <= _TURN_LOCKS_MAX:
                    break
                candidate = _TURN_LOCKS[stale_key]
                if candidate is not lock and not candidate.locked():
                    _TURN_LOCKS.pop(stale_key, None)
    return lock


def _cache_receipt(key: str, outcome: HarnessSettleOutcome) -> None:
    _RECEIPTS[key] = outcome
    while len(_RECEIPTS) > _RECEIPT_CACHE_MAX:
        _RECEIPTS.popitem(last=False)


def reset_for_tests() -> None:
    _RECEIPTS.clear()
    _TURN_LOCKS.clear()


# ── Disclosure effect sequence（E1..E6；单一实现，预跑与结算共用）─────────


async def run_settle_effects(
    session_id: str,
    turn_id: str,
    settle_class: str,
    *,
    reason: str = "turn_settled",
    state_trigger: str = "execution_settled",
) -> Tuple[List[SettleEffectReceipt], Optional[dict]]:
    """E1..E6 固定顺序（clean 与 reduced 共用实现，差异只在 E1/E5 门）。

    每段独立 never-raise + 收据（ADR-0223 D2 修正 1：终验抛错不再级联
    跳过 workflow/state/checkpoint）。返回 (receipts, map_product)。
    """
    receipts: List[SettleEffectReceipt] = []
    map_product: Optional[dict] = None
    clean = settle_class == "clean"
    enabled = settle_effects_enabled()

    completion: Any = None
    if clean:
        if not enabled:
            receipts.append(SettleEffectReceipt(EFFECT_FINAL_GATE, "skipped", "kill switch off"))
        else:
            try:
                from app.services.gis_harness.map_completion import (
                    maybe_finalize_map_product,
                )

                completion = await maybe_finalize_map_product(
                    session_id, reason=reason, final_gate=True,
                )
                receipts.append(SettleEffectReceipt(EFFECT_FINAL_GATE, "applied"))
            except Exception as exc:  # noqa: BLE001 — 单段失败不级联
                receipts.append(SettleEffectReceipt(EFFECT_FINAL_GATE, "failed", str(exc)))
    else:
        receipts.append(SettleEffectReceipt(
            EFFECT_FINAL_GATE, "skipped", "reduced settle",
        ))

    if enabled:
        try:
            from app.services.gis_harness.workflow_instance import (
                maybe_update_workflow_instance,
            )

            await maybe_update_workflow_instance(session_id, reason=reason, event="auto")
            receipts.append(SettleEffectReceipt(EFFECT_WORKFLOW_PROJECTION, "applied"))
        except Exception as exc:  # noqa: BLE001
            receipts.append(SettleEffectReceipt(EFFECT_WORKFLOW_PROJECTION, "failed", str(exc)))
        try:
            from app.services.gis_harness.runtime_state_machine import (
                maybe_update_runtime_state,
            )

            await maybe_update_runtime_state(
                session_id, reason=reason, trigger=state_trigger, turn_settled=True,
            )
            receipts.append(SettleEffectReceipt(EFFECT_RUNTIME_STATE_PROJECTION, "applied"))
        except Exception as exc:  # noqa: BLE001
            receipts.append(SettleEffectReceipt(EFFECT_RUNTIME_STATE_PROJECTION, "failed", str(exc)))
        try:
            from app.services.gis_harness.runtime_state_machine import (
                runtime_state_enabled,
            )

            if runtime_state_enabled():
                from app.services.gis_harness.context_layers import (
                    checkpoint_context_layers,
                )

                await checkpoint_context_layers(session_id)
                receipts.append(SettleEffectReceipt(EFFECT_CONTEXT_CHECKPOINT, "applied"))
            else:
                receipts.append(SettleEffectReceipt(EFFECT_CONTEXT_CHECKPOINT, "skipped", "runtime state disabled"))
        except Exception as exc:  # noqa: BLE001
            receipts.append(SettleEffectReceipt(EFFECT_CONTEXT_CHECKPOINT, "failed", str(exc)))
    else:
        for effect in (EFFECT_WORKFLOW_PROJECTION, EFFECT_RUNTIME_STATE_PROJECTION,
                       EFFECT_CONTEXT_CHECKPOINT):
            receipts.append(SettleEffectReceipt(effect, "skipped", "kill switch off"))

    if clean:
        if not enabled:
            receipts.append(SettleEffectReceipt(EFFECT_MAP_PRODUCT_DISCLOSURE, "skipped", "kill switch off"))
        else:
            try:
                if completion is not None and completion.status != "pending":
                    spec_snapshot: Tuple[Any, Any] = (None, None)
                    if completion.repairs_applied:
                        from app.services.gis_harness.map_completion import (
                            current_mapspec_for_disclosure,
                        )

                        spec_snapshot = await current_mapspec_for_disclosure(session_id)
                    from app.services.gis_harness.map_completion import (
                        finalization_sse_payload,
                    )

                    map_product = finalization_sse_payload(
                        completion,
                        session_id,
                        mapspec=spec_snapshot[0],
                        mutation_revision=spec_snapshot[1],
                    )
                elif completion is None:
                    # 幂等门跳过（complete+revision 一致）→ 仍披露已存储完成态。
                    from app.services.gis_harness.map_completion import (
                        read_stored_map_product,
                    )

                    map_product = await read_stored_map_product(session_id)
                receipts.append(SettleEffectReceipt(EFFECT_MAP_PRODUCT_DISCLOSURE, "applied"))
            except Exception as exc:  # noqa: BLE001
                receipts.append(SettleEffectReceipt(EFFECT_MAP_PRODUCT_DISCLOSURE, "failed", str(exc)))
    else:
        receipts.append(SettleEffectReceipt(
            EFFECT_MAP_PRODUCT_DISCLOSURE, "skipped", "reduced settle",
        ))

    # 证据链与 finalization 分离（finalization 失败不阻断链持久化）；
    # 非 clean 结算同样落 USER_OUTPUT（task_complete=False）——可回放证据
    # 覆盖 error/cancel/abort（F03 D2）。
    if not enabled:
        receipts.append(SettleEffectReceipt(EFFECT_EVIDENCE_CHAIN, "skipped", "kill switch off"))
        return receipts, map_product
    try:
        from app.lib.runtime.chain_emitters import emit_chain_for
        from app.lib.runtime.gis_trace import Stage
        from app.services.gis_harness.trace_store import persist_turn_chain

        emit_chain_for(
            turn_id,
            Stage.USER_OUTPUT,
            final_gate=True,
            task_complete=(
                map_product.get("task_complete")
                if isinstance(map_product, dict)
                else False
            ) is True,
        )
        persist_turn_chain(turn_id, session_id=session_id)
        receipts.append(SettleEffectReceipt(EFFECT_EVIDENCE_CHAIN, "applied"))
    except Exception as exc:  # noqa: BLE001 — 记录面绝不阻断 settle
        receipts.append(SettleEffectReceipt(EFFECT_EVIDENCE_CHAIN, "failed", str(exc)))

    return receipts, map_product


# ── Coordinator legs（K/P）────────────────────────────────────────────────


async def _end_turn_leg(
    inp: HarnessSettleInput, status: str, *, was_running: bool
) -> SettleEffectReceipt:
    """kernel end_turn（shield + 预算 + 吞异常——结算必须在清理路径中存活）。

    收据按调用前台账状态判定：``was_running``（快照读到 running）→ 本次
    写终态（``applied``；end_turn 对 running 记录必写——除非锁丢失/保存被
    拒，那两条 kernel 侧已有 ``stale_write_refused`` 指标与日志）；已终态
    → 幂等 no-op（``already``）。返回值事件列表不可用作写入信号（无在飞
    step 的正常结算恒为空表）。
    """
    try:
        from app.services.harness_kernel.runtime import get_runtime

        await asyncio.wait_for(
            asyncio.shield(asyncio.ensure_future(
                get_runtime(inp.session_id).end_turn(
                    inp.turn_id, host=inp.host, status=status,  # type: ignore[arg-type]
                )
            )),
            timeout=_END_TURN_BUDGET_S,
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        return SettleEffectReceipt(
            EFFECT_KERNEL_END_TURN, "failed", "shielded settle timed out or cancelled",
        )
    except Exception as exc:  # noqa: BLE001 — authority 腿失败必须显式可见
        logger.warning(
            "[HarnessSettle] kernel end_turn failed session=%s turn=%s: %s",
            inp.session_id, inp.turn_id, exc,
        )
        return SettleEffectReceipt(EFFECT_KERNEL_END_TURN, "failed", str(exc))
    return SettleEffectReceipt(
        EFFECT_KERNEL_END_TURN, "applied" if was_running else "already",
    )


async def _parity_leg(session_id: str, turn_id: str) -> SettleEffectReceipt:
    return await observe_lifecycle_parity(session_id, turn_id)


async def observe_lifecycle_parity(session_id: str, turn_id: str) -> SettleEffectReceipt:
    """canonical ↔ V7 parity 观测行（增值；必须在 end_turn 之后读）。

    公共观测入口：``pi_post_dispatch.log_lifecycle_parity`` 薄委托于此，
    测试以模块级 patch 替身。
    """
    try:
        from app.services.harness_kernel import phase_adapter
        from app.services.harness_kernel.runtime import get_runtime

        snap = await get_runtime(session_id).lifecycle_parity_snapshot(turn_id)
        if snap is None:
            return SettleEffectReceipt(EFFECT_LIFECYCLE_PARITY, "skipped", "no snapshot")
        verdict = phase_adapter.terminal_parity(snap.get("phase", ""), snap.get("v7_phase", ""))
        if verdict:
            _parity_metric()
        logger.info(
            "[LifecycleParity] session=%s turn=%s canonical=%s(%s) v7=%s parity=%s",
            session_id, turn_id, snap.get("phase"), snap.get("status"),
            snap.get("v7_phase") or "absent", verdict or "ok",
        )
        return SettleEffectReceipt(EFFECT_LIFECYCLE_PARITY, "applied", verdict or "ok")
    except Exception as exc:  # noqa: BLE001 — 投影观测绝不阻断 settle
        return SettleEffectReceipt(EFFECT_LIFECYCLE_PARITY, "failed", str(exc))


def _parity_metric() -> None:
    try:
        from app.services.harness_kernel import metrics as hk_metrics

        hk_metrics.record("phase_parity_drift")
    except Exception:  # noqa: BLE001
        logger.debug("[HarnessSettle] parity metric failed", exc_info=True)


async def resolve_turn_refusal(session_id: str, turn_id: str) -> Optional[dict]:
    """ADR-0208 refused 判定（kernel 纯读；失败按无降级收口）。

    公共入口：``pi_post_dispatch.resolve_turn_refusal`` 薄委托于此，
    coordinator 经模块级名字调用（测试可打桩）。
    """
    try:
        from app.services.harness_kernel.runtime import get_runtime

        return await get_runtime(session_id).turn_refusal_candidate(turn_id)
    except Exception:  # noqa: BLE001 — 观测面绝不阻断结算
        logger.debug(
            "[HarnessSettle] refusal detection failed session=%s",
            session_id, exc_info=True,
        )
        return None


async def read_settle_snapshot(session_id: str, turn_id: str) -> Optional[dict]:
    """kernel 台账快照（lifecycle_parity_snapshot 复用读：phase/status）。

    读失败抛出（fail-closed：调用方按未知处理，不缓存、不跑效果）。
    """
    from app.services.harness_kernel.runtime import get_runtime

    return await get_runtime(session_id).lifecycle_parity_snapshot(turn_id)


# ── The single settle entry ───────────────────────────────────────────────


async def settle_turn(inp: HarnessSettleInput) -> HarnessSettleOutcome:
    """唯一生产入口：归一化 cause → refusal 降级 → E1..E6 → kernel end_turn
    → parity → 收据/指标。幂等（同键重放收据）、stale 防护、逐段 never-raise
    （authority 腿失败显式记收据，绝不静默）。"""
    started = time.monotonic()
    if not inp.session_id or not inp.turn_id:
        return HarnessSettleOutcome(
            kernel_status="", settle_class="", disposition="unknown_turn",
        )
    key = inp.idempotency_key or f"{inp.session_id}:{inp.turn_id}"
    async with _lock_for(key):
        cached = _RECEIPTS.get(key)
        if cached is not None:
            return replace(cached, disposition="duplicate")
        try:
            snap = await read_settle_snapshot(inp.session_id, inp.turn_id)
        except Exception:  # noqa: BLE001 — 快照读失败：不缓存、零效果（fail-closed）
            return HarnessSettleOutcome(
                kernel_status="", settle_class="", disposition="unknown_turn",
                receipts=(SettleEffectReceipt("settle_snapshot", "failed", "unreadable ledger"),),
            )
        computed = status_for_cause(inp.cause)
        if snap is None:
            outcome = HarnessSettleOutcome(
                kernel_status=computed,
                settle_class=settle_class_for_status(computed),
                disposition="unknown_turn",
                receipts=(SettleEffectReceipt("settle_snapshot", "skipped", "no turn record"),),
                failure_class=inp.failure_class,
                failure_detail=inp.failure_detail,
            )
            # unknown_turn 不缓存：turn 记录可能尚未可见（begin_turn 竞态），
            # 缓存会永久吞掉后续正规结算。
        else:
            recorded = str(snap.get("status") or "running")
            if recorded != "running" and not inp.pre_terminated:
                if recorded == computed:
                    disposition = "duplicate"
                else:
                    disposition = "stale"
                outcome = HarnessSettleOutcome(
                    kernel_status=recorded,
                    settle_class=settle_class_for_status(recorded),
                    disposition=disposition,
                    receipts=(SettleEffectReceipt(
                        "settle_snapshot", "already",
                        f"terminal={recorded}",
                    ),),
                    failure_class=inp.failure_class,
                    failure_detail=inp.failure_detail,
                )
                _cache_receipt(key, outcome)
            else:
                outcome = await _settle_running(inp, computed, was_running=(recorded == "running"))
                _cache_receipt(key, outcome)
        _observe(inp, outcome, started)
        return outcome


async def _settle_running(
    inp: HarnessSettleInput, computed: str, *, was_running: bool
) -> HarnessSettleOutcome:
    """台账仍 running（或 legacy 预终态化后的正规首次调用）的完整结算。"""
    status = computed
    refusal: Optional[dict] = None
    # Refusal 降级仅 clean 且本协调器将写终态的路径（pre_terminated 的终态
    # 已由锁内 safe_end_turn 写定，first-wins 不追改）。
    if status == "completed" and not inp.pre_terminated:
        refusal = await resolve_turn_refusal(inp.session_id, inp.turn_id)
        if refusal:
            status = "refused"
    settle_class = settle_class_for_status(status)
    receipts: List[SettleEffectReceipt] = []
    map_product: Optional[dict] = None
    if inp.projections_pre_settled:
        receipts.extend(
            SettleEffectReceipt(e, "already", "host pre-settled")
            for e in DISCLOSURE_EFFECTS
        )
    else:
        effect_receipts, map_product = await run_settle_effects(
            inp.session_id, inp.turn_id, settle_class,
            reason=inp.reason, state_trigger=inp.state_trigger,
        )
        receipts.extend(effect_receipts)
    receipts.append(await _end_turn_leg(inp, status, was_running=was_running))
    receipts.append(await _parity_leg(inp.session_id, inp.turn_id))
    if refusal:
        logger.info(
            "[HarnessSettle] turn settled as refused session=%s turn=%s "
            "reason=%s question=%s",
            inp.session_id, inp.turn_id,
            refusal.get("reason_code", ""), str(refusal.get("question") or "")[:80],
        )
    return HarnessSettleOutcome(
        kernel_status=status,
        settle_class=settle_class,
        disposition="settled",
        receipts=tuple(receipts),
        map_product=map_product,
        refusal=refusal,
        failure_class=inp.failure_class,
        failure_detail=inp.failure_detail,
    )


def _observe(inp: HarnessSettleInput, outcome: HarnessSettleOutcome, started: float) -> None:
    """有界观测：一行结构化日志 + kernel counter（closed vocab）。"""
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[HarnessSettle] session=%s turn=%s host=%s disposition=%s status=%s "
        "effects=%s duration_ms=%d",
        inp.session_id, inp.turn_id, inp.host, outcome.disposition,
        outcome.kernel_status, outcome.receipt_line(), duration_ms,
    )
    try:
        from app.services.harness_kernel import metrics as hk_metrics

        hk_metrics.record(
            "turn_settled",
            host=inp.host,
            disposition=outcome.disposition,
            status=outcome.kernel_status or "none",
        )
    except Exception:  # noqa: BLE001 — 观测绝不阻断结算
        logger.debug("[HarnessSettle] metric failed", exc_info=True)


__all__ = [
    "CAUSE_STATUS",
    "DISCLOSURE_EFFECTS",
    "HarnessSettleInput",
    "HarnessSettleOutcome",
    "SETTLE_CAUSES",
    "SettleEffectReceipt",
    "STATUS_SETTLE_CLASS",
    "cause_from_host_signals",
    "cause_from_legacy_outcome",
    "observe_lifecycle_parity",
    "read_settle_snapshot",
    "reset_for_tests",
    "resolve_turn_refusal",
    "run_settle_effects",
    "settle_class_for_status",
    "settle_effects_enabled",
    "settle_turn",
    "status_for_cause",
]
