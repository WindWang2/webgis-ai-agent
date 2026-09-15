"""Parameter Generalizer —— 常量模式识别与 typed 参数泛化（ADR-0191 D2）。

三步法第一步：把成功轨迹里硬编码在工具参数中的常量（经纬度、地名、
阈值、日期、字段名……）提取为带语义角色与约束的 typed 参数槽位，
并用 ``pydantic.create_model`` 合成参数 Schema——换参重放时新值可
校验、越界值 ``ValidationError``（沙盒变体重放的拦截层）。

去毒前置纪律（ADR-0191 D4）：敏感键（token/key/password/credential 族）
与多行/超长自由文本**不泛化、不入 Schema**，只记入 ``skipped``。

全部确定性：同输入同输出，零 LLM、零 I/O。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field, create_model

#: 参数语义角色词表。
SEMANTIC_ROLES = (
    "coordinate", "threshold", "place_name", "field", "date", "generic",
)

#: 敏感键模式：命中即跳过泛化（去毒前置，ADR-0191 D4）。
SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|passwd|api_?key|credential|authorization)",
    re.IGNORECASE,
)

#: 行政区后缀（地名语义的确定性信号）。
_ADMIN_SUFFIXES = ("省", "市", "区", "县", "镇", "乡", "自治州", "盟")

#: ISO 日期形态。
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 安全文本模式（约束层；注入载荷在该层即被拒绝）。
SAFE_TEXT_PATTERN = r"^[\w\-\.\u4e00-\u9fa5 ]+$"
_PLACE_PATTERN = r"^[\w\u4e00-\u9fa5\-]+$"
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

_THRESHOLD_KEYS = ("threshold", "limit", "alpha", "sig", "cutoff", "p_value")
_FIELD_KEYS = ("layer", "field", "column", "attribute")

#: 经纬度键名 → 值域（坐标语义的硬边界）。
_COORDINATE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "lon": (-180.0, 180.0),
    "lng": (-180.0, 180.0),
    "latitude": (-90.0, 90.0),
    "lat": (-90.0, 90.0),
}

_MAX_TEXT_VALUE = 128  # 超长自由文本不泛化


class InducedParameter(BaseModel):
    """一个泛化出的参数槽位（语义角色 + 约束 + 溯源）。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str                            # float | int | str | bool
    semantic_role: str                   # ⊆ SEMANTIC_ROLES
    constraints: Dict[str, Any] = Field(default_factory=dict)
    example: Any = None                  # 源轨迹观测值（JSON 标量）
    description: str = ""
    source_tool: str = ""
    source_key: str = ""


def build_model_from_parameters(
    parameters: List[InducedParameter],
    *,
    model_name: str = "InducedParameters",
) -> Type[BaseModel]:
    """参数槽位 → 动态 Pydantic 模型（全部必填；约束进入 Field）。"""
    fields: Dict[str, Tuple[Any, Any]] = {}
    for p in parameters:
        annotation = {"float": float, "int": int,
                      "str": str, "bool": bool}.get(p.type, str)
        kwargs: Dict[str, Any] = {
            "description": p.description or f"{p.semantic_role} 参数"}
        for key in ("ge", "le", "min_length", "max_length", "pattern"):
            if key in p.constraints:
                kwargs[key] = p.constraints[key]
        fields[p.name] = (annotation, Field(**kwargs))
    return create_model(model_name, **fields)


def _slug_identifier(key: str) -> str:
    slug = re.sub(r"\W", "_", key)
    slug = re.sub(r"^(\d)", r"p_\1", slug)
    return slug or "param"


def _numeric_constraints(key: str, value: float
                         ) -> Tuple[str, str, Dict[str, Any]]:
    """(role, type, constraints)；坐标/阈值/一般数值三条确定性规则。"""
    lk = key.lower()
    numeric_type = "int" if isinstance(value, int) else "float"
    coord_key = lk if lk in _COORDINATE_BOUNDS else lk.rstrip("0123456789")
    if coord_key in _COORDINATE_BOUNDS:
        ge, le = _COORDINATE_BOUNDS[coord_key]
        return "coordinate", "float", {"ge": ge, "le": le}
    if any(k in lk for k in _THRESHOLD_KEYS):
        bound = abs(float(value)) * 10.0 or 1.0
        if value >= 0:
            return "threshold", numeric_type, {"ge": 0, "le": bound}
        return "threshold", numeric_type, {"ge": bound * -1.0, "le": 0}
    # 其余数值：观测值数量级邻域带（/10 .. ×10），确定性。
    if value > 0:
        return "generic", numeric_type, {"ge": value / 10.0, "le": value * 10.0}
    if value < 0:
        return "generic", numeric_type, {"ge": value * 10.0, "le": value / 10.0}
    return "generic", numeric_type, {"ge": -1.0, "le": 1.0}


def _classify(key: str, value: Any) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """(键, 值) → (semantic_role, type, constraints)；返回 None = 不泛化。"""
    lk = (key or "").lower()
    if SENSITIVE_KEY_PATTERN.search(lk):
        return None
    if isinstance(value, bool):
        return "generic", "bool", {}
    if isinstance(value, (int, float)):
        return _numeric_constraints(key, value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or "\n" in value or len(value) > _MAX_TEXT_VALUE:
            return None
        if _ISO_DATE.match(stripped) or "date" in lk:
            return "date", "str", {"pattern": _DATE_PATTERN,
                                   "min_length": 10, "max_length": 10}
        if any(stripped.endswith(s) for s in _ADMIN_SUFFIXES) \
                and len(stripped) <= 16:
            return "place_name", "str", {"min_length": 1, "max_length": 64,
                                         "pattern": _PLACE_PATTERN}
        if len(stripped) <= 8 and stripped.isalnum() \
                and stripped.upper() == stripped:
            return "field", "str", {"min_length": 1, "max_length": 64,
                                    "pattern": SAFE_TEXT_PATTERN}
        if any(k in lk for k in _FIELD_KEYS):
            return "field", "str", {"min_length": 1, "max_length": 64,
                                    "pattern": SAFE_TEXT_PATTERN}
        return "generic", "str", {"min_length": 1, "max_length": 128,
                                  "pattern": SAFE_TEXT_PATTERN}
    return None  # list/dict/None 等非安全标量不泛化


class ParameterGeneralization(BaseModel):
    """泛化产物：参数槽位集 + 跳过清单（审计面）。"""

    parameters: List[InducedParameter] = Field(default_factory=list)
    skipped: List[str] = Field(default_factory=list)

    def build_model(self) -> Type[BaseModel]:
        return build_model_from_parameters(self.parameters)

    def by_name(self) -> Dict[str, InducedParameter]:
        return {p.name: p for p in self.parameters}


def _multi_numeric_constraints(
        key: str, values: List[Any]) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """多观测数值的约束带：[min/10, max×10]（跨轨迹证据互相印证）。"""
    nums = [v for v in values
            if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    numeric_type = "int" if all(isinstance(v, int) for v in nums) else "float"
    lk = key.lower()
    coord_key = lk if lk in _COORDINATE_BOUNDS else lk.rstrip("0123456789")
    if coord_key in _COORDINATE_BOUNDS:
        ge, le = _COORDINATE_BOUNDS[coord_key]
        return "coordinate", "float", {"ge": ge, "le": le}
    if any(k in lk for k in _THRESHOLD_KEYS):
        bound = abs(hi) * 10.0 or 1.0
        if lo >= 0:
            return "threshold", numeric_type, {"ge": 0, "le": bound}
        return "threshold", numeric_type, {"ge": -bound, "le": 0}
    if lo > 0:
        return "generic", numeric_type, {"ge": lo / 10.0, "le": hi * 10.0}
    if hi < 0:
        return "generic", numeric_type, {"ge": lo * 10.0, "le": hi / 10.0}
    return ("generic", numeric_type,
            {"ge": min(-1.0, lo * 10.0), "le": max(1.0, hi * 10.0)})


def generalize_parameters(
    analysis,
    *,
    capability_map: Optional[Mapping[str, str]] = None,
) -> ParameterGeneralization:
    """从分析产物的步骤参数中提取 typed 参数槽位（纯函数）。

    步骤带 ``observations``（跨轨迹证据池，见 ``merge_analyses``）时，
    数值约束带由全部观测值的 [min/10, max×10] 张成；描述注明观测数。
    """
    parameters: List[InducedParameter] = []
    skipped: List[str] = []
    taken: set = set()

    for step in analysis.steps:
        for key, value in sorted((step.arguments or {}).items()):
            skey = str(key)
            if SENSITIVE_KEY_PATTERN.search(skey.lower()):
                skipped.append(f"{step.tool_name}.{skey} (sensitive_key)")
                continue
            pool = [v for v in (step.observations or {}).get(skey, [value])
                    if isinstance(v, (str, int, float, bool))]
            values = pool or [value]
            primary = values[0]
            classified = _classify(skey, primary)
            if classified is None:
                skipped.append(f"{step.tool_name}.{skey} (non_generalizable)")
                continue
            role, vtype, constraints = classified
            note = ""
            if len(values) > 1 and isinstance(primary, (int, float)) \
                    and not isinstance(primary, bool):
                widened = _multi_numeric_constraints(skey, values)
                if widened is not None:
                    role, vtype, constraints = widened
                    note = f"；n={len(values)} 观测"
            name = _slug_identifier(skey)
            if name in taken:
                name = f"{name}_{step.seq}"
            taken.add(name)
            parameters.append(InducedParameter(
                name=name,
                type=vtype,
                semantic_role=role,
                constraints=constraints,
                example=primary,
                description=f"{role} 参数（自 {step.tool_name}.{skey} 泛化；"
                            f"示例={primary!r}{note}）"[:200],
                source_tool=step.tool_name,
                source_key=skey,
            ))

    return ParameterGeneralization(parameters=parameters, skipped=skipped)


__all__ = [
    "SEMANTIC_ROLES",
    "SENSITIVE_KEY_PATTERN",
    "SAFE_TEXT_PATTERN",
    "InducedParameter",
    "ParameterGeneralization",
    "build_model_from_parameters",
    "generalize_parameters",
]
