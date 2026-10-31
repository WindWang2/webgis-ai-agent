"""AnalysisWorkflowContract v1 —— 步骤×数据角色×科学合同的薄契约层。

设计约束（goal-02 M2，接口冻结见 INTERFACE_CONTRACTS.md）：

- **不是 PlanGraph**：没有节点调度/波次语义——只有「族 × 有序步骤 ×
  数据合同」；执行编排（波次/并发/取消）属于 executor，不进契约。
- **词表单一事实源**：data role / recompute 维度 / 科学前置 id 分别引用
  workflow_schema.DATA_ROLES、RECOMPUTE_DIMENSIONS 与
  scientific_preconditions._PRECONDITIONS——本模块零新词表。
- **确定性**：契约指纹 = canonical-JSON sha256（schema_version 进指纹）；
  模型内无时间戳。
- **fail-closed 校验**：悬空工具名 / 未知角色 / 未注册前置 / 环依赖 /
  空单位合同在 ``validate_workflow_contract`` 一律显式失败（词表谓词与
  工具存在性检查器以参数注入——纯函数，不依赖 registry 单例）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.workflow_schema import (
    DATA_ROLES,
    RECOMPUTE_DIMENSIONS,
    role_reason_code,
)

#: 契约 schema 版本（进内容指纹；additive 演进只升此号）。
ANALYSIS_WORKFLOW_SCHEMA_VERSION = 1

# ── Typed 原因码（fail-closed；工作流层 AWF_ 前缀，语义与既有
#    DATA_ROLE_MISSING_<ROLE> / F_TASK_* 家族对齐，不替换原码）─────────────

AWF_DATA_ROLE_MISSING = "AWF_DATA_ROLE_MISSING"          # + ":<role>" 细节
AWF_NO_NETWORK_FOR_ACCESSIBILITY = "AWF_NO_NETWORK_FOR_ACCESSIBILITY"
AWF_DENOMINATOR_MISSING = "AWF_DENOMINATOR_MISSING"
AWF_TEMPORAL_MISMATCH = "AWF_TEMPORAL_MISMATCH"
AWF_TOOL_UNREGISTERED = "AWF_TOOL_UNREGISTERED"          # + ":<tool>"
AWF_UNIT_CONTRACT_VIOLATION = "AWF_UNIT_CONTRACT_VIOLATION"
AWF_BUDGET_EXCEEDED = "AWF_BUDGET_EXCEEDED"
AWF_CARDINALITY_UNBOUNDED = "AWF_CARDINALITY_UNBOUNDED"
AWF_UNKNOWN_FAMILY = "AWF_UNKNOWN_FAMILY"
AWF_INVALID_CONTRACT = "AWF_INVALID_CONTRACT"

#: 步骤参数单位合同的合法单位词表（长度/时间/无量纲；扩展走 additive）。
UNIT_VOCABULARY = ("m", "km", "min", "h", "unitless")


def role_missing_code(role: str) -> str:
    """角色缺失码（AWF 前缀 + 既有 role_reason_code 尾段，保持同构）。"""
    tail = role_reason_code(role).removeprefix("DATA_ROLE_MISSING_")
    return f"{AWF_DATA_ROLE_MISSING}:{tail or role.upper()}"


class ScienceContext(BaseModel):
    """工作流级科学上下文（进每步指纹——单位/时区变了就是不同计算）。

    metric_crs_policy：距离/面积计算的坐标策略声明（"auto_utm" = 复用
    既有算子的 UTM 自动投影纪律；本层不新造投影语义，只把策略钉进指纹）。
    """

    metric_crs_policy: str = "auto_utm"
    units: Dict[str, str] = Field(default_factory=dict)   # 逻辑量 → 单位
    timezone: str = ""
    denominator_policy: str = "explicit_only"             # ADR-0099 对齐


class DataRoleBinding(BaseModel):
    """一个数据角色在本工作流实例中的绑定状态（诚实三态）。

    ref 非空 = 已绑定（session store ref 或本地路径）；
    ref 空 + missing=True = 显式缺失（typed blocked 裁决依据）；
    二者皆空 = 未决（调用方还没给——plan 期允许，run 期按缺失处理）。
    """

    role: str                                            # ⊆ DATA_ROLES
    ref: str = ""
    dataset_hint: str = ""                               # 供给线索（不猜 ref）
    missing: bool = False
    qualifier: Dict[str, Any] = Field(default_factory=dict)  # 行数/几何类/年份等画像事实


class AnalysisStepSpec(BaseModel):
    """一个步骤：既有工具调用 + 角色/单位合同 + 科学前置。

    role_inputs 值格式（executor 解析）：
    - ``role:<role>``   —— 来自 DataRoleBinding（run 期先水合为载荷）
    - ``step:<step_id>`` —— 来自上游步骤输出（FC 直接传递，不重复落 ref）
    """

    step_id: str
    tool_name: str
    capability: str = ""
    role_inputs: Dict[str, str] = Field(default_factory=dict)
    step_params: Dict[str, Any] = Field(default_factory=dict)
    unit_contract: Dict[str, str] = Field(default_factory=dict)  # 参数名 → UNIT_VOCABULARY
    depends_on: Tuple[str, ...] = ()
    preconditions: Tuple[str, ...] = ()                  # scientific_preconditions id
    output_roles: Tuple[str, ...] = ()
    fallback: str = ""                                   # 显式降级名（如 buffer_proxy_disclosed）
    optional: bool = False                               # True: 输入缺失时 typed skipped 而非 blocked


class AnalysisWorkflowContract(BaseModel):
    """族实例契约（薄；可视化建议只是元数据——制图裁决仍在 converter/planner）。"""

    schema_version: int = ANALYSIS_WORKFLOW_SCHEMA_VERSION
    workflow_id: str
    family: str
    label_zh: str = ""
    recipe_refs: Tuple[str, ...] = ()                    # 引用既有 recipe id（不复制）
    steps: Tuple[AnalysisStepSpec, ...] = ()
    data_contracts: Dict[str, DataRoleBinding] = Field(default_factory=dict)
    science_context: ScienceContext = Field(default_factory=ScienceContext)
    recompute_dimensions: Tuple[str, ...] = ("data", "algorithm", "parameter")
    risk_notes: Tuple[str, ...] = ()
    visualization_hints: Tuple[str, ...] = ()


#: 步骤结局状态词表（与 workflow_instance.StageState 词形对齐；
#: failed/cancelled 绝不冒充 satisfied）。
STEP_OUTCOME_STATES = (
    "pending", "running", "satisfied", "skipped", "blocked",
    "failed", "cancelled", "stale",
)


class StepOutcome(BaseModel):
    """步骤执行结果（诚实语义：failed/cancelled/skipped 不是完成）。"""

    state: str = "pending"
    reason_code: str = ""
    message: str = ""


class AnalysisArtifactEnvelope(BaseModel):
    """步骤产物封套（大 FC 本体在 ref: 里——由 dispatch 缝铸造，本层只记账）。

    与 artifact_registry.ArtifactRecord 的关系：Record 是注册表行（lineage/
    supersede/LRU），本封套是工作流步骤视角的**结果证据**（stats/单位/
    receipt/警告），二者经 ``ref`` 关联，不复制 lineage 语义。
    """

    step_id: str
    workflow_id: str
    tool_name: str
    ref: str = ""                                         # ref:geojson-* / ref:raster/*
    crs: str = ""
    units: Dict[str, str] = Field(default_factory=dict)
    feature_count: int = 0
    empty: bool = False
    stats: Dict[str, Any] = Field(default_factory=dict)
    warnings: Tuple[str, ...] = ()
    lineage_inputs: Tuple[str, ...] = ()                  # 上游 ref（≤16，对齐 registry）
    method_algorithm_id: str = ""                         # MethodUseContract.algorithm_id
    fingerprint: str = ""                                 # recompute.step_fingerprint
    output_roles: Tuple[str, ...] = ()


class ContractValidationError(ValueError):
    """契约校验失败（fail-closed；错误信息机器可切分：[code] 前缀）。"""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{detail} [{code}]")


def canonical_json(payload: Any) -> str:
    """canonical JSON（确定性序列化；与 workflow_instance 同纪律）。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def contract_fingerprint(contract: AnalysisWorkflowContract) -> str:
    """契约内容指纹（结构+参数；不含绑定 ref——ref 换内容才算变化）。"""
    payload = {
        "schema": contract.schema_version,
        "family": contract.family,
        "steps": [s.model_dump() for s in contract.steps],
        "science": contract.science_context.model_dump(),
        "recompute": list(contract.recompute_dimensions),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _check_step_dag(steps: List[AnalysisStepSpec]) -> None:
    """依赖闭包校验：悬空引用 / 自环 / 环 / 重复 step_id 显式失败。"""
    by_id = {s.step_id: s for s in steps}
    if len(by_id) != len(steps):
        dupes = sorted({s.step_id for s in steps if
                        [x.step_id for x in steps].count(s.step_id) > 1})
        raise ContractValidationError(
            AWF_INVALID_CONTRACT, f"重复 step_id: {dupes}")
    for step in steps:
        for dep in step.depends_on:
            if dep not in by_id:
                raise ContractValidationError(
                    AWF_INVALID_CONTRACT,
                    f"步骤 {step.step_id} 依赖不存在的步骤 {dep}")
        if step.step_id in step.depends_on:
            raise ContractValidationError(
                AWF_INVALID_CONTRACT, f"步骤 {step.step_id} 自引用")
        for param, source in step.role_inputs.items():
            if source.startswith("step:"):
                upstream = source.removeprefix("step:")
                if upstream not in by_id:
                    raise ContractValidationError(
                        AWF_INVALID_CONTRACT,
                        f"步骤 {step.step_id} 参数 {param} 引用不存在的上游步骤 {upstream}")
        # 环检测（逐步 DFS，契约步骤 ≤24，无需 Tarjan）
        seen: set[str] = set()
        stack = [step.step_id]
        while stack:
            cur = stack.pop()
            if cur in seen:
                raise ContractValidationError(
                    AWF_INVALID_CONTRACT, f"依赖环：包含步骤 {cur}")
            seen.add(cur)
            stack.extend(by_id[cur].depends_on)


def validate_workflow_contract(
    contract: AnalysisWorkflowContract,
    *,
    tool_exists: Optional[Callable[[str], bool]] = None,
    precondition_exists: Optional[Callable[[str], bool]] = None,
    family_exists: Optional[Callable[[str], bool]] = None,
) -> List[str]:
    """fail-closed 契约校验；返回有界警告列表（不抛的降级披露）。

    - 角色/维度词表越界、环依赖、单位越界 → ContractValidationError；
    - 悬空工具名 → tool_exists 注入时校验（None = 跳过注册面检查——
      模板静态期允许，run 期必查）；
    - 未注册科学前置 → 同上（precondition_exists 默认接
      scientific_preconditions.precondition_exists）。
    """
    warnings: List[str] = []
    if contract.schema_version != ANALYSIS_WORKFLOW_SCHEMA_VERSION:
        raise ContractValidationError(
            AWF_INVALID_CONTRACT,
            f"schema_version 不支持: {contract.schema_version}")
    if family_exists is not None and not family_exists(contract.family):
        raise ContractValidationError(
            AWF_UNKNOWN_FAMILY, f"未知工作流族: {contract.family}")
    if not contract.steps:
        raise ContractValidationError(
            AWF_INVALID_CONTRACT, "契约无步骤")
    if len(contract.steps) > 24:
        raise ContractValidationError(
            AWF_INVALID_CONTRACT,
            f"步骤数超上限 24: {len(contract.steps)}")

    for dim in contract.recompute_dimensions:
        if dim not in RECOMPUTE_DIMENSIONS:
            raise ContractValidationError(
                AWF_INVALID_CONTRACT,
                f"未知重算维度 {dim}（词表: {RECOMPUTE_DIMENSIONS}）")
    if "style" in contract.recompute_dimensions:
        warnings.append("style 维度不参与科学重算（声明将被忽略）")

    for role in contract.data_contracts:
        if role not in DATA_ROLES:
            raise ContractValidationError(
                AWF_INVALID_CONTRACT,
                f"未知数据角色 {role}（词表: {DATA_ROLES}）")

    _check_step_dag(list(contract.steps))

    pc = precondition_exists
    if pc is None:
        from app.lib.gis.scientific_preconditions import (
            precondition_exists as _pc,
        )
        pc = _pc
    for step in contract.steps:
        if not step.tool_name:
            raise ContractValidationError(
                AWF_INVALID_CONTRACT, f"步骤 {step.step_id} 无 tool_name")
        if tool_exists is not None and not tool_exists(step.tool_name):
            raise ContractValidationError(
                f"{AWF_TOOL_UNREGISTERED}:{step.tool_name}",
                f"步骤 {step.step_id} 引用未注册工具 {step.tool_name}")
        for pre in step.preconditions:
            if not pc(pre):
                raise ContractValidationError(
                    AWF_INVALID_CONTRACT,
                    f"步骤 {step.step_id} 引用未注册科学前置 {pre}")
        for param, unit in step.unit_contract.items():
            if unit not in UNIT_VOCABULARY:
                raise ContractValidationError(
                    AWF_UNIT_CONTRACT_VIOLATION,
                    f"步骤 {step.step_id} 参数 {param} 单位越界: {unit}")
        for source in step.role_inputs.values():
            if not (source.startswith("role:") or source.startswith("step:")):
                raise ContractValidationError(
                    AWF_INVALID_CONTRACT,
                    f"步骤 {step.step_id} 输入源格式非法: {source}")
            if source.startswith("role:"):
                role = source.removeprefix("role:")
                if role not in contract.data_contracts:
                    raise ContractValidationError(
                        AWF_INVALID_CONTRACT,
                        f"步骤 {step.step_id} 引用未声明角色 {role}")
    return warnings
