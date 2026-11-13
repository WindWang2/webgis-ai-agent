"""工具侧运行中交互封套 —— 受信发起与受权消费（G29 / WP05+WP06）。

封套是**本任务新增的内部契约**（上游 Pi 没有 GIS 决策 API）：生产 GIS 工具
在需要用户决策时，经 ``mint_envelope`` 返回带 HMAC 签名的有界载荷（随
``PiToolResponse.details.webgis_interaction_request`` 发回扩展）。扩展校验签名
后调用真实 ``ctx.ui.<method>``；用户回答经扩展回调
``POST /pi-tools/interaction-resolve`` 进入 ``consume_answer`` —— 服务端按
自己保存的选项表把显示答案精确反解为领域值（ref/文本），并产出工具结果。

信任边界（任务 §5）：

- 普通工具文本 / 数据字段 / 外部内容**无法伪造**待答请求 —— 只有经
  ``mint_envelope``（HMAC(bridge_secret)）签名的封套能触发扩展弹窗；伪造
  载荷过不了签名，也过不了服务端注册表（envelope_id 必须在待答表中）。
- 选择项用可读文字展示，但领域消费落在稳定引用：选项表服务端保存
  ``display → value`` 映射，回答按 display 精确反解，不做宽松搜索。
- per-turn 待答封套上限 2（并行工具按顺序排队，不合并同标题请求）。
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import time
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from app.services.chat.pi_runtime_interaction import get_interaction_service
from app.services.chat.pi_ui_protocol import (
    MAX_MESSAGE_CHARS,
    MAX_OPTION_CHARS,
    MAX_OPTIONS,
    MAX_TEXT_CHARS,
    MAX_TITLE_CHARS,
)

logger = logging.getLogger(__name__)

#: 封套字段界（比原生协议更紧 —— 决策问题不是数据通道）。
ENVELOPE_MAX_OPTIONS = min(MAX_OPTIONS, 8)
ENVELOPE_TIMEOUT_MIN_MS = 10_000
ENVELOPE_TIMEOUT_MAX_MS = 15 * 60 * 1000
#: per-turn 待答封套上限（同时挂起的决策问题数）。
MAX_PENDING_ENVELOPES_PER_TURN = 2

ENVELOPE_KINDS = frozenset({"select", "confirm", "input", "editor"})

#: 当前派发的 toolCallId（dispatch_tool 绑定；工具体内经 mint_envelope 读取）。
_current_tool_call_id: ContextVar[str] = ContextVar("webgis_pi_tool_call_id", default="")


@contextlib.contextmanager
def bind_tool_call_id(tool_call_id: str) -> Iterator[None]:
    """dispatch_tool 进入/退出工具执行时绑定 toolCallId（ContextVar 传播）。"""
    token = _current_tool_call_id.set(str(tool_call_id or "")[:80])
    try:
        yield
    finally:
        _current_tool_call_id.reset(token)


def current_tool_context() -> tuple[str, str, str]:
    """(session_id, turn_id, tool_call_id) —— 来自已验证的运行时上下文。

    dispatch_tool 已把 verify 过的 sid/tid 绑进 RuntimeContext；toolCallId
    经 bind_tool_call_id 绑定。任一缺席 = 该调用不在可交互的 turn 上下文。
    """
    session_id = turn_id = ""
    try:
        from app.lib.runtime.context import current_runtime_context

        rt = current_runtime_context()
        if rt is not None:
            session_id = str(rt.session_id or "")
            turn_id = str(rt.turn_id or "")
    except Exception:  # noqa: BLE001
        pass
    return session_id, turn_id, _current_tool_call_id.get()


class EnvelopeError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _bridge_secret() -> bytes:
    from app.core.bridge_secret import get_bridge_secret

    return get_bridge_secret().encode("utf-8")


def _envelope_digest(payload: dict[str, Any]) -> str:
    """规范化序列化摘要（签名人/验证人两侧一致）。"""
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sign(payload: dict[str, Any]) -> str:
    return hmac.new(
        _bridge_secret(), _envelope_digest(payload).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_envelope(token: dict[str, Any]) -> Optional[dict[str, Any]]:
    """校验封套签名与时效；通过返回原封套，否则 None（扩展据此不弹窗）。"""
    if not isinstance(token, dict):
        return None
    signature = token.get("sig")
    if not isinstance(signature, str) or not signature:
        return None
    payload = {k: v for k, v in token.items() if k != "sig"}
    expected = _sign(payload)
    if not hmac.compare_digest(signature, expected):
        return None
    exp = token.get("exp")
    if not isinstance(exp, (int, float)) or time.time() > float(exp):
        return None
    if token.get("v") != 1 or token.get("kind") not in ENVELOPE_KINDS:
        return None
    return token


def envelope_result(token: dict[str, Any]) -> dict[str, Any]:
    """把封套放进 PiToolResponse.details 的挂载形状（tool → 扩展契约）。"""
    return {"webgis_interaction_request": token}


def pending_envelope_count(turn_id: str) -> int:
    """该 turn 当前 awaiting_native 的封套数（上限闸用）。"""
    service = get_interaction_service()
    return sum(
        1
        for (_, tid), queue in service._awaiting_envelopes.items()
        if tid == turn_id
        for _ in queue
    )


def mint_envelope(
    *,
    tool_name: str,
    kind: str,
    title: str,
    message: str = "",
    options: Optional[list[dict[str, Any]]] = None,
    placeholder: str = "",
    prefill: str = "",
    timeout_ms: Optional[int] = None,
    business: Optional[dict[str, Any]] = None,
    ttl_s: float = 20 * 60,
    session_id: str = "",
    turn_id: str = "",
    tool_call_id: str = "",
) -> tuple[dict[str, Any], Optional[str]]:
    """铸造封套并登记待答（awaiting native request）。

    ``session_id``/``turn_id``/``tool_call_id`` 缺省时从已验证的运行时上下文
    解析（dispatch_tool 绑定）；任一缺席 → ``ENVELOPE_NO_TURN_CONTEXT``。

    返回 (token, error)。error 非空 = 铸造被拒（上限/校验失败）—— 调用方
    应把错误作为工具结果返回（isError，引导模型在文本中说明或收口再问）。
    """
    ctx_sid, ctx_tid, ctx_tcid = current_tool_context()
    session_id = session_id or ctx_sid
    turn_id = turn_id or ctx_tid
    tool_call_id = tool_call_id or ctx_tcid
    if kind not in ENVELOPE_KINDS:
        return {}, "ENVELOPE_BAD_KIND"
    if not title or len(title) > MAX_TITLE_CHARS:
        return {}, "ENVELOPE_BAD_TITLE"
    if len(message) > MAX_MESSAGE_CHARS:
        return {}, "ENVELOPE_BAD_MESSAGE"
    option_pairs: list[dict[str, str]] = []
    if kind == "select":
        if not options:
            return {}, "ENVELOPE_NEED_OPTIONS"
        if len(options) > ENVELOPE_MAX_OPTIONS:
            return {}, "ENVELOPE_TOO_MANY_OPTIONS"
        seen_displays: set[str] = set()
        for opt in options:
            display = str((opt or {}).get("display", ""))[:MAX_OPTION_CHARS]
            value = str((opt or {}).get("value", ""))
            if not display or not value:
                return {}, "ENVELOPE_BAD_OPTION"
            if display in seen_displays:
                return {}, "ENVELOPE_DUPLICATE_DISPLAY"
            seen_displays.add(display)
            if len(value) > 512:
                return {}, "ENVELOPE_BAD_OPTION"
            option_pairs.append({"display": display, "value": value})
    if len(placeholder) > MAX_TEXT_CHARS or len(prefill) > MAX_TEXT_CHARS:
        return {}, "ENVELOPE_BAD_TEXT"
    if timeout_ms is not None and not (ENVELOPE_TIMEOUT_MIN_MS <= timeout_ms <= ENVELOPE_TIMEOUT_MAX_MS):
        return {}, "ENVELOPE_BAD_TIMEOUT"
    if not session_id or not turn_id:
        return {}, "ENVELOPE_NO_TURN_CONTEXT"

    if pending_envelope_count(turn_id) >= MAX_PENDING_ENVELOPES_PER_TURN:
        return {}, "ENVELOPE_TURN_CAP"

    now = time.time()
    token: dict[str, Any] = {
        "v": 1,
        "envelope_id": "env-" + hashlib.sha256(
            f"{session_id}:{turn_id}:{tool_call_id}:{kind}:{now}".encode("utf-8")
        ).hexdigest()[:12],
        "session_id": session_id,
        "turn_id": turn_id,
        "tool_call_id": tool_call_id,
        "tool": tool_name[:80],
        "kind": kind,
        "title": title,
        "message": message,
        "iat": int(now),
        "exp": int(now + ttl_s),
    }
    if kind == "select":
        # wire 上只发 display 列表（value 表留服务端，反解时使用）。
        token["options"] = [p["display"] for p in option_pairs]
    if message:
        token["message"] = message
    if placeholder:
        token["placeholder"] = placeholder
    if prefill:
        token["prefill"] = prefill
    if timeout_ms is not None:
        token["timeout_ms"] = int(timeout_ms)
    if business:
        token["business"] = {k: business[k] for k in list(business)[:6]}
    token["sig"] = _sign({k: v for k, v in token.items()})

    # 服务端待答登记：native 请求到达时按 (session, turn, method) 配对。
    get_interaction_service().register_envelope_wait(session_id, turn_id, {
        "envelope_id": token["envelope_id"],
        "method": kind,
        "tool": tool_name[:80],
        "tool_call_id": tool_call_id,
        "business": token.get("business", {}),
        "option_values": {p["display"]: p["value"] for p in option_pairs},
        "exp": token["exp"],
        "native_id": "",
        "interaction_id": "",
    })
    return token, None


def consume_answer(
    *,
    session_id: str,
    turn_id: str,
    tool_call_id: str,
    envelope_id: str,
    native_id: str,
    answer_kind: str,
    display: str = "",
    interaction_state: str = "",
    record_answer_kind: str = "",
    envelope: Optional[dict] = None,
) -> dict[str, Any]:
    """扩展 resolve 回调的受权消费入口（bridge secret + turn token 已在路由层验证）。

    ``answer_kind`` 是扩展从 ctx.ui 收到的原始结果（value/confirmed/
    no_answer）；宿主交互记录（``interaction_state``/``record_answer_kind``）
    提供**可得证据的准确分类** —— vendor 的 confirm 超时默认 false 与用户
    明确选「否」在协议上不可分，宿主记录（用户是否真的提交过回答）是唯一
    证据源，据此把 no_answer/timeout/cancelled 与真实用户决定区分开。

    返回 {"ok": bool, "code": str, "content": str, "domain": {...}}：
    - ok=True：content 作为工具结果文本回给模型（含用户决定的领域负载）。
    - ok=False：code 描述拒绝原因（封套不在待答表 / 会话-回合不匹配 / 超时）。
    """
    if not isinstance(envelope, dict) or not envelope:
        return {"ok": False, "code": "ENVELOPE_NOT_PENDING",
                "content": "交互请求已不在待答状态（可能已超时或已被处理）。"}
    record = get_interaction_service().get(interaction_id=envelope.get("interaction_id") or "")
    if record is not None and record.envelope:
        envelope = record.envelope
    if envelope.get("tool_call_id") != tool_call_id:
        return {"ok": False, "code": "ENVELOPE_TOOL_MISMATCH",
                "content": "交互回答与发起工具不匹配，已拒绝。"}
    if time.time() > float(envelope.get("exp", 0)):
        return {"ok": False, "code": "ENVELOPE_EXPIRED",
                "content": "交互请求已过期。"}

    # 标记宿主交互记录为已解决（扩展等待已结束并继续）。
    interaction_id = envelope.get("interaction_id") or ""
    if interaction_id and native_id:
        get_interaction_service().mark_resolved_by_callback(
            interaction_id, "extension-resolve")

    # ── 可得证据分类（宿主记录优先于扩展收到的默认值） ──
    if interaction_state == "timed_out":
        return {
            "ok": True, "code": "TIMED_OUT",
            "content": "等待用户回答超时，本次决策未作出（未采用任何默认选项；"
                       "扩展收到的值是协议默认，不是用户选择）。请勿臆测；"
                       "在最终回复中说明等待超时并请用户重新发起。",
            "domain": {"timed_out": True},
        }
    if interaction_state == "invalidated":
        return {
            "ok": True, "code": "INVALIDATED",
            "content": "该交互所属任务已结束/被停止，决策未被采用。请勿继续依赖该决策。",
            "domain": {"invalidated": True},
        }
    if record_answer_kind == "cancelled" or answer_kind == "cancelled":
        return {
            "ok": True, "code": "CANCELLED",
            "content": "用户取消了本次决策。不要臆测选择；如仍需要该信息，"
                       "请在最终回复中说明需要用户补充。",
            "domain": {"cancelled": True},
        }
    if answer_kind == "no_answer":
        # 扩展拿到 undefined（select/input/editor 超时/abort 默认）且宿主
        # 无用户提交记录 —— 按超时披露（用户没有做任何选择）。
        return {
            "ok": True, "code": "TIMED_OUT",
            "content": "等待用户回答超时/中断，本次决策未作出（未采用任何默认选项）。"
                       "请勿臆测；在最终回复中说明。",
            "domain": {"timed_out": True},
        }

    option_values: dict[str, str] = envelope.get("option_values") or {}
    if option_values:
        if answer_kind != "value" or display not in option_values:
            return {"ok": False, "code": "ANSWER_NOT_IN_OPTIONS",
                    "content": "回答不在选项表内，已拒绝（不进行宽松匹配）。"}
        chosen = option_values[display]
        return {
            "ok": True,
            "code": "ANSWERED",
            "content": f"用户已选择：{display}（引用：{chosen}）。请使用该引用继续当前任务，不要重新猜测数据源。",
            "domain": {"display": display, "value": chosen},
        }
    if answer_kind == "confirmed":
        confirmed = display != "false"
        return {
            "ok": True,
            "code": "ANSWERED",
            "content": "用户已确认：是。" if confirmed else "用户已确认：否。",
            "domain": {"confirmed": confirmed},
        }
    if answer_kind == "value":
        value = display[:MAX_TEXT_CHARS]
        return {
            "ok": True,
            "code": "ANSWERED",
            "content": f"用户已提供内容（原样采用，勿改写）：\n{value}",
            "domain": {"text": value},
        }
    return {"ok": False, "code": "ANSWER_BAD_KIND", "content": "回答类型无法识别。"}
