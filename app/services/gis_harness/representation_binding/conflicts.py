"""Conflict policy reason-code matrix（ADR-0219 C5）。

双向桥的冲突裁决表：每个冲突情形 → 稳定 reason code + 胜者 + 动作 +
披露。**禁止 silent last-write-wins**：任何拒绝/跳过/回退都必须携带
本表中的 code（binding 的 ``error_code``、投影的 errors 列表回传）。

裁决总原则（与 W15/#1070/ADR-0215 D-05 同源）：
user 显式决策 > requirement 用户锁 > agent 建议 > system 修复。
CAS 过期不是冲突而是并发事实（SUPERSEDED，可携新基线重试）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional

ConflictWinner = Literal["user", "agent", "system", "none"]
ConflictAction = Literal[
    "refuse",            # 拒绝（整笔，不部分提交）
    "retry_with_fresh_revision",
    "skip_disclosed",    # 跳过并披露（非错误）
    "rebase",            # 语义重定基（undo 路径）
    "apply",             # 放行
    "defer_to_user",     # 需用户裁定（不自动执行）
]

#: 稳定 reason codes（机器可读；跨轮稳定，不做自由文本）。
REASON_CODES = {
    # ── C3（requirement → MapSpec）方向 ───────────────────────────────
    "binding_target_locked": "agent 绑定命中 workbench 用户锁（user-wins；引擎单码 layer_locked）",
    "binding_user_wins_presentation": "agent 绑定试图反转用户最后显隐决策（UserPresentationGuard）",
    "binding_stale_revision": "expected_revision 过期（CAS；非错误，携新基线重试）",
    "binding_target_absent": "绑定目标（层/组件）在权威 spec 中不存在",
    "binding_ineligible_representation": "用户 pin 的表达与数据/renderer 不兼容（grammar face 披露回退）",
    "binding_duplicate_replay": "同 fingerprint 幂等重放（引擎 dedup 命中，revision 不推进）",
    "binding_document_superseded": "requirement 文档已被超替（新任务），旧绑定不落地",
    "binding_engine_refused": "绑定被 MapSpec 引擎拒绝（增量非法/冲突、质量门禁、自愈未收敛、不支持的意图等；引擎信息经 disclosure 透传）",
    # ── C4（MapSpec → Requirement）方向 ───────────────────────────────
    "projection_non_user_origin": "非 user origin 的 mutation 不投影为用户偏好",
    "projection_ephemeral": "presentation-ephemeral 面（pan/zoom/opacity-only）不进入 requirement",
    "projection_system_repair": "system repair 不冒充用户偏好",
    "projection_unknown_kind": "taxonomy 未登记的 intent kind（fail-closed 不投影）",
    "projection_document_absent": "requirement 文档不存在（首次建图前），本次不投影",
    "projection_document_superseded": "requirement 文档已超替封存，不复活旧文档",
    "projection_patch_stale": "requirement patch CAS 过期（绝对值集语义，下轮投影自愈）",
    "projection_patch_refused": "requirement patch 被拒（user-wins/锁/校验，错误码透传）",
    "projection_duplicate_mutation": "duplicate mutation_id 幂等重放（首次提交已投影）",
    # ── 双向共用 ──────────────────────────────────────────────────────
    "conflict_two_tabs_cas": "双标签页并发：后到者 CAS 过期（superseded 409 回灌真相）",
    "conflict_undo_rebaseline": "用户 undo：requirement 语义面按提交后权威 spec 重定基（防幽灵锁）",
    "conflict_user_unlock": "用户解锁后 agent 可再建议（锁移除即放行）",
    "conflict_finalizer_user_disabled": "finalizer 不得自动恢复用户显式禁用的组件（引擎 AUTO_SAFE 抑制 + requirement 记录）",
}


@dataclass(frozen=True)
class ConflictResolution:
    """一次冲突的裁决（确定性；code ∈ REASON_CODES）。"""

    code: str
    winner: ConflictWinner
    action: ConflictAction
    disclosure: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "winner": self.winner,
            "action": self.action,
            "disclosure": self.disclosure,
        }


def resolve_conflict(code: str, *, detail: str = "") -> ConflictResolution:
    """reason-code matrix 的单一裁决点（纯函数）。

    每个输入 code 恰一个裁决；未知 code fail-closed → refuse（绝不让
    未登记的冲突静默通过）。
    """
    matrix: Dict[str, ConflictResolution] = {
        "binding_target_locked": ConflictResolution(
            "binding_target_locked", "user", "refuse",
            "图层/组件由用户锁定；如需变更请先请求用户解锁。"),
        "binding_user_wins_presentation": ConflictResolution(
            "binding_user_wins_presentation", "user", "refuse",
            "显示状态由用户最后设定；保留用户值继续，不自动反转。"),
        "binding_stale_revision": ConflictResolution(
            "binding_stale_revision", "none", "retry_with_fresh_revision",
            "并发写入推进了 revision；以新基线重试（有界次数）。"),
        "binding_target_absent": ConflictResolution(
            "binding_target_absent", "none", "skip_disclosed",
            "绑定目标已不存在；跳过并披露，不视为错误。"),
        "binding_ineligible_representation": ConflictResolution(
            "binding_ineligible_representation", "user", "defer_to_user",
            "用户 pin 与数据能力不兼容；披露候选与理由，交由用户改选。"),
        "binding_duplicate_replay": ConflictResolution(
            "binding_duplicate_replay", "none", "apply",
            "同 fingerprint 重放命中幂等；返回存证回执，不重复落盘。"),
        "binding_engine_refused": ConflictResolution(
            "binding_engine_refused", "system", "refuse",
            "绑定被引擎校验/门禁拒绝（增量非法或冲突、质量门禁、"
            "自愈未收敛、意图不支持）；按 disclosure 指引修正后重试。"),
        "binding_document_superseded": ConflictResolution(
            "binding_document_superseded", "user", "skip_disclosed",
            "需求文档已超替；携带用户态的新文档是后续绑定的输入。"),
        "projection_non_user_origin": ConflictResolution(
            "projection_non_user_origin", "agent", "skip_disclosed",
            "agent/system 的地图变更不写用户偏好。"),
        "projection_ephemeral": ConflictResolution(
            "projection_ephemeral", "none", "skip_disclosed",
            "呈现瞬态不进入需求文档。"),
        "projection_system_repair": ConflictResolution(
            "projection_system_repair", "system", "skip_disclosed",
            "自动修复不冒充用户偏好；修复事实留在 MapSpec/provenance。"),
        "projection_unknown_kind": ConflictResolution(
            "projection_unknown_kind", "none", "skip_disclosed",
            "未登记分类的意图不投影（fail-closed）。"),
        "projection_document_absent": ConflictResolution(
            "projection_document_absent", "none", "skip_disclosed",
            "需求文档尚未建立；不为此创建文档。"),
        "projection_document_superseded": ConflictResolution(
            "projection_document_superseded", "user", "skip_disclosed",
            "文档已超替封存；新文档由 ensure_document 承接携带语义。"),
        "projection_patch_stale": ConflictResolution(
            "projection_patch_stale", "none", "apply",
            "投影写绝对值集，CAS 过期由下一次投影自愈收敛。"),
        "projection_patch_refused": ConflictResolution(
            "projection_patch_refused", "user", "skip_disclosed",
            "投影 patch 被拒（user-wins/校验）；错误码已上报。"),
        "projection_duplicate_mutation": ConflictResolution(
            "projection_duplicate_mutation", "none", "skip_disclosed",
            "重放回执；首次提交已完成投影。"),
        "conflict_two_tabs_cas": ConflictResolution(
            "conflict_two_tabs_cas", "none", "retry_with_fresh_revision",
            "并发标签页以后到者 CAS 过期收场；409 回灌权威真相。"),
        "conflict_undo_rebaseline": ConflictResolution(
            "conflict_undo_rebaseline", "user", "rebase",
            "用户 undo 后按提交后权威 spec 重定基语义面。"),
        "conflict_user_unlock": ConflictResolution(
            "conflict_user_unlock", "user", "apply",
            "锁已由用户移除；agent 建议恢复常规裁决。"),
        "conflict_finalizer_user_disabled": ConflictResolution(
            "conflict_finalizer_user_disabled", "user", "refuse",
            "组件由用户显式禁用；finalizer 不自动补回（硬标准要求时须请求/披露）。"),
    }
    resolution = matrix.get(code)
    if resolution is None:
        return ConflictResolution(
            code, "none", "refuse", f"未知冲突 code（fail-closed 拒绝）：{detail or code}")
    if detail:
        return ConflictResolution(
            resolution.code, resolution.winner, resolution.action,
            f"{resolution.disclosure} [{detail[:120]}]")
    return resolution


def disclosure_for(code: str) -> str:
    """code → 披露文本（REASON_CODES 注册面）。"""
    return REASON_CODES.get(code, f"未注册 reason code: {code}")


#: 引擎回执码 → 冲突矩阵登记码（C5「禁止 silent」的登记闭环）。
#: 已知引擎码显式登记；未登记码（含 ``HEAL_*`` 族）统一落已注册的通用
#: 拒绝码 —— 透传 off-vocabulary 引擎码会让按码聚合的遥测/披露漏类。
_ENGINE_CODE_TO_MATRIX: Dict[str, str] = {
    "layer_locked": "binding_target_locked",
    "user_wins_presentation": "binding_user_wins_presentation",
    "workbench_delta_invalid": "binding_engine_refused",
    "workbench_delta_conflict": "binding_engine_refused",
    "quality_gate_blocked": "binding_engine_refused",
    "UNSUPPORTED_MUTATION_INTENT": "binding_engine_refused",
    "HEAL_CONVERGENCE_EXHAUSTED": "binding_engine_refused",
    "HEAL_PLAN_EMPTY": "binding_engine_refused",
}


def resolve_from_result(
    *,
    is_error: bool,
    superseded: bool,
    duplicate: bool,
    error_code: Optional[str],
) -> Optional[str]:
    """MapSpecResult → C3 方向的 reason code（事务回执 → 冲突矩阵入口）。"""
    if duplicate:
        return "binding_duplicate_replay"
    if superseded:
        return "binding_stale_revision"
    if is_error:
        engine_code = error_code or ""
        if engine_code.startswith("HEAL_"):
            return "binding_engine_refused"
        return _ENGINE_CODE_TO_MATRIX.get(engine_code, "binding_engine_refused")
    return None
