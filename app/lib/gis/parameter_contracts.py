"""Parameter Contracts —— 类型化算法参数契约注册表（VNext §12）。

此前 ``AlgorithmDescriptor.parameter_contract_ref`` 是零消费的前向声明，
参数默认值/范围/单位散落在各工具签名里。本模块把「参数语义」收编为
可校验的注册表：

- 契约 id 即 descriptor 的 ``parameter_contract_ref``（单一事实源）；
- 每个参数声明 type/default/min/max/enum/unit/语义/是否数据依赖默认；
- ``validate_parameters`` 供测试与工具做收敛校验（不抛错的证据式返回）；
  ``apply_contract`` 是工具侧的严格入口（机器可读错误码）；
- 数据依赖默认（"auto"）不复制实现逻辑：契约只登记规则 id
  （``DATA_DEPENDENT_RULES``），规则本体在算法实现里 —— 契约是文档 +
  校验层，不是第二实现。

单位词表封闭（PARAM_UNIT_VOCABULARY）；algorithm 级
``unit_requirements`` 词表（米制分析前提）保持 algorithm_registry 侧
不动，二者职责不同：前者描述**参数**单位，后者描述**数据/输出**单位族。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

# ── 封闭词表 ─────────────────────────────────────────────────────────
ParamType = Literal["number", "integer", "string", "boolean", "enum"]

PARAM_UNIT_VOCABULARY = frozenset({
    "meters", "kilometers", "degrees", "pixels", "seconds",
    "m", "km", "unitless", "count", "ratio",
})

# "auto" 类数据依赖默认的规则登记（id → 规则说明）。规则本体在算法实现。
DATA_DEPENDENT_RULES: Dict[str, str] = {
    "variogram_least_rss": "variogram_model=auto → 加权 RSS 最低的模型（spherical/exponential/gaussian）",
    "h3_extent_resolution": "resolution=auto → 按数据度量范围选 H3 分辨率",
    "bandwidth_scott": "bandwidth=auto → Scott 正态参考规则 + kNN 上限钳制",
    "distance_band_8nn": "distance_band=auto → 8 近邻平均距离（Gi* 权重带宽）",
    "zfactor_latitude": "z_factor=auto → 按纬度 cos 修正（度栅格）",
}

MAX_CONTRACT_PARAMS = 24


class ParameterSpec(BaseModel):
    """一个算法参数的类型化声明。"""

    name: str
    type: ParamType
    description: str = ""                       # 语义（人读 + 文档生成）
    required: bool = False
    default: Optional[Any] = None
    minimum: Optional[float] = None             # number/integer
    maximum: Optional[float] = None
    enum_values: List[str] = Field(default_factory=list)   # enum 类型必填
    unit: str = ""                              # PARAM_UNIT_VOCABULARY
    data_dependent_default: str = ""            # DATA_DEPENDENT_RULES id

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        if not v or not v.replace("_", "a").isidentifier():
            raise ValueError(f"invalid parameter name: {v!r}")
        return v

    @field_validator("description")
    @classmethod
    def _bounded_description(cls, v: str) -> str:
        return v[:160]

    @field_validator("unit")
    @classmethod
    def _unit_vocab(cls, v: str) -> str:
        if v and v not in PARAM_UNIT_VOCABULARY:
            raise ValueError(
                f"unknown unit {v!r} (vocabulary: {sorted(PARAM_UNIT_VOCABULARY)})")
        return v

    @field_validator("data_dependent_default")
    @classmethod
    def _rule_exists(cls, v: str) -> str:
        if v and v not in DATA_DEPENDENT_RULES:
            raise ValueError(
                f"unknown data-dependent rule {v!r} "
                f"(registered: {sorted(DATA_DEPENDENT_RULES)})")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> "ParameterSpec":
        if self.type == "enum":
            if not self.enum_values:
                raise ValueError(f"enum parameter {self.name} needs enum_values")
            if self.default is not None and str(self.default) not in self.enum_values:
                raise ValueError(
                    f"parameter {self.name}: default {self.default!r} not in enum")
        if self.type in ("number", "integer"):
            if self.minimum is not None and self.maximum is not None \
                    and self.minimum > self.maximum:
                raise ValueError(f"parameter {self.name}: minimum > maximum")
        if self.required and self.default is not None:
            raise ValueError(
                f"parameter {self.name}: required 参数不应带默认值")
        if self.enum_values and self.type != "enum":
            raise ValueError(f"parameter {self.name}: enum_values 仅 enum 类型可用")
        return self


class ParameterContract(BaseModel):
    """一个算法的参数契约（id = descriptor.parameter_contract_ref）。"""

    id: str
    version: int = 1
    description: str = ""
    parameters: List[ParameterSpec] = Field(default_factory=list)

    @field_validator("parameters")
    @classmethod
    def _bounded_and_unique(cls, v: List[ParameterSpec]) -> List[ParameterSpec]:
        if len(v) > MAX_CONTRACT_PARAMS:
            raise ValueError(f"contract exceeds {MAX_CONTRACT_PARAMS} parameters")
        names = [p.name for p in v]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"duplicate parameters: {dupes}")
        return v

    def spec(self, name: str) -> Optional[ParameterSpec]:
        return next((p for p in self.parameters if p.name == name), None)

    def required_names(self) -> List[str]:
        return [p.name for p in self.parameters if p.required]


class ParameterValidationResult(BaseModel):
    """证据式校验结果（不抛错；issues 空 = 通过）。"""

    contract_id: str
    normalized: Dict[str, Any] = Field(default_factory=dict)
    issues: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    applied_defaults: List[str] = Field(default_factory=list)


def _check_type(spec: ParameterSpec, value: Any) -> Tuple[Optional[Any], Optional[str]]:
    """类型检查/安全收敛；返回 (value, issue)。"""
    if spec.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"{spec.name}: expected number, got {type(value).__name__}"
        return float(value), None
    if spec.type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, float) and float(value).is_integer():
                return int(value), None
            return None, f"{spec.name}: expected integer, got {type(value).__name__}"
        return int(value), None
    if spec.type == "boolean":
        if not isinstance(value, bool):
            return None, f"{spec.name}: expected boolean, got {type(value).__name__}"
        return value, None
    if spec.type == "enum":
        if str(value) not in spec.enum_values:
            return None, f"{spec.name}: {value!r} not in {spec.enum_values}"
        return str(value), None
    # string
    if not isinstance(value, str):
        return None, f"{spec.name}: expected string, got {type(value).__name__}"
    return value, None


def validate_parameters(
    contract: ParameterContract, params: Dict[str, Any],
) -> ParameterValidationResult:
    """校验 + 收敛：填默认值、类型收敛、范围/枚举检查。

    未知参数不报 issue（工具签名常有数据输入参数如 geojson/raster_path
    不在算法契约内）—— 契约只约束**算法参数**。
    """
    result = ParameterValidationResult(contract_id=contract.id)
    normalized: Dict[str, Any] = dict(params or {})
    by_name = {p.name: p for p in contract.parameters}

    for name, spec in by_name.items():
        if name not in normalized:
            if spec.default is not None:
                normalized[name] = spec.default
                result.applied_defaults.append(name)
            elif spec.required:
                result.issues.append(f"{name}: required parameter missing")
            continue
        value, issue = _check_type(spec, normalized[name])
        if issue:
            result.issues.append(issue)
            continue
        if spec.type in ("number", "integer") and value is not None:
            if spec.minimum is not None and value < spec.minimum:
                result.issues.append(
                    f"{name}: {value} < minimum {spec.minimum}")
                continue
            if spec.maximum is not None and value > spec.maximum:
                result.issues.append(
                    f"{name}: {value} > maximum {spec.maximum}")
                continue
        normalized[name] = value
        if spec.data_dependent_default and value == "auto":
            result.warnings.append(
                f"{name}=auto resolved by rule '{spec.data_dependent_default}'")

    result.normalized = normalized
    return result


# ── 种子契约（与既有 tool 签名逐位对齐；参数默认值即工具现值）──────
_SEED_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="buffer_analysis", version=1,
        description="几何缓冲：米制距离 → UTM 精确缓冲 → 回原 CRS。",
        parameters=[
            ParameterSpec(
                name="distance", type="number", required=True,
                minimum=0.0001,
                unit="meters",
                description="缓冲距离（unit 换算后取米值；必须 > 0）",
            ),
            ParameterSpec(
                name="unit", type="enum", default="m",
                enum_values=["m", "km"],
                unit="m",
                description="距离单位",
            ),
        ],
    ),
    ParameterContract(
        id="idw_interpolation", version=1,
        description="反距离加权插值（H3 格网，米制投影下执行）。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="插值数值字段名",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=8,
                minimum=6, maximum=9,
                description="H3 分辨率",
            ),
            ParameterSpec(
                name="power", type="integer", default=2,
                minimum=1, maximum=5,
                description="距离权重幂次（越大越偏局部）",
            ),
        ],
    ),
    ParameterContract(
        # v2：method 枚举（ordinary/universal）随插值域包 VNext 扩展加入；
        # version 提升使指纹反映契约面变化（runtime manifest v3）。
        id="kriging_interpolation", version=2,
        description="克里金插值（OK 默认；UK 线性漂移）：经验半变异函数 + 有界 LS 拟合 + k 邻域系统。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="插值数值字段名",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=7,
                minimum=5, maximum=9,
                description="H3 分辨率",
            ),
            ParameterSpec(
                name="variogram_model", type="enum", default="auto",
                enum_values=["auto", "spherical", "exponential", "gaussian"],
                data_dependent_default="variogram_least_rss",
                description="变异函数模型",
            ),
            ParameterSpec(
                name="neighbors", type="integer", default=12,
                minimum=1, maximum=24,
                description="克里金邻域样本数上限",
            ),
            ParameterSpec(
                name="cross_validate", type="boolean", default=True,
                description="是否执行 5 折交叉验证",
            ),
            ParameterSpec(
                name="method", type="enum", default="ordinary",
                enum_values=["ordinary", "universal"],
                description="ordinary=常均值 OK；universal=线性坐标漂移 UK（残差变异函数）",
            ),
        ],
    ),
]


class ParameterContractRegistry:
    """参数契约目录（与 capability/algorithm registry 同构的注册表）。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, ParameterContract] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        for contract in _SEED_CONTRACTS:
            self.register(contract)
        # 域包契约（ADR-0099 §34）：各算法域模块可导出
        # ``PARAMETER_CONTRACTS: List[ParameterContract]`` —— 契约随域
        # 演化，中央文件不再膨胀。惰性导入避免环（域模块 import 本模块
        # 的 ParameterContract 类）。
        from app.lib.gis.algorithms import iter_contract_packs

        for pack in iter_contract_packs():
            for contract in pack:
                self.register(contract)

    def register(self, contract: ParameterContract) -> None:
        if not contract.id:
            raise ValueError("parameter contract id must be non-empty")
        if contract.id in self._by_id:
            raise ValueError(f"duplicate parameter contract id: {contract.id}")
        self._by_id[contract.id] = contract

    def get(self, contract_id: str) -> Optional[ParameterContract]:
        return self._by_id.get(contract_id)

    def has(self, contract_id: str) -> bool:
        return contract_id in self._by_id

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def validate(self) -> List[str]:
        """结构自检（pydantic 已覆盖大部分；这里补注册表级检查）。"""
        issues: List[str] = []
        for contract in self._by_id.values():
            for spec in contract.parameters:
                if spec.data_dependent_default and "auto" not in (
                    spec.enum_values + [str(spec.default or "")]
                ):
                    # 哨兵值约定：数值参数可用 0 表达「自动」（如
                    # distance_band=0 → 8nn 自动带宽）—— 规则声明的语义
                    # 由实现侧解释；无哨兵且无 auto 通道才报 issue。
                    has_zero_sentinel = (
                        spec.type in ("number", "integer")
                        and spec.default == 0)
                    if not has_zero_sentinel:
                        issues.append(
                            f"contract {contract.id}.{spec.name}: 数据依赖默认声明了"
                            f" auto 规则，但 default/enum 不含 'auto' 通道")
                if spec.type == "enum" and spec.required and spec.default is None:
                    pass  # required enum 无默认合法（用户必须显式给）
        return issues


_registry: Optional[ParameterContractRegistry] = None


def get_parameter_contract_registry() -> ParameterContractRegistry:
    global _registry
    if _registry is None:
        _registry = ParameterContractRegistry()
        _registry.load_builtins()
    return _registry


def reset_parameter_contract_registry() -> None:
    global _registry
    _registry = None


def apply_contract(contract_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """工具侧严格入口：校验失败抛 ValueError（机器可读前缀，进 dispatch
    的 correction_hint 通道）；成功返回收敛后的参数（默认值已填）。

    科学性失败（单位/数据）不在此层 —— 这里是参数层。
    """
    registry = get_parameter_contract_registry()
    contract = registry.get(contract_id)
    if contract is None:
        raise ValueError(f"unknown parameter contract: {contract_id}")
    result = validate_parameters(contract, params or {})
    if result.issues:
        detail = "; ".join(result.issues[:4])
        raise ValueError(
            f"parameter_contract_violation:{contract_id}: {detail}")
    return result.normalized
