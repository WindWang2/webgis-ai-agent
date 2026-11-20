"""Pi 原生 extension_ui 子协议 —— 纯类型化解析/构造层（G29 / WP02）。

vendor v1.5.2 契约（packages/coding-agent/src/modes/rpc/rpc-types.ts 的
``RpcExtensionUIRequest`` / ``RpcExtensionUIResponse`` 与 docs/rpc-extension-ui.md）：

- Dialog 方法（``select`` / ``confirm`` / ``input`` / ``editor``）：扩展经 ``ctx.ui``
  发出 ``extension_ui_request`` 并阻塞，宿主以 ``extension_ui_response``（同 ``id``）
  回答；响应只允许 ``value`` / ``confirmed`` / ``cancelled:true`` 三态之一。
  vendor 侧 ``timeout``/abort 到期自行 resolve 默认值（select/input/editor→undefined，
  confirm→false）并删除 pending —— 迟到响应被静默忽略。
- Fire-and-forget 方法（``notify`` / ``setStatus`` / ``setWidget`` / ``setTitle`` /
  ``set_editor_text``）：不期待任何响应。
- 原生协议**没有**逐请求消费回执 —— stdin 写出成功 ≠ 扩展已恢复 ≠ 领域结果完成。

本模块只做协议事实的解析、界校验与响应构造（零状态、零 I/O）；生命周期归属
``pi_runtime_interaction``，单向写出归属 ``PiRpcClient.send_ui_response``。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Dialog 方法（期待 extension_ui_response）。editor 无原生 timeout 字段 ——
#: vendor ``createDialogPromise`` 只对带 opts.timeout 的方法发 timeout。
DIALOG_METHODS = frozenset({"select", "confirm", "input", "editor"})

#: Fire-and-forget 方法（无响应、只读投影）。
FIRE_FORGET_METHODS = frozenset({
    "notify", "setStatus", "setWidget", "setTitle", "set_editor_text",
})

ALL_METHODS = DIALOG_METHODS | FIRE_FORGET_METHODS

# 界（CONFIG-04 同款可调，但上限收紧 —— UI 卡片是给人看的，不是数据通道）。
MAX_TITLE_CHARS = int(__import__("os").environ.get("PI_UI_MAX_TITLE_CHARS", 160))
MAX_MESSAGE_CHARS = int(__import__("os").environ.get("PI_UI_MAX_MESSAGE_CHARS", 4000))
MAX_OPTIONS = int(__import__("os").environ.get("PI_UI_MAX_OPTIONS", 12))
MAX_OPTION_CHARS = int(__import__("os").environ.get("PI_UI_MAX_OPTION_CHARS", 160))
MAX_TEXT_CHARS = int(__import__("os").environ.get("PI_UI_MAX_TEXT_CHARS", 8000))
MAX_WIDGET_LINES = 24
MAX_NATIVE_ID_CHARS = 80
#: 原生 timeout 字段合法窗（ms）。低于下限视为无效（vendor 一样会接受，但宿主
#: 不让扩展发出不可能及时回答的问题）；无上限语义 —— 上限由宿主等待策略管理。
MIN_NATIVE_TIMEOUT_MS = 3_000


@dataclass
class ParsedUiRequest:
    """类型化的 ``extension_ui_request``（解析通过才有实例）。"""

    native_id: str
    method: str
    is_dialog: bool
    title: str = ""
    options: list[str] = field(default_factory=list)
    message: str = ""
    placeholder: str = ""
    prefill: str = ""
    notify_type: str = ""
    status_key: str = ""
    status_text: Optional[str] = None
    widget_key: str = ""
    widget_lines: Optional[list[str]] = None
    widget_placement: str = ""
    set_title: str = ""
    editor_text: str = ""
    native_timeout_ms: Optional[int] = None
    #: 原样保留（诊断/审计），只在日志层使用，绝不回传给客户端。
    raw: dict[str, Any] = field(default_factory=dict)

    def dialog_default_value(self) -> Any:
        """vendor 超时/abort 时的默认 resolve 值（rpc-mode.ts createDialogPromise）。"""
        if self.method == "confirm":
            return False
        return None  # select / input / editor → undefined


class UiProtocolError(ValueError):
    """解析/校验失败。``code`` 用于有界诊断（WP11 错误码词表）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _bounded_str(value: Any, cap: int, *, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise UiProtocolError("UI_FIELD_TYPE", f"{field_name} must be a string")
    return value[:cap]


def parse_ui_request(obj: Any) -> ParsedUiRequest:
    """把 stdout 读到的 JSON 对象解析为 :class:`ParsedUiRequest`。

    只接受精确匹配 vendor 联合类型的方法与字段形态；未知方法 / 缺 id / 界超限
    抛 :class:`UiProtocolError`（调用方记日志并丢弃 —— 坏行不得毒化读循环）。
    """
    if not isinstance(obj, dict):
        raise UiProtocolError("UI_NOT_OBJECT", "extension_ui_request payload is not an object")
    if obj.get("type") != "extension_ui_request":
        raise UiProtocolError("UI_WRONG_TYPE", "expected type=extension_ui_request")
    method = obj.get("method")
    if not isinstance(method, str) or method not in ALL_METHODS:
        raise UiProtocolError("UI_UNKNOWN_METHOD", f"unsupported method {method!r}")
    native_id = obj.get("id")
    if not isinstance(native_id, str) or not native_id or len(native_id) > MAX_NATIVE_ID_CHARS:
        raise UiProtocolError("UI_BAD_ID", "missing/invalid native request id")

    req = ParsedUiRequest(
        native_id=native_id,
        method=method,
        is_dialog=method in DIALOG_METHODS,
        raw=obj,
    )
    timeout = obj.get("timeout")
    if timeout is not None:
        # vendor 只在有 opts.timeout 时携带；非法形态按缺失处理（不毒化整请求）。
        if isinstance(timeout, (int, float)) and timeout >= MIN_NATIVE_TIMEOUT_MS:
            req.native_timeout_ms = int(timeout)
        else:
            req.native_timeout_ms = None

    if method == "select":
        options = obj.get("options")
        if not isinstance(options, list) or not options:
            raise UiProtocolError("UI_BAD_OPTIONS", "select requires a non-empty options array")
        if len(options) > MAX_OPTIONS:
            raise UiProtocolError("UI_TOO_MANY_OPTIONS", f"select options > {MAX_OPTIONS}")
        req.options = [
            _bounded_str(o, MAX_OPTION_CHARS, field_name=f"options[{i}]")
            for i, o in enumerate(options)
        ]
        req.title = _bounded_str(obj.get("title"), MAX_TITLE_CHARS, field_name="title")
    elif method == "confirm":
        req.title = _bounded_str(obj.get("title"), MAX_TITLE_CHARS, field_name="title")
        req.message = _bounded_str(obj.get("message"), MAX_MESSAGE_CHARS, field_name="message")
    elif method == "input":
        req.title = _bounded_str(obj.get("title"), MAX_TITLE_CHARS, field_name="title")
        req.placeholder = _bounded_str(obj.get("placeholder"), MAX_TEXT_CHARS, field_name="placeholder")
    elif method == "editor":
        req.title = _bounded_str(obj.get("title"), MAX_TITLE_CHARS, field_name="title")
        req.prefill = _bounded_str(obj.get("prefill"), MAX_TEXT_CHARS, field_name="prefill")
    elif method == "notify":
        req.message = _bounded_str(obj.get("message"), MAX_MESSAGE_CHARS, field_name="message")
        notify_type = obj.get("notifyType")
        req.notify_type = notify_type if notify_type in ("info", "warning", "error") else "info"
    elif method == "setStatus":
        req.status_key = _bounded_str(obj.get("statusKey"), 64, field_name="statusKey")
        status_text = obj.get("statusText")
        # undefined/缺席 = 清除该键（vendor 契约）；其它类型必须字符串。
        if status_text is not None and not isinstance(status_text, str):
            raise UiProtocolError("UI_FIELD_TYPE", "statusText must be a string or undefined")
        req.status_text = status_text[:MAX_TEXT_CHARS] if isinstance(status_text, str) else None
    elif method == "setWidget":
        req.widget_key = _bounded_str(obj.get("widgetKey"), 64, field_name="widgetKey")
        lines = obj.get("widgetLines")
        if lines is not None:
            if not isinstance(lines, list):
                raise UiProtocolError("UI_FIELD_TYPE", "widgetLines must be an array or undefined")
            req.widget_lines = [
                _bounded_str(ln, MAX_TEXT_CHARS, field_name=f"widgetLines[{i}]")
                for i, ln in enumerate(lines[:MAX_WIDGET_LINES])
            ]
        placement = obj.get("widgetPlacement")
        req.widget_placement = placement if placement in ("aboveEditor", "belowEditor") else "aboveEditor"
    elif method == "setTitle":
        req.set_title = _bounded_str(obj.get("title"), MAX_TITLE_CHARS, field_name="title")
    elif method == "set_editor_text":
        req.editor_text = _bounded_str(obj.get("text"), MAX_TEXT_CHARS, field_name="text")
    return req


def build_ui_response(
    native_id: str,
    *,
    value: Optional[str] = None,
    confirmed: Optional[bool] = None,
    cancelled: bool = False,
) -> dict[str, Any]:
    """构造 vendor 契约的 ``extension_ui_response``（stdin 单向写出）。

    三态互斥（value / confirmed / cancelled）—— 与
    ``RpcExtensionUIResponse`` 联合类型一致；违反即抛错（宁可拒绝写出也
    不发畸形响应毒化 vendor 输入解析）。
    """
    if not isinstance(native_id, str) or not native_id:
        raise UiProtocolError("UI_BAD_ID", "response requires the native request id")
    payload: dict[str, Any] = {"type": "extension_ui_response", "id": native_id}
    flags = sum([
        1 if value is not None else 0,
        1 if confirmed is not None else 0,
        1 if cancelled else 0,
    ])
    if flags != 1:
        raise UiProtocolError(
            "UI_RESPONSE_MUTEX",
            "exactly one of value/confirmed/cancelled must be set",
        )
    if value is not None:
        if not isinstance(value, str):
            raise UiProtocolError("UI_FIELD_TYPE", "value must be a string")
        if len(value) > MAX_TEXT_CHARS:
            raise UiProtocolError("UI_VALUE_TOO_LARGE", f"value exceeds {MAX_TEXT_CHARS} chars")
        payload["value"] = value
    elif confirmed is not None:
        payload["confirmed"] = bool(confirmed)
    else:
        payload["cancelled"] = True
    return payload
