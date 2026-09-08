"""Workflow Parameters V4 —— 运行时参数解析 + provenance（不阻塞人工）。

参数语义的单一事实源是算法层 ParameterContractRegistry（parameter_contracts
注册表：type/default/min/max/enum/unit/数据依赖默认规则）。本模块把选中
方法的算法参数提升为**工作流参数**：

    extract（契约引用）→ resolve（默认自动 → hint → 用户覆盖）→
    ResolvedParameter（provenance 明确：recipe_default / hint / user）

红线：

- 默认值确定性自动解析，**不阻塞等待人工确认**；歧义但可安全消歧的
  情形走默认并披露；
- 用户值非法（超范围/枚举外）→ 回落默认 + 披露（安全降级，不失败）；
  不静默接受非法值；
- 数据依赖默认（"auto"）不在本模块求解 —— 只登记规则 id，求解归算法
  实现（契约层同红线：不是第二实现）；
- 全部确定性、零 LLM / 零 I/O。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel

#: 参数 provenance 词表（来源透明：谁决定了这个值）。
PARAMETER_PROVENANCES = ("recipe_default", "hint", "user")

_MAX_PARAMS = 16


class WorkflowParameter(BaseModel):
    """一个工作流参数（引用算法层契约，不复制默认值逻辑）。"""
    name: str
    value_type: str = "number"         # ⊆ ParamType（parameter_contracts）
    default: Optional[Any] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    enum_values: Tuple[str, ...] = ()
    unit: str = ""
    data_dependent_default: str = ""   # DATA_DEPENDENT_RULES id（求解归算法层）
    node_id: str = ""                  # 拥有该参数的 typed DAG 节点
    description: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name[:48],
            "value_type": self.value_type[:16],
            "default": self.default,
            "unit": self.unit[:16],
            "data_dependent_default": self.data_dependent_default[:48],
            "node_id": self.node_id[:64],
            "description": self.description[:120],
        }


class ResolvedParameter(BaseModel):
    """解析后的参数值 + 来源 + 披露（非法用户值回落时非空 disclosure）。"""
    name: str
    value: Any
    provenance: str                    # ⊆ PARAMETER_PROVENANCES
    disclosure: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name[:48],
            "value": self.value,
            "provenance": self.provenance,
            "disclosure": self.disclosure[:200],
        }


def extract_workflow_parameters(
    selected_method: Any,
    *,
    node_id: str = "",
) -> List[WorkflowParameter]:
    """选中方法 → 工作流参数（引用 parameter contracts 单一事实源）。"""
    from app.lib.gis.parameter_contracts import (
        get_parameter_contract_registry,
    )
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    registry = get_parameter_contract_registry()
    algorithms = get_algorithm_registry()
    specs: List[WorkflowParameter] = []
    seen: set = set()
    for alg_id in (getattr(selected_method, "algorithm_ids", ()) or ()):
        algo = algorithms.get(alg_id)
        if algo is None or not algo.parameter_contract_ref:
            continue
        contract = registry.get(algo.parameter_contract_ref)
        if contract is None:
            continue
        for p in contract.parameters:
            if p.name in seen:
                continue
            seen.add(p.name)
            specs.append(WorkflowParameter(
                name=p.name, value_type=p.type, default=p.default,
                minimum=p.minimum, maximum=p.maximum,
                enum_values=tuple(p.enum_values), unit=p.unit,
                data_dependent_default=p.data_dependent_default,
                node_id=node_id, description=p.description[:120],
            ))
            if len(specs) >= _MAX_PARAMS:
                return specs
    return specs


def _value_valid(param: WorkflowParameter, value: Any) -> bool:
    if value is None:
        return False
    if param.enum_values:
        return str(value) in param.enum_values
    if param.value_type == "integer":
        # integer 严格：拒绝浮点/布尔静默通过（不静默接受非法值红线）
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    elif param.value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
    if param.value_type in ("number", "integer"):
        if param.minimum is not None and value < param.minimum:
            return False
        if param.maximum is not None and value > param.maximum:
            return False
    if param.value_type == "boolean" and not isinstance(value, bool):
        return False
    return True


def resolve_workflow_parameters(
    params: Sequence[WorkflowParameter],
    *,
    user_values: Optional[Dict[str, Any]] = None,
    hint_values: Optional[Dict[str, Any]] = None,
) -> List[ResolvedParameter]:
    """确定性解析：user > hint > 默认；非法值回落默认 + 披露（不阻塞）。"""
    resolved: List[ResolvedParameter] = []
    for p in params:
        user_vals = user_values or {}
        hint_vals = hint_values or {}
        if p.name in user_vals and _value_valid(p, user_vals[p.name]):
            resolved.append(ResolvedParameter(
                name=p.name, value=user_vals[p.name], provenance="user"))
            continue
        if p.name in hint_vals and _value_valid(p, hint_vals[p.name]):
            resolved.append(ResolvedParameter(
                name=p.name, value=hint_vals[p.name], provenance="hint"))
            continue
        if p.name in user_vals or p.name in hint_vals:
            resolved.append(ResolvedParameter(
                name=p.name, value=p.default, provenance="recipe_default",
                disclosure=(
                    f"参数 {p.name} 的提供值非法（超出范围/枚举），"
                    f"已回落默认值 {p.default!r}。")))
            continue
        resolved.append(ResolvedParameter(
            name=p.name, value=p.default, provenance="recipe_default"))
    return resolved
