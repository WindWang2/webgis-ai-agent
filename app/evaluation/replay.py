"""Replay harness（ADR-0101 Wave 8, §34）—— 三种确定性重放模式。

1. **Tool replay**（``replay_tools``）：给定录制的 (tool, args) 序列 +
   会话夹具，重新 dispatch **声明为可安全重放**的工具（描述符
   replay_safe —— destructive / external_side_effect 永不自动执行），
   以结果契约视图（ToolResultView.contract_key）比较形状等价性。

2. **Agent-loop simulation**（``simulate_agent_loop``）：脚本化模型输出
   （工具调用序列，无 LLM），经真实 registry dispatch + 去重哨兵 +
   CallPatternTracker，验证运行时不变量：参数归一化、别名折叠、
   no-progress 检出、破坏性工具被 tier-3 闸拒绝。

3. **Trace invariant replay**（``check_trace_invariants``）：对 TurnTrace
   做状态机性质校验（不执行任何副作用）：dispatch_completed 必须有
   dispatch_started 前驱；turn_settled 至多一次；fallback 后必有
   model_selected。

所有模式：确定性、无网络、无 LLM —— 主测试门不依赖外部服务。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lib.runtime.result_contract import inspect_tool_result
from app.lib.runtime.trace import (
    EVENT_DISPATCH_COMPLETED,
    EVENT_FALLBACK,
    EVENT_MODEL_SELECTED,
    EVENT_NO_PROGRESS,
    EVENT_TURN_SETTLED,
    TurnTrace,
)
from app.services.chat.no_progress import CallPatternTracker, canonical_call_signature
from app.tools.descriptor import SideEffectClass
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 模式 1: Tool replay
# ---------------------------------------------------------------------------

@dataclass
class ReplayEntry:
    tool: str
    arguments: Dict[str, Any]
    #: 录制时的结果契约键（可选 —— 提供时做形状比较）
    recorded_contract: Optional[Dict[str, Any]] = None


@dataclass
class ToolReplayOutcome:
    tool: str
    action: str                    # replayed | skipped_unsafe | error
    contract_match: Optional[bool] = None
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool, "action": self.action,
            "contract_match": self.contract_match, "detail": self.detail,
        }


async def replay_tools(
    registry: ToolRegistry,
    session_id: str,
    entries: Sequence[ReplayEntry],
    *,
    confirm_tier3: bool = False,
) -> List[ToolReplayOutcome]:
    """重放安全工具；跳过不安全/未知工具（诚实留痕，不静默）。

    confirm_tier3 由调用方显式传入时也只是**跳过**而非执行 —— 本 harness
    的红线（§38：replay 绝不自动执行破坏性外部副作用）；参数保留用于
    前向兼容，当前实现永不授予。
    """
    outcomes: List[ToolReplayOutcome] = []
    for entry in entries:
        try:
            desc = registry.descriptor(entry.tool)
        except KeyError:
            outcomes.append(ToolReplayOutcome(
                tool=entry.tool, action="error", detail="unknown tool",
            ))
            continue
        if desc.replay_safe is False or desc.side_effect in (
            SideEffectClass.DESTRUCTIVE, SideEffectClass.EXTERNAL_SIDE_EFFECT,
        ):
            outcomes.append(ToolReplayOutcome(
                tool=entry.tool, action="skipped_unsafe",
                detail=f"side_effect={desc.side_effect.value}",
            ))
            continue
        if desc.status not in ():
            pass  # 状态由 dispatch 自身把关（planned 拒绝）
        result = await registry.dispatch(entry.tool, dict(entry.arguments), session_id)
        view = inspect_tool_result(result)
        outcome = ToolReplayOutcome(tool=entry.tool, action="replayed")
        if not view.ok:
            outcome.action = "error"
            outcome.detail = f"{view.error_code}: {view.message[:120]}"
        elif entry.recorded_contract is not None:
            outcome.contract_match = view.contract_key() == entry.recorded_contract
        outcomes.append(outcome)
    return outcomes


# ---------------------------------------------------------------------------
# 模式 2: Agent-loop simulation（脚本化模型输出）
# ---------------------------------------------------------------------------

@dataclass
class ScriptedCall:
    """脚本化的一步：assistant 提议的工具调用（含别名/坏参数形态）。"""

    tool: str
    arguments: Any                   # dict 或 JSON 串（模拟模型输出形态）
    expect_error_code: Optional[str] = None   # 断言该步返回的错误码
    expect_outcome: str = "ok"       # ok | error
    expect_no_progress_reasons: Optional[List[str]] = None


@dataclass
class SimulationReport:
    steps: List[Dict[str, Any]] = field(default_factory=list)
    invariants_held: bool = True
    violations: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "steps": self.steps,
            "invariants_held": self.invariants_held,
            "violations": self.violations,
        }


async def simulate_agent_loop(
    registry: ToolRegistry,
    session_id: str,
    script: Sequence[ScriptedCall],
    *,
    tracker: Optional[CallPatternTracker] = None,
) -> SimulationReport:
    """脚本化 agent 循环：逐步 dispatch，校验运行时不变量。

    不变量：
    a) 脚本声明的期望错误码/结果形态必须命中（归一化 + 校验链真实行为）；
    b) 破坏性工具在无 confirm_tier3 上下文时**必须**被拒
       （TIER3_CONFIRMATION_REQUIRED）—— 任何脚本形态都不允许绕过；
    c) 同签名重复失败触发 no-progress 原因码（模式检测联动）。
    """
    report = SimulationReport()
    tracker = tracker or CallPatternTracker()
    for idx, step in enumerate(script):
        from app.tools.argument_normalization import (
            normalization_report_var,
        )

        result = await registry.dispatch(step.tool, step.arguments, session_id)
        view = inspect_tool_result(result)
        entry: Dict[str, Any] = {
            "step": idx,
            "tool": step.tool,
            "signature": canonical_call_signature(step.tool, step.arguments),
            "ok": view.ok,
            "error_code": view.error_code,
            "repairs": [r.as_dict() for r in normalization_report_var.get()],
        }

        # 不变量 b：破坏性拒绝闸（双保险 —— 即使脚本期望 error）
        try:
            desc = registry.descriptor(step.tool)
            if desc.destructive_level >= 3 and view.ok:
                report.violations.append(
                    f"step {idx}: destructive tool {step.tool} executed without confirmation"
                )
        except KeyError:
            pass

        # 不变量 a：脚本期望
        if step.expect_error_code is not None and view.error_code != step.expect_error_code:
            report.violations.append(
                f"step {idx}: expected error {step.expect_error_code}, got {view.error_code}"
            )
        if step.expect_outcome == "ok" and not view.ok and step.expect_error_code is None:
            report.violations.append(
                f"step {idx}: expected ok, got error {view.error_code}: {view.message[:80]}"
            )

        # 不变量 c：no-progress 模式联动（工具类别由描述符声明 —— 波 1 语义
        # 分类驱动波 6 模式检测的闭环）
        is_read_only = False
        is_mutation = False
        try:
            _desc = registry.descriptor(step.tool)
            is_read_only = _desc.side_effect in (
                SideEffectClass.PURE,
                SideEffectClass.DETERMINISTIC_COMPUTE,
                SideEffectClass.CACHEABLE_READ,
            )
            is_mutation = _desc.side_effect in (
                SideEffectClass.STATE_MUTATION,
                SideEffectClass.ARTIFACT_CREATION,
            )
        except KeyError:
            pass
        reasons = tracker.record(
            step.tool, step.arguments,
            "ok" if view.ok else "error",
            is_read_only=is_read_only,
            is_mutation=is_mutation,
        )
        if step.expect_no_progress_reasons:
            for expected in step.expect_no_progress_reasons:
                if expected not in reasons and not any(
                    r.startswith(expected.split(":")[0]) for r in reasons
                ):
                    report.violations.append(
                        f"step {idx}: expected no-progress reason {expected}, got {reasons}"
                    )
        entry["no_progress_reasons"] = reasons
        report.steps.append(entry)
    report.invariants_held = not report.violations
    return report


# ---------------------------------------------------------------------------
# 模式 3: Trace invariant replay
# ---------------------------------------------------------------------------

def check_trace_invariants(trace: TurnTrace) -> List[str]:
    """对 TurnTrace 做状态机性质校验（零副作用）。返回违规列表。"""
    violations: List[str] = []
    open_dispatches: set[str] = set()
    settled_count = 0
    saw_model_selected = False

    for event in trace.events:
        if event.kind == EVENT_MODEL_SELECTED:
            saw_model_selected = True
        elif event.kind == "dispatch_started":
            open_dispatches.add(event.tool_call_id)
        elif event.kind == EVENT_DISPATCH_COMPLETED:
            if event.tool_call_id and event.tool_call_id not in open_dispatches:
                violations.append(
                    f"dispatch_completed without dispatch_started: {event.tool_call_id}"
                )
            open_dispatches.discard(event.tool_call_id)
        elif event.kind == EVENT_TURN_SETTLED:
            settled_count += 1
            if open_dispatches:
                violations.append(
                    f"turn_settled with in-flight dispatches: {sorted(open_dispatches)}"
                )
        elif event.kind == EVENT_FALLBACK and not saw_model_selected:
            violations.append("fallback before any model_selected")
        elif event.kind == EVENT_NO_PROGRESS and settled_count:
            violations.append("no_progress after settle")

    if settled_count > 1:
        violations.append(f"turn_settled emitted {settled_count} times")
    return violations
