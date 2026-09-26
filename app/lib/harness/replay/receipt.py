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
    """T4 沙箱：env 关闸 + **全部** session 单例替换 + MapSpec 存储目录交换。

    session_data_manager 在四个模块被 **import 时绑定**（dispatch 服务经
    ctor 注入绕开；``mapspec.store`` / ``mapspec.lifecycle_engine`` /
    ``mapspec_store`` / artifact ledger 的函数级 import 走源模块）——
    沙箱必须逐一替换模块属性，否则 Redis 部署下 lifecycle 会对真实
    后端写键（review P1-1）。退出对称恢复；进入路径部分失败即回滚。
    """

    #: import 期绑定 session 单例、需要逐一交换的模块（惰性 import）。
    _SINGLETON_MODULES = (
        "app.services.session_data",
        "app.services.mapspec.store",
        "app.services.mapspec.lifecycle_engine",
        "app.services.mapspec_store",
    )

    def __enter__(self):
        import os
        import tempfile
        from pathlib import Path

        saved_env = {k: os.environ.get(k) for k in _SANDBOX_ENV_OFF}
        for key in _SANDBOX_ENV_OFF:
            os.environ[key] = "0"
        # MapSpec lifecycle / recovery ledger 的磁盘落点 → 一次性沙箱。
        tmp = tempfile.mkdtemp(prefix="f09-receipt-sandbox-")
        saved_mutspec_dir = os.environ.get("MAPSPEC_STORAGE_DIR")
        os.environ["MAPSPEC_STORAGE_DIR"] = tmp
        swapped: List[Any] = []
        store_module = None
        saved_base_dir = None
        try:
            from app.services.mapspec import store as _store_module

            store_module = _store_module
            saved_base_dir = store_module.BASE_STORAGE_DIR
            store_module.BASE_STORAGE_DIR = Path(tmp)

            memory_store = _MemorySessionStore()
            # 逐一交换模块级单例（import 期绑定的只读别名由此断开）。
            import importlib

            for module_name in self._SINGLETON_MODULES:
                module = importlib.import_module(module_name)
                if hasattr(module, "session_data_manager"):
                    swapped.append(
                        (module, getattr(module, "session_data_manager")))
                    setattr(module, "session_data_manager", memory_store)
        except Exception:
            # 部分失败 → 回滚全部已生效状态（review P2-4：绝不泄漏）。
            for module, original in reversed(swapped):
                setattr(module, "session_data_manager", original)
            if store_module is not None and saved_base_dir is not None:
                store_module.BASE_STORAGE_DIR = saved_base_dir
            os.environ.pop("MAPSPEC_STORAGE_DIR", None)
            if saved_mutspec_dir is not None:
                os.environ["MAPSPEC_STORAGE_DIR"] = saved_mutspec_dir
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
            raise
        self._memory_store = memory_store
        self._store_module = store_module
        self._saved_base_dir = saved_base_dir
        self._swapped = swapped
        self._saved_env = saved_env
        self._tmp = tmp
        self._saved_mutspec_dir = saved_mutspec_dir
        return self

    def __exit__(self, *exc):
        import os
        import shutil

        for module, original in reversed(self._swapped):
            setattr(module, "session_data_manager", original)
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

    @property
    def memory_store(self) -> "_MemorySessionStore":
        return self._memory_store


class _MemorySessionStore:
    """SessionStoreProtocol 最小内存替身（T4 沙箱专用；全部有界）。

    MapSpec lifecycle 的 map-state 面（set_map_state/get_state_field/
    指纹读写）一并实现 —— 沙箱内 lifecycle 持久化全部落在进程内 dict，
    Redis 部署下也零真实后端写（review P1-1）。``commit_mapspec_state``
    刻意缺席：生产端 getattr 缺席 → 走既有 fallback 序列（后端缺接口
    的既有语义）。
    """

    _MAX_REFS = 128
    _MAX_EVENTS = 256
    _MAX_MAP_STATE_KEYS = 256
    _MAX_ALIASES = 128

    def __init__(self) -> None:
        self._data: Dict[Tuple[str, str], Any] = {}
        self._aliases: Dict[Tuple[str, str], str] = {}
        self._map_state: Dict[str, Dict[str, Any]] = {}
        self._events: List[Dict[str, Any]] = []
        self._seq = itertools.count(1)

    async def store(self, session_id: str, data: Any,
                    prefix: str = "data") -> str:
        ref_id = f"ref:{prefix}:t4-{next(self._seq):06d}"
        self._data[(session_id, ref_id)] = data
        if len(self._data) > self._MAX_REFS:
            for stale in list(self._data)[: len(self._data) - self._MAX_REFS]:
                self._data.pop(stale, None)
        return ref_id

    async def get(self, session_id: str, ref_id: str) -> Optional[Any]:
        return self._data.get((session_id, ref_id))

    async def overwrite(self, session_id: str, ref_id: str, data: Any) -> bool:
        key = (session_id, ref_id)
        if key not in self._data:
            return False
        self._data[key] = data
        return True

    async def delete_ref(self, session_id: str, ref_id: str) -> bool:
        return self._data.pop((session_id, ref_id), None) is not None

    # ── alias 面（有界）──────────────────────────────────────────────────

    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str:
        return self._aliases.get((session_id, ref_or_alias), ref_or_alias)

    async def resolve_aliases(self, session_id: str,
                              strings: List[str]) -> Dict[str, str]:
        return {s: await self.resolve_alias(session_id, s) for s in strings}

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        if len(self._aliases) >= self._MAX_ALIASES:
            for stale in list(self._aliases)[: len(self._aliases)
                                              - self._MAX_ALIASES + 1]:
                self._aliases.pop(stale, None)
        self._aliases[(session_id, alias)] = ref_id

    # ── map-state 面（lifecycle 持久化；有界）───────────────────────────

    def _session_state(self, session_id: str) -> Dict[str, Any]:
        state = self._map_state.setdefault(session_id, {})
        if len(self._map_state) > 32:
            for stale in list(self._map_state)[:-32]:
                self._map_state.pop(stale, None)
        return state

    async def set_map_state(self, session_id: str, key: str, value: Any,
                            seq: Optional[int] = None) -> bool:
        state = self._session_state(session_id)
        if len(state) >= self._MAX_MAP_STATE_KEYS and key not in state:
            return False
        state[str(key)[:64]] = value
        return True

    async def get_map_state(self, session_id: str) -> Dict[str, Any]:
        return dict(self._map_state.get(session_id) or {})

    async def get_state_field(self, session_id: str, key: str) -> Optional[Any]:
        return self._map_state.get(session_id, {}).get(str(key)[:64])

    async def set_map_state_fields(self, session_id: str,
                                   fields: Dict[str, Any]) -> bool:
        state = self._session_state(session_id)
        for key, value in dict(fields or {}).items():
            state[str(key)[:64]] = value
        return True

    async def set_map_spec_fingerprint(self, session_id: str,
                                       fingerprint: str) -> bool:
        return await self.set_map_state(session_id, "_mapspec_fp", fingerprint)

    async def get_map_spec_fingerprint(self, session_id: str) -> Optional[str]:
        value = await self.get_state_field(session_id, "_mapspec_fp")
        return str(value) if value is not None else None

    def invalidate_local_cache(self, session_id: str) -> None:
        return None

    async def clear_session(self, session_id: str) -> None:
        self._map_state.pop(session_id, None)
        for key in [k for k in self._data if k[0] == session_id]:
            self._data.pop(key, None)

    # ── descriptor / event 面 ────────────────────────────────────────────

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

    async def list_refs(self, session_id: str) -> Dict[str, str]:
        return {
            ref: "stored" for (sid, ref) in self._data
            if sid == session_id
        }


# ── recorded provider（回放 canned receipt；绝不触网）────────────────────────


class _RecordedProviderRegistry:
    """duck-typed registry：metadata（capability 声明）+ dispatch（canned）。

    收据匹配：优先 (tool, 规范化参数) 精确对齐 —— bind 拒绝的调用不经
    provider、不消费收据，出现序游标会在其后同工具 op 上错位
    （review P2-2）；参数不可归一化 → 回退「工具名 + 出现序」（与链上
    TOOL_RESULTS 回填规则同源）。ok 收据重建为 inline FeatureCollection
    raw result（真实 dispatch 走 store 合同铸出新 ref）；error 收据以
    录制的折叠 code 重建 std 错误形状（真实 dispatch 走错误折叠合同）。
    """

    def __init__(self, ops: List[ScenarioOp],
                 tool_registry: Dict[str, List[str]]):
        self._receipts = [op for op in ops if op.tool]
        self._by_args: Dict[Tuple[str, str], ScenarioOp] = {}
        for op in self._receipts:
            key = self._args_key(op.tool, op.arguments)
            if key is not None and key not in self._by_args:
                self._by_args[key] = op
        self._cursor: Dict[str, int] = {}
        self._consumed: set = set()
        self._tool_registry = tool_registry or {}

    @staticmethod
    def _args_key(tool: str, arguments: Any) -> Optional[Tuple[str, str]]:
        import json

        try:
            normalized = json.dumps(
                arguments if isinstance(arguments, dict) else {},
                sort_keys=True, ensure_ascii=False, default=str)
            return (tool, normalized)
        except (TypeError, ValueError):
            return None

    def metadata(self, name: str) -> Dict[str, Any]:
        return {
            "capabilities": list(self._tool_registry.get(name) or [])[:8],
            "cost": "light",
        }

    def _next(self, tool_name: str,
              args_key: Optional[Tuple[str, str]]) -> Optional[ScenarioOp]:
        if args_key is not None:
            op = self._by_args.get(args_key)
            if op is not None and op.call_id not in self._consumed:
                self._consumed.add(op.call_id)
                return op
        offset = self._cursor.get(tool_name, 0)
        candidates = [op for op in self._receipts if op.tool == tool_name
                      and op.call_id not in self._consumed]
        if offset >= len(candidates):
            return None
        chosen = candidates[offset]
        self._cursor[tool_name] = offset + 1
        self._consumed.add(chosen.call_id)
        return chosen

    async def dispatch(self, tool_name: str, tool_args_raw: Any,
                       session_id: Optional[str] = None) -> Dict[str, Any]:
        op = self._next(tool_name, self._args_key(tool_name, tool_args_raw))
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
                # 与录制同一折叠 code（pin 与实测同量纲，review P2-2）。
                "code": op.error_code or "TOOL_ERROR",
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
