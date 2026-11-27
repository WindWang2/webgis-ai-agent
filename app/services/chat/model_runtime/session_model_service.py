"""G37：会话模型的 turn 期应用编排（WP05/WP06/WP07/WP10）。

一个职责：把「会话 preference + 管理员目录 + 能力门 + 预算投影」收敛成
一次**可回执**的 Pi 运行时应用：

1. 解析目标模型（inherit → 目录基础项；explicit → 目录重验证，撤销/
   越权 → 明确拒绝，不静默跨 provider）；
2. 能力门（M16/M17）：工具不支持 → 拒绝；上下文窗已知且装不下投影 →
   拒绝并指向压缩/默认模型；
3. 预算 admission（M30）：会话累计（宿主账本实数）+ 下一 turn 投影
   （绑定后 prompt 估算 + 输出预算）> 上限 → **prompt 字节发出前**拒绝；
4. Pi 应用：``set_model`` → ``get_state`` 回读（requested/effective 分
   列；set_thinking_level 回执不带实际级别，必须回读，M15）；失败如实
   status（M09/M10），绝不以 UI 乐观状态继续。

调用点：``PiBridge.stream_prompt`` 在 ``_isolate_vendor_session`` 之后、
``prompt`` 之前。全部 I/O 走短事务/RPC，绝不写共享 PI_AGENT_DIR。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: 申请失败/预算拒绝的稳定 reason 码（进 receipt 与 SSE，前端可本地化）。
REASON_CATALOG_MISS = "catalog_item_unavailable"
REASON_CREDENTIAL_MISSING = "credential_missing"
REASON_TOOLS_UNSUPPORTED = "tools_unsupported"
REASON_CONTEXT_WINDOW = "context_window_insufficient"
REASON_BUDGET_EXCEEDED = "session_token_budget_exceeded"
REASON_APPLY_FAILED = "pi_apply_failed"
REASON_READBACK_MISMATCH = "pi_readback_mismatch"


@dataclass
class ModelApplicationResult:
    """一次 turn 期应用的结果（receipt 的内存形态；持久化由调用方派发）。"""

    ok: bool
    status: str                    # applied | inherit_default | failed | budget_rejected
    reason_code: str = ""          # 空 = 成功
    reason_detail: str = ""
    mode: str = "inherit"          # inherit | explicit
    requested_model: str = ""
    requested_provider: str = ""
    thinking_requested: Optional[str] = None
    thinking_effective: Optional[str] = None
    thinking_controllable: bool = False
    effective_provider: Optional[str] = None
    effective_model: Optional[str] = None
    preference_revision: Optional[int] = None
    catalog_generation: str = ""
    config_generation: str = ""
    budget: Dict[str, Any] = field(default_factory=dict)

    def sse_payload(self, session_id: str, turn_id: str) -> Dict[str, Any]:
        """model_status SSE 的有界投影（无凭证/无内部路径）。"""
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "status": self.status,
            "ok": self.ok,
            "mode": self.mode,
            "requested": (
                {"provider": self.requested_provider, "model": self.requested_model}
                if self.requested_model else None
            ),
            "effective": (
                {"provider": self.effective_provider, "model": self.effective_model}
                if self.effective_model else None
            ),
            "thinking": {
                "requested": self.thinking_requested,
                "effective": self.thinking_effective,
                "controllable": self.thinking_controllable,
            },
            "reason_code": self.reason_code or None,
            "reason_detail": self.reason_detail or None,
            "catalog_generation": self.catalog_generation,
            "budget": dict(self.budget) if self.budget else None,
        }


def _estimate_tokens(text: str) -> int:
    """CJK-aware token 估算（复用 history_compression 单一估算语义）。"""
    from app.services.chat.context.history_compression import _estimate_tokens

    return max(0, int(_estimate_tokens(text or "")))


def _projected_next_turn_tokens(bound_message: str, max_output: Optional[int]) -> int:
    from app.core.config import settings

    output_budget = int(max_output or settings.LLM_MAX_TOKENS or 0)
    return _estimate_tokens(bound_message) + output_budget


async def resolve_session_target(
    db,
    session_id: str,
) -> Dict[str, Any]:
    """解析会话目标模型（不动 Pi）：preference → 目录重验证 → 能力门。

    返回 dict：{ok, item, pref, reason_code, reason_detail, catalog, catalog_generation}。
    供应用编排与 API 状态查询共用（GET /model 与 turn 期同一语义）。
    """
    from app.services.chat.model_runtime import session_catalog
    from app.services.chat.model_runtime.session_model_prefs import get_pref

    catalog = session_catalog.build_catalog()
    catalog_gen = session_catalog.catalog_generation()
    pref = await get_pref(db, session_id)

    if not pref.is_explicit:
        item = session_catalog.default_item(catalog)
        if item is None:
            return {
                "ok": False, "item": None, "pref": pref, "catalog": catalog,
                "catalog_generation": catalog_gen,
                "reason_code": REASON_CATALOG_MISS,
                "reason_detail": "no default model resolvable from server config",
            }
        # P2：default_item 恒有值（build_catalog 无条件推 is_default 项），因此
        # 空/非法 base_model 只能靠 available 诚实拒绝。credential_missing 例外：
        # inherit 目标即 spawn 快照默认（Pi 子进程自带凭证），保留 get_state
        # 回读的 degraded 披露路径（M09/M10，既有测试锁死该范围界限）。
        if not item.available and item.unavailable_reason != "credential_missing":
            return {
                "ok": False, "item": item, "pref": pref, "catalog": catalog,
                "catalog_generation": catalog_gen,
                "reason_code": REASON_CATALOG_MISS,
                "reason_detail": f"model unavailable: {item.unavailable_reason}",
            }
        return {
            "ok": True, "item": item, "pref": pref, "catalog": catalog,
            "catalog_generation": catalog_gen, "reason_code": "", "reason_detail": "",
        }

    item = session_catalog.find_item(pref.catalog_item_id or "", catalog)
    if item is None:
        return {
            "ok": False, "item": None, "pref": pref, "catalog": catalog,
            "catalog_generation": catalog_gen,
            "reason_code": REASON_CATALOG_MISS,
            "reason_detail": (
                f"selected model {pref.catalog_item_id!r} is no longer in the "
                "administered catalog (revoked or renamed); pick another model "
                "or revert to inherit"
            ),
        }
    if not item.available:
        return {
            "ok": False, "item": item, "pref": pref, "catalog": catalog,
            "catalog_generation": catalog_gen,
            "reason_code": (
                REASON_CREDENTIAL_MISSING
                if item.unavailable_reason == "credential_missing"
                else REASON_CATALOG_MISS
            ),
            "reason_detail": f"model unavailable: {item.unavailable_reason}",
        }
    return {
        "ok": True, "item": item, "pref": pref, "catalog": catalog,
        "catalog_generation": catalog_gen, "reason_code": "", "reason_detail": "",
    }


def capability_gate(
    item,
    *,
    needs_tools: bool,
    context_estimate_tokens: Optional[int],
) -> tuple[bool, str, str]:
    """能力门（纯函数）：(ok, reason_code, detail)。unknown 能力按既有
    保守默认放行（supports_tools(assume_for_unknown=True) 与生产假设一
    致）；显式 False / 已知窗口不足才拒绝。"""
    if needs_tools and item.tool_calling is False:
        return (
            False, REASON_TOOLS_UNSUPPORTED,
            f"model {item.item_id} does not support tool calling; this agent "
            "path requires tools for GIS tasks",
        )
    if (
        item.context_window is not None
        and context_estimate_tokens is not None
        and context_estimate_tokens > 0
        and item.context_window < context_estimate_tokens
    ):
        return (
            False, REASON_CONTEXT_WINDOW,
            f"declared context window ({item.context_window}) is smaller than "
            f"the projected next-turn context ({context_estimate_tokens}); "
            "compact the session or select a larger-window model",
        )
    return (True, "", "")


async def budget_admission(
    db,
    session_id: str,
    *,
    projected_tokens: int,
) -> Dict[str, Any]:
    """下一 turn 的确定性预算 admission（prompt 字节发出前调用）。

    投影口径（review P2 修复）：``projected_tokens`` 是**本 turn 增量**
    （绑定消息估算 + 输出预算）；会话历史上下文以**上一 turn 的真实
    input tokens**（账本实数，含当时全部历史）为代理计入 —— 不猜窗口、
    不用累计冒充上下文。返回 {admitted, cap, cumulative, projected,
    history_context, remaining, reason_code, reason_detail}。cap=None →
    无预算控制（如实 admitted，面上披露 unlimited）。
    """
    from sqlalchemy import func, select

    from app.models.db_model import ChatTurnRuntime
    from app.services.chat.model_runtime.session_model_prefs import (
        effective_session_token_cap,
    )

    cap = await effective_session_token_cap(db, session_id)
    cumulative = (
        await db.execute(
            select(func.coalesce(func.sum(ChatTurnRuntime.tokens_total), 0)).where(
                ChatTurnRuntime.session_id == session_id
            )
        )
    ).scalar_one()
    cumulative = int(cumulative or 0)
    # 上一 turn 的真实 input（含当时历史）≈ 本 turn 的历史上下文规模；
    # 无历史（新会话）= 0。R2-P3#1 修复：跳过零输入行（budget_rejected/
    # failed 等回执行 tokens_input=0）—— 否则一次拒绝后的下一次 admission
    # 会把历史误判为 0，重开一 turn 超支窗口。
    last_input = (
        await db.execute(
            select(ChatTurnRuntime.tokens_input)
            .where(
                ChatTurnRuntime.session_id == session_id,
                ChatTurnRuntime.tokens_input > 0,
            )
            .order_by(ChatTurnRuntime.created_at.desc(), ChatTurnRuntime.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    history_context = int(last_input or 0)

    out: Dict[str, Any] = {
        "cap": cap,
        "cumulative": cumulative,
        "projected": int(projected_tokens),
        "history_context": history_context,
        "remaining": None,
        "admitted": True,
        "reason_code": "",
        "reason_detail": "",
    }
    if cap is None:
        return out
    remaining = max(0, int(cap) - cumulative)
    out["remaining"] = remaining
    total_projected = history_context + int(projected_tokens)
    if cumulative + total_projected > int(cap):
        out["admitted"] = False
        out["reason_code"] = REASON_BUDGET_EXCEEDED
        out["reason_detail"] = (
            f"session token budget exceeded: cumulative {cumulative} + "
            f"projected {total_projected} (history {history_context} + turn "
            f"{int(projected_tokens)}) > cap {int(cap)}; start a new "
            "session, raise your session cap (within the admin limit), or "
            "select a cheaper model"
        )
    return out


def _state_model_identity(state: Any) -> tuple[Optional[str], Optional[str]]:
    """从 get_state 返回提取 (provider, modelId)；形状不符返回 (None, None)。"""
    if not isinstance(state, dict):
        return (None, None)
    model = state.get("model")
    if not isinstance(model, dict):
        return (None, None)
    provider = model.get("provider") if isinstance(model.get("provider"), str) else None
    model_id = model.get("id") if isinstance(model.get("id"), str) else None
    return (provider, model_id)


def _degraded_readback(result: "ModelApplicationResult", detail: str) -> "ModelApplicationResult":
    """回读不可得/不可读（协议异常、mock 面、RPC 失败）时的诚实降级。

    区分两件事（M10 精确语义）：
    - 「读回不同模型」= 伪生效 → 一律拒绝（见调用处）；
    - 「读不回/读不懂」= 未确认 —— inherit 模式降级披露（默认模型由
      spawn 快照构造保证，receipt 如实 unverified）；explicit 模式拒绝
      （用户点名要 B，未确认绝不冒充）。
    """
    result.ok = True
    result.status = "degraded_inherit"
    result.reason_detail = detail
    return result


async def apply_session_model(
    rpc,
    db,
    *,
    session_id: str,
    turn_id: str,
    bound_message: str = "",
    needs_tools: bool = True,
    force_apply: bool = False,
) -> ModelApplicationResult:
    """turn 期应用全链（解析 → 能力门 → admission → set_model → 回读）。

    ``force_apply``：worker respawn / new_session 后即使目标与上次相同也
    重应用（Pi 会话模型回到 settings 默认，必须重设并回读，M05/M06）。
    """
    from app.services.chat.model_config import config_generation
    from app.services.chat.model_runtime import session_catalog

    result = ModelApplicationResult(
        ok=False, status="failed",
        catalog_generation=session_catalog.catalog_generation(),
        config_generation=config_generation(),
    )

    target = await resolve_session_target(db, session_id)
    pref = target["pref"]
    result.preference_revision = pref.revision if pref.revision else None
    result.mode = "explicit" if pref.is_explicit else "inherit"
    if not target["ok"]:
        result.reason_code = target["reason_code"]
        result.reason_detail = target["reason_detail"]
        return result

    item = target["item"]
    result.requested_model = item.item_id
    result.requested_provider = item.provider_id
    result.thinking_requested = pref.thinking_level

    # 能力门（上下文投影 = 绑定后消息估算 + 输出预算）。
    projected = _projected_next_turn_tokens(bound_message, item.max_output)
    gate_ok, gate_code, gate_detail = capability_gate(
        item, needs_tools=needs_tools, context_estimate_tokens=projected,
    )
    if not gate_ok:
        result.reason_code = gate_code
        result.reason_detail = gate_detail
        return result

    # 预算 admission（prompt 字节发出前的确定性检查）。
    budget = await budget_admission(db, session_id, projected_tokens=projected)
    result.budget = {
        k: budget[k] for k in
        ("cap", "cumulative", "projected", "history_context", "remaining",
         "admitted", "reason_code", "reason_detail")
    }
    if not budget["admitted"]:
        result.status = "budget_rejected"
        result.reason_code = budget["reason_code"]
        result.reason_detail = budget["reason_detail"]
        return result

    # Pi 应用：set_model（目录内 provider/modelId）→ get_state 回读。
    apply_needed = force_apply or result.mode == "explicit"
    if apply_needed:
        try:
            await rpc.request("set_model", {
                "provider": item.provider_id, "modelId": item.item_id,
            })
        except Exception as exc:  # noqa: BLE001
            if result.mode == "explicit":
                # 用户点名选择：应用失败绝不假成功、不静默跑旧模型。
                result.reason_code = REASON_APPLY_FAILED
                result.reason_detail = f"set_model failed: {exc}"[:240]
                return result
            # inherit：目标即 spawn 快照默认 —— set_model 失败不阻断，
            # 继续 get_state 回读验证（读不回/读不懂 → degraded 披露）。
            logger.warning(
                "[session-model] set_model(default) failed on inherit path "
                "(session=%s): %s", session_id, exc,
            )

    try:
        state = await rpc.request("get_state", {})
    except Exception as exc:  # noqa: BLE001
        if result.mode == "explicit":
            result.reason_code = REASON_READBACK_MISMATCH
            result.reason_detail = f"get_state readback failed: {exc}"[:240]
            return result
        return _degraded_readback(
            result,
            f"get_state unavailable ({str(exc)[:120]}); running on the "
            "worker default model (unverified)",
        )

    provider, model_id = _state_model_identity(state)
    if model_id is None:
        # 负载不可读 / 缺 model 字段：不是「不同模型」的伪生效证据 ——
        # inherit 降级披露，explicit 拒绝（不冒充已确认）。
        if result.mode == "explicit":
            result.reason_code = REASON_READBACK_MISMATCH
            result.reason_detail = (
                "get_state returned an unreadable payload; refusing to "
                "prompt on an unconfirmed model selection"
            )
            return result
        result.effective_provider = provider
        result.effective_model = model_id
        result.thinking_effective = (
            state.get("thinkingLevel")
            if isinstance(state, dict) and isinstance(state.get("thinkingLevel"), str)
            else None
        )
        result.thinking_controllable = _thinking_effort_supported()
        return _degraded_readback(
            result,
            "get_state payload unreadable; running on the worker default "
            "model (unverified)",
        )

    result.effective_provider = provider
    result.effective_model = model_id
    result.thinking_effective = (
        state.get("thinkingLevel")
        if isinstance(state, dict) and isinstance(state.get("thinkingLevel"), str)
        else None
    )
    # compat.supportsReasoningEffort=False（当前端点契约）→ 档位不可控，
    # 不发送 set_thinking_level、不宣称 requested 生效（M14/M15）。
    result.thinking_controllable = _thinking_effort_supported()

    if model_id != item.item_id or (provider or item.provider_id) != item.provider_id:
        result.reason_code = REASON_READBACK_MISMATCH
        result.reason_detail = (
            f"get_state reports {provider}/{model_id} after applying "
            f"{item.provider_id}/{item.item_id}; refusing to prompt on an "
            "unconfirmed model"
        )
        return result

    result.ok = True
    result.status = "applied" if result.mode == "explicit" else "inherit_default"
    return result


def _thinking_effort_supported() -> bool:
    """当前端点契约是否支持 reasoning effort 参数。

    与 spawn 侧 models.json 的 ``compat.supportsReasoningEffort`` 同源
    （当前固定 False —— 不为启用 UI 而盲改 true，M14）。
    """
    from app.core.config import settings  # noqa: F401 — 预留配置化接缝

    return False


__all__ = [
    "ModelApplicationResult",
    "REASON_APPLY_FAILED",
    "REASON_BUDGET_EXCEEDED",
    "REASON_CATALOG_MISS",
    "REASON_CONTEXT_WINDOW",
    "REASON_CREDENTIAL_MISSING",
    "REASON_READBACK_MISMATCH",
    "REASON_TOOLS_UNSUPPORTED",
    "apply_session_model",
    "budget_admission",
    "capability_gate",
    "resolve_session_target",
]
