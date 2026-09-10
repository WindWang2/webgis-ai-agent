"""HarnessRuntime —— 任务级运行时状态机（V7 / ADR-0130 决策 D1）。

V6 基线缺口（harness-v6 PR summary follow-ups + V7 Goal Phase A）：编排
事实散落在触发点（agent_pi_bridge 的 tool_result / turn_settled 分别驱动
finalizer / WorkflowInstance / runtime projection），「这个任务现在处于
认知闭环的哪一步」没有显式答案 —— intent → … → commit 的每一步都有机制
（planner / plan_graph / dispatch / render_observation / finalizer /
anchor），但**没有单一状态机**把它们串成可追踪、可恢复、可测试的序列。

V7 契约：

- **阶段 = 权威事实的只读派生**（与 WorkflowInstance 同纪律）：阶段不是
  独立累加的事件状态，而是从章节事实（plan 行 / map_product 裁决 /
  workflow_instance stale 债 / runtime commit 标记）+ 输入证据
  （recovery 预算余量）确定性推出。同输入同阶段；删块重建值不变 ——
  不产生第二事实源。
- **转移记录 = 派生差分**：``RuntimeTransition`` 环形有界（≤16），记录
  revision / trigger / from→to / reason_code。合法转移表既是文档也是
  测试 oracle；命令式转移（未来 human confirmation / 显式驱动器）经
  ``validate_transition`` fail-closed。
- **SUSPENDED 是覆盖旗标不是阶段**：turn 收尾未终态 → ``suspended=true``
  （任务可经锚点恢复）；新触发点到达即清除。避免「恢复后又执行」在
  suspended ↔ executing 间振荡刷转移记录。
- **有界一切**：阶段词表封闭（12）、触发词表封闭、转移环形、循环预算
  投影复用 durable_context.LOOP_BUDGETS（本模块不建第二本预算账）。
- **零断言业务语义**：本模块不判断「地图好不好」（finalizer 职责）、
  不重算科学契约（workflow_instance 职责）、不改行状态（_mark_progress
  单写者）—— 它只回答「闭环走到哪一步、下一步该哪条回路」。

持久化：``gis_chapter["runtime_state"]`` 单键 additive（旧读者忽略）；
写路径复用 SessionPlan per-session lock + 漂移守卫（goal/rows/块自身）。
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.durable_context import LOOP_BUDGETS
from app.services.gis_harness.workflow_instance import (
    canonical_fingerprint,
    rows_fingerprint,
)

logger = logging.getLogger(__name__)

#: gis_chapter 单键（additive；旧读者忽略）。
RUNTIME_STATE_KEY = "runtime_state"


def _enabled() -> bool:
    """降级开关（默认开）。"""
    return os.getenv("GIS_RUNTIME_STATE_MACHINE", "1") not in ("0", "false", "False")


# ── 封闭词表 ─────────────────────────────────────────────────────────────

class RuntimePhase(str, Enum):
    """任务级认知闭环阶段（封闭词表；弱→强推进，回路阶段可回退）。

    正向主线：idle → intent_resolved → plan_ready → executing →
    critiquing → finalizing → committed。
    回路阶段：recomputing（依赖图局部重算）/ repairing（运行时/成品
    修复）/ replanning（重规划）/ observing（等渲染取证）—— 任一回路
    阶段收敛后回到其父阶段（executing/critiquing）。
    """

    IDLE = "idle"
    INTENT_RESOLVED = "intent_resolved"
    PLAN_READY = "plan_ready"
    EXECUTING = "executing"
    RECOMPUTING = "recomputing"
    OBSERVING = "observing"
    CRITIQUING = "critiquing"
    REPAIRING = "repairing"
    REPLANNING = "replanning"
    FINALIZING = "finalizing"
    COMMITTED = "committed"
    ABORTED = "aborted"


#: 触发词表（封闭）。触发点 = 既有生产事件（pi bridge / finalizer /
#: observation POST / resume），本模块不新开事件源。
TRIGGERS: Tuple[str, ...] = (
    "intent_parsed",          # 章节 query/intent 出现
    "plan_compiled",          # plan_id + 行出现
    "execution_started",      # 首个行离开 pending
    "execution_progressed",   # 行状态推进（工具结果落账）
    "execution_settled",      # DAG 终态（全部行离场）
    "recompute_debt",         # stale 债出现（数据/算法/参数漂移）
    "recompute_done",         # 债清偿
    "observation_received",   # render observation 到达且非 pending
    "observation_pending",    # 观察 pending/unknown（等取证）
    "critique_started",       # 终验起步
    "repair_needed",          # needs_repair 且有可修复项
    "repair_converged",       # 修复收敛（回到 critiquing）
    "repair_exhausted_replan",  # 修复预算尽 → 转重规划
    "replan_committed",       # 重规划落账（回到 plan_ready/executing）
    "verdict_ready",          # READY 裁决
    "context_committed",      # 上下文提交（V7 commit 标记）
    "abort_budget",           # 全回路预算耗尽
    "resume",                 # 锚点恢复
    "supersede",              # 目标被替换
)


class RuntimeTransition(BaseModel):
    """一次阶段转移记录（环形 ≤MAX；无时间戳 —— revision 即序）。"""

    revision: int
    trigger: str = ""
    from_phase: str = ""
    to_phase: str = ""
    reason_code: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "revision": self.revision,
            "trigger": self.trigger[:32],
            "from_phase": self.from_phase[:24],
            "to_phase": self.to_phase[:24],
            "reason_code": self.reason_code[:64],
        }


#: 有界预算。
MAX_TRANSITIONS = 16
MAX_REASON = 64


# ── 合法转移表（文档 + 测试 oracle + 命令式转移 fail-closed 校验）───────

#: (from_phase, trigger) → to_phase。派生函数的输出对已存阶段 + 触发的
#: 组合都应落在此表内（测试穷举钉住）；表外组合照常记录转移但 reason_code
#: 标 ``DERIVED_OUTSIDE_TABLE``（可观测，不静默）。
LEGAL_TRANSITIONS: Dict[Tuple[str, str], str] = {
    # 主线
    ("idle", "intent_parsed"): "intent_resolved",
    ("intent_resolved", "plan_compiled"): "plan_ready",
    ("plan_ready", "execution_started"): "executing",
    ("executing", "execution_progressed"): "executing",
    ("executing", "execution_settled"): "critiquing",
    ("critiquing", "critique_started"): "critiquing",
    ("critiquing", "verdict_ready"): "finalizing",
    ("finalizing", "context_committed"): "committed",
    # 回路：重算
    ("executing", "recompute_debt"): "recomputing",
    ("recomputing", "recompute_done"): "executing",
    ("recomputing", "execution_settled"): "critiquing",
    # 回路：观察
    ("critiquing", "observation_pending"): "observing",
    ("observing", "observation_received"): "critiquing",
    ("observing", "observation_pending"): "observing",
    # 回路：修复
    ("critiquing", "repair_needed"): "repairing",
    ("repairing", "repair_converged"): "critiquing",
    ("repairing", "repair_needed"): "repairing",
    ("repairing", "observation_pending"): "observing",
    # 回路：重规划
    ("repairing", "repair_exhausted_replan"): "replanning",
    ("critiquing", "repair_exhausted_replan"): "replanning",
    ("replanning", "replan_committed"): "plan_ready",
    ("replanning", "plan_compiled"): "plan_ready",
    # 提交后增量续跑（long-horizon 多轮）
    ("committed", "intent_parsed"): "intent_resolved",
    ("committed", "supersede"): "intent_resolved",
    # 中止 / 恢复 / 替换
    ("repairing", "abort_budget"): "aborted",
    ("replanning", "abort_budget"): "aborted",
    ("critiquing", "abort_budget"): "aborted",
    ("executing", "abort_budget"): "aborted",
    ("aborted", "resume"): "executing",
    ("intent_resolved", "supersede"): "intent_resolved",
    ("plan_ready", "supersede"): "intent_resolved",
    ("executing", "supersede"): "intent_resolved",
    # 跨阶段观察/取证的诚实路径
    ("plan_ready", "execution_progressed"): "executing",
    ("recomputing", "recompute_debt"): "recomputing",
    ("committed", "context_committed"): "committed",
    ("finalizing", "verdict_ready"): "finalizing",
    ("finalizing", "repair_needed"): "repairing",
    ("finalizing", "repair_exhausted_replan"): "replanning",
}


def validate_transition(from_phase: str, trigger: str, to_phase: str) -> bool:
    """命令式转移校验（fail-closed 用；派生转移不强约束 —— 只披露）。"""
    return LEGAL_TRANSITIONS.get((from_phase, trigger)) == to_phase


# ── 派生（纯函数；权威事实 → 阶段）──────────────────────────────────────

def _row_states(chapter: Dict[str, Any]) -> List[str]:
    rows = list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    )
    return [str(r.get("status") or "pending") for r in rows if isinstance(r, dict)]


def _dag_terminal(states: List[str]) -> bool:
    return bool(states) and all(
        s in ("available", "done", "complete", "unavailable", "skipped", "failed")
        for s in states
    )


def _dag_started(states: List[str]) -> bool:
    return any(s not in ("pending",) for s in states)


def _recompute_debt(chapter: Dict[str, Any]) -> int:
    """stale 债（workflow_instance stages 的 stale 计数；块缺席 = 0）。"""
    block = chapter.get("workflow_instance")
    if not isinstance(block, dict):
        return 0
    return sum(
        1 for s in (block.get("stages") or [])
        if isinstance(s, dict) and s.get("state") == "stale"
    )


def _repairable_count(chapter: Dict[str, Any]) -> int:
    """成品块中可修复 finding 计数（repair_plan.actions 有界投影）。"""
    product = chapter.get("map_product")
    if not isinstance(product, dict):
        return 0
    plan = product.get("repair_plan")
    if not isinstance(plan, dict):
        return 0
    actions = plan.get("actions")
    if isinstance(actions, list):
        return len(actions)
    return int(plan.get("action_count") or 0)


def _runtime_render_only(chapter: Dict[str, Any]) -> bool:
    """needs_repair 是否仅由 runtime 渲染缺口构成（等再取证而非修复）。"""
    product = chapter.get("map_product")
    if not isinstance(product, dict):
        return False
    summary = str(product.get("summary") or "")
    return "await runtime re-observation" in summary


def _committed_marker(chapter: Dict[str, Any]) -> Dict[str, Any]:
    stored = chapter.get(RUNTIME_STATE_KEY)
    if not isinstance(stored, dict):
        return {}
    marker = stored.get("context_commit")
    return marker if isinstance(marker, dict) else {}


def _verdict_ready(chapter: Dict[str, Any]) -> bool:
    product = chapter.get("map_product")
    if not isinstance(product, dict):
        return False
    return str(product.get("product_verdict") or "").startswith("READY")


def _task_complete(chapter: Dict[str, Any]) -> bool:
    product = chapter.get("map_product")
    if not isinstance(product, dict):
        return False
    return bool(product.get("task_complete")) is True


def _budgets_exhausted(recovery_loops: Dict[str, int]) -> bool:
    """recovery 预算余量全 0（deepen/requalify/repair/replan）。"""
    if not recovery_loops:
        return False
    return all(int(v) <= 0 for v in recovery_loops.values())


def _unresolved(chapter: Dict[str, Any]) -> bool:
    """任务有未收口的成品缺口（verdict 非 READY 且成品块在场）。"""
    product = chapter.get("map_product")
    if not isinstance(product, dict):
        return False
    return not _verdict_ready(chapter)


def derive_runtime_phase(
    chapter: Dict[str, Any],
    *,
    recovery_loops: Optional[Dict[str, int]] = None,
    stored: Optional[Dict[str, Any]] = None,
) -> str:
    """章节权威事实 → 运行时阶段（确定性；同输入同输出）。

    优先级（高→低）：committed → finalizing(READY 未提交) → aborted
    （未收口 + 预算尽）→ replanning（重规划挂起）→ repairing → observing
    → critiquing（DAG 终态无终验）→ recomputing（stale 债）→ executing
    → plan_ready → intent_resolved → idle。
    """
    loops = {k: int(v) for k, v in (recovery_loops or {}).items()}
    if not isinstance(chapter, dict) or not (
        str(chapter.get("query") or "") or chapter.get("plan_id")
    ):
        return RuntimePhase.IDLE.value
    if not chapter.get("plan_id"):
        return RuntimePhase.INTENT_RESOLVED.value

    states = _row_states(chapter)
    has_product = isinstance(chapter.get("map_product"), dict)

    # 1) 已提交（READY + commit 标记）
    if _task_complete(chapter) and _committed_marker(chapter):
        return RuntimePhase.COMMITTED.value
    # 2) READY 未提交 → finalizing（等上下文提交）
    if _verdict_ready(chapter) and has_product:
        return RuntimePhase.FINALIZING.value
    # 3) 未收口 + 全预算耗尽 → aborted（诚实部分完成）
    if _unresolved(chapter) and _budgets_exhausted(loops):
        return RuntimePhase.ABORTED.value
    # 4) 重规划挂起（V7 plan_runtime.replan 记录未消费）
    plan_runtime = chapter.get("plan_runtime")
    if isinstance(plan_runtime, dict) and plan_runtime.get("replan_pending"):
        return RuntimePhase.REPLANNING.value
    # 5) 修复回路（needs_repair + 可修复项在场）
    product = chapter.get("map_product")
    if has_product and str(product.get("status") or "") == "needs_repair":
        if _repairable_count(chapter) > 0:
            return RuntimePhase.REPAIRING.value
        if _runtime_render_only(chapter):
            return RuntimePhase.OBSERVING.value
        return RuntimePhase.CRITIQUING.value
    # 6) DAG 终态、无终验结论 → critiquing
    if states and _dag_terminal(states):
        return RuntimePhase.CRITIQUING.value
    # 7) stale 债 → recomputing
    if _recompute_debt(chapter) > 0:
        return RuntimePhase.RECOMPUTING.value
    # 8) 执行中 / 就绪
    if states and _dag_started(states):
        return RuntimePhase.EXECUTING.value
    if states:
        return RuntimePhase.PLAN_READY.value
    # 组件-only / analysis-only 章节（无数据行）：有 plan 即视为执行面
    return RuntimePhase.EXECUTING.value


# ── 状态块 ───────────────────────────────────────────────────────────────

class RuntimeContextCommit(BaseModel):
    """上下文提交标记（Phase F：READY 后写入；revision 对齐成品块）。"""

    product_checked_revision: int = 0
    render_observation_seq: int = 0
    domains_digest: str = ""            # 九域 context 摘要指纹（[:32]）
    anchor_saved: bool = False

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "product_checked_revision": int(self.product_checked_revision),
            "render_observation_seq": int(self.render_observation_seq),
            "domains_digest": self.domains_digest[:32],
            "anchor_saved": self.anchor_saved,
        }


class RuntimeStateBlock(BaseModel):
    """任务级运行时状态块（gis_chapter[RUNTIME_STATE_KEY] 的 schema）。"""

    schema_version: str = "runtime_state.v1"
    task_id: str = ""                    # session:envelope:plan
    phase: str = RuntimePhase.IDLE.value
    phase_revision: int = 0              # 单调（内容变化 +1）
    suspended: bool = False              # turn 收尾未终态（覆盖旗标）
    last_trigger: str = ""
    plan_version: int = 0                # plan_runtime 版本镜像（缺席 = 0）
    loops_remaining: Dict[str, int] = Field(default_factory=dict)
    recompute_debt: int = 0
    transitions: List[RuntimeTransition] = Field(default_factory=list)
    context_commit: RuntimeContextCommit = Field(default_factory=RuntimeContextCommit)
    gate_fingerprint: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema_version,
            "task_id": self.task_id[:128],
            "phase": self.phase,
            "phase_revision": self.phase_revision,
            "suspended": self.suspended,
            "last_trigger": self.last_trigger[:32],
            "plan_version": int(self.plan_version),
            "loops_remaining": {k: int(v)
                                for k, v in list(self.loops_remaining.items())[:6]},
            "recompute_debt": int(self.recompute_debt),
            "transitions": [t.to_bounded_dict()
                            for t in self.transitions[:MAX_TRANSITIONS]],
            "context_commit": self.context_commit.to_bounded_dict(),
            "gate_fingerprint": self.gate_fingerprint[:32],
        }


def _input_gate_fingerprint(
    chapter: Dict[str, Any],
    recovery_loops: Dict[str, int],
    plan_version: int,
) -> str:
    """门键：行指纹 + 阶段输入摘要（便宜，O(rows)）。"""
    product = chapter.get("map_product") if isinstance(chapter, dict) else None
    product_fp = canonical_fingerprint({
        "status": str((product or {}).get("status") or ""),
        "verdict": str((product or {}).get("product_verdict") or ""),
        "task_complete": bool((product or {}).get("task_complete")),
        "checked_revision": int((product or {}).get("checked_revision") or 0),
        "repair_plan_actions": _repairable_count(chapter),
    }) if product else ""
    instance = chapter.get("workflow_instance") if isinstance(chapter, dict) else None
    return canonical_fingerprint({
        "rows": rows_fingerprint(chapter if isinstance(chapter, dict) else {})[:2048],
        "product": product_fp,
        "stale_debt": _recompute_debt(chapter if isinstance(chapter, dict) else {}),
        "loops": sorted((recovery_loops or {}).items()),
        "plan_version": int(plan_version),
        "replan_pending": bool(
            isinstance(chapter.get("plan_runtime"), dict)
            and chapter["plan_runtime"].get("replan_pending")
        ),
        "instance_rev": int((instance or {}).get("state_revision") or 0),
    })


def derive_runtime_state(
    chapter: Dict[str, Any],
    *,
    task_id: str = "",
    trigger: str = "",
    recovery_loops: Optional[Dict[str, int]] = None,
    turn_settled: bool = False,
    stored: Optional[Dict[str, Any]] = None,
) -> RuntimeStateBlock:
    """权威事实 + 触发 → 新运行时状态块（纯函数）。

    - 阶段由 ``derive_runtime_phase`` 派生；
    - 转移记录 = 新旧阶段差分（环形 ≤MAX_TRANSITIONS）；
    - ``suspended``：turn_settled 且阶段 ∉ {committed, aborted} → True；
      其余触发一律 False；
    - ``context_commit`` 标记只在 stored 已有且仍成立时保留（提交由
      ``commit_runtime_context`` 显式写入 —— 派生不伪造提交）。
    """
    loops = {k: int(v) for k, v in (recovery_loops or {}).items()}
    stored_block = _parse_stored(stored)
    phase = derive_runtime_phase(
        chapter, recovery_loops=loops, stored=stored)
    plan_runtime = chapter.get("plan_runtime") if isinstance(chapter, dict) else None
    plan_version = int((plan_runtime or {}).get("version") or 0)

    revision = stored_block.phase_revision
    transitions = list(stored_block.transitions)
    if phase != stored_block.phase or (
            stored_block.last_trigger and trigger != stored_block.last_trigger):
        revision += 1
        reason = "derived"
        if not validate_transition(stored_block.phase, trigger, phase):
            reason = "DERIVED_OUTSIDE_TABLE"
        transitions.append(RuntimeTransition(
            revision=revision,
            trigger=trigger,
            from_phase=stored_block.phase,
            to_phase=phase,
            reason_code=reason,
        ))
        del transitions[:-MAX_TRANSITIONS]

    suspended = bool(
        turn_settled
        and phase not in (
            RuntimePhase.COMMITTED.value,
            RuntimePhase.ABORTED.value,
            RuntimePhase.FINALIZING.value,  # READY + settle → commit 同路径
        )
    )
    # 提交标记保留语义：stored 的标记仍指向当前成品（revision 一致）才保留；
    # 成品重验（revision 前进）后旧标记失效 —— 由 commit 步骤重写。
    commit = stored_block.context_commit
    product = chapter.get("map_product") if isinstance(chapter, dict) else None
    if (
        commit.product_checked_revision
        and isinstance(product, dict)
        and int(product.get("checked_revision") or 0) != commit.product_checked_revision
    ):
        commit = RuntimeContextCommit()

    return RuntimeStateBlock(
        task_id=task_id or stored_block.task_id,
        phase=phase,
        phase_revision=revision,
        suspended=suspended,
        last_trigger=trigger[:32],
        plan_version=plan_version,
        loops_remaining=loops,
        recompute_debt=_recompute_debt(chapter if isinstance(chapter, dict) else {}),
        transitions=transitions,
        context_commit=commit,
        gate_fingerprint=_input_gate_fingerprint(chapter, loops, plan_version),
    )


def _parse_stored(stored: Optional[Dict[str, Any]]) -> RuntimeStateBlock:
    """stored dict → 块（schema 不符按空块；绝不抛）。"""
    if not isinstance(stored, dict) or stored.get("schema") != "runtime_state.v1":
        return RuntimeStateBlock()
    transitions = [
        RuntimeTransition(
            revision=int(t.get("revision") or 0),
            trigger=str(t.get("trigger") or "")[:32],
            from_phase=str(t.get("from_phase") or "")[:24],
            to_phase=str(t.get("to_phase") or "")[:24],
            reason_code=str(t.get("reason_code") or "")[:64],
        )
        for t in (stored.get("transitions") or [])[:MAX_TRANSITIONS]
        if isinstance(t, dict)
    ]
    commit_raw = stored.get("context_commit")
    commit = RuntimeContextCommit()
    if isinstance(commit_raw, dict):
        commit = RuntimeContextCommit(
            product_checked_revision=int(commit_raw.get("product_checked_revision") or 0),
            render_observation_seq=int(commit_raw.get("render_observation_seq") or 0),
            domains_digest=str(commit_raw.get("domains_digest") or "")[:32],
            anchor_saved=bool(commit_raw.get("anchor_saved")),
        )
    return RuntimeStateBlock(
        task_id=str(stored.get("task_id") or "")[:128],
        phase=str(stored.get("phase") or RuntimePhase.IDLE.value),
        phase_revision=int(stored.get("phase_revision") or 0),
        suspended=bool(stored.get("suspended")),
        last_trigger=str(stored.get("last_trigger") or "")[:32],
        plan_version=int(stored.get("plan_version") or 0),
        loops_remaining={
            str(k)[:16]: int(v)
            for k, v in list((stored.get("loops_remaining") or {}).items())[:6]
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        },
        recompute_debt=int(stored.get("recompute_debt") or 0),
        transitions=transitions,
        context_commit=commit,
        gate_fingerprint=str(stored.get("gate_fingerprint") or "")[:32],
    )


# ── 有界投影（LLM 面）────────────────────────────────────────────────────

def format_runtime_line(chapter: Optional[Dict[str, Any]]) -> str:
    """[GIS Runtime] 单行投影（SessionPlan projection 的 additive 行）。"""
    if not isinstance(chapter, dict):
        return ""
    stored = chapter.get(RUNTIME_STATE_KEY)
    if not isinstance(stored, dict) or not stored.get("phase"):
        return ""
    parts = [
        f"[GIS Runtime] phase={stored.get('phase')}"
        f" rev={stored.get('phase_revision')}"
    ]
    if stored.get("suspended"):
        parts.append("suspended=true（可经锚点恢复）")
    loops = stored.get("loops_remaining") or {}
    if loops:
        parts.append("loops:" + ",".join(
            f"{k}={v}" for k, v in sorted(loops.items())[:4]))
    if int(stored.get("recompute_debt") or 0) > 0:
        parts.append(f"debt=stale:{int(stored['recompute_debt'])}")
    if isinstance(stored.get("context_commit"), dict) and stored["context_commit"].get(
            "product_checked_revision"):
        parts.append("committed=true")
    return " ".join(parts)[:480]


def runtime_phase_of(chapter: Optional[Dict[str, Any]]) -> str:
    """只读面：stored 阶段（缺席派生一次 —— finalizer/critique 消费用）。"""
    if not isinstance(chapter, dict):
        return RuntimePhase.IDLE.value
    stored = chapter.get(RUNTIME_STATE_KEY)
    if isinstance(stored, dict) and stored.get("schema") == "runtime_state.v1":
        return str(stored.get("phase") or RuntimePhase.IDLE.value)
    return derive_runtime_phase(chapter)


# ── 服务入口（异步、锁内持久化；workflow_instance persist 模式的轻量版）──

async def maybe_update_runtime_state(
    session_id: str,
    *,
    reason: str = "tool_result",
    trigger: str = "execution_progressed",
    turn_settled: bool = False,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """触发点入口：廉价门 + 纯派生 + 锁内持久化（幂等、有界、可关停）。

    返回新块 dict（跳过/禁用时 None）。绝不触碰行状态 / workflow_instance /
    map_product —— 阶段是它们的只读投影；失败只记日志，下一触发点重试。
    """
    if not session_id or not _enabled():
        return None
    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return None
    chapter = plan.gis_chapter
    if not chapter.get("plan_id") and not chapter.get("query"):
        return None

    # V7 D2：计划版本随同一触发面推进（指纹门内 else 幂等 no-op；
    # replan_pending 在计划事实变化时由版本推进清除 → 阶段派生读新值）。
    try:
        from app.services.gis_harness.plan_runtime import (
            maybe_advance_plan_version,
        )

        await maybe_advance_plan_version(session_id, reason=reason)
    except Exception:  # noqa: BLE001 — 版本面是增值披露
        logger.debug(
            "[HarnessRuntime] plan version advance failed session=%s",
            session_id, exc_info=True)
    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return None
    chapter = plan.gis_chapter

    from app.services.gis_harness.durable_context import load_recovery_state

    recovery = await load_recovery_state(session_id)
    used = recovery.get("loops") or {}
    loops_remaining = {
        k: max(0, int(LOOP_BUDGETS.get(k, 0)) - int(used.get(k) or 0))
        for k in LOOP_BUDGETS
    }
    stored = chapter.get(RUNTIME_STATE_KEY)
    gate = _input_gate_fingerprint(
        chapter, loops_remaining,
        int(((chapter.get("plan_runtime") or {}) if isinstance(
            chapter.get("plan_runtime"), dict) else {}).get("version") or 0),
    )
    if (
        not force
        and isinstance(stored, dict)
        and str(stored.get("gate_fingerprint") or "") == gate
        and bool(stored.get("suspended")) == turn_settled
    ):
        return None

    task_id = f"{session_id}:{plan.envelope_id}:{chapter.get('plan_id')}"
    new_block = derive_runtime_state(
        chapter,
        task_id=task_id,
        trigger=trigger,
        recovery_loops=loops_remaining,
        turn_settled=turn_settled,
        stored=stored if isinstance(stored, dict) else None,
    )

    from app.services.session_plan import goal_key, save_session_plan

    validated_goal = goal_key(chapter, plan.user_goal)
    validated_rows = rows_fingerprint(chapter)
    try:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            fresh = await load_session_plan(session_id)
            if fresh is None or not isinstance(fresh.gis_chapter, dict) or lock.lost:
                return None
            if goal_key(fresh.gis_chapter, fresh.user_goal) != validated_goal:
                return None
            if rows_fingerprint(fresh.gis_chapter)[:2048] != validated_rows[:2048]:
                return None
            fresh_stored = fresh.gis_chapter.get(RUNTIME_STATE_KEY)
            if (
                isinstance(stored, dict)
                and isinstance(fresh_stored, dict)
                and str(fresh_stored.get("gate_fingerprint") or "")
                != str(stored.get("gate_fingerprint") or "")
            ):
                return None
            block = new_block.to_bounded_dict()
            fresh.gis_chapter[RUNTIME_STATE_KEY] = block
            await save_session_plan(fresh)
            return block
    except Exception:  # noqa: BLE001 — 投影失败不阻断 turn
        logger.warning(
            "[HarnessRuntime] persist failed session=%s (retry on next trigger)",
            session_id, exc_info=True,
        )
        return None


async def commit_runtime_context(
    session_id: str,
    *,
    domains_digest: str = "",
    anchor_saved: bool = False,
) -> Optional[Dict[str, Any]]:
    """READY 后的上下文提交（Phase F finalizer 调用）：写 commit 标记。

    幂等：同一成品 revision 已提交 → no-op。派生器对旧标记（成品
    revision 前进后）自动失效 —— 重验后由本函数重写。
    """
    if not session_id:
        return None
    from app.services.session_plan import load_session_plan, save_session_plan
    from app.services.gis_harness.render_observation import (
        load_render_observation,
        observation_sequence,
    )
    from app.services.session_data import session_data_manager

    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return None
    chapter = plan.gis_chapter
    product = chapter.get("map_product")
    if not isinstance(product, dict) or not product.get("task_complete"):
        return None
    try:
        map_state = await session_data_manager.get_map_state(session_id)
    except Exception:  # noqa: BLE001 — 观察缺席按 0
        map_state = None
    render_seq = observation_sequence(await load_render_observation(session_id, map_state))
    stored = chapter.get(RUNTIME_STATE_KEY)
    block = _parse_stored(stored if isinstance(stored, dict) else None)
    checked = int(product.get("checked_revision") or 0)
    if (
        block.context_commit.product_checked_revision == checked
        and block.context_commit.product_checked_revision > 0
    ):
        return None  # 幂等：已提交同一成品

    async def _persist() -> Optional[Dict[str, Any]]:
        from app.services.session_plan import goal_key
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            fresh = await load_session_plan(session_id)
            if fresh is None or not isinstance(fresh.gis_chapter, dict) or lock.lost:
                return None
            if goal_key(fresh.gis_chapter, fresh.user_goal) != goal_key(chapter, plan.user_goal):
                return None
            fresh_product = fresh.gis_chapter.get("map_product")
            if not isinstance(fresh_product, dict) or not fresh_product.get("task_complete"):
                return None
            stored_now = _parse_stored(fresh.gis_chapter.get(RUNTIME_STATE_KEY))
            stored_now.context_commit = RuntimeContextCommit(
                product_checked_revision=int(fresh_product.get("checked_revision") or 0),
                render_observation_seq=int(render_seq),
                domains_digest=str(domains_digest)[:32],
                anchor_saved=bool(anchor_saved),
            )
            if not stored_now.task_id:
                stored_now.task_id = f"{session_id}:{plan.envelope_id}:{fresh.gis_chapter.get('plan_id')}"
            if not stored_now.phase:
                stored_now.phase = RuntimePhase.FINALIZING.value
            fresh.gis_chapter[RUNTIME_STATE_KEY] = stored_now.to_bounded_dict()
            await save_session_plan(fresh)
            return fresh.gis_chapter[RUNTIME_STATE_KEY]

    return await _persist()


__all__ = [
    "RUNTIME_STATE_KEY",
    "RuntimePhase",
    "TRIGGERS",
    "RuntimeTransition",
    "RuntimeContextCommit",
    "RuntimeStateBlock",
    "LEGAL_TRANSITIONS",
    "validate_transition",
    "derive_runtime_phase",
    "derive_runtime_state",
    "format_runtime_line",
    "runtime_phase_of",
    "maybe_update_runtime_state",
    "commit_runtime_context",
]
