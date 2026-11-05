"""AnalysisWorkflow 规划器（goal-02 M3）—— 场景 → 契约 + typed 裁决。

纯函数（词表/存在性谓词与画像事实注入）；run 期由 tools 层接 session
store 与 registry。裁决语义：

- **required+block 角色缺失** → 步骤 blocked + 族特定 typed 码
  （network 缺失 = AWF_NO_NETWORK_FOR_ACCESSIBILITY，含 buffer 替代
  alternatives；其余 = AWF_DATA_ROLE_MISSING:<ROLE>）；
- **optional 步骤的角色缺失** → 步骤 typed skipped（如 per-capita 密度
  无分母——只给计数/面积密度，绝不伪装率）；
- **temporal 族**：baseline/target_time 年份（qualifier 时间事实）缺失
  或相同 → typed AWF_TEMPORAL_MISMATCH（不静默差分）；
- **科学前置**：本层只做**声明性引用**（step.preconditions 进契约与
  receipt 披露）；权威裁决在执行层 dispatch/算法层 scientific
  preconditions——本层不重复评估（避免第二套科学语义）。

诚实红线：plan 期不猜测 ref（dataset_hint 只是供给线索）；缺数据给
alternatives（获取通道 + 可行替代分析），绝不编造「等一下就有了」。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_DENOMINATOR_MISSING,
    AWF_NO_NETWORK_FOR_ACCESSIBILITY,
    AWF_ROLE_KIND_MISMATCH,
    AWF_TEMPORAL_MISMATCH,
    AnalysisStepSpec,
    AnalysisWorkflowContract,
    DataRoleBinding,
    contract_fingerprint,
    role_missing_code,
    validate_workflow_contract,
)
from app.services.gis_harness.analysis_workflows.families import (
    build_contract,
    family_exists,
    role_requirements,
    visualization_hints,
)
from app.services.gis_harness.analysis_workflows.semantic_gate import (
    check_role_qualifier,
)

#: 各角色的获取建议（alternatives 文案；通道语义对齐 ACQUISITION_CHANNELS）。
_ROLE_ALTERNATIVES: Dict[str, Tuple[str, ...]] = {
    "subject": (
        "用本地 POI 工具获取（get_local_pois / local_osm 提取）",
        "请用户上传点数据（user_upload）",
    ),
    "boundary": (
        "用本地行政区工具获取（get_local_admin_boundary / get_local_child_districts）",
        "请用户上传行政边界（user_upload）",
    ),
    "network": (
        "用本地 OSM 路网提取（local_osm 工具族）",
        "请用户上传路网数据（user_upload）",
        "若只需欧氏邻近筛查：buffer_analysis（直线距离 proxy，科学含义与路网通达不同，必须披露）",
    ),
    "hazard": ("请提供危险源图层（user_upload / data_fabric）",),
    "receptor": (
        "用本地建筑/POI 数据充当承灾体",
        "请用户上传资产/人口分布（user_upload）",
    ),
    "facility": (
        "用本地 POI 工具获取设施点（get_local_pois / local_osm 提取）",
        "请用户上传设施点数据（user_upload）",
    ),
    "baseline": ("请提供基期（较早一期）数据",),
    "target_time": ("请提供目标期（较近一期）数据",),
    "denominator": (
        "请提供人口/单元总数分母数据（缺失时只输出原始计数，不给率）",
    ),
    "population": ("请提供人口点/栅格数据（缺失时只给覆盖面积/占比）",),
}


class StepPlanVerdict(BaseModel):
    """plan 期步骤裁决（ready / blocked / skipped + typed 码与建议）。"""

    step_id: str
    verdict: str                          # ready | blocked | skipped
    reason_code: str = ""
    message: str = ""
    alternatives: Tuple[str, ...] = ()


class WorkflowPlanResult(BaseModel):
    """plan 输出（契约 + 逐步骤裁决 + 指纹；零执行）。"""

    contract: AnalysisWorkflowContract
    fingerprint: str
    step_verdicts: List[StepPlanVerdict] = Field(default_factory=list)
    runnable: bool = True                 # 存在至少一个 ready 步骤且无 required block
    blocked_reasons: Tuple[str, ...] = ()
    alternatives: Tuple[str, ...] = ()
    visualization_hints: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


def _roles_of_step(step: AnalysisStepSpec) -> List[str]:
    return [v.removeprefix("role:") for v in step.role_inputs.values()
            if v.startswith("role:")]


def _binding_missing(binding: Optional[DataRoleBinding]) -> bool:
    if binding is None:
        return True
    return binding.missing or not binding.ref


def _temporal_gate(
    contract: AnalysisWorkflowContract,
) -> Optional[Tuple[str, str]]:
    """temporal 族对齐门：两期年份必须都存在且不同（否则 typed 拒绝）。

    年份事实从 binding.qualifier 读（键 ``year`` / ``time_field`` /
    ``temporal_label``，由上游数据画像写入）；本层不猜年份。
    """
    if contract.family != "temporal_change":
        return None
    base = contract.data_contracts.get("baseline")
    target = contract.data_contracts.get("target_time")

    def _year(b: Optional[DataRoleBinding]) -> Optional[int]:
        if b is None:
            return None
        for key in ("year", "target_year", "baseline_year"):
            raw = b.qualifier.get(key)
            if isinstance(raw, int) and raw > 0:
                return raw
            if isinstance(raw, str) and raw[:4].isdigit():
                return int(raw[:4])
        return None

    y0, y1 = _year(base), _year(target)
    if y0 is None or y1 is None:
        return (AWF_TEMPORAL_MISMATCH,
                "双期数据的年份画像缺失（需 qualifier.year）——不静默差分")
    if y0 == y1:
        return (AWF_TEMPORAL_MISMATCH,
                f"基期与目标期年份相同（{y0}）——无可比变化")
    if y0 > y1:
        return (AWF_TEMPORAL_MISMATCH,
                f"基期年份（{y0}）晚于目标期（{y1}）——顺序颠倒")
    return None


def _denominator_field_gate(
    step: AnalysisStepSpec,
    contract: AnalysisWorkflowContract,
) -> Optional[Tuple[str, str]]:
    """分母字段 plan 期校验（ADR-0282 D4；断点 6 修复）。

    qualifier.fields（可用列清单，上游数据画像写入）声明而步骤将使用的
    分母列不在其中 → typed skipped（AWF_DENOMINATOR_MISSING）——
    列清单是事实，缺列的分母请求在 plan 期就诚实拒绝，绝不运行期
    MissingRequiredField failed；fields 未声明=不拦截（权威在工具）。
    """
    if step.step_params.get("denominator_kind") != "field":
        return None
    target = str(step.step_params.get("denominator") or "")
    for source in step.role_inputs.values():
        if not source.startswith("role:"):
            continue
        role = source.removeprefix("role:")
        binding = contract.data_contracts.get(role)
        if binding is None or _binding_missing(binding):
            continue
        fields = binding.qualifier.get("fields")
        if (isinstance(fields, (list, tuple)) and fields
                and target not in fields):
            return (
                AWF_DENOMINATOR_MISSING,
                f"分母角色 {role} 声明的字段清单 {sorted(map(str, fields))}"
                f"不含目标列 {target!r}——分母不可用，typed skipped"
                "（只给计数/面积密度，不伪装率）")
    return None


def plan_workflow(
    family: str,
    *,
    workflow_id: str,
    bindings: Optional[Dict[str, DataRoleBinding]] = None,
    params_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    recipe_refs: Tuple[str, ...] = (),
    tool_exists: Optional[Callable[[str], bool]] = None,
    include_optional_steps: bool = True,
) -> WorkflowPlanResult:
    """族 → 契约 + 逐步骤 typed 裁决（纯函数，零 IO）。"""
    if not family_exists(family):
        raise KeyError(
            f"未知工作流族 {family!r}（词表: admin_facility_distribution / "
            "service_accessibility / hazard_exposure / temporal_change / "
            "statistical_zones / raster_zonal_summary / network_proximity）")
    contract = build_contract(
        family, workflow_id=workflow_id, bindings=bindings,
        recipe_refs=recipe_refs, params_overrides=params_overrides,
        include_optional_steps=include_optional_steps)
    warnings = validate_workflow_contract(
        contract, tool_exists=tool_exists, family_exists=family_exists)

    req_by_role = {r.role: r for r in role_requirements(family)}
    verdicts: List[StepPlanVerdict] = []
    blocked_reasons: List[str] = []
    alternatives: List[str] = []
    runnable = True

    temporal_fail = _temporal_gate(contract)

    verdict_map: Dict[str, StepPlanVerdict] = {}
    for step in contract.steps:
        # 1) temporal 族门：差分步骤未过对齐门 → blocked
        if temporal_fail and step.step_id in ("change_diff", "change_share"):
            code, msg = temporal_fail
            verdict_map[step.step_id] = StepPlanVerdict(
                step_id=step.step_id, verdict="blocked",
                reason_code=code, message=msg,
                alternatives=_ROLE_ALTERNATIVES.get("baseline", ()))
            blocked_reasons.append(f"{step.step_id}:{code}")
            runnable = False
            continue
        # 2) 上游裁决传播：blocked 上游 → 下游 blocked；skipped 上游 →
        #    下游 optional 则 skipped、否则 blocked（依赖事实缺失）。
        upstream_states = [
            (dep, verdict_map[dep]) for dep in step.depends_on
            if dep in verdict_map and verdict_map[dep].verdict != "ready"
        ]
        upstream_states += [
            (src.removeprefix("step:"), verdict_map[src.removeprefix("step:")])
            for src in step.role_inputs.values()
            if src.startswith("step:")
            and src.removeprefix("step:") in verdict_map
            and verdict_map[src.removeprefix("step:")].verdict != "ready"
        ]
        if upstream_states:
            dep_id, dep_verdict = upstream_states[0]
            downstream_state = ("skipped" if dep_verdict.verdict == "skipped"
                                and step.optional else "blocked")
            verdict_map[step.step_id] = StepPlanVerdict(
                step_id=step.step_id, verdict=downstream_state,
                reason_code=f"UPSTREAM_{dep_verdict.verdict.upper()}"
                            f":{dep_id}:{dep_verdict.reason_code}",
                message=f"上游步骤 {dep_id} 未就绪（{dep_verdict.verdict}）")
            if downstream_state == "blocked":
                blocked_reasons.append(
                    f"{step.step_id}:UPSTREAM_{dep_verdict.verdict.upper()}")
                runnable = False
            continue
        # 3) 角色缺失裁决
        missing_roles = [
            role for role in _roles_of_step(step)
            if _binding_missing(contract.data_contracts.get(role))
        ]
        if missing_roles:
            required_missing = [
                r for r in missing_roles
                if req_by_role.get(r) is not None
                and req_by_role[r].required
                and req_by_role[r].missing_policy == "block"
            ]
            if required_missing:
                # 族特定码：可达性/邻近族无路网 = 专属码 + 显式替代语义
                if (family in ("service_accessibility", "network_proximity")
                        and "network" in required_missing
                        and any(v == "role:network"
                                for v in step.role_inputs.values())):
                    code = AWF_NO_NETWORK_FOR_ACCESSIBILITY
                    msg = ("无真实路网数据——不得给出『15 分钟可达』/『最近设施"
                           "网络距离』类数值结论；可用 buffer 替代但科学含义"
                           "不同（欧氏距离≠通达时间）")
                    alts = _ROLE_ALTERNATIVES["network"]
                else:
                    role = required_missing[0]
                    code = role_missing_code(role)
                    msg = f"必需数据角色 {role} 未绑定"
                    alts = _ROLE_ALTERNATIVES.get(role, ())
                verdict_map[step.step_id] = StepPlanVerdict(
                    step_id=step.step_id, verdict="blocked",
                    reason_code=code, message=msg, alternatives=alts)
                blocked_reasons.append(f"{step.step_id}:{code}")
                alternatives.extend(alts)
                runnable = False
                continue
            # optional 步骤缺 optional 角色 → typed skipped（分母语义专用码）
            if step.optional:
                code = (AWF_DENOMINATOR_MISSING
                        if any(r in ("denominator", "population")
                               for r in missing_roles)
                        else role_missing_code(missing_roles[0]))
                verdict_map[step.step_id] = StepPlanVerdict(
                    step_id=step.step_id, verdict="skipped",
                    reason_code=code,
                    message=(
                        f"可选步骤输入缺失（{', '.join(missing_roles)}）——"
                        "typed skipped，不伪装输出"))
                continue
            # required 但 policy=degrade 的角色缺失：步骤 blocked + 披露
            role = missing_roles[0]
            verdict_map[step.step_id] = StepPlanVerdict(
                step_id=step.step_id, verdict="blocked",
                reason_code=role_missing_code(role),
                message=f"数据角色 {role} 缺失（degrade 政策——需披露后降级）",
                alternatives=_ROLE_ALTERNATIVES.get(role, ()))
            blocked_reasons.append(
                f"{step.step_id}:{role_missing_code(role)}")
            runnable = False
            continue
        # ADR-0282 D4：分母字段 plan 期校验（列清单是画像事实）——
        # 目标列不在清单 → typed skipped（不运行期 failed、不伪装率）。
        denom_fail = _denominator_field_gate(step, contract)
        if denom_fail is not None:
            code, msg = denom_fail
            verdict_map[step.step_id] = StepPlanVerdict(
                step_id=step.step_id, verdict="skipped",
                reason_code=code, message=msg)
            continue
        # ADR-0282 D3：role_kind 门——角色需求声明 raster/table 载荷类型
        # 而绑定携带其它类型（或反之）→ typed blocked（栅格 ref 不能被
        # 当 vector 水合、vector FC 不能直通 raster_path 参数）。
        kind_block: Optional[Tuple[str, str]] = None
        for role in _roles_of_step(step):
            req = req_by_role.get(role)
            binding = contract.data_contracts.get(role)
            if req is None or binding is None or _binding_missing(binding):
                continue
            req_kinds = set(req.geometry_kinds or ())
            if not (req_kinds and req_kinds <= {"raster", "table"}):
                continue
            if binding.role_kind not in req_kinds:
                kind_block = (
                    f"{AWF_ROLE_KIND_MISMATCH}:{role.upper()}",
                    f"角色 {role} 需要载荷类型 {sorted(req_kinds)}"
                    f"（绑定声明 {binding.role_kind!r}）——栅格/矢量类型"
                    "不通，typed 拒绝（绑定需 role_kind=raster 的 ref/路径）")
                break
        if kind_block is not None:
            code, msg = kind_block
            verdict_map[step.step_id] = StepPlanVerdict(
                step_id=step.step_id, verdict="blocked",
                reason_code=code, message=msg)
            blocked_reasons.append(f"{step.step_id}:{code}")
            runnable = False
            continue
        # ADR-0282 D2：角色几何画像门——事实在且冲突 → typed blocked
        # （画像来自上游数据事实，非猜测）；事实缺失 → 警告披露（不阻断，
        # 执行期衔接门与工具级 preflight 兜底——不虚构事实也不假装校验过）。
        semantic_block = None
        for role in _roles_of_step(step):
            req = req_by_role.get(role)
            binding = contract.data_contracts.get(role)
            if req is None or binding is None or _binding_missing(binding):
                continue
            finding = check_role_qualifier(
                {"role": role, "geometry_kinds": req.geometry_kinds}, binding)
            if finding is not None:
                semantic_block = finding
                break
            if req.geometry_kinds and not (
                    isinstance(binding.qualifier.get("geometry_types"),
                               (list, tuple))
                    and binding.qualifier.get("geometry_types")):
                warnings.append(
                    f"角色 {role} 几何画像缺失（qualifier.geometry_types）"
                    "——plan 期未核验，执行期衔接门兜底")
        if semantic_block is not None:
            verdict_map[step.step_id] = StepPlanVerdict(
                step_id=step.step_id, verdict="blocked",
                reason_code=semantic_block.code,
                message=semantic_block.message)
            blocked_reasons.append(
                f"{step.step_id}:{semantic_block.code}")
            runnable = False
            continue
        verdict_map[step.step_id] = StepPlanVerdict(
            step_id=step.step_id, verdict="ready")

    verdicts = list(verdict_map.values())

    return WorkflowPlanResult(
        contract=contract,
        fingerprint=contract_fingerprint(contract),
        step_verdicts=verdicts,
        runnable=runnable,
        blocked_reasons=tuple(blocked_reasons),
        alternatives=tuple(dict.fromkeys(alternatives)),
        visualization_hints=visualization_hints(family),
        warnings=tuple(warnings),
    )
