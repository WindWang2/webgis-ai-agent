"""Algorithm Extension SDK（ADR-0104 / Wave 4）。

第三方算法以 :class:`AlgorithmExtensionSpec` 描述，SDK 构造核心
``AlgorithmDescriptor``（app/lib/gis/algorithm_registry.py）并做宿主规则
校验：

- id 强制命名空间隔离（``<ns>.<id>``）；
- ``capabilities`` 必须引用 CapabilityRegistry 已存在的 id 或本扩展声明
  的算法自身 id（不存在 → typed error）；
- ``tool_candidates`` 必须引用 ToolRegistry 已注册工具（扩展自己的工具
  先注册即可）；
- 默认 ``scientific_status="EXPERIMENTAL"``；声明 VALIDATED/PRODUCTION
  必须提供可解析的 conformance 节点（authoring harness 检查）；
- backend_variants 上限 4（核心同规则）。

authoring harness（:func:`run_authoring_checks`）供「实现 → 契约校验 →
数值 smoke → 规模守卫 → 取消测试 → 注册」的本地闭环使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..diagnostics import DiagnosticCode, ExtensionDiagnostic
from .tool import ToolExtensionSpec

_VALID_SCIENTIFIC_STATUS = frozenset({"", "EXPERIMENTAL", "VALIDATED", "PRODUCTION", "DEPRECATED"})
_VALID_RUNTIME_STATUS = frozenset({"native", "planned", "unavailable"})


@dataclass
class NumericalSmokeCase:
    """authoring harness 的最小数值用例：给参数、比结果（确定性）。"""

    arguments: dict[str, Any]
    expect_key: str
    expect_value: Any
    tolerance: float = 1e-9


@dataclass
class AlgorithmExtensionSpec:
    id: str
    name: str
    capabilities: list[str] = field(default_factory=list)
    category: str = ""
    subcategory: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    input_artifact_types: list[str] = field(default_factory=list)
    output_artifact_type: str = ""
    geometry_requirements: list[str] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)
    min_features: Optional[int] = None
    max_features_hint: Optional[int] = None
    crs_requirements: str = ""
    crs_class: str = ""
    deterministic: bool = True
    cpu_cost: str = "medium"
    memory_cost: str = "medium"
    io_cost: str = "medium"
    tool_candidates: list[str] = field(default_factory=list)
    compatible_map_models: list[str] = field(default_factory=list)
    priority: int = 50
    version: str = "1.0"
    algorithm_family: str = ""
    method_references: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    scientific_preconditions: list[str] = field(default_factory=list)
    uncertainty_outputs: list[str] = field(default_factory=list)
    random_seed_policy: str = "deterministic"
    numerical_tolerance: str = ""
    scientific_status: str = "EXPERIMENTAL"
    conformance_tests: list[str] = field(default_factory=list)
    backend_variants: list[dict[str, Any]] = field(default_factory=list)
    # authoring harness 输入（不进入 descriptor）
    smoke_cases: list[NumericalSmokeCase] = field(default_factory=list)
    scale_guard_features: Optional[int] = None
    cancellation_probe: Optional[Callable[[], Any]] = None

    def validate(self, known_capabilities: frozenset[str], known_tools: frozenset[str]) -> list[ExtensionDiagnostic]:
        diagnostics: list[ExtensionDiagnostic] = []
        if not self.id or not self.id.replace("_", "").isalnum() or self.id[0].isdigit():
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"algorithm id {self.id!r} must be snake_case identifier"
                )
            )
        if self.scientific_status not in _VALID_SCIENTIFIC_STATUS:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"invalid scientific_status {self.scientific_status!r}"
                )
            )
        if len(self.backend_variants) > 4:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, "backend_variants exceeds 4 entries (core rule)"
                )
            )
        for cap in self.capabilities:
            if known_capabilities and cap not in known_capabilities:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                        f"algorithm {self.id!r} references unknown capability {cap!r} "
                        "(declare no capabilities or use existing ids; CapabilityRegistry is a frozen seam)",
                    )
                )
        for tool in self.tool_candidates:
            if known_tools and tool not in known_tools:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                        f"algorithm {self.id!r} references unregistered tool {tool!r} "
                        "(register the tool before the algorithm)",
                    )
                )
        if self.scientific_status in {"VALIDATED", "PRODUCTION"} and not self.conformance_tests:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"algorithm {self.id!r}: {self.scientific_status} requires conformance_tests "
                    "(core registry enforces the same rule for in-repo algorithms)",
                )
            )
        if self.scientific_status == "DEPRECATED":
            diagnostics.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"algorithm {self.id!r} declared DEPRECATED; provide fallback_algorithms via descriptor",
                )
            )
        return diagnostics

    def build_descriptor(self, namespaced_id: str):
        """构造核心 AlgorithmDescriptor（kwargs 与核心域包同构）。"""
        from app.lib.gis.algorithm_registry import AlgorithmDescriptor, BackendVariant

        variants = [BackendVariant.model_validate(v) for v in self.backend_variants]
        return AlgorithmDescriptor(
            id=namespaced_id,
            name=self.name,
            capabilities=list(self.capabilities),
            category=self.category,
            subcategory=self.subcategory,
            tags=list(self.tags),
            input_artifact_types=list(self.input_artifact_types),
            output_artifact_type=self.output_artifact_type,
            geometry_requirements=list(self.geometry_requirements),
            required_fields=list(self.required_fields),
            min_features=self.min_features,
            max_features_hint=self.max_features_hint,
            crs_requirements=self.crs_requirements,
            crs_class=self.crs_class,
            deterministic=self.deterministic,
            cpu_cost=self.cpu_cost,
            memory_cost=self.memory_cost,
            io_cost=self.io_cost,
            tool_candidates=list(self.tool_candidates),
            runtime_status="native",
            compatible_map_models=list(self.compatible_map_models),
            priority=self.priority,
            version=self.version,
            algorithm_family=self.algorithm_family,
            method_references=list(self.method_references),
            assumptions=list(self.assumptions),
            limitations=list(self.limitations),
            scientific_preconditions=list(self.scientific_preconditions),
            uncertainty_outputs=list(self.uncertainty_outputs),
            random_seed_policy=self.random_seed_policy,
            numerical_tolerance=self.numerical_tolerance,
            scientific_status=self.scientific_status,
            conformance_tests=list(self.conformance_tests),
            backend_variants=variants,
        )


def run_authoring_checks(
    spec: AlgorithmExtensionSpec,
    implementation: Callable[..., dict[str, Any]],
) -> list[ExtensionDiagnostic]:
    """本地 authoring harness：契约 → 数值 smoke → 规模守卫 → 取消探测。

    全部确定性、无网络、无 LLM。供 `ext scaffold` 生成的测试与 CI 复用。
    """
    diagnostics: list[ExtensionDiagnostic] = []
    for case in spec.smoke_cases:
        try:
            result = implementation(**case.arguments)
        except Exception as exc:  # noqa: BLE001 - harness 收集而非中断
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"smoke case raised: {exc!r}",
                    context={"arguments": case.arguments},
                )
            )
            continue
        actual = result.get(case.expect_key) if isinstance(result, dict) else None
        if isinstance(case.expect_value, float) and isinstance(actual, (int, float)):
            if abs(float(actual) - case.expect_value) > case.tolerance:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.MANIFEST_INVALID,
                        f"smoke case {case.arguments!r}: expected "
                        f"{case.expect_key}≈{case.expect_value}, got {actual!r}",
                    )
                )
        elif actual != case.expect_value:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"smoke case {case.arguments!r}: expected "
                    f"{case.expect_key}={case.expect_value!r}, got {actual!r}",
                )
            )
    if spec.scale_guard_features and spec.cancellation_probe is not None:
        try:
            spec.cancellation_probe()
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"cancellation probe raised: {exc!r}"
                )
            )
    return diagnostics


def validate_tool_algorithm_parity(
    algorithms: list["AlgorithmExtensionSpec"],
    tools: list[ToolExtensionSpec],
) -> list[ExtensionDiagnostic]:
    """包内 parity：算法引用的 tool_candidates 必须落在同包工具集合内。"""
    tool_names = {t.name for t in tools}
    diagnostics: list[ExtensionDiagnostic] = []
    for algo in algorithms:
        for tool in algo.tool_candidates:
            if tool not in tool_names:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                        f"algorithm {algo.id!r} references tool {tool!r} not in this pack's tools",
                    )
                )
    return diagnostics
