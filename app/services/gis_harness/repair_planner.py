"""Repair Planner（V6 Wave 10–11）—— finding → 修复分类 → 有界修复计划。

定位（Prompt §17/§18/§19 + 04-unified-findings.md）：

- 输入是 **UnifiedFinding 投影**（W6 单一投影面）—— 不再各 domain 各自
  决定怎么修；
- 本模块只做**分类、计划、护栏**：执行仍归既有通道（runtime_repair 的
  reassert/visibility、finalizer 的组件修复、Pi 的重执行）——不建第三
  修复通道（红线）；
- 修复安全分级（§18）：``safe_automatic`` / ``semantics_preserving`` /
  ``presentation_only`` / ``requires_user_approval`` / ``not_allowed``；
  用户锁（lockedLayerIds 等）与用户 override 是硬约束 —— 命中即
  ``not_allowed``，任何路径不得绕过（user-wins，ADR-0072/0118 D2）；
- 防无限循环（§19/W11）：finding 指纹 + 状态 epoch + 尝试计数账本；
  同一 finding 在同一 epoch 重复出现 → ``no_progress``；尝试计数达上限
  → ``repair_exhausted`` → 披露（abort_with_disclosure），绝不无限对抗。

全部纯函数（账本读写由服务包装做 IO）；确定性：同输入同计划。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from app.services.gis_harness.completion.unified_findings import UnifiedFinding
from app.services.gis_harness.workflow_instance import canonical_fingerprint

logger = logging.getLogger(__name__)

#: 修复类别词表（§17；小写对齐既有 RemediationAction 风格；执行归属见
#: 各类的 executor 字段 —— 词表是分类，不是新执行通道）。
REPAIR_CLASSES = (
    "retry_tool",            # 同参重试工具
    "reprofile_data",        # 重画像数据（深挖 DatasetProfile）
    "reproject",             # 重投影（CRS 类失败）
    "reparameterize",        # 改参重算
    "reselect_method",       # 换方法/工具（fallback）
    "recompute_node",        # 单节点重算（W5 reuse 语义）
    "recompute_subgraph",    # 受影响子图重算（W4 RecomputePlan）
    "rebuild_layer",         # 重建图层（spec 层缺失/未挂载）
    "reapply_style",         # 重放样式
    "relayout_component",    # 组件重排（重叠/越界）
    "regenerate_legend",     # 图例重生成
    "regenerate_labels",     # 标注重生成
    "rerender",              # 重渲染（runtime reassert）
    "reobserve",             # 重观测（等前端遥测收敛）
    "ask_user",              # 需要用户裁决
    "abort_with_disclosure", # 预算耗尽/不可自动 → 诚实披露
)

#: 输出侧词表断言用集合（review A/MAJOR-3：planner 输出的 repair_class
#: 只许是本表 16 类，外来词一律在分类侧归一化）。
_REPAIR_CLASS_SET = frozenset(REPAIR_CLASSES)

#: 修复安全分级（§18）。
SAFETY_CLASSES = (
    "safe_automatic",         # 可自动（幂等呈现/重观测/重试）
    "semantics_preserving",   # 可自动且保持语义（同方法同参重算、重投影）
    "presentation_only",      # 只动呈现（布局/样式）
    "requires_user_approval", # 语义变更需用户批准（换方法/改参）
    "not_allowed",            # 禁止（用户锁/override/预算耗尽）
)

#: W11 账本（map_state 键；session 级 ephemeral，与 runtime repair 同生命周期）。
REPAIR_LOOP_KEY = "_repair_loop_v6"
#: 单 finding 尝试上限（与 REMEDIATION_POLICY 全表 max ≤3 同量级）。
MAX_REPAIR_ATTEMPTS_PER_FINDING = 3
#: 账本容量（finding 指纹数；LRU 由调用方截断控制）。
_MAX_LOOP_ENTRIES = 32
#: 单次计划动作上限。
_MAX_PLAN_ACTIONS = 6

LOOP_OK = "ok"
LOOP_NO_PROGRESS = "no_progress"
LOOP_EXHAUSTED = "repair_exhausted"


@dataclass
class RepairAction:
    """一条分类后的修复动作（bounded；执行归 executor 列明的既有通道）。"""

    repair_class: str                  # ⊆ REPAIR_CLASSES
    safety: str                        # ⊆ SAFETY_CLASSES
    target: str = ""                   # node_id / layer_id / component_id / ref
    domain: str = ""                   # UnifiedFinding.domain
    code: str = ""                     # UnifiedFinding.code
    executor: str = "none"             # runtime_repair | finalizer | pi_recompute
                                       # | quality_loop | user | none
    loop_status: str = LOOP_OK         # W11 护栏裁决
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repair_class": self.repair_class,
            "safety": self.safety,
            "target": self.target[:64],
            "domain": self.domain[:32],
            "code": self.code[:64],
            "executor": self.executor,
            "loop_status": self.loop_status,
            "detail": self.detail[:160],
        }


@dataclass
class RepairPlan:
    """一次修复计划（bounded、可解释）。"""

    actions: List[RepairAction] = field(default_factory=list)
    deferred: List[RepairAction] = field(default_factory=list)   # requires_user_approval
    refused: List[RepairAction] = field(default_factory=list)    # not_allowed
    exhausted: List[str] = field(default_factory=list)           # finding 指纹（耗尽）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "actions": [a.to_dict() for a in self.actions[:_MAX_PLAN_ACTIONS]],
            "deferred": [a.to_dict() for a in self.deferred[:4]],
            "refused": [a.to_dict() for a in self.refused[:4]],
            "exhausted": [e[:32] for e in self.exhausted[:8]],
        }


def finding_fingerprint(uf: UnifiedFinding) -> str:
    """finding 指纹（domain+code+entity；§19 防循环键）。"""
    return canonical_fingerprint({
        "domain": uf.domain, "code": uf.code, "entity": uf.affected_entity,
    })


# ── 分类映射（确定性表驱动；code 精确 → scope 兜底 → reobserve 保底）──────

# code → (repair_class, safety, executor)
_CODE_MAP: Dict[str, Tuple[str, str, str]] = {
    "artifact_missing": ("recompute_node", "semantics_preserving", "pi_recompute"),
    "artifact_expired": ("recompute_node", "semantics_preserving", "pi_recompute"),
    "empty_result": ("reselect_method", "requires_user_approval", "pi_recompute"),
    "crs_not_wgs84": ("reproject", "semantics_preserving", "pi_recompute"),
    "needs_execution": ("recompute_subgraph", "semantics_preserving", "pi_recompute"),
    "execution_blocked": ("recompute_subgraph", "requires_user_approval", "pi_recompute"),
    "no_result_layer": ("rebuild_layer", "safe_automatic", "finalizer"),
    "layer_missing": ("rebuild_layer", "safe_automatic", "finalizer"),
    "source_missing": ("rerender", "safe_automatic", "runtime_repair"),
    "layer_hidden": ("reapply_style", "presentation_only", "finalizer"),
    "layer_transparent": ("reapply_style", "presentation_only", "finalizer"),
    "component_missing": ("rerender", "safe_automatic", "finalizer"),
    "component_disabled": ("rerender", "safe_automatic", "finalizer"),
    "layout_conflict": ("relayout_component", "presentation_only", "quality_loop"),
    "orphan_binding": ("rebuild_layer", "safe_automatic", "finalizer"),
    "semantic_legend_missing": ("regenerate_legend", "safe_automatic", "finalizer"),
    "semantic_legend_mismatch": ("regenerate_legend", "safe_automatic", "finalizer"),
    "title_missing_report_product": ("rerender", "safe_automatic", "finalizer"),
    "chart_data_missing": ("reobserve", "safe_automatic", "runtime_repair"),
    "runtime_node_stale": ("recompute_node", "semantics_preserving", "pi_recompute"),
    "runtime_node_failed": ("retry_tool", "safe_automatic", "pi_recompute"),
}

_SCOPE_FALLBACK: Dict[str, Tuple[str, str, str]] = {
    "node": ("recompute_node", "semantics_preserving", "pi_recompute"),
    "data": ("reprofile_data", "semantics_preserving", "pi_recompute"),
    "layer": ("rerender", "safe_automatic", "runtime_repair"),
    "source": ("rerender", "safe_automatic", "runtime_repair"),
    "component": ("rerender", "safe_automatic", "finalizer"),
    "chart": ("reobserve", "safe_automatic", "runtime_repair"),
    "workflow": ("recompute_subgraph", "semantics_preserving", "pi_recompute"),
    "map": ("reobserve", "safe_automatic", "runtime_repair"),
    "export": ("reobserve", "safe_automatic", "none"),
}


def classify_repair(
    uf: UnifiedFinding,
    *,
    locked_entities: FrozenSet[str] = frozenset(),
    user_overridden: FrozenSet[str] = frozenset(),
) -> RepairAction:
    """单条 finding → 修复动作（表驱动；锁/override 硬约束优先于一切）。

    user-wins：受影响实体被用户锁定或已有用户 override → not_allowed，
    无论分类表怎么说（§18/§33）。锁命中走统一 guard
    （lifecycle_engine.is_entity_locked），not_allowed 语义不变。
    """
    # W15 锁下沉：锁判断复用统一 guard（函数内懒导入，避免循环依赖）。
    from app.services.mapspec.lifecycle_engine import (
        LOCK_CONFLICT_CODE,
        is_entity_locked,
    )
    code_hit = uf.code in _CODE_MAP
    scope_hit = uf.scope in _SCOPE_FALLBACK
    if code_hit:
        repair_class, safety, executor = _CODE_MAP[uf.code]
    elif scope_hit:
        repair_class, safety, executor = _SCOPE_FALLBACK[uf.scope]
    else:
        # 双缺席保守缺省（review B/Q2）：未知码＋未知域不猜执行语义 ——
        # not_allowed 进 refused 披露，绝不静默丢弃。
        repair_class, safety, executor = ("reobserve", "not_allowed", "none")
    # 软视觉发现：一律需用户裁决（§13/§18——评估器只产 finding，确定性
    # 层之外的语义/呈现改动不自动执行）。仅已知码/域才软化 —— 双缺席
    # 保守缺省不降级（未知视觉码不断言可裁决执行）。
    if uf.domain == "visual" and (code_hit or scope_hit):
        safety = "requires_user_approval"
        executor = "user"
    detail_note = ""
    if uf.degradation_only and uf.severity != "error":
        # 降级披露面（导出诊断/info）不产生自动动作。repair_class 槽只装
        # 本表词表（review A/MAJOR-3：视觉评估器等外来 repair_class 如
        # retry/replan 是 RemediationAction 词 —— 此处归一化丢弃，原文留
        # detail 备查，不跨词表混装）。
        executor = "none"
        safety = "safe_automatic"
        if uf.repair_class and uf.repair_class not in _REPAIR_CLASS_SET:
            detail_note = f" | orig_repair={uf.repair_class[:32]}"
    action = RepairAction(
        repair_class=repair_class,
        safety=safety,
        target=uf.affected_entity,
        domain=uf.domain,
        code=uf.code,
        executor=executor,
        detail=(uf.evidence + detail_note)[:160],
    )
    entity = uf.affected_entity
    if entity and (
        is_entity_locked(entity, locked_entities) or entity in user_overridden
    ):
        action.safety = "not_allowed"
        action.executor = "none"
        action.detail = (
            action.detail + f" | user-locked/overridden [{LOCK_CONFLICT_CODE}] — user-wins"
        )[:160]
    return action


def evaluate_repair_loop(
    ledger: Optional[Dict[str, Any]],
    fingerprints: List[str],
    *,
    state_epoch: str,
    max_attempts: int = MAX_REPAIR_ATTEMPTS_PER_FINDING,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """W11 防循环护栏（纯函数；账本读写归调用方）。

    ``state_epoch``：状态代次（runtime_revision:mapspec_revision）—— finding
    指纹已见且 epoch 未推进 = 同一 finding+state 的重复（§19）。

    返回 ``(new_ledger, verdicts)``：verdicts fp → ok/no_progress/
    repair_exhausted。账本条目结构 {attempts, epoch_first, epoch_last}。
    """
    new_ledger: Dict[str, Dict[str, Any]] = dict(ledger or {})
    verdicts: Dict[str, str] = {}
    for fp in fingerprints[:_MAX_LOOP_ENTRIES]:
        entry = new_ledger.get(fp)
        if not isinstance(entry, dict):
            entry = {"attempts": 0, "epoch_first": state_epoch, "epoch_last": ""}
        attempts = int(entry.get("attempts") or 0)
        if attempts >= max_attempts:
            verdicts[fp] = LOOP_EXHAUSTED
            new_ledger[fp] = entry
            continue
        if entry.get("epoch_last") == state_epoch and attempts >= 1:
            verdicts[fp] = LOOP_NO_PROGRESS  # 同 finding 同 epoch 重复 —— 无进展
        else:
            verdicts[fp] = LOOP_OK
        entry["attempts"] = attempts + 1
        entry["epoch_last"] = state_epoch
        new_ledger[fp] = entry
    # 容量截断（确定性：保留最近更新语义不可得 —— 按键名序保守截断）。
    if len(new_ledger) > _MAX_LOOP_ENTRIES:
        for key in sorted(new_ledger)[:-_MAX_LOOP_ENTRIES]:
            del new_ledger[key]
    return new_ledger, verdicts


def plan_repairs(
    findings: List[UnifiedFinding],
    *,
    locked_entities: FrozenSet[str] = frozenset(),
    user_overridden: FrozenSet[str] = frozenset(),
    loop_verdicts: Optional[Dict[str, str]] = None,
) -> RepairPlan:
    """findings → 有界修复计划（确定性序：安全级优先、domain 序稳定）。

    - safe_automatic / semantics_preserving / presentation_only → actions；
    - requires_user_approval → deferred（披露等用户）；
    - not_allowed → refused（user-wins 披露）；
    - loop exhausted → 改写 abort_with_disclosure（executor none）并记
      exhausted；no_progress → 保留动作但如实标注（披露面可见）。
    - 动作槽满（actions ≥ 6）仅停 actions 追加 —— deferred / refused /
      exhausted 继续分类（review A/MAJOR-1：break 整循环会吞掉槽满之后
      的 refused/deferred，锁披露丢失）。
    """
    plan = RepairPlan()
    seen: set = set()
    for uf in findings:
        key = (uf.domain, uf.code, uf.affected_entity)
        if key in seen:
            continue
        seen.add(key)
        action = classify_repair(
            uf, locked_entities=locked_entities, user_overridden=user_overridden)
        # 输出侧词表断言（review A/MAJOR-3）：planner 输出只许是 16 类词表。
        assert action.repair_class in _REPAIR_CLASS_SET, action.repair_class
        fp = finding_fingerprint(uf)
        verdict = (loop_verdicts or {}).get(fp, LOOP_OK)
        action.loop_status = verdict
        if verdict == LOOP_EXHAUSTED:
            action.repair_class = "abort_with_disclosure"
            action.safety = "not_allowed"
            action.executor = "none"
            action.detail = (action.detail + " | repair budget exhausted — disclosure")[:160]
            plan.exhausted.append(fp)
            plan.refused.append(action)
            continue
        if action.safety == "not_allowed":
            plan.refused.append(action)
        elif action.safety == "requires_user_approval":
            plan.deferred.append(action)
        elif action.executor != "none":
            if len(plan.actions) < _MAX_PLAN_ACTIONS:
                plan.actions.append(action)
    return plan


# ── 服务包装（IO 面：mapspec 锁集 + 账本读写）────────────────────────────

async def plan_repairs_for_chapter(
    session_id: str,
    chapter: Dict[str, Any],
    result: Any,
    *,
    mapspec: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """终验块的修复计划（findings 统一投影 → 护栏 → 计划 → 账本落账）。

    返回 ``RepairPlan.to_dict()``（失败/无 finding → None，调用方不写键）。
    账本写 map_state[REPAIR_LOOP_KEY]（session 级 ephemeral，与 runtime
    repair ledger 同生命周期）；任何失败降级日志，绝不阻断终验。
    """
    from app.services.gis_harness.completion.unified_findings import (
        collect_unified_findings,
    )
    from app.services.gis_harness.runtime_bridge import WORKFLOW_RUNTIME_KEY

    runtime_block = chapter.get(WORKFLOW_RUNTIME_KEY)
    unified = collect_unified_findings(result=result, runtime_block=runtime_block)
    blocking = [u for u in unified if u.blocks_completion or u.severity == "error"]
    if not blocking:
        return None

    # W15 锁下沉：锁集走统一 guard（lockedLayerIds + lockedComponentIds，
    # 缺席=空；§33 硬约束输入）。
    from app.services.mapspec.lifecycle_engine import (
        locked_component_ids_of,
        locked_layer_ids_of,
    )
    locked: FrozenSet[str] = frozenset()
    if isinstance(mapspec, dict):
        locked = frozenset(
            locked_layer_ids_of(mapspec) + locked_component_ids_of(mapspec)
        )

    # 状态 epoch：runtime 块 revision + mapspec mutation revision。
    runtime_rev = 0
    if isinstance(runtime_block, dict):
        try:
            runtime_rev = int(runtime_block.get("runtime_revision") or 0)
        except (TypeError, ValueError):
            runtime_rev = 0
    epoch = f"{runtime_rev}"
    from app.services.session_data import session_data_manager

    try:
        map_state = await session_data_manager.get_map_state(session_id)
    except Exception:  # noqa: BLE001 — 读失败按无账本处理
        map_state = None
    if isinstance(map_state, dict):
        epoch = f"{runtime_rev}:{int(map_state.get('_cartographic_mutation_revision') or 0)}"
        ledger = map_state.get(REPAIR_LOOP_KEY)
        ledger = ledger if isinstance(ledger, dict) else None
    else:
        ledger = None

    fps = [finding_fingerprint(u) for u in blocking]
    new_ledger, verdicts = evaluate_repair_loop(ledger, fps, state_epoch=epoch)
    plan = plan_repairs(blocking, locked_entities=locked, loop_verdicts=verdicts)
    try:
        await session_data_manager.set_map_state(
            session_id, REPAIR_LOOP_KEY, new_ledger)
    except Exception:  # noqa: BLE001 — 账本落账失败只影响轮数记忆
        logger.warning("[RepairPlanner] ledger persist failed session=%s", session_id)
    return plan.to_dict()


__all__ = [
    "REPAIR_CLASSES",
    "SAFETY_CLASSES",
    "REPAIR_LOOP_KEY",
    "MAX_REPAIR_ATTEMPTS_PER_FINDING",
    "LOOP_OK",
    "LOOP_NO_PROGRESS",
    "LOOP_EXHAUSTED",
    "RepairAction",
    "RepairPlan",
    "classify_repair",
    "evaluate_repair_loop",
    "finding_fingerprint",
    "plan_repairs",
    "plan_repairs_for_chapter",
]
