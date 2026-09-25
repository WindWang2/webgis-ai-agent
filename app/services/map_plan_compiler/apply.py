"""Plan mutation 提交器（F12 / ADR-0214 D5/D7）。

唯一提交通道 = ``MapSpecLifecycleEngine.apply_mutation``（不绕开
ADR-0068 dispatch 拥有者，不造第二写路径）：

- 逐步 CAS：step i 期望 ``base_revision + i - 1``；任何 superseded ⇒
  中止剩余步骤（fail-closed：陈旧计划绝不静默续跑）；
- 幂等：mutation_id = ``c:<client_mutation_id>`` —— 同编译重放命中
  引擎去重，回执记 duplicate 而非二次执行；
- 回执：CompileReceipt 逐步回填，终态写入 map_state ``_plan_receipts``
  有界环 + plan_compile DecisionRecord（溯源面）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.services.map_plan_compiler.compiler import PlanCompilation, PlanMutation
from app.services.map_plan_compiler.receipt import (
    PLAN_RECEIPTS_KEY,
    AppliedMutationRecord,
    CompileReceipt,
    bound_receipt_ring,
    make_plan_compile_decision,
    new_receipt_skeleton,
)

logger = logging.getLogger(__name__)

__all__ = ["PlanApplyResult", "apply_plan", "mutation_to_engine_intent"]


class PlanApplyResult(BaseModel):
    """一次 apply 的结果（含完整 receipt —— 回执即证据）。"""

    model_config = ConfigDict(frozen=True)

    status: str = Field(max_length=24)  # applied|partial|superseded|blocked|failed|empty
    receipt: CompileReceipt
    reason_codes: List[str] = Field(default_factory=list, max_length=24)


def mutation_to_engine_intent(m: PlanMutation) -> Any:
    """编译产物 → 引擎 Intent（单词表映射；未知 intent 抛 ValueError）。"""
    from app.services.mapspec.lifecycle_engine import (
        PatchComponentIntent,
        PatchLayerPresentationIntent,
        PatchLayerStyleIntent,
        RemoveComponentIntent,
        RemoveLayerIntent,
        UpsertLayerIntent,
    )

    payload = m.payload or {}
    if m.intent == "upsert_layer":
        return UpsertLayerIntent(layer=dict(payload.get("layer") or {}))
    if m.intent == "patch_layer_style":
        return PatchLayerStyleIntent(layer_id=m.target, paint=dict(payload.get("paint") or {}))
    if m.intent == "patch_layer_presentation":
        return PatchLayerPresentationIntent(
            layer_id=m.target,
            visible=payload.get("visible"),
            opacity=payload.get("opacity"),
        )
    if m.intent == "patch_component":
        return PatchComponentIntent(
            component_id=m.target,
            component_type=payload.get("component_type"),
            enabled=payload.get("enabled"),
            position=payload.get("position"),
            options=payload.get("options"),
            upsert=bool(payload.get("upsert")),
        )
    if m.intent == "remove_layer":
        return RemoveLayerIntent(layer_id=m.target)
    if m.intent == "remove_component":
        return RemoveComponentIntent(component_id=m.target)
    raise ValueError(f"未知编译 mutation intent: {m.intent!r}（step {m.step}）")


async def apply_plan(
    session_id: str,
    compilation: PlanCompilation,
    *,
    engine: Optional[Any] = None,
    origin: str = "agent",
    record_receipt: bool = True,
) -> PlanApplyResult:
    """有序提交编译产物；返回 receipt（无论成败 —— 回执即证据）。"""
    from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine
    from app.services.session_data import session_data_manager

    engine = engine or MapSpecLifecycleEngine()
    receipt = new_receipt_skeleton(compilation, session_id=session_id)

    if compilation.status == "blocked" or not compilation.mutations:
        status = "blocked" if compilation.status == "blocked" else "empty"
        receipt = _sealed(receipt, status=status, final_revision=compilation.base_revision,
                          final_fingerprint=compilation.base_fingerprint,
                          extra_codes=[f"PLAN_APPLY_{status.upper()}"])
        if record_receipt:
            await _store_receipt(session_id, receipt)
        return PlanApplyResult(status=status, receipt=receipt,
                               reason_codes=list(receipt.reason_codes))

    applied: List[AppliedMutationRecord] = []
    status = "applied"
    final_revision = compilation.base_revision
    final_fingerprint = compilation.base_fingerprint
    last_mapspec: Optional[Dict[str, Any]] = None

    for m in compilation.mutations:
        expected = compilation.base_revision + m.step - 1
        try:
            intent = mutation_to_engine_intent(m)
        except ValueError as exc:
            applied.append(AppliedMutationRecord(
                step=m.step, client_mutation_id=m.client_mutation_id,
                intent=m.intent, target=m.target, ok=False,
                error_code="PLAN_INTENT_INVALID", error_msg=str(exc)[:256]))
            status = "failed"
            break
        try:
            result = await engine.apply_mutation(
                session_id, intent,
                origin=origin,  # type: ignore[arg-type]
                expected_revision=expected,
                mutation_id=f"c:{m.client_mutation_id}",
            )
        except Exception as exc:  # noqa: BLE001 — 引擎外异常也进回执（不静默）
            logger.warning("plan apply step %s failed: %s", m.step, exc, exc_info=True)
            applied.append(AppliedMutationRecord(
                step=m.step, client_mutation_id=m.client_mutation_id,
                intent=m.intent, target=m.target, ok=False,
                error_code="PLAN_APPLY_EXCEPTION", error_msg=str(exc)[:256]))
            status = "failed"
            break

        record = AppliedMutationRecord(
            step=m.step, client_mutation_id=m.client_mutation_id,
            intent=m.intent, target=m.target,
            ok=not result.is_error,
            duplicate=bool(result.duplicate),
            mutation_revision=int(result.mutation_revision or 0),
            error_code=str(result.error_code or "")[:64],
            error_msg=str(result.error_msg or "")[:256],
        )
        applied.append(record)

        if result.superseded:
            status = "superseded"
            break
        if result.is_error:
            status = "failed"
            break
        final_revision = int(result.mutation_revision or final_revision)
        if result.mapspec is not None:
            last_mapspec = result.mapspec
        # duplicate（幂等重放）不算失败也不推进 revision —— 继续后续步骤。

    if status == "applied" and len(applied) < len(compilation.mutations):
        status = "partial"  # 理论不可达（循环内必 break）；防御性收口
    if status != "applied":
        codes = ["PLAN_APPLY_" + status.upper()]
    else:
        codes = ["PLAN_APPLIED"]
    if last_mapspec is not None:
        final_fingerprint = cartographic_fingerprint(last_mapspec)
    receipt = _sealed(receipt, status=status, final_revision=final_revision,
                      final_fingerprint=final_fingerprint,
                      extra_codes=codes, applied=applied)
    if record_receipt:
        await _store_receipt(session_id, receipt)
    return PlanApplyResult(status=status, receipt=receipt,
                           reason_codes=list(receipt.reason_codes))


def _sealed(
    receipt: CompileReceipt,
    *,
    status: str,
    final_revision: int,
    final_fingerprint: str,
    extra_codes: List[str],
    applied: Optional[List[AppliedMutationRecord]] = None,
) -> CompileReceipt:
    codes = list(receipt.reason_codes)
    for c in extra_codes:
        if c not in codes:
            codes.append(c)
    body = receipt.model_dump()
    body.update({
        "status": status,
        "applied": [a.model_dump() for a in (applied or receipt.applied)],
        "final_revision": int(final_revision),
        "final_fingerprint": final_fingerprint,
        "reason_codes": codes[-24:],
    })
    sealed = CompileReceipt(**body)
    decision = make_plan_compile_decision(sealed)
    return sealed.model_copy(update={"decision": decision})


async def _store_receipt(session_id: str, receipt: CompileReceipt) -> None:
    """回执落 map_state 有界环（回链面；写失败只告警不阻断主流程）。"""
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id) or {}
        ring = bound_receipt_ring(state.get(PLAN_RECEIPTS_KEY), receipt)
        await session_data_manager.set_map_state(session_id, PLAN_RECEIPTS_KEY, ring)
    except Exception:  # noqa: BLE001 — 回执存储失败不影响已提交 mutations
        logger.warning("plan receipt store failed session=%s receipt=%s",
                       session_id, receipt.receipt_id, exc_info=True)
