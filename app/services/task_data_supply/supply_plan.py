"""WP3 · 受预算的多候选供给计划（Task Supply Plan）。

GOAL-20261126-02：把覆盖矩阵的结论组合成**角色级供给计划**——声明「每
个角色从哪条通道、以什么投影取数、花多少、缺了怎么办」，并把执行委派给
既有数据面（ADS PlanCompiler → data_fabric materialization → ref 铸造）。

红线（任务书 C/E 节）：
- **不新建取数执行引擎**：步骤携带对 ADS ``PlanRequest`` 的投影
  （``ads_request``），执行由既有生产数据面完成（WP4 工具接线）；
- 优先序：已有合格绑定数据 → 已配置本地/远程来源 → 派生 → 澄清/上传
  （与 workflow_v4 ``ACQUISITION_ALTERNATIVES`` 优先序一致，不复制词表）；
- 比较候选**适用性**（覆盖/资格/口径）而非简单低价排序；
- 部分失败/超预算/覆盖缺口 → 显式替代方案（缩小范围/保留缺口/换数据/
  上传/接受超支），**绝不静默采样、填补、合并异质数据**；
- 幂等：``plan_id`` = 确定性哈希（requirement_id + 角色×候选×版本×选择）；
  重复请求同计划；候选版本漂移 → 指纹变化，旧证明不复用；
- 局部失败诚实分流：``partial`` 保留合法产物 ref 与收据，状态可见。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.task_data_supply.coverage import (
    COVER_DECLARED_ONLY,
    COVER_NONE,
    COVER_PARTIAL,
    COVER_UNKNOWN,
    CoverageCandidate,
    CoverageMatrix,
)
from app.services.task_data_supply.requirement import TaskDataRequirement

# ── 封闭词表 ─────────────────────────────────────────────────────────────

PLAN_ACTION_USE_BOUND = "use_bound"      # 已绑定合格数据，零获取成本
PLAN_ACTION_ACQUIRE = "acquire"          # 从已配置来源获取（ADS 投影）
PLAN_ACTION_DERIVE = "derive"            # 由既有能力派生
PLAN_ACTION_CLARIFY = "clarify"          # 需要澄清/更多搜索
PLAN_ACTION_USER_UPLOAD = "user_upload"  # 必须用户提供

STEP_ACTIONS = (PLAN_ACTION_USE_BOUND, PLAN_ACTION_ACQUIRE,
                PLAN_ACTION_DERIVE, PLAN_ACTION_CLARIFY,
                PLAN_ACTION_USER_UPLOAD)

PLAN_STATES = ("draft", "requires_confirmation", "ready",
               "executing", "completed", "partial", "cancelled")

#: 用户可选决策（封闭词表；可能改变统计口径的决策必须显式确认）。
CHOICE_KINDS = (
    "keep_gap",        # 保留缺口继续（缺口进入披露）
    "shrink_scope",    # 缩小研究区
    "switch",          # 换候选（switch:<candidate_id>）
    "accept_exceed",   # 接受超预算（显式确认）
    "upload",          # 走上传
    "search_more",     # 先继续搜索已配置通道（步骤保持 clarify，执行时
                       # 类型化拒绝并给出指引——诚实不自动执行）
)

_STEP_OUTCOMES = ("pending", "completed", "partial", "failed", "cancelled")

_MAX_STEPS = 16
_MAX_ALTERNATIVES = 5


def _cost_of(cand: Optional[CoverageCandidate]) -> Dict[str, Any]:
    """候选成本提示 → 有界三元组（rows/bytes/latency_ms）。"""
    if cand is None or not cand.cost_hint:
        return {"rows": None, "bytes": None, "latency_ms": None}
    hint = cand.cost_hint
    out: Dict[str, Any] = {}
    for k in ("rows", "bytes", "latency_ms"):
        v = hint.get(k)
        out[k] = int(v) if isinstance(v, (int, float)) and v >= 0 else None
    return out


@dataclass
class SupplyStep:
    """一个角色的供给步骤（声明 + ADS 投影 + 替代方案 + 结果回写）。"""
    role: str
    action: str
    candidate_id: str = ""
    channel: str = ""
    source_ref: str = ""       # fabric 通道：source_id（执行面 id 解析用）
    ads_request: Dict[str, Any] = field(default_factory=dict)
    cost_estimate: Dict[str, Any] = field(default_factory=dict)
    alternatives: List[Dict[str, str]] = field(default_factory=list)
    disclosures: List[str] = field(default_factory=list)
    numeric_stats_qualified: bool = False
    outcome: str = "pending"
    receipt_ref: str = ""
    rows_written: Optional[int] = None
    user_choice: str = ""
    dedup_key: str = ""    # plan_id + role（执行层幂等去重）
    step_fp: str = ""      # 角色×候选×版本×投影 指纹（版本漂移→新键）

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role[:32],
            "action": self.action,
            "candidate_id": self.candidate_id[:96],
            "channel": self.channel[:32],
            "source_ref": self.source_ref[:96],
            "ads_request": {
                k: v for k, v in list(self.ads_request.items())[:8]
            },
            "cost_estimate": self.cost_estimate,
            "alternatives": [
                {k: str(v)[:120] for k, v in a.items()}
                for a in self.alternatives[:_MAX_ALTERNATIVES]
            ],
            "disclosures": [d[:200] for d in self.disclosures[:4]],
            "numeric_stats_qualified": self.numeric_stats_qualified,
            "outcome": self.outcome,
            "receipt_ref": self.receipt_ref[:96],
            "rows_written": self.rows_written,
            "user_choice": self.user_choice[:32],
            "dedup_key": self.dedup_key[:96],
            "step_fp": self.step_fp,
        }


@dataclass
class SupplyPlan:
    """整任务供给计划（角色 × 步骤 + 预算 + 状态机）。"""
    requirement: TaskDataRequirement
    steps: List[SupplyStep] = field(default_factory=list)
    plan_id: str = ""
    state: str = "draft"
    budget_status: str = "within"       # within / exceeded / no_cap
    budget_max_bytes: Optional[int] = None
    estimated_total_bytes: Optional[int] = None
    budget_acknowledged: bool = False
    in_flight: bool = False   # 执行互斥旗标（进程内；不持久化语义）

    def step(self, role: str) -> SupplyStep:
        for s in self.steps:
            if s.role == role:
                return s
        raise KeyError(f"SUPPLY_STEP_ROLE_MISSING: {role!r}")

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "state": self.state,
            "budget_status": self.budget_status,
            "budget_max_bytes": self.budget_max_bytes,
            "estimated_total_bytes": self.estimated_total_bytes,
            "budget_acknowledged": self.budget_acknowledged,
            "requirement_id": self.requirement.requirement_id,
            "steps": [s.to_bounded_dict() for s in
                      self.steps[:_MAX_STEPS]],
        }


class SupplyPlanner:
    """计划构建与结果回写（无状态工具对象；plan 本身持有可变结果）。"""

    def record_outcome(
        self,
        plan: SupplyPlan,
        role: str,
        *,
        outcome: str,
        receipt_ref: str = "",
        rows_written: Optional[int] = None,
        disclosure: str = "",
    ) -> None:
        """执行层结果回写（诚实分流：partial 保留合法产物）。"""
        if outcome not in ("completed", "partial", "failed", "cancelled"):
            raise ValueError(f"SUPPLY_OUTCOME_UNKNOWN: {outcome!r}")
        step = plan.step(role)
        step.outcome = outcome
        step.receipt_ref = str(receipt_ref or "")[:96]
        step.rows_written = rows_written
        if disclosure:
            step.disclosures.append(str(disclosure)[:200])
        self._refresh_state(plan)

    def record_cancel(self, plan: SupplyPlan, *,
                      reason: str = "") -> None:
        """整计划取消：已完成/部分产物保留可见，未完成步骤标 cancelled。"""
        for s in plan.steps:
            if s.outcome == "pending":
                s.outcome = "cancelled"
            if reason:
                s.disclosures.append(f"已取消：{str(reason)[:120]}")
        plan.state = "cancelled"

    def _refresh_state(self, plan: SupplyPlan) -> None:
        # R2 评审 B P1：executing 不再作为持久状态（roles 过滤执行会把
        # 计划永久楔死在 executing，后续一律 BUSY）——互斥由
        # execution.execute_plan_steps 的 in-flight 旗标承载，状态机只
        # 表达可恢复的业务终态/可继续态。
        outcomes = {s.outcome for s in plan.steps}
        if outcomes == {"completed"}:
            plan.state = "completed"
        elif "partial" in outcomes or "failed" in outcomes:
            plan.state = "partial"
        elif "cancelled" in outcomes and "pending" not in outcomes:
            plan.state = "cancelled"
        else:
            plan.state = "ready"  # 尚有 pending 且无失败：可继续执行


# ── 构建 ─────────────────────────────────────────────────────────────────

def _choice_parts(choice: str) -> Tuple[str, str]:
    """choice 字符串 → (kind, argument)；kind ∈ CHOICE_KINDS。"""
    if not choice:
        return "", ""
    if ":" in choice:
        kind, arg = choice.split(":", 1)
    else:
        kind, arg = choice, ""
    if kind not in CHOICE_KINDS:
        raise ValueError(
            f"SUPPLY_CHOICE_KIND_UNKNOWN: {choice!r} ∉ "
            f"{','.join(CHOICE_KINDS)}")
    return kind, arg


def build_supply_plan(
    requirement: TaskDataRequirement,
    coverage: CoverageMatrix,
    candidates: List[CoverageCandidate],
    *,
    budget_max_bytes: Optional[int] = None,
    choices: Optional[Dict[str, str]] = None,
) -> SupplyPlan:
    """覆盖矩阵 + 候选 → 供给计划（确定性；执行委派既有数据面）。"""
    choices = dict(choices or {})
    req_roles = {it.role for it in requirement.items}
    for role in choices:
        if role not in req_roles:
            raise ValueError(
                f"SUPPLY_CHOICE_ROLE_UNKNOWN: {role!r} ∉ 需求角色 "
                f"{sorted(req_roles)}")

    cand_by_id = {c.candidate_id: c for c in candidates}
    scope = requirement.task_scope
    eval_by_role: Dict[str, List[Any]] = {}
    for rc in coverage.roles:
        eval_by_role[rc.role] = rc.evaluations

    steps: List[SupplyStep] = []
    plan_parts: List[Dict[str, Any]] = []

    for item in requirement.items[:_MAX_STEPS]:
        role = item.role
        kind, arg = _choice_parts(choices.get(role, ""))
        evals = eval_by_role.get(role, [])

        # 1) 已绑定合格数据 → 零成本使用
        if item.state == "satisfied" and item.bound_ref:
            step = SupplyStep(
                role=role, action=PLAN_ACTION_USE_BOUND,
                candidate_id=item.bound_ref,
                cost_estimate={"rows": 0, "bytes": 0, "latency_ms": 0},
                numeric_stats_qualified=True,
                disclosures=["使用已绑定合格数据（无获取成本）"])
            steps.append(step)
            plan_parts.append({"role": role, "action": step.action,
                               "cand": item.bound_ref})
            continue

        # 2) 用户决策优先：switch / upload / keep_gap / shrink
        if kind == "upload":
            step = SupplyStep(
                role=role, action=PLAN_ACTION_USER_UPLOAD,
                alternatives=[{"kind": "search_more",
                               "note": "仍可继续搜索已配置来源"}],
                disclosures=["用户选择上传该角色数据"],
                user_choice=choices[role])
            steps.append(step)
            plan_parts.append({"role": role, "action": step.action})
            continue
        if kind == "switch":
            if not arg:
                # R1 评审 C P1-2：裸 switch（无候选目标）不得视为已决策
                # ——静默落回 best 会欺骗性放行执行原候选
                raise ValueError(
                    "SUPPLY_CHOICE_SWITCH_TARGET_REQUIRED: switch 决策必须"
                    "带候选目标（switch:<candidate_id>）")
            evals = [e for e in evals if e.candidate_id == arg]
            if not evals:
                raise ValueError(
                    f"SUPPLY_CHOICE_SWITCH_UNKNOWN: {arg!r} 不在该角色"
                    "评估行中")
        if kind == "search_more":
            # R2 评审 A 新 P1：acquire 步骤上的 search_more 必须转为
            # clarify——"继续搜索匹配候选"意味着**不获取当前候选**，
            # 记录决策却照常执行会静默获取用户明确拒绝的口径/缺口数据
            step = SupplyStep(
                role=role, action=PLAN_ACTION_CLARIFY,
                alternatives=[{"kind": "upload",
                               "note": "也可直接由用户提供该角色数据"}],
                disclosures=["用户选择继续搜索匹配候选——当前候选不获取"],
                user_choice=choices[role])
            steps.append(step)
            plan_parts.append({"role": role, "action": step.action,
                               "choice": "search_more"})
            continue
        if kind == "shrink_scope":
            # 缩小范围由调用方以新 TaskScope 重建需求（范围变化可追溯，
            # 指纹随新 scope 变化）；本计划记录该决策供审计
            step = SupplyStep(
                role=role, action=PLAN_ACTION_CLARIFY,
                alternatives=[{"kind": "shrink_scope",
                               "note": "用新范围重建需求合同后再生成计划"}],
                disclosures=["用户选择缩小研究区——范围变更需重新评估覆盖"],
                user_choice=choices[role])
            steps.append(step)
            plan_parts.append({"role": role, "action": step.action,
                               "choice": "shrink_scope"})
            continue

        # 3) 无候选/不可评估 → 澄清 + 搜索/上传
        if not evals:
            step = SupplyStep(
                role=role, action=PLAN_ACTION_CLARIFY,
                alternatives=[
                    {"kind": "search_more",
                     "note": "在更多已配置通道搜索（目录/STAC/受权 provider）"},
                    {"kind": "upload",
                     "note": "由用户提供该数据"},
                ],
                disclosures=["无候选评估行——未搜索 ≠ 不存在"],
                user_choice=choices.get(role, ""))
            steps.append(step)
            plan_parts.append({"role": role, "action": step.action})
            continue

        best = evals[0]
        cand = cand_by_id.get(best.candidate_id)
        fit_numeric = best.numeric_stats_qualified

        # 4) 有候选：获取步骤（ADS 投影）
        ads_req: Dict[str, Any] = {
            "dataset_key": best.candidate_id,
            "bbox": (list(scope.bbox) if scope.bbox is not None else None),
            "time_range": (list(scope.time_range)
                           if scope.time_range is not None else None),
            "columns": list(item.required_fields)[:8] or None,
            "version": (cand.version if cand and cand.version else "latest"),
            "sample_rate": 1.0,  # 显式不抽样：降级须用户批准，不静默
        }
        disclosures: List[str] = list(best.disclosures[:2])
        if (scope.bbox is not None and scope.bbox[0] > scope.bbox[2]):
            # R1 评审 A P2-7：跨日期线 bbox 对单 bbox 下推的数据面不可解析
            ads_req["crosses_dateline"] = True
            disclosures.append(
                "任务范围跨日期线：单 bbox 下推不可用——执行面将如实披露"
                "实际获取范围，绝不静默拆分或补齐")
        alternatives: List[Dict[str, str]] = []
        # P1-1A：期次不匹配必须设确认门——不得静默获取口径错配的数据
        if best.temporal_verdict == "none":
            disclosures.append(
                "候选期次与任务时间窗不匹配（版本/期次缺口）——需用户确认"
                "口径后才能获取")
            alternatives.append(
                {"kind": "keep_gap",
                 "note": "接受期次缺口（结论将带口径披露，不得伪称同期比较）"})
            alternatives.append(
                {"kind": "shrink_scope", "note": "改用候选实际覆盖的期次为任务窗"
                                                "（重新生成需求合同）"})
            alternatives.append(
                {"kind": "search_more", "note": "继续搜索匹配期次的候选"})
            alternatives.append(
                {"kind": "upload", "note": "由用户提供匹配期次数据"})

        if best.verdict == COVER_DECLARED_ONLY:
            disclosures.append("覆盖未核验（仅声明 bbox 相交）——执行后"
                               "以实际产物核对覆盖")
            alternatives.append(
                {"kind": "keep_gap",
                 "note": "接受未核验覆盖直接获取（产物核对后仍可保留缺口）"})
            alternatives.append(
                {"kind": "shrink_scope", "note": "先缩小研究区再评估"})
        if best.verdict == COVER_PARTIAL:
            disclosures.append(
                f"候选仅部分覆盖（{best.spatial_ratio and round(best.spatial_ratio, 2)}）"
                "——需用户选择如何处理缺口")
        if best.verdict in (COVER_NONE, COVER_UNKNOWN) or (
                best.verdict == COVER_PARTIAL):
            alternatives.append(
                {"kind": "shrink_scope", "note": "缩小研究区至候选覆盖范围"})
            alternatives.append(
                {"kind": "keep_gap", "note": "保留缺口继续（缺口将进入披露）"})
            if len(evals) > 1:
                # R1 评审 C P1-2：switch 逐候选展开（kind=switch:<id>），
                # UI 单选值即完整决策串——杜绝裸 switch 无效决策
                for alt in evals[1:_MAX_ALTERNATIVES]:
                    alternatives.append(
                        {"kind": f"switch:{alt.candidate_id}",
                         "note": f"换候选 → {alt.candidate_id}"})
            alternatives.append(
                {"kind": "upload", "note": "由用户提供该角色数据"})
        if not fit_numeric:
            alternatives.append(
                {"kind": "upload",
                 "note": "候选不可数值资格——上传数值级数据或另寻数值级来源"})

        step = SupplyStep(
            role=role, action=PLAN_ACTION_ACQUIRE,
            candidate_id=best.candidate_id,
            channel=cand.channel if cand else "",
            source_ref=(cand.source if cand and cand.channel.startswith(
                "data_fabric") and not str(cand.source).startswith("catalog:")
                else ""),
            ads_request=ads_req,
            cost_estimate=_cost_of(cand),
            alternatives=alternatives[:_MAX_ALTERNATIVES],
            disclosures=disclosures[:4],
            numeric_stats_qualified=fit_numeric,
            user_choice=choices.get(role, ""))
        steps.append(step)
        plan_parts.append({
            "role": role, "action": step.action, "cand": step.candidate_id,
            "version": ads_req["version"], "fit": fit_numeric,
            "choice": step.user_choice,
            # R1 评审 A P2-6：覆盖证据进指纹——证据修正（如 footprint
            # 从 FULL 改 PARTIAL）必须换键，"旧证明不复用"才对证据漂移成立
            "verdict": best.verdict,
            "ratio": (round(best.spatial_ratio, 4)
                      if best.spatial_ratio is not None else None),
            "temporal": best.temporal_verdict})

    # ── 预算 ──
    total_bytes = 0
    bytes_known = True
    for s in steps:
        b = s.cost_estimate.get("bytes")
        if b is None:
            bytes_known = False
            continue
        total_bytes += int(b)
    estimated = total_bytes if bytes_known else None
    if budget_max_bytes is None:
        budget_status = "no_cap"
    elif estimated is None or estimated <= budget_max_bytes:
        budget_status = "within"
    else:
        budget_status = "exceeded"

    # ── 状态与指纹 ──
    def _choice_kind(choice: str) -> str:
        return choice.split(":", 1)[0]

    needs_confirmation = (
        budget_status == "exceeded" and "accept_exceed" not in
        [_choice_kind(choices.get(s.role, "")) for s in steps]
    ) or any(
        # R1 评审 A P2-2 + C：任何带替代方案的步骤未决策即需确认
        #（含 display_only×numeric FULL 只有 upload 替代的路径——
        # 统计口径可能因执行而改变，必须显式确认）；
        # 已决策（user_choice 非空）不再无限拦（search_more/keep_gap
        # 是合法终态——执行面会对不可执行步骤类型化拒绝并给指引）
        bool(s.alternatives) and not s.user_choice
        for s in steps
    )
    budget_acknowledged = any(
        _choice_kind(c) == "accept_exceed" for c in choices.values())

    fp_blob = json.dumps({
        "requirement_id": requirement.requirement_id,
        "parts": plan_parts,
        "budget_max_bytes": budget_max_bytes,
    }, ensure_ascii=False, sort_keys=True, default=str)
    plan_id = "sup-" + hashlib.sha256(
        fp_blob.encode("utf-8")).hexdigest()[:24]
    for s in steps:
        s.dedup_key = f"{plan_id}:{s.role}"
        s.step_fp = "stepfp-" + hashlib.sha256(json.dumps(
            {"plan": fp_blob, "role": s.role}, ensure_ascii=False,
            sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]

    plan = SupplyPlan(
        requirement=requirement, steps=steps, plan_id=plan_id,
        budget_max_bytes=budget_max_bytes,
        estimated_total_bytes=estimated,
        budget_status=budget_status,
        budget_acknowledged=budget_acknowledged)
    if budget_status == "exceeded":
        # 超预算：不自动降级——给替代方案，用户显式选择
        for s in steps:
            if s.action == PLAN_ACTION_ACQUIRE:
                kinds = {a["kind"] for a in s.alternatives}
                if "accept_exceed" not in kinds:
                    s.alternatives.append(
                        {"kind": "accept_exceed",
                         "note": "接受超出预算上限（显式确认后仍执行）"})
                if "shrink_scope" not in kinds:
                    s.alternatives.append(
                        {"kind": "shrink_scope",
                         "note": "缩小研究区以降低获取量"})
                if "keep_gap" not in kinds:
                    s.alternatives.append(
                        {"kind": "keep_gap",
                         "note": "不获取该角色数据，保留缺口（进入披露）"})
        plan.state = ("ready" if budget_acknowledged
                      else "requires_confirmation")
    elif needs_confirmation:
        plan.state = "requires_confirmation"
    else:
        plan.state = "ready"
    return plan


__all__ = [
    "STEP_ACTIONS", "PLAN_STATES", "CHOICE_KINDS",
    "PLAN_ACTION_USE_BOUND", "PLAN_ACTION_ACQUIRE", "PLAN_ACTION_DERIVE",
    "PLAN_ACTION_CLARIFY", "PLAN_ACTION_USER_UPLOAD",
    "SupplyStep", "SupplyPlan", "SupplyPlanner", "build_supply_plan",
]
