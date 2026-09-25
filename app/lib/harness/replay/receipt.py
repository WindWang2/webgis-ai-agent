"""T4 receipt 级 ToolDispatchService 重放（ADR-0214 D6，WP1）。

#1486 遗留的「receipt 级经 ToolDispatchService 重发」在本模块实装：
在进程内沙箱跑**真实**的 :meth:`ToolDispatchService.dispatch` ——
dispatch 合同（判别式 status、dedup、capability bind 拒绝、错误折叠、
ref 铸造）被完整执行，全部外部副作用被替身化：

- provider：``_RecordedProviderRegistry`` 回放 canned receipt（重建 raw
  result 形状；绝不调用真实 provider/网络）；
- session store：``_MemorySessionStore``（SessionStoreProtocol 最小内存
  实现，有界）经 ctor 注入；``app.services.session_data`` 的模块级单例
  一并替换（artifact ledger 的函数级 import 面）；
- 广播：``fire_broadcast=None``；
- 横切闸：GIS_ANALYSIS_REUSE / GOVERNOR_TOOL_SURFACE / SPATIAL_GUARDRAILS
  / GIS_RECOVERY_LEDGER 全关；MAPSPEC_STORAGE_DIR 指向一次性沙箱目录；
- 会话 id：沿用重放器 ``rsess`` 种子化纪律（与真实会话空间不相交）。

比对面（可被 ``expect.receipt`` / ``expect.receipt_repeat`` 钉住）：
``{status, ref_minted, error_code}`` per call + 首 op 的同参重发
``"repeated"``（dedup 合同）。计时与 llm_payload 文本不进比对（非确定
面）；ref 字符串不比对（uuid 类，不可跨 run 复现），只比对「铸出 ref」。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.lib.harness.replay.replayer import ScenarioOp, TurnSpec

#: T4 沙箱强制关闭的横切闸（env → 关闸取值）。
_SANDBOX_ENV_OFF = (
    "GIS_ANALYSIS_REUSE",
    "GOVERNOR_TOOL_SURFACE",
    "SPATIAL_GUARDRAILS",
    "GIS_RECOVERY_LEDGER",
)


# ── 沙箱（env 作用域 + session store 替换 + MapSpec 目录沙箱）────────────────


class _dispatch_sandbox:
    """T4 沙箱：env 关闸 + 模块级 session 单例替换 + MapSpec 存储目录交换。

    进入/退出即生效；单进程顺序重放下无竞争（bench 纪律）。
    """

    def __enter__(self):
        import os
        import shutil
        import tempfile
        from pathlib import Path

        self._saved_env = {k: os.environ.get(k) for k in _SANDBOX_ENV_OFF}
        for key in _SANDBOX_ENV_OFF:
            os.environ[key] = "0"
        # MapSpec lifecycle / recovery ledger 的磁盘落点 → 一次性沙箱。
        self._tmp = tempfile.mkdtemp(prefix="f09-receipt-sandbox-")
        self._saved_mutspec_dir = os.environ.get("MAPSPEC_STORAGE_DIR")
        os.environ["MAPSPEC_STORAGE_DIR"] = self._tmp

        from app.services.mapspec import store as store_module

        self._store_module = store_module
        self._saved_base_dir = store_module.BASE_STORAGE_DIR
        store_module.BASE_STORAGE_DIR = Path(self._tmp)

        # artifact ledger / reuse 面的函数级 import 走模块单例 → 替换。
        import app.services.session_data as session_data_module

        self._sd_module = session_data_module
        self._saved_singleton = session_data_module.session_data_manager
        self.memory_store = _MemorySessionStore()
        session_data_module.session_data_manager = self.memory_store
        return self

    def __exit__(self, *exc):
        import os
        import shutil

        self._sd_module.session_data_manager = self._saved_singleton
        self._store_module.BASE_STORAGE_DIR = self._saved_base_dir
        if self._saved_mutspec_dir is None:
            os.environ.pop("MAPSPEC_STORAGE_DIR", None)
        else:
            os.environ["MAPSPEC_STORAGE_DIR"] = self._saved_mutspec_dir
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self._tmp, ignore_errors=True)
        return False


class _MemorySessionStore:
    """SessionStoreProtocol 最小内存替身（T4 沙箱专用；全部有界）。"""

    _MAX_REFS = 128
    _MAX_EVENTS = 256

    def __init__(self) -> None:
        self._data: Dict[Tuple[str, str], Any] = {}
        self._aliases: Dict[Tuple[str, str], str] = {}
        self._events: List[Dict[str, Any]] = []
        self._seq = itertools.count(1)

    async def store(self, session_id: str, data: Any,
                    prefix: str = "data") -> str:
        ref_id = f"ref:{prefix}:t4-{next(self._seq):06d}"
        key = (session_id, ref_id)
        self._data[key] = data
        if len(self._data) > self._MAX_REFS:
            # FIFO 淘汰最旧（保底有界；重放单 turn 远小于该界）。
            for stale in list(self._data)[: len(self._data) - self._MAX_REFS]:
                self._data.pop(stale, None)
        return ref_id

    async def get(self, session_id: str, ref_id: str) -> Optional[Any]:
        return self._data.get((session_id, ref_id))

    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str:
        return self._aliases.get((session_id, ref_or_alias), ref_or_alias)

    async def resolve_aliases(self, session_id: str,
                              strings: List[str]) -> Dict[str, str]:
        return {s: await self.resolve_alias(session_id, s) for s in strings}

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        self._aliases[(session_id, alias)] = ref_id

    async def get_ref_descriptor(self, session_id: str,
                                 ref_id: str) -> Optional[Dict[str, Any]]:
        data = self._data.get((session_id, ref_id))
        if data is None:
            return None
        return {"ref_id": ref_id, "bytes": len(str(data))}

    async def get_ref_descriptor_authorized(self, session_id: str, ref_id: str,
                                            owner_token: Optional[str] = None):
        from app.services.session_data_protocol import SessionRefDataResult

        descriptor = await self.get_ref_descriptor(session_id, ref_id)
        return SessionRefDataResult(success=descriptor is not None,
                                    data=descriptor)

    async def append_event(self, session_id: str, event: str,
                           data: dict) -> None:
        self._events.append({"event": str(event)[:48], "data": dict(data)})
        if len(self._events) > self._MAX_EVENTS:
            del self._events[: len(self._events) - self._MAX_EVENTS]

    async def get_event_log(self, session_id: str) -> List[Dict[str, Any]]:
        return list(self._events)


# ── recorded provider（回放 canned receipt；绝不触网）────────────────────────


class _RecordedProviderRegistry:
    """duck-typed registry：metadata（capability 声明）+ dispatch（canned）。

    dispatch 按「工具名 + 出现序」消费录制收据 —— 与链上 TOOL_RESULTS 的
    回填规则同源。ok 收据重建为 inline FeatureCollection raw result（真实
    dispatch 走 store 合同铸出新 ref）；error 收据重建为 std 错误形状
    （真实 dispatch 走错误折叠合同）。
    """

    def __init__(self, ops: List[ScenarioOp],
                 tool_registry: Dict[str, List[str]]):
        self._receipts = [
            (op.tool, op) for op in ops if op.tool
        ]
        self._cursor: Dict[str, int] = {}
        self._tool_registry = tool_registry or {}

    def metadata(self, name: str) -> Dict[str, Any]:
        return {
            "capabilities": list(self._tool_registry.get(name) or [])[:8],
            "cost": "light",
        }

    def _next(self, tool_name: str) -> Optional[ScenarioOp]:
        offset = self._cursor.get(tool_name, 0)
        candidates = [op for (t, op) in self._receipts if t == tool_name]
        if offset >= len(candidates):
            return None
        self._cursor[tool_name] = offset + 1
        return candidates[offset]

    async def dispatch(self, tool_name: str, tool_args_raw: Any,
                       session_id: Optional[str] = None) -> Dict[str, Any]:
        op = self._next(tool_name)
        if op is None:
            return {
                "success": False,
                "error": f"no recorded receipt for tool {tool_name}",
                "code": "RECEIPT_MISSING",
            }
        if op.is_error or str(op.result.get("status") or "") in (
                "error", "failed"):
            return {
                "success": False,
                "error": op.error_msg or "recorded tool failure",
                "code": "TOOL_ERROR",
            }
        return {
            "success": True,
            "summary": f"recorded receipt replay for {tool_name}",
            "data": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": {"type": "Point",
                                 "coordinates": [0.0, 0.0]},
                    "properties": {"receipt": op.call_id[:48]},
                }],
            },
        }


# ── T4 重放 ──────────────────────────────────────────────────────────────────


@dataclass
class ReceiptReplayResult:
    """一个 turn 的 T4 结论（可被 expect.receipt / receipt_repeat 钉住）。"""

    entries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    repeat: Dict[str, str] = field(default_factory=dict)
    skipped_reason: str = ""

    def actual_projection(self) -> Dict[str, Any]:
        """实测树（空 entries = T4 未实际运行 → 不进 actual，触发 not_run）。"""
        out: Dict[str, Any] = {}
        if self.entries:
            out["receipt"] = dict(self.entries)
        if self.repeat:
            out["receipt_repeat"] = dict(self.repeat)
        return out


def _error_code_of(error_msg: Optional[str], raw: Any) -> Optional[str]:
    if isinstance(raw, dict) and raw.get("code"):
        return str(raw["code"])[:48]
    if error_msg:
        return str(error_msg)[:48]
    return None


async def replay_receipt_level(
    turn: TurnSpec,
    *,
    session_id: str,
    tool_registry: Dict[str, List[str]],
) -> ReceiptReplayResult:
    """turn 的冻结 ops 过真实 ToolDispatchService（T4 receipt 级）。

    每 turn 一个独立 service/executed_tools（任务语义载体）；全部 op
    跑完后把首 op 同参重发一次 —— dedup 合同的确定性断言。
    """
    ops = [op for op in turn.ops if op.tool]
    if not ops:
        return ReceiptReplayResult(skipped_reason="no_ops")
    result = ReceiptReplayResult()
    with _dispatch_sandbox() as sandbox:
        from app.services.tool_dispatch_service import ToolDispatchService

        service = ToolDispatchService(
            registry=_RecordedProviderRegistry(ops, tool_registry),
            fire_broadcast=None,
            session_data=sandbox.memory_store,
        )
        executed_tools: set = set()
        first_call: Optional[Tuple[str, Dict[str, Any]]] = None
        for op in ops:
            tc = {
                "id": op.call_id,
                "function": {
                    "name": op.tool,
                    "arguments": dict(op.arguments or {}),
                },
            }
            try:
                dispatch = await service.dispatch(
                    tc, session_id, executed_tools)
            except Exception as exc:  # noqa: BLE001 — 重放面诚实失败
                result.entries[op.call_id] = {
                    "status": "error",
                    "ref_minted": False,
                    "error_code": f"T4_SANDBOX:{type(exc).__name__}"[:48],
                }
                continue
            result.entries[op.call_id] = {
                "status": str(dispatch.status)[:24],
                "ref_minted": dispatch.geojson_ref is not None,
                "error_code": _error_code_of(dispatch.error_msg,
                                             dispatch.raw_result),
            }
            if first_call is None:
                first_call = (op.tool, dict(op.arguments or {}))
        # dedup 合同：同参重发 → repeated（绝不重新执行）。
        if first_call is not None:
            tool_name, args = first_call
            tc = {
                "id": "t4-repeat",
                "function": {"name": tool_name, "arguments": args},
            }
            try:
                repeat = await service.dispatch(
                    tc, session_id, executed_tools)
                result.repeat[ops[0].call_id] = str(repeat.status)[:24]
            except Exception:  # noqa: BLE001
                result.repeat[ops[0].call_id] = "error"
    return result


__all__ = [
    "ReceiptReplayResult",
    "replay_receipt_level",
    "_dispatch_sandbox",
]
