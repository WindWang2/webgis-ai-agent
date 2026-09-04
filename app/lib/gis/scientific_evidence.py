"""Scientific Evidence —— 统一科学证据块构造器（VNext §26）。

每个分析结果都应能报告「用了什么方法、什么版本、什么参数、什么假设、
做了什么变换、fallback 属于哪类、什么警告、什么不确定性」—— Agent 不
应该自己编这些解释，科学层生成它们。

形状（全部有界）：

    scientific_evidence = {
      capability, algorithm, algorithm_version, tool,
      parameter_contract: {ref, version},
      parameters_applied: {...} (≤16 键，值收敛),
      inputs: {artifact_type, feature_count, crs, crs_class, units},
      assumptions: [...], limitations: [...],          # descriptor + 运行时追加
      transformations_applied: [...],                  # 如 "auto UTM (zone 50N)"
      fallback: {occurred, from, to, semantics},       # 科学等价性分类
      warnings: [...], diagnostics: [...],
      uncertainty: [...],                              # uncertainty.py 类型块
      validation: {...} | None,                        # ValidationMetrics
      reproducibility: {deterministic, random_seed_policy, seed},
    }

约束：证据是**摘要**不是数据搬运工；完整面/表走 ref。工具包装层职责
= validate → resolve refs → 调科学实现 → 挂本证据块 → 注册产物。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.crs_safety import classify_crs
from app.lib.gis.uncertainty import (
    UncertaintyBlock,
    ValidationMetrics,
    uncertainty_blocks_to_evidence,
)

_MAX_LIST = 8
_MAX_VALUE_STR = 160


class Diagnostic(BaseModel):
    """一条运行时诊断事实（有界）。"""

    name: str
    value: Optional[float] = None
    text: str = ""
    unit: str = ""

    @field_validator("text")
    @classmethod
    def _bounded(cls, v: str) -> str:
        return v[:_MAX_VALUE_STR]


class FallbackRecord(BaseModel):
    """结果实际经历的降级路径（科学等价性分类必填 semantics）。"""

    occurred: bool = False
    from_element: str = ""
    to_element: str = ""
    semantics: str = ""          # equivalent/approximation/proxy/degraded/
                                 # not_allowed（词表在 algorithm_registry 侧）

    def to_dict(self) -> Dict[str, Any]:
        if not self.occurred:
            return {"occurred": False}
        return {
            "occurred": True,
            "from": self.from_element,
            "to": self.to_element,
            "semantics": self.semantics,
        }


class InputEvidence(BaseModel):
    artifact_type: str = ""
    feature_count: Optional[int] = None
    crs: str = ""
    crs_class: str = "unknown"
    units: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            k: v for k, v in self.model_dump().items()
            if v not in (None, "", "unknown")
        } or {"declared": "none"}


class Reproducibility(BaseModel):
    deterministic: bool = True
    random_seed_policy: str = "deterministic"
    seed: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"deterministic": self.deterministic}
        if self.random_seed_policy:
            out["random_seed_policy"] = self.random_seed_policy
        if self.seed is not None:
            out["seed"] = self.seed
        return out


class ScientificEvidenceBuilder(BaseModel):
    """证据块构造器：descriptor 事实 + 运行时事实 → 有界证据。"""

    capability: str = ""
    algorithm: str = ""
    algorithm_version: str = ""
    tool: str = ""
    parameter_contract_ref: str = ""
    parameter_contract_version: Optional[int] = None
    parameters_applied: Dict[str, Any] = Field(default_factory=dict)
    inputs: InputEvidence = Field(default_factory=InputEvidence)
    assumptions: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    transformations_applied: List[str] = Field(default_factory=list)
    fallback: FallbackRecord = Field(default_factory=FallbackRecord)
    warnings: List[str] = Field(default_factory=list)
    diagnostics: List[Diagnostic] = Field(default_factory=list)
    uncertainty: List[UncertaintyBlock] = Field(default_factory=list)
    validation: Optional[ValidationMetrics] = None
    reproducibility: Reproducibility = Field(default_factory=Reproducibility)
    method_references: List[str] = Field(default_factory=list)

    @field_validator("assumptions", "limitations", "warnings",
                     "transformations_applied", "method_references")
    @classmethod
    def _bounded_lists(cls, v: List[str]) -> List[str]:
        return [str(x)[:_MAX_VALUE_STR] for x in v[:_MAX_LIST]]

    @field_validator("parameters_applied")
    @classmethod
    def _bounded_params(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for k, val in list(v.items())[:16]:
            if isinstance(val, (int, float, bool)) or val is None:
                out[str(k)] = val
            else:
                out[str(k)] = str(val)[:_MAX_VALUE_STR]
        return out

    @field_validator("diagnostics")
    @classmethod
    def _bounded_diagnostics(cls, v: List[Diagnostic]) -> List[Diagnostic]:
        return v[:_MAX_LIST]

    @field_validator("uncertainty")
    @classmethod
    def _bounded_uncertainty(cls, v: List[UncertaintyBlock]) -> List[UncertaintyBlock]:
        return v[:6]

    # ── 工厂 ────────────────────────────────────────────────────────
    @classmethod
    def from_descriptor(
        cls,
        descriptor: AlgorithmDescriptor,
        *,
        tool: str = "",
        capability: str = "",
    ) -> "ScientificEvidenceBuilder":
        """从注册表 descriptor 起步（假设/局限/出处/复现策略来自声明）。"""
        cap = capability or (descriptor.capabilities[0] if descriptor.capabilities else "")
        return cls(
            capability=cap,
            algorithm=descriptor.id,
            algorithm_version=descriptor.version,
            tool=tool,
            parameter_contract_ref=descriptor.parameter_contract_ref,
            assumptions=list(descriptor.assumptions),
            limitations=list(descriptor.limitations),
            method_references=list(descriptor.method_references),
            reproducibility=Reproducibility(
                deterministic=descriptor.deterministic,
                random_seed_policy=descriptor.random_seed_policy,
            ),
        )

    def with_input_facts(
        self,
        *,
        artifact_type: str = "",
        feature_count: Optional[int] = None,
        crs: str = "",
        units: str = "",
    ) -> "ScientificEvidenceBuilder":
        self.inputs = InputEvidence(
            artifact_type=artifact_type,
            feature_count=feature_count,
            crs=crs,
            crs_class=classify_crs(crs) if crs else "unknown",
            units=units,
        )
        return self

    def with_contract_version(self, version: int) -> "ScientificEvidenceBuilder":
        self.parameter_contract_version = version
        return self

    def to_evidence(self) -> Dict[str, Any]:
        """序列化为工具结果里的 evidence 块（确定性键序由 json 层保证）。"""
        out: Dict[str, Any] = {
            "capability": self.capability,
            "algorithm": self.algorithm,
            "algorithm_version": self.algorithm_version,
            "tool": self.tool,
            "inputs": self.inputs.to_dict(),
            "assumptions": list(self.assumptions),
            "limitations": list(self.limitations),
            "transformations_applied": list(self.transformations_applied),
            "fallback": self.fallback.to_dict(),
            "warnings": list(self.warnings),
            "diagnostics": [d.model_dump() for d in self.diagnostics],
            "uncertainty": uncertainty_blocks_to_evidence(list(self.uncertainty)),
            "reproducibility": self.reproducibility.to_dict(),
        }
        if self.parameter_contract_ref:
            out["parameter_contract"] = {
                "ref": self.parameter_contract_ref,
                "version": self.parameter_contract_version,
            }
        if self.parameters_applied:
            out["parameters_applied"] = dict(self.parameters_applied)
        if self.validation is not None:
            out["validation"] = self.validation.to_evidence()
        if self.method_references:
            out["method_references"] = list(self.method_references)
        return out


def build_evidence(
    descriptor: AlgorithmDescriptor,
    *,
    tool: str = "",
    capability: str = "",
    parameters_applied: Optional[Dict[str, Any]] = None,
    input_facts: Optional[Dict[str, Any]] = None,
    warnings: Optional[List[str]] = None,
    transformations: Optional[List[str]] = None,
    diagnostics: Optional[List[Diagnostic]] = None,
    uncertainty: Optional[List[UncertaintyBlock]] = None,
    validation: Optional[ValidationMetrics] = None,
    fallback: Optional[FallbackRecord] = None,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """一步式证据构造（工具包装层的主入口）。"""
    builder = ScientificEvidenceBuilder.from_descriptor(
        descriptor, tool=tool, capability=capability)
    builder.parameters_applied = dict(parameters_applied or {})
    if input_facts:
        builder.with_input_facts(**input_facts)
    builder.warnings = list(warnings or [])
    builder.transformations_applied = list(transformations or [])
    builder.diagnostics = list(diagnostics or [])
    builder.uncertainty = list(uncertainty or [])
    builder.validation = validation
    if fallback is not None:
        builder.fallback = fallback
    if seed is not None:
        builder.reproducibility = Reproducibility(
            deterministic=builder.reproducibility.deterministic,
            random_seed_policy=builder.reproducibility.random_seed_policy,
            seed=seed,
        )
    return builder.to_evidence()
