"""Deterministic fake 资产正式化（E15 D2/D3）。

后端长期存在三处分散的 scripted fake 先例（patch ``_call_llm``、
``replay/receipt._RecordedProviderRegistry``、前端 ``llm-stub.mjs``）；
本模块把"脚本化 provider + 计划驱动故障"正式化为可被场景规格声明的
离线资产。纪律：

- **确定性**：同脚本 + 同故障计划 → 同观测序列（LabClock 只advance 不 sleep）；
- **不伪造成功**：故障产出 typed 错误收据/取消/拒绝，绝不降级为 ok；
- **零生产钩子**：fake 只在 lab 边界被组装，生产路径不感知。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

from app.lib.harness.lab.spec import FaultStep, ProviderOp


class LabClock:
    """确定性时钟门面：只前进、不阻塞（与 ``DeterministicClock`` 同纪律）。

    fake latency / fake 等待以 ``advance`` 表达 —— 时间面进 tolerant 观测，
    绝不进 digest；真实超时执行语义属生产代码，fake 只模拟其**可观测结果**。
    """

    def __init__(self, start_ms: float = 0.0):
        self._now_ms = float(start_ms)

    @property
    def now_ms(self) -> float:
        return self._now_ms

    def advance_ms(self, ms: float) -> float:
        self._now_ms += float(ms)
        return self._now_ms


@dataclass
class DispatchOutcome:
    """fake dispatch 的统一观测形态（provider 收据 ∘ 故障变换的产物）。"""

    result: Dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    error_code: str = ""
    error_msg: str = ""
    #: 本次 dispatch 实际生效的故障类型（有序；观测/断言面）。
    faults_applied: List[str] = field(default_factory=list)
    #: duplicate_event 故障标记：本次投递是同收据的重放（幂等断言面）。
    duplicate: bool = False

    def as_receipt(self) -> Dict[str, Any]:
        if self.is_error:
            return {
                "success": False,
                "error": self.error_msg or "scripted failure",
                "code": self.error_code or "TOOL_ERROR",
            }
        return dict(self.result)


class _Provider(Protocol):
    """duck-typed registry/provider 缝（与 ``_RecordedProviderRegistry`` 同形）。"""

    def metadata(self, name: str) -> Dict[str, Any]: ...

    async def dispatch(self, tool_name: str, tool_args_raw: Any,
                       session_id: Optional[str] = None,
                       call_id: str = "") -> DispatchOutcome: ...


class ScriptedToolProvider:
    """canned 收据 provider：(tool, 规范化参数) 精确对齐 → 出现序游标回退。

    泛化自 ``replay/receipt._RecordedProviderRegistry``（保留其对齐规则，
    使录制收据与手写脚本可互换）；差异：返回 :class:`DispatchOutcome`（可
    被故障计划再变换），收据缺失显式 ``RECEIPT_MISSING`` 错误 —— 永不
    伪造成功。
    """

    def __init__(self, ops: List[ProviderOp],
                 tool_capabilities: Optional[Dict[str, List[str]]] = None):
        self._ops = [op for op in ops if op.tool]
        self._by_args: Dict[Tuple[str, str], ProviderOp] = {}
        for op in self._ops:
            key = self._args_key(op.tool, op.arguments)
            if key is not None and key not in self._by_args:
                self._by_args[key] = op
        self._cursor: Dict[str, int] = {}
        self._consumed: set = set()
        # call_id 是幂等重投递键：同 call_id 的重复投递回放同一收据，
        # 不消费新收据（dedup 合同的 fake 侧前提）。
        self._by_call: Dict[str, ProviderOp] = {}
        self._capabilities = tool_capabilities or {}
        self.calls: List[Dict[str, Any]] = []

    @staticmethod
    def _args_key(tool: str, arguments: Any) -> Optional[Tuple[str, str]]:
        try:
            normalized = json.dumps(
                arguments if isinstance(arguments, dict) else {},
                sort_keys=True, ensure_ascii=False, default=str)
            return (tool, normalized)
        except (TypeError, ValueError):
            return None

    def metadata(self, name: str) -> Dict[str, Any]:
        return {
            "capabilities": list(self._capabilities.get(name) or [])[:8],
            "cost": "light",
        }

    def _next(self, tool: str,
              args_key: Optional[Tuple[str, str]]) -> Optional[ProviderOp]:
        if args_key is not None:
            op = self._by_args.get(args_key)
            if op is not None and op.call_id not in self._consumed:
                self._consumed.add(op.call_id)
                return op
        offset = self._cursor.get(tool, 0)
        candidates = [op for op in self._ops
                      if op.tool == tool and op.call_id not in self._consumed]
        if offset >= len(candidates):
            return None
        chosen = candidates[offset]
        self._cursor[tool] = offset + 1
        self._consumed.add(chosen.call_id)
        return chosen

    async def dispatch(self, tool_name: str, tool_args_raw: Any,
                       session_id: Optional[str] = None,
                       call_id: str = "") -> DispatchOutcome:
        self.calls.append({
            "call_id": call_id, "tool": tool_name,
            "arguments": tool_args_raw if isinstance(tool_args_raw, dict)
            else {},
        })
        if call_id and call_id in self._by_call:
            # 幂等重投递：同一 call_id → 同一收据（消费序不前进）。
            op = self._by_call[call_id]
        else:
            op = self._next(tool_name, self._args_key(tool_name, tool_args_raw))
            if op is not None and call_id:
                self._by_call[call_id] = op
        if op is None:
            return DispatchOutcome(
                is_error=True, error_code="RECEIPT_MISSING",
                error_msg=f"no scripted receipt for tool {tool_name}",
            )
        if op.is_error:
            return DispatchOutcome(
                is_error=True, error_code=op.error_code or "TOOL_ERROR",
                error_msg=op.error_msg or "scripted tool failure",
            )
        return DispatchOutcome(result=dict(op.result))


class FaultInjector:
    """计划驱动的确定性故障编排（E15 D3 执行面词表）。

    纯变换层：包裹任意 provider dispatch，按 (target_call | 任意) 匹配的
    FaultStep 依计划顺序变换 outcome。语义：

    - ``latency``：LabClock advance（ms），不改收据 —— 时间面可观测；
    - ``timeout`` / ``disconnect`` / ``cancel``：typed 错误收据（code 显式），
      cancel 额外置 ``cancelled=True`` 语义（消费方不得折叠成普通失败）；
    - ``malformed_result``：收据换成畸形形状（下游必须诚实 fail，不猜）；
    - ``duplicate_event``：正常收据 + ``duplicate=True`` 重放标记（幂等门
      断言面 —— 重放收据不得二次铸 ref/二次投影）；
    - ``restart``：首次命中 typed ``PI_RESTART`` 错误，重试成功（恢复路径
      的确定性最小化：一次失败 + 一次成功，可断言）；
    - ``policy_deny`` / ``resource_reject``：生产 seam 的拒绝面在
      settlement 模式驱动（真实 AdmissionPolicy / bind gate），这里不做
      假拒绝 —— 拒绝**必须来自生产判定**，fake 不代答。
    """

    _TRANSFORM_FAULTS = (
        "latency", "timeout", "disconnect", "cancel", "malformed_result",
        "duplicate_event", "restart",
    )

    def __init__(self, plan: List[FaultStep], clock: Optional[LabClock] = None,
                 provider: Optional[_Provider] = None):
        self.clock = clock or LabClock()
        self._provider = provider
        self._plan = [f for f in plan if f.type in self._TRANSFORM_FAULTS]
        self._restart_seen: Dict[str, bool] = {}

    def _match(self, fault: FaultStep, call_id: str, tool: str) -> bool:
        if fault.target_call:
            return fault.target_call == call_id
        # 无 target_call：按 target_turn 语义退化为"第 N 次同工具调用"
        # （target_turn 即 0 基序号；确定性匹配，不依赖 wall-clock）。
        tool_calls = [c for c in (self._provider.calls if self._provider else [])
                      if c["tool"] == tool]
        return len(tool_calls) - 1 == max(0, fault.target_turn)

    async def dispatch(self, tool_name: str, tool_args_raw: Any,
                       session_id: Optional[str] = None,
                       call_id: str = "") -> DispatchOutcome:
        if self._provider is None:
            raise RuntimeError("FaultInjector requires a provider")
        outcome = await self._provider.dispatch(
            tool_name, tool_args_raw, session_id=session_id, call_id=call_id)
        for fault in self._plan:
            if not self._match(fault, call_id, tool_name):
                continue
            outcome.faults_applied.append(fault.type)
            if fault.type == "latency":
                self.clock.advance_ms(float(fault.params.get("ms") or 0))
            elif fault.type == "timeout":
                outcome.is_error = True
                outcome.error_code = "TOOL_TIMEOUT"
                outcome.error_msg = str(
                    fault.params.get("msg") or "tool timeout after 30000ms")
                outcome.result = {}
            elif fault.type == "disconnect":
                outcome.is_error = True
                outcome.error_code = "DISCONNECTED"
                outcome.error_msg = str(
                    fault.params.get("msg") or "connection dropped mid-call")
                outcome.result = {}
            elif fault.type == "cancel":
                outcome.is_error = True
                outcome.error_code = "CANCELLED"
                outcome.error_msg = str(
                    fault.params.get("msg") or "cancelled by user")
                outcome.result = {"cancelled": True}
            elif fault.type == "malformed_result":
                outcome.is_error = False
                outcome.result = {"unexpected_shape": True}
            elif fault.type == "duplicate_event":
                outcome.duplicate = True
            elif fault.type == "restart":
                key = f"{call_id or tool_name}"
                if not self._restart_seen.get(key):
                    self._restart_seen[key] = True
                    outcome.is_error = True
                    outcome.error_code = "PI_RESTART"
                    outcome.error_msg = str(
                        fault.params.get("msg") or "provider restarted")
                    outcome.result = {}
        return outcome