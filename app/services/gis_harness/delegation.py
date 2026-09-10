"""Delegation —— Harness 程序化委派（V7 ADR-0130 决策 D7）。

V6 基线缺口（审计 G）：12 个专用角色（gis_inspector /
cartography_reviewer / algorithm_reviewer / result_verifier …）已注册但
**harness 零程序化调用** —— 唯一派发点是 LLM 工具（模型自行 spawn）；
planner/finalizer 无法把「数据审计 / 算法选择 / 制图 QA」下发给专门
worker，也没有 handoff schema、父侧状态跟踪与子任务失败回收。

V7 契约（**机制复用，不重建 agent 框架**）：

- **HandoffSpec**：``delegation_id / role / task / plan_step（capability）/
  required_outputs / max_rounds`` —— role 经 ``get_subagent_role``
  fail-closed 校验（未知角色拒绝）；预算档/工具交集/递归深度上限全部由
  ``SubagentDispatcher`` 既有语义承载（本模块零复制）。
- **父侧台账**：``gis_chapter["delegations"]`` additive 单键，有界
  （≤MAX_DELEGATIONS 条环形）；记录 status（pending→running→
  completed/failed）+ result 摘要 + refs + lineage（父 turn 关联，审计面）。
- **失败回收**：确定性裁决 —— 首败同 role 重试一次（bounded），再败诚实
  放弃并披露（不换 role 盲目重试 —— 预算纪律）；裁决经 recovery_state
  ``repair`` 预算约束（有余才重试）。
- **生产驱动点**：``delegate_cartography_qa`` —— finalizer 批评面存在
  warning 级未修复发现时可选下发给 cartography_reviewer 复核；env
  ``GIS_HARNESS_DELEGATION=1`` 显式开启（默认关 —— LLM 依赖不进终验
  热路径；开启时每成品 revision 至多 1 次，幂等）。
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: gis_chapter 单键（additive；旧读者忽略）。
DELEGATIONS_KEY = "delegations"

#: 台账上界（环形；含历史）。
MAX_DELEGATIONS = 8

#: 状态词表（封闭）。
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
#: 默认轮数上界（角色 max_rounds 取更严者 —— dispatcher 语义）。
DEFAULT_MAX_ROUNDS = 4


def delegation_enabled() -> bool:
    """生产驱动点开关（默认关；显式 env 开启）。"""
    return os.getenv("GIS_HARNESS_DELEGATION", "0") in ("1", "true", "True")


# ── Handoff schema ───────────────────────────────────────────────────────


class DelegationSpec(BaseModel):
    """一次委派的 handoff 声明（父 → 专门 worker）。"""

    role: str                            # 注册表角色名（未知 fail-closed）
    task: str
    plan_step: str = ""                  # 关联 plan 行 capability（可空）
    required_outputs: List[str] = Field(default_factory=list)  # 期望产出描述
    max_rounds: int = DEFAULT_MAX_ROUNDS

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role[:40],
            "task": self.task[:400],
            "plan_step": self.plan_step[:64],
            "required_outputs": [o[:80] for o in self.required_outputs[:4]],
            "max_rounds": int(self.max_rounds),
        }


class DelegationRecord(BaseModel):
    """父侧台账条目（状态 + 结果摘要 + 血缘）。"""

    delegation_id: str
    spec: DelegationSpec
    status: str = STATUS_PENDING
    result_summary: str = ""
    refs: List[str] = Field(default_factory=list)
    error: str = ""
    lineage: Dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "delegation_id": self.delegation_id[:40],
            "spec": self.spec.to_bounded_dict(),
            "status": self.status,
            "result_summary": self.result_summary[:400],
            "refs": [r[:64] for r in self.refs[:8]],
            "error": self.error[:200],
            "lineage": {k: str(v)[:48]
                        for k, v in list(self.lineage.items())[:6]},
            "attempts": int(self.attempts),
        }


# ── 台账（锁内单键持久化；runtime 模块同款纪律）────────────────────────


def _parse_records(stored: Any) -> List[DelegationRecord]:
    if not isinstance(stored, dict):
        return []
    out: List[DelegationRecord] = []
    for raw in (stored.get("records") or [])[:MAX_DELEGATIONS]:
        if not isinstance(raw, dict) or not isinstance(raw.get("spec"), dict):
            continue
        try:
            spec = DelegationSpec(**{
                "role": str(raw["spec"].get("role") or "")[:40],
                "task": str(raw["spec"].get("task") or "")[:400],
                "plan_step": str(raw["spec"].get("plan_step") or "")[:64],
                "required_outputs": [str(o)[:80] for o in
                                     (raw["spec"].get("required_outputs") or [])[:4]],
                "max_rounds": int(raw["spec"].get("max_rounds") or DEFAULT_MAX_ROUNDS),
            })
        except Exception:  # noqa: BLE001 — 坏条目跳过（诚实降级）
            continue
        out.append(DelegationRecord(
            delegation_id=str(raw.get("delegation_id") or "")[:40],
            spec=spec,
            status=str(raw.get("status") or STATUS_PENDING),
            result_summary=str(raw.get("result_summary") or "")[:400],
            refs=[str(r)[:64] for r in (raw.get("refs") or [])[:8]],
            error=str(raw.get("error") or "")[:200],
            lineage=raw.get("lineage") if isinstance(
                raw.get("lineage"), dict) else {},
            attempts=int(raw.get("attempts") or 0),
        ))
    return out


async def _persist_records(
    session_id: str,
    records: List[DelegationRecord],
    *,
    validated_goal: Any,
    merge_by_id: Optional[DelegationRecord] = None,
) -> Optional[List[Dict[str, Any]]]:
    """台账锁内合并写（评审 F5）。

    台账是**事件日志**而非行派生块 —— 行漂移（子代理跑工具本身会改行）
    不应丢弃审计记录：锁内重读 fresh 台账、按 delegation_id 合并本条后
    整体写回；仅 supersede（goal 变化）放弃。"""
    from app.services.session_plan import goal_key, load_session_plan, save_session_plan

    try:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            fresh = await load_session_plan(session_id)
            if fresh is None or not isinstance(fresh.gis_chapter, dict) or lock.lost:
                return None
            if goal_key(fresh.gis_chapter, fresh.user_goal) != validated_goal:
                return None
            fresh_records = _parse_records(
                fresh.gis_chapter.get(DELEGATIONS_KEY))
            if merge_by_id is not None:
                merged = {r.delegation_id: r for r in fresh_records}
                merged[merge_by_id.delegation_id] = merge_by_id
                fresh_records = list(merged.values())
            else:
                # 全量替换语义仅用于调用方已基于 fresh 台账构造的情形：
                # 以 fresh 为基底合并本批（id 覆盖）
                merged = {r.delegation_id: r for r in fresh_records}
                for r in records:
                    merged[r.delegation_id] = r
                fresh_records = list(merged.values())
            fresh_records = fresh_records[-MAX_DELEGATIONS:]
            block = {
                "schema": "delegations.v1",
                "records": [r.to_bounded_dict() for r in fresh_records],
            }
            fresh.gis_chapter[DELEGATIONS_KEY] = block
            await save_session_plan(fresh)
            return block["records"]
    except Exception:  # noqa: BLE001 — 台账失败不阻断委派本身
        logger.warning("[Delegation] persist failed session=%s", session_id,
                       exc_info=True)
        return None


# ── 委派执行 ─────────────────────────────────────────────────────────────


async def delegate(
    session_id: str,
    spec: DelegationSpec,
    *,
    dispatcher: Optional[Any] = None,
) -> DelegationRecord:
    """执行一次受控委派（handoff → dispatcher → 台账回写）。

    ``dispatcher`` 可注入（测试用 fake）；缺省用真实 SubagentDispatcher。
    失败回收：首败且 recovery ``repair`` 预算有余 → 重试一次；再败 →
    failed + 披露（不盲目换 role，不无限对抗）。
    """
    from app.services.session_plan import goal_key, load_session_plan
    from app.services.subagent_roles import get_subagent_role

    # fail-closed：未知角色拒绝（不 spawn）；失败记录入台账（审计面）。
    try:
        get_subagent_role(spec.role)
    except Exception as exc:  # noqa: BLE001 — 角色校验失败即拒绝
        record = DelegationRecord(
            delegation_id=f"dg-{uuid.uuid4().hex[:8]}",
            spec=spec, status=STATUS_FAILED,
            error=f"unknown role: {exc}"[:200],
        )
        plan = await load_session_plan(session_id)
        if plan is not None and isinstance(plan.gis_chapter, dict):
            from app.services.session_plan import goal_key as _gk

            await _persist_records(
                session_id, [record],
                validated_goal=_gk(plan.gis_chapter, plan.user_goal),
                merge_by_id=record)
        return record

    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return DelegationRecord(
            delegation_id=f"dg-{uuid.uuid4().hex[:8]}",
            spec=spec, status=STATUS_FAILED, error="no gis chapter",
        )
    validated_goal = goal_key(plan.gis_chapter, plan.user_goal)

    record = DelegationRecord(
        delegation_id=f"dg-{uuid.uuid4().hex[:8]}",
        spec=spec, status=STATUS_RUNNING,
    )

    if dispatcher is None:
        from app.services.subagent import SubagentDispatcher

        dispatcher = SubagentDispatcher(_registry_for(), session_id)

    result = None
    for attempt in (1, 2):
        record.attempts = attempt
        try:
            result = await dispatcher.run(
                task=spec.task,
                max_rounds=spec.max_rounds,
                role=spec.role,
            )
        except Exception as exc:  # noqa: BLE001 — 派发异常按失败处理
            result = None
            record.error = str(exc)[:200]
        if result is not None and getattr(result, "success", False):
            record.status = STATUS_COMPLETED
            record.result_summary = str(result.summary or "")[:400]
            record.refs = [str(r)[:64] for r in (result.refs or [])[:8]]
            record.lineage = dict(result.lineage or {})
            record.error = ""
            break
        record.error = str(getattr(result, "error", None) or record.error
                           or "subagent failed")[:200]
        if attempt == 1:
            from app.services.gis_harness.durable_context import (
                LOOP_BUDGETS,
                load_recovery_state,
            )

            recovery = await load_recovery_state(session_id)
            repair_budget = int(LOOP_BUDGETS.get("repair", 2))
            remaining = max(
                0, repair_budget
                - int((recovery.get("loops") or {}).get("repair") or 0))
            if remaining <= 0:
                # 预算尽 → 不重试（诚实放弃）
                record.status = STATUS_FAILED
                break
            record.status = STATUS_RUNNING  # 将重试
        else:
            record.status = STATUS_FAILED

    # 台账合并写（评审 F5：子代理本身会改行 —— rows 漂移不丢审计记录）
    await _persist_records(session_id, [record],
                           validated_goal=validated_goal,
                           merge_by_id=record)
    return record


def _registry_for():
    from app.tools.registry import ToolRegistry

    return ToolRegistry()


# ── 生产驱动点（可选；默认关）────────────────────────────────────────────


async def delegate_cartography_qa(
    session_id: str,
    *,
    findings_codes: List[str],
    product_revision: int,
) -> Optional[DelegationRecord]:
    """finalizer 批评面的可选 QA 复核（env GIS_HARNESS_DELEGATION=1 开启）。

    幂等：同一成品 revision 至多委派一次（台账查重）。关闭 → None。"""
    if not delegation_enabled() or not session_id:
        return None
    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return None
    stored = plan.gis_chapter.get(DELEGATIONS_KEY)
    for rec in _parse_records(stored):
        if (rec.spec.plan_step == f"qa:{product_revision}"
                and rec.status in (STATUS_RUNNING, STATUS_COMPLETED)):
            return None  # 幂等
    spec = DelegationSpec(
        role="cartography_reviewer",
        task=(
            "复核当前地图成品的批评发现：" + ",".join(findings_codes[:6])
            + "。只评估制图质量语义（图例/标注/配色/完整性），不重跑分析。"
        ),
        plan_step=f"qa:{product_revision}",
        required_outputs=["复核结论", "建议修复项（如有）"],
    )
    return await delegate(session_id, spec)


def format_delegations_line(chapter: Optional[Dict[str, Any]]) -> str:
    """[GIS Delegation] 单行投影（LLM 面；台账缺席零漂移）。"""
    if not isinstance(chapter, dict):
        return ""
    stored = chapter.get(DELEGATIONS_KEY)
    records = _parse_records(stored)
    if not records:
        return ""
    active = [r for r in records if r.status == STATUS_RUNNING]
    done = [r for r in records if r.status == STATUS_COMPLETED]
    failed = [r for r in records if r.status == STATUS_FAILED]
    parts = [f"[GIS Delegation] active={len(active)}"
             f" done={len(done)} failed={len(failed)}"]
    for r in active[:2]:
        parts.append(f"running:{r.spec.role}({r.spec.plan_step[:24]})")
    return " ".join(parts)[:480]


__all__ = [
    "DELEGATIONS_KEY",
    "MAX_DELEGATIONS",
    "DelegationSpec",
    "DelegationRecord",
    "delegation_enabled",
    "delegate",
    "delegate_cartography_qa",
    "format_delegations_line",
]
