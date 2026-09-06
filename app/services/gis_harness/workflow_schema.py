"""Workflow Recipe DSL V2 —— 专业 GIS 工作流契约层（Goal C / ADR-0101）。

在 :class:`CartographyRecipe`（制图方法契约）之上，additive 地建立更完整的
分析方法 DSL：数据角色（C3）、科学义务（C4）、完成契约（C7 的声明侧）、
回退语义（C6）与内容指纹（C12）。

红线（与 recipes.py 一致）：

- Recipe 声明「这类工作流需要什么」，不硬编码工具序列 —— 具体工具仍由
  CapabilityRegistry / AlgorithmResolver 解析；
- Recipe 不是第二个 workflow engine —— durable execution 仍由
  SessionPlan / Pi runtime 负责；
- 科学资格的最终裁决复用算法层 scientific preconditions
  （app/lib/gis/scientific_preconditions.py），本模块只做工作流级声明与
  联动，不重复实现科学语义；
- 全部确定性：同输入同输出，零 LLM、零 I/O。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ── 词汇表 ───────────────────────────────────────────────────────────────

#: Workflow Recipe DSL 版本。带 workflow 的 recipe 必须声明 V2 语义。
RECIPE_SCHEMA_VERSION = 2

#: C3 数据角色词汇（稳定契约：进入 Analysis Graph / SessionPlan evidence，
#: conformance corpus 按角色断言；文案可演进，role 值不复用为其它含义）。
DATA_ROLES = (
    "subject",          # 分析主体（POI / 要素 / 栅格主题）
    "measure",          # 被分析的数值变量（统计/检验的字段载体）
    "boundary",         # 行政/自然边界（聚合、分区制图的空间骨架）
    "denominator",      # 归一化分母（人口/面积/单元总数）——缺省禁公平性/率结论
    "network",          # 路网/管网等网络数据
    "elevation",        # DEM / 高程
    "hazard",           # 危险源/致灾因子
    "receptor",         # 承灾体/受体
    "population",       # 人口（区别于 denominator：可为主体也可为分母）
    "criteria",         # 决策准则因子
    "constraint",       # 约束/排斥因子
    "reference",        # 参考底图/辅助参照
    "baseline",         # 基期数据（变化/趋势）
    "target_time",      # 目标期数据
    "comparison_time",  # 对比期数据
)

#: 数据获取通道：local（本地/会话内）、data_fabric（数据目录/服务）、
#: user_upload（必须用户提供）、derived（可由其它 capability 派生）。
ACQUISITION_CHANNELS = ("local", "data_fabric", "user_upload", "derived")

#: 缺失策略：block（阻断该工作流的科学结论，verdict → BLOCKED_BY_DATA）
#: 或 degrade（降级并强制披露，不得静默）。
MISSING_POLICIES = ("block", "degrade")

#: 科学义务类型。
OBLIGATION_KINDS = (
    "precondition",   # 直接引用算法层 scientific precondition id（联动不重复）
    "denominator",    # 需要归一化分母
    "temporal",       # 需要时间维度/最低观测数
    "uncertainty",    # 需要不确定性输出/披露
    "transformation", # 前置变换（如投影/定标）
    "disclosure",     # 方法论披露义务
)

#: 义务违反的处理语义。
OBLIGATION_ACTIONS = ("block_method", "degrade_with_disclosure", "warn")

#: C7 完成契约维度（完成判断从「工具跑过」提升为七维可审计状态）。
COMPLETION_DIMENSIONS = (
    "data",                    # 数据角色覆盖完整？
    "analysis",                # 能力 DAG 关键节点完成？
    "science",                 # 科学义务满足（方法成立）？
    "cartography",             # 主/辅制图与组件落地？
    "observed_map",            # 前端实测渲染有效？
    "methodology_disclosure",  # 方法论义务已披露？
    "uncertainty_disclosure",  # 不确定性已披露？
)

#: C6 语义降级分类（复用算法层 fallback_semantics 词表，见
#: algorithm_registry.FallbackSemanticsClass —— 单一事实源，不另造词表）。
DOWNGRADE_CLASSES = ("equivalent", "approximation", "proxy", "degraded", "not_allowed")

#: C8 五维重算轴（与 product_graph._FACET_RECOMPUTE_DIMS 同词表）。
RECOMPUTE_DIMENSIONS = ("data", "algorithm", "parameter", "style", "output")

_GEOMETRY_KINDS = ("point", "line", "polygon", "raster", "table", "network", "unknown")

_ROLE_CODE_RE = re.compile(r"[^A-Z0-9]+")


def role_reason_code(role: str) -> str:
    """角色缺省原因码：DATA_ROLE_MISSING_<ROLE>（稳定、机器可读）。"""
    tail = _ROLE_CODE_RE.sub("_", role.upper()).strip("_")
    return f"DATA_ROLE_MISSING_{tail}"


# ── DSL 模型 ─────────────────────────────────────────────────────────────

class DataRoleRequirement(BaseModel):
    """一个数据角色需求：工作流需要什么数据、缺了怎么办。

    required=True + missing_policy=block → 缺失阻断科学结论（诚实 BLOCK）；
    required=True + missing_policy=degrade → 缺失降级并强制披露；
    required=False → 可选增强，缺失不影响 verdict。
    """
    role: str                          # ⊆ DATA_ROLES
    required: bool = True
    acquisition: str = "local"         # ⊆ ACQUISITION_CHANNELS
    capability_hint: str = ""          # 常见供给 capability（存在性由 registry_validation 校验）
    accepted_artifact_types: List[str] = Field(default_factory=list)  # ⊆ ArtifactTypeRegistry
    geometry_kinds: List[str] = Field(default_factory=list)           # ⊆ _GEOMETRY_KINDS
    missing_policy: str = "block"      # ⊆ MISSING_POLICIES
    reason_code: str = ""              # 空 → role_reason_code(role)
    degrade_disclosure: str = ""       # degrade 时的用户可见披露（空则用默认模板）
    must_not_guess: bool = True        # LLM/模型不得编造该数据
    note: str = ""


class ScientificObligation(BaseModel):
    """工作流级科学义务声明。

    kind=precondition 时 precondition_id 必须命中算法层已注册的
    scientific precondition（联动而非重复实现）；其余 kind 为工作流级
    声明，由 compiler 按数据角色绑定/profile 事实评估。
    """
    obligation_id: str
    kind: str                          # ⊆ OBLIGATION_KINDS
    precondition_id: str = ""          # kind=precondition 时必填且必须已注册
    warning_code: str = ""             # 违反时的稳定机器可读码（空 → 派生）
    description: str = ""
    on_violation: str = "warn"         # ⊆ OBLIGATION_ACTIONS


class CompletionRequirement(BaseModel):
    """完成契约的一维声明（C7）：这个工作流算「完成」要满足什么。"""
    dimension: str                     # ⊆ COMPLETION_DIMENSIONS
    required: bool = True
    evidence: str = ""                 # 什么证据满足该维度（披露给用户/Agent）


class WorkflowFallbackPolicy(BaseModel):
    """机器可读回退语义（C6）：触发原因码 → 目标 + 降级分类 + 披露。

    与 :class:`RecipeFallback` 的关系：RecipeFallback 仍是制图元素级
    资格回退（INSUFFICIENT_POINTS → point_distribution）；本策略是工作流
    级语义回退（如 hotspot 显著性不可用 → 仅描述密度），二者都最终产出
    结构化 FallbackDecision 证据。
    """
    reason_code: str
    from_element: str = ""
    to_element: str = ""
    downgrade_class: str = "degraded"  # ⊆ DOWNGRADE_CLASSES
    disclosure: str = ""               # material 时必须用户可见
    blocks_completion: bool = False    # True → 该回退使完成契约 science 维不满足


class WorkflowProfile(BaseModel):
    """V2 工作流画像：挂在 CartographyRecipe.workflow（optional，additive）。

    无 workflow 的 recipe（17 个 seed）保持 V1 行为不变；带 workflow 的
    recipe 获得：专业关键词路由、数据角色解析、科学义务联动、完成契约、
    语义回退与证据要求。
    """
    schema_version: int = RECIPE_SCHEMA_VERSION
    domain: str = ""                   # 领域包名（distribution/density/…）
    workflow_family: str = ""          # 工作流族（如 hotspot_significance）
    keywords_zh: List[str] = Field(default_factory=list)   # 专业路由词（query 子串命中）
    keywords_en: List[str] = Field(default_factory=list)
    data_roles: List[DataRoleRequirement] = Field(default_factory=list)
    obligations: List[ScientificObligation] = Field(default_factory=list)
    completion_requirements: List[CompletionRequirement] = Field(default_factory=list)
    fallback_policies: List[WorkflowFallbackPolicy] = Field(default_factory=list)
    required_disclosures: List[str] = Field(default_factory=list)  # 触发时必须披露的义务码
    recompute_dimensions: List[str] = Field(default_factory=list)  # ⊆ RECOMPUTE_DIMENSIONS
    evidence_requirements: List[str] = Field(default_factory=list) # 证据种类要求（如 "calibration_evidence"）


# ── 评估结果模型 ─────────────────────────────────────────────────────────

class DataRoleResolution(BaseModel):
    """一个数据角色的解析结果（compiler 阶段 5 输出）。"""
    role: str
    required: bool
    acquisition: str
    # unresolved（尚无数据）/ bound（已绑定 capability 供给）/ external（依赖
    # data_fabric / 用户上传，规划期不可证伪）/ degraded（缺失已按策略降级）
    status: str = "unresolved"
    source_capability: str = ""
    bound_ref: str = ""
    missing_policy: str = "block"
    reason_code: str = ""
    disclosure: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "required": self.required,
            "acquisition": self.acquisition,
            "status": self.status,
            "source_capability": self.source_capability[:64],
            "bound_ref": self.bound_ref[:64],
            "missing_policy": self.missing_policy,
            "reason_code": self.reason_code[:64],
            "disclosure": self.disclosure[:200],
        }


class ObligationEvaluation(BaseModel):
    """一个科学义务的评估结果（compiler 阶段 7 输出）。"""
    obligation_id: str
    kind: str
    # satisfied / warning / degraded / blocked / unknown（profile 缺事实 ≠ 不满足）
    status: str = "unknown"
    warning_code: str = ""
    on_violation: str = "warn"
    detail: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "obligation_id": self.obligation_id[:64],
            "kind": self.kind,
            "status": self.status,
            "warning_code": self.warning_code[:64],
            "on_violation": self.on_violation,
            "detail": self.detail[:200],
            "evidence": {k: v for k, v in list(self.evidence.items())[:6]},
        }


class WorkflowContractReport(BaseModel):
    """workflow 契约评估汇总（compiler / finalize 共用）。"""
    recipe_id: str
    roles: List[DataRoleResolution] = Field(default_factory=list)
    obligations: List[ObligationEvaluation] = Field(default_factory=list)
    # 从义务/角色评估中确定性导出的方法论警告（码 + 披露），planner 会把它
    # 并入 plan.methodology_warnings —— 复用既有 verdict 联动（READY 永远
    # 让位于披露）。
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    # block_method 义务被触发的义务 id（完成契约 science 维不满足的直接证据）
    method_blockers: List[str] = Field(default_factory=list)
    # block 策略且缺失的数据角色（verdict → BLOCKED_BY_DATA 的直接证据）
    data_blockers: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "roles": [r.to_bounded_dict() for r in self.roles[:16]],
            "obligations": [o.to_bounded_dict() for o in self.obligations[:16]],
            "warnings": [w for w in self.warnings[:8]],
            "method_blockers": self.method_blockers[:8],
            "data_blockers": self.data_blockers[:8],
        }


# ── 校验（registry_validation 消费）─────────────────────────────────────

def validate_workflow_profile(
    profile: WorkflowProfile,
    *,
    capability_exists=None,
    artifact_type_exists=None,
    precondition_exists=None,
) -> List[str]:
    """静态完整性校验，返回违规列表（空 = 通过）。纯函数。

    capability_exists / artifact_type_exists / precondition_exists 是注入的
    词表谓词（避免本模块 import registry 单例造成循环依赖）。
    """
    violations: List[str] = []
    seen_roles: set = set()
    for req in profile.data_roles:
        tag = f"workflow.data_roles[{req.role}]"
        if req.role not in DATA_ROLES:
            violations.append(f"{tag}: unknown role")
        if req.role in seen_roles:
            violations.append(f"{tag}: duplicate role")
        seen_roles.add(req.role)
        if req.acquisition not in ACQUISITION_CHANNELS:
            violations.append(f"{tag}: unknown acquisition {req.acquisition}")
        if req.missing_policy not in MISSING_POLICIES:
            violations.append(f"{tag}: unknown missing_policy {req.missing_policy}")
        if req.capability_hint and capability_exists and not capability_exists(req.capability_hint):
            violations.append(f"{tag}: capability_hint {req.capability_hint} 不存在")
        for at in req.accepted_artifact_types:
            if artifact_type_exists and not artifact_type_exists(at):
                violations.append(f"{tag}: artifact type {at} 未注册")
        for gk in req.geometry_kinds:
            if gk not in _GEOMETRY_KINDS:
                violations.append(f"{tag}: unknown geometry kind {gk}")

    seen_obl: set = set()
    for obl in profile.obligations:
        tag = f"workflow.obligations[{obl.obligation_id}]"
        if obl.obligation_id in seen_obl:
            violations.append(f"{tag}: duplicate obligation_id")
        seen_obl.add(obl.obligation_id)
        if obl.kind not in OBLIGATION_KINDS:
            violations.append(f"{tag}: unknown kind {obl.kind}")
        if obl.on_violation not in OBLIGATION_ACTIONS:
            violations.append(f"{tag}: unknown on_violation {obl.on_violation}")
        if obl.kind == "precondition":
            if not obl.precondition_id:
                violations.append(f"{tag}: precondition kind 需要 precondition_id")
            elif precondition_exists and not precondition_exists(obl.precondition_id):
                violations.append(f"{tag}: precondition {obl.precondition_id} 未注册")
        elif obl.kind == "temporal" and obl.precondition_id:
            # R2-6：temporal 义务的 precondition 引用在此校验 —— 既支持
            # 声明性引用已注册 id（如 temporal_field_required），也支持
            # 参数化 min_temporal_observations:N（评估分支按前缀解析 N，
            # typo 会静默回落默认 2，必须在静态期拦下）。
            if obl.precondition_id.startswith("min_temporal_observations:"):
                try:
                    n = int(obl.precondition_id.split(":", 1)[1])
                    if not 2 <= n <= 365:
                        violations.append(f"{tag}: min_temporal_observations 越界 {n}")
                except (TypeError, ValueError):
                    violations.append(f"{tag}: min_temporal_observations:N 语法错误")
            elif precondition_exists and not precondition_exists(obl.precondition_id):
                violations.append(f"{tag}: precondition {obl.precondition_id} 未注册")

    seen_dim: set = set()
    for req in profile.completion_requirements:
        tag = f"workflow.completion_requirements[{req.dimension}]"
        if req.dimension not in COMPLETION_DIMENSIONS:
            violations.append(f"{tag}: unknown dimension")
        if req.dimension in seen_dim:
            violations.append(f"{tag}: duplicate dimension")
        seen_dim.add(req.dimension)

    seen_fb: set = set()
    for fb in profile.fallback_policies:
        tag = f"workflow.fallback_policies[{fb.reason_code}]"
        if fb.reason_code in seen_fb:
            violations.append(f"{tag}: duplicate reason_code")
        seen_fb.add(fb.reason_code)
        if fb.downgrade_class not in DOWNGRADE_CLASSES:
            violations.append(f"{tag}: unknown downgrade_class {fb.downgrade_class}")
        if fb.downgrade_class == "not_allowed" and not fb.blocks_completion:
            violations.append(f"{tag}: not_allowed 降级必须 blocks_completion")

    for dim in profile.recompute_dimensions:
        if dim not in RECOMPUTE_DIMENSIONS:
            violations.append(f"workflow.recompute_dimensions: unknown dimension {dim}")
    return violations


# ── 评估（确定性；compiler 阶段 5/7 与 finalize 共用）───────────────────

#: 强分母字段提示：仅这些证据能把 denominator 角色升为 bound（review R1-A11：
#: total/count/bed 等弱提示会把分子字段误判成分母 —— 公平性红线的字段证据
#: 必须保守；弱提示一律只走义务 warning，永不满足）。
_DENOMINATOR_FIELD_HINTS = (
    "population", "pop_", "人口", "household", "户数",
)
_TIME_FIELD_HINTS = ("time", "date", "year", "month", "day", "时间", "日期", "年份", "时期")


def _field_names(profile: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(profile, dict):
        return []
    fields = profile.get("fields")
    if isinstance(fields, dict):
        return [str(k) for k in fields.keys()]
    if isinstance(fields, (list, tuple)):
        return [str(f) for f in fields]
    return []


def _field_lower_contains(names: List[str], hints) -> bool:
    for name in names:
        low = name.lower()
        if any(h in low for h in hints):
            return True
    return False


def resolve_data_roles(
    recipe_id: str,
    profile: Optional[WorkflowProfile],
    *,
    resolver_profile: Optional[Dict[str, Any]] = None,
    bound_refs: Optional[Dict[str, str]] = None,
) -> List[DataRoleResolution]:
    """阶段 5：数据角色解析（确定性）。

    - capability_hint 存在于 capability registry → status=bound（supply path
      确定，具体工具仍由 resolver 决定）；
    - acquisition ∈ {data_fabric, user_upload} → external（规划期不可证伪，
      不假设缺失 —— 由完成契约 data 维追踪）；
    - resolver_profile 已含满足角色几何/字段事实的本地数据 → bound；
    - 其余本地角色 → unresolved，按 missing_policy 导出 reason_code。
    """
    if profile is None:
        return []
    del recipe_id  # 预留 evidence 关联；当前以调用方聚合为准

    capability_ids: set = set()
    try:
        from app.lib.gis.capability_registry import get_capability_registry
        ids = get_capability_registry().all_ids
        capability_ids = set(ids() if callable(ids) else ids)
    except Exception:  # noqa: BLE001 - registry 缺席时退化为保守 unresolved
        capability_ids = set()

    bound_refs = bound_refs or {}
    fields = _field_names(resolver_profile)
    resolutions: List[DataRoleResolution] = []
    for req in profile.data_roles:
        code = req.reason_code or role_reason_code(req.role)
        res = DataRoleResolution(
            role=req.role,
            required=req.required,
            acquisition=req.acquisition,
            missing_policy=req.missing_policy,
            reason_code=code,
        )
        # 字段事实优先于获取通道：profile 已能证明角色数据在场时（如分母
        # 字段），无论 local 还是 data_fabric 获取通道都视为 bound ——
        # unknown ≠ unsatisfied 的对称面：evidence ≠ unsatisfied。
        profile_backed = (
            req.role == "denominator"
            and _field_lower_contains(fields, _DENOMINATOR_FIELD_HINTS)
        )
        if req.role in bound_refs:
            res.status = "bound"
            res.bound_ref = bound_refs[req.role]
            res.source_capability = req.capability_hint
        elif profile_backed:
            res.status = "bound"
            res.source_capability = "profile_fields"
        elif req.acquisition in ("data_fabric", "user_upload"):
            res.status = "external"
            res.disclosure = req.degrade_disclosure or (
                f"数据角色 {req.role} 依赖外部获取（{req.acquisition}），"
                "规划期不假设其存在；缺失时按策略降级。"
            )
        elif req.capability_hint and req.capability_hint in capability_ids:
            res.status = "bound"
            res.source_capability = req.capability_hint
        elif not req.required:
            res.status = "unresolved"
        else:
            res.status = "unresolved"
            if req.missing_policy == "degrade":
                res.status = "degraded"
                res.disclosure = req.degrade_disclosure or (
                    f"缺少 {req.role} 数据：已降级处理，不得出依赖该数据的结论。"
                )
        resolutions.append(res)
    return resolutions


def evaluate_workflow_obligations(
    recipe_id: str,
    profile: Optional[WorkflowProfile],
    *,
    resolver_profile: Optional[Dict[str, Any]] = None,
    role_resolutions: Optional[List[DataRoleResolution]] = None,
) -> WorkflowContractReport:
    """阶段 7：工作流科学义务评估（确定性，联动算法层 preconditions）。

    - kind=precondition → 直接调用算法层 evaluate_precondition（单一事实源）；
      profile 缺事实时算法层返回 PASS（unknown ≠ unsatisfied）；
    - kind=denominator → 数据角色绑定或 profile 字段证据；
    - kind=temporal → hasTimeField / temporalObservationCount 事实或时间字段名；
    - 其余 kind → unknown（需要运行期证据，规划期不假判断）。
    """
    report = WorkflowContractReport(recipe_id=recipe_id)
    if profile is None:
        return report

    role_by_name = {r.role: r for r in (role_resolutions or [])}
    fields = _field_names(resolver_profile)
    rp = resolver_profile if isinstance(resolver_profile, dict) else {}

    for obl in profile.obligations:
        ev: ObligationEvaluation
        if obl.kind == "precondition" and obl.precondition_id:
            from app.lib.gis.scientific_preconditions import (
                evaluate_precondition,
                precondition_exists,
            )
            if not precondition_exists(obl.precondition_id):
                ev = ObligationEvaluation(
                    obligation_id=obl.obligation_id, kind=obl.kind,
                    status="unknown", warning_code=obl.warning_code,
                    on_violation=obl.on_violation,
                    detail=f"precondition {obl.precondition_id} 未注册（规划期视为未知）",
                )
            else:
                result = evaluate_precondition(obl.precondition_id, rp)
                status_map = {
                    "PASS": "satisfied",
                    "PASS_WITH_WARNINGS": "warning",
                    "REQUIRES_TRANSFORM": "degraded",
                    "INSUFFICIENT_DATA": "blocked",
                    "INVALID_METHOD": "blocked",
                }
                status = status_map.get(result.verdict, "unknown")
                # R1-A9：声明 block_method 的义务遇到「需先变换」（如角度
                # 坐标要先投影）时，变换完成前方法同样不成立 —— 升级为
                # blocked，让声明的阻断真正可达（否则 on_violation=
                # block_method + REQUIRES_TRANSFORM 既不阻断也无回退）。
                if (status == "degraded"
                        and obl.on_violation == "block_method"):
                    status = "blocked"
                ev = ObligationEvaluation(
                    obligation_id=obl.obligation_id, kind=obl.kind,
                    status=status,
                    warning_code=obl.warning_code,
                    on_violation=obl.on_violation,
                    detail=result.message,
                    evidence={"verdict": result.verdict,
                              "transform_hint": result.transform_hint[:80]},
                )
        elif obl.kind == "denominator":
            role_res = role_by_name.get("denominator")
            has_denominator_field = _field_lower_contains(fields, _DENOMINATOR_FIELD_HINTS)
            if (role_res is not None and role_res.status in ("bound",)) or has_denominator_field:
                ev = ObligationEvaluation(
                    obligation_id=obl.obligation_id, kind=obl.kind,
                    status="satisfied", warning_code=obl.warning_code,
                    on_violation=obl.on_violation,
                    detail="denominator 数据角色已绑定或字段证据存在",
                )
            else:
                ev = ObligationEvaluation(
                    obligation_id=obl.obligation_id, kind=obl.kind,
                    status="blocked" if obl.on_violation == "block_method" else "warning",
                    warning_code=obl.warning_code,
                    on_violation=obl.on_violation,
                    detail="缺少归一化分母：不得下人均/率/公平性结论",
                )
        elif obl.kind == "temporal":
            # R2-6：画像完全缺席时间事实（无 hasTimeField/temporalObservation
            # Count 键）= unknown ≠ unsatisfied；画像**在场**但事实不足才是
            # blocked/warning（InSAR 栈门槛的严格语义以画像在手为前提）。
            if not rp or (
                "hasTimeField" not in rp
                and "temporalObservationCount" not in rp
                and not _field_lower_contains(fields, _TIME_FIELD_HINTS)
            ):
                ev = ObligationEvaluation(
                    obligation_id=obl.obligation_id, kind=obl.kind,
                    status="unknown", warning_code=obl.warning_code,
                    on_violation=obl.on_violation,
                    detail="画像无时间事实：未知（不虚构满足也不虚构违反）",
                )
                report.obligations.append(ev)
                continue
            has_time = bool(rp.get("hasTimeField")) or _field_lower_contains(fields, _TIME_FIELD_HINTS)
            obs = rp.get("temporalObservationCount")
            min_obs = 2
            if obl.precondition_id.startswith("min_temporal_observations:"):
                try:
                    min_obs = int(obl.precondition_id.split(":", 1)[1])
                except (TypeError, ValueError):
                    min_obs = 2
            obs_ok = isinstance(obs, (int, float)) and obs >= min_obs
            if has_time and (not isinstance(obs, (int, float)) or obs_ok):
                ev = ObligationEvaluation(
                    obligation_id=obl.obligation_id, kind=obl.kind,
                    status="satisfied" if obs_ok or not isinstance(obs, (int, float)) else "warning",
                    warning_code=obl.warning_code,
                    on_violation=obl.on_violation,
                    detail=f"时间维度可用（观测数要求 ≥{min_obs}）",
                    evidence={"temporalObservationCount": obs if obs is not None else "unknown"},
                )
            else:
                ev = ObligationEvaluation(
                    obligation_id=obl.obligation_id, kind=obl.kind,
                    status="blocked" if obl.on_violation == "block_method" else "warning",
                    warning_code=obl.warning_code,
                    on_violation=obl.on_violation,
                    detail=f"时间维度不足（观测数要求 ≥{min_obs}）",
                    evidence={"temporalObservationCount": obs if obs is not None else "unknown",
                              "hasTimeField": bool(has_time)},
                )
        elif obl.kind in ("transformation", "disclosure"):
            # 变换/披露类义务是「处理/披露义务」：规划期即作为披露浮出
            # （material 时用户必须可见），运行期证据到齐后由完成契约核验
            # 是否解除 —— unknown 的是「是否已满足」，不是「是否该披露」。
            ev = ObligationEvaluation(
                obligation_id=obl.obligation_id, kind=obl.kind,
                status=("warning"
                        if obl.on_violation == "degrade_with_disclosure"
                        else "unknown"),
                warning_code=obl.warning_code,
                on_violation=obl.on_violation,
                detail=obl.description or "处理/披露义务：运行期证据核验",
            )
        else:
            # uncertainty 等：需要运行期证据，规划期诚实 unknown。
            ev = ObligationEvaluation(
                obligation_id=obl.obligation_id, kind=obl.kind,
                status="unknown", warning_code=obl.warning_code,
                on_violation=obl.on_violation,
                detail="运行期证据义务：由完成契约在 finalize 阶段核验",
            )
        report.obligations.append(ev)

    # 从评估结果确定性导出警告与阻断项
    for res in role_resolutions or []:
        if res.required and res.status == "unresolved" and res.missing_policy == "block":
            report.data_blockers.append(res.role)
    for ev in report.obligations:
        if ev.status == "blocked" and ev.on_violation == "block_method":
            report.method_blockers.append(ev.obligation_id)
        if ev.status in ("warning", "degraded", "blocked"):
            code = ev.warning_code or f"OBLIGATION_{ev.obligation_id.upper()}_UNMET"
            report.warnings.append({
                "pattern": "workflow_obligation",
                "code": code,
                "warning_codes": [code],
                "obligation_id": ev.obligation_id,
                "on_violation": ev.on_violation,
                "disclosures": [ev.detail] if ev.detail else [],
                "stage": "workflow_compile",
            })
    for res in role_resolutions or []:
        if res.status == "degraded" and res.disclosure:
            code = res.reason_code
            report.warnings.append({
                "pattern": "workflow_data_role",
                "code": code,
                "warning_codes": [code],
                "role": res.role,
                "on_violation": "degrade_with_disclosure",
                "disclosures": [res.disclosure],
                "stage": "workflow_compile",
            })
    return report


# ── 指纹（C12）──────────────────────────────────────────────────────────

def recipe_content_fingerprint(recipe: Any) -> str:
    """recipe 内容指纹：canonical JSON 的 SHA256（稳定、排序、无时间戳）。

    参与项为完整 model_dump（含 workflow V2 块）——workflow 语义变化必然
    改变指纹，runtime manifest 因此可感知 plan stale（复用既有
    manifest.fingerprint / is_stale_plan 体系，不另造第二套 manifest）。
    """
    try:
        payload = recipe.model_dump()
    except Exception:  # noqa: BLE001 - 非 pydantic 对象退化为 repr（诚实退化）
        payload = {"repr": repr(recipe)}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()


def recipe_capability_ids(recipe: Any) -> List[str]:
    """recipe 引用的全部 capability id（去重、排序），供 manifest 投影/校验。"""
    caps = set(getattr(recipe, "preferred_analysis", None) or [])
    caps.update(getattr(recipe, "optional_analysis", None) or [])
    for caps_list in (getattr(recipe, "task_optional_analysis", None) or {}).values():
        caps.update(caps_list or [])
    wf = getattr(recipe, "workflow", None)
    if wf is not None:
        for req in getattr(wf, "data_roles", []) or []:
            if req.capability_hint:
                caps.add(req.capability_hint)
    return sorted(caps)
