"""MethodUseContract —— 方法使用契约的单一投影视图（G02，schema v1）。

定位（单一真相纪律）：本模块**不持有任何元数据**，只是既有权威的
只读投影 + 版本化视图：

- AlgorithmRegistry（algorithm_registry.py）—— crs_class /
  scientific_preconditions / fallback 语义 / uncertainty_outputs /
  method_references / assumptions / limitations / 数值容差；
- ParameterContractRegistry（parameter_contracts.py）—— 参数名/类型/
  单位/界/枚举；
- MethodReferences（method_references.py）—— 文献条目存在性；
- CapabilityRegistry（capability_registry.py）—— capability 反查。

消费面：plan/execute/final 三段科学守门对账（method/parameter/unit 一致
性）、只读 CLI 检视（scripts/inspect_method_contract.py）、制品披露。
投影失败诚实缺省（空串/空表 + findings），绝不虚构、绝不抛错阻断调用方。
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from pydantic import BaseModel, Field

#: 契约投影的 schema 版本（投影结构变更时 +1；权威数据自身的版本在各
#: registry，不在此重复维护）。
CONTRACT_SCHEMA_VERSION = "1"

#: 单算法 not_evaluated/finding 披露上限（有界证据纪律）。
_MAX_FINDINGS = 12


class ContractParameter(BaseModel):
    """参数契约的投影行（来自 ParameterContractRegistry，零复制权威值）。"""

    name: str
    type: str = ""
    required: bool = False
    unit: str = ""                 # PARAM_UNIT_VOCABULARY 词表值（可空=未声明）
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    enum_values: List[str] = Field(default_factory=list, max_length=16)
    data_dependent_default: str = ""
    description: str = ""


class MethodUseContract(BaseModel):
    """一个算法的方法使用契约投影（只读、确定性、零 IO）。

    ``findings`` 是投影过程中的结构化诚实披露（词表外单位/悬空引用等），
    不是运行时校验错误 —— 调用方（CLI / final gate）决定如何消费。
    """

    schema_version: str = CONTRACT_SCHEMA_VERSION
    algorithm_id: str
    capabilities: List[str] = Field(default_factory=list, max_length=8)
    algorithm_family: str = ""
    runtime_status: str = ""
    scientific_status: str = ""
    version: str = ""
    crs_class: str = ""
    unit_requirements: str = ""
    geometry_requirements: List[str] = Field(default_factory=list, max_length=8)
    min_features: Optional[int] = None
    scientific_preconditions: List[str] = Field(default_factory=list, max_length=12)
    precondition_descriptions: Dict[str, str] = Field(default_factory=dict)
    parameters: List[ContractParameter] = Field(default_factory=list, max_length=24)
    parameter_contract_id: str = ""
    parameter_contract_version: Optional[int] = None
    fallback_algorithms: List[str] = Field(default_factory=list, max_length=6)
    fallback_semantics: Dict[str, str] = Field(default_factory=dict)
    uncertainty_outputs: List[str] = Field(default_factory=list, max_length=8)
    method_references: List[str] = Field(default_factory=list, max_length=8)
    assumptions: List[str] = Field(default_factory=list, max_length=8)
    limitations: List[str] = Field(default_factory=list, max_length=8)
    numerical_tolerance: str = ""
    deterministic: bool = True
    findings: List[str] = Field(default_factory=list, max_length=_MAX_FINDINGS)

    @property
    def has_parameter_contract(self) -> bool:
        return bool(self.parameter_contract_id)


def _precondition_description(precondition_id: str) -> str:
    """前置条件 id → 描述（参数化 id 取词表参数化键）。"""
    from app.lib.gis.scientific_preconditions import PRECONDITION_DESCRIPTIONS

    if precondition_id in PRECONDITION_DESCRIPTIONS:
        return PRECONDITION_DESCRIPTIONS[precondition_id]
    prefix = precondition_id.split(":", 1)[0]
    key = f"{prefix}:N"
    return PRECONDITION_DESCRIPTIONS.get(key, "")


def _capability_reverse_map() -> Dict[str, List[str]]:
    """algorithm_id → capabilities（单次遍历构建；注册表只读）。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.capability_registry import get_capability_registry

    reg = get_algorithm_registry()
    caps = get_capability_registry()
    reverse: Dict[str, List[str]] = {}
    for cid in caps.all_ids:
        try:
            for algo in reg.algorithms_for_capability(cid):
                reverse.setdefault(algo.id, []).append(cid)
        except Exception:  # noqa: BLE001 — 投影是增值，注册表异常不阻断
            continue
    return reverse


def build_method_use_contract(algorithm_id: str) -> MethodUseContract:
    """构建单算法契约投影（未知算法 → 空 capabilities + finding，不抛错）。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    findings: List[str] = []
    algo = get_algorithm_registry().get(algorithm_id)
    if algo is None:
        return MethodUseContract(
            algorithm_id=algorithm_id,
            findings=[f"unknown_algorithm:{algorithm_id}"],
        )

    capabilities = sorted(_capability_reverse_map().get(algorithm_id, []))
    if not capabilities:
        findings.append("no_capability_binding")

    precond_desc = {
        pid: _precondition_description(pid)
        for pid in algo.scientific_preconditions
    }
    for pid, desc in precond_desc.items():
        if not desc:
            findings.append(f"precondition_without_description:{pid}")

    parameters: List[ContractParameter] = []
    contract_id = ""
    contract_version: Optional[int] = None
    if algo.parameter_contract_ref:
        from app.lib.gis.parameter_contracts import (
            PARAM_UNIT_VOCABULARY,
            get_parameter_contract_registry,
        )

        contract = get_parameter_contract_registry().get(
            algo.parameter_contract_ref)
        if contract is None:
            findings.append(
                f"dangling_parameter_contract:{algo.parameter_contract_ref}")
        else:
            contract_id = contract.id
            contract_version = contract.version
            for spec in contract.parameters:
                parameters.append(ContractParameter(
                    name=spec.name,
                    type=spec.type,
                    required=spec.required,
                    unit=spec.unit,
                    minimum=spec.minimum,
                    maximum=spec.maximum,
                    enum_values=list(spec.enum_values)[:16],
                    data_dependent_default=spec.data_dependent_default,
                    description=spec.description[:160],
                ))
                if spec.unit and spec.unit not in PARAM_UNIT_VOCABULARY:
                    findings.append(
                        f"unit_outside_vocabulary:{spec.name}:{spec.unit}")

    if algo.method_references:
        from app.lib.gis.method_references import reference_exists

        for ref in algo.method_references:
            if not reference_exists(ref):
                findings.append(f"dangling_method_reference:{ref}")

    for fb_id in algo.fallback_algorithms:
        if fb_id not in algo.fallback_semantics:
            findings.append(f"fallback_without_semantics:{fb_id}")

    return MethodUseContract(
        algorithm_id=algo.id,
        capabilities=capabilities[:8],
        algorithm_family=algo.algorithm_family,
        runtime_status=algo.runtime_status,
        scientific_status=algo.scientific_status,
        version=algo.version,
        crs_class=algo.crs_class,
        unit_requirements=algo.unit_requirements,
        geometry_requirements=list(algo.geometry_requirements)[:8],
        min_features=algo.min_features,
        scientific_preconditions=list(algo.scientific_preconditions)[:12],
        precondition_descriptions=precond_desc,
        parameters=parameters,
        parameter_contract_id=contract_id,
        parameter_contract_version=contract_version,
        fallback_algorithms=list(algo.fallback_algorithms)[:6],
        fallback_semantics=dict(algo.fallback_semantics),
        uncertainty_outputs=list(algo.uncertainty_outputs)[:8],
        method_references=list(algo.method_references)[:8],
        assumptions=list(algo.assumptions)[:8],
        limitations=list(algo.limitations)[:8],
        numerical_tolerance=algo.numerical_tolerance[:160],
        deterministic=algo.deterministic,
        findings=findings[:_MAX_FINDINGS],
    )


def iter_method_use_contracts() -> Iterator[MethodUseContract]:
    """全注册表的契约投影迭代（确定性顺序：algorithm id 升序）。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    for algorithm_id in sorted(get_algorithm_registry().all_ids):
        yield build_method_use_contract(algorithm_id)


def contract_to_dict(contract: MethodUseContract) -> Dict[str, Any]:
    """契约 → 可序列化 dict（CLI/JSON 消费；pydantic model_dump 投影）。"""
    return contract.model_dump()
