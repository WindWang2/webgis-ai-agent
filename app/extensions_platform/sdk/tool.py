"""Tool Extension SDK（ADR-0104 / Wave 3）。

第三方以声明式 spec 描述工具，SDK 负责：
- 校验（词表封闭 + 宿主规则：tier<=2、destructive 禁用、network ⇒
  network 权限、capabilities/algorithms 引用必须真实存在）；
- 生成与核心 ``ToolRegistry.register`` 完全一致的 kwargs（不绕过任何
  注册路径，Pi surface 继续由既有投影层产出）；
- 权限包裹：声明了 ``required_permissions`` 的工具在执行前检查授权，
  失败抛 typed :class:`ExtensionPermissionDenied`。

描述符字段（ADR-0101/0103 V3 词表）是 spec 的一等字段；parity 测试锁定
本字段集合与核心 ``ToolRegistry._DESCRIPTOR_KWARGS`` 一致。

用法（扩展入口模块内）::

    from app.extensions_platform.sdk import ToolExtensionSpec

    def _run(x: float) -> dict: ...

    TOOLS = [
        ToolExtensionSpec(
            name="bbox_area",
            description="Compute bbox area in CRS units.",
            func=_run,
            side_effect="pure",
            deterministic=True,
        ),
    ]
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass, field, fields
from typing import Any, Callable, Optional

from ..diagnostics import DiagnosticCode, ExtensionDiagnostic
from ..permissions import ExtensionPermissionDenied, Permission, PermissionGrantSet

_VALID_SIDE_EFFECTS = frozenset(
    {
        "unclassified", "pure", "deterministic_compute", "cacheable_read",
        "state_mutation", "artifact_creation", "external_side_effect", "destructive",
    }
)
_VALID_COSTS = frozenset({"light", "medium", "heavy"})
_VALID_POLICIES = frozenset({"inline", "thread", "async", "celery"})
_VALID_LATENCY = frozenset({"unknown", "fast", "medium", "slow"})
_VALID_MEMORY = frozenset({"unknown", "light", "medium", "heavy"})
_VALID_SCALE = frozenset({"unknown", "small", "medium", "large"})
_VALID_RESULT_SIZE = frozenset(
    {"unknown", "bounded_small", "bounded_medium", "bounded_large", "unbounded"}
)


@dataclass
class ToolExtensionSpec:
    """一个扩展工具的完整声明（SDK 校验后的稳定中间形态）。"""

    name: str
    description: str
    func: Callable[..., Any]
    summary: str = ""
    tier: int = 2
    domains: list[str] = field(default_factory=list)
    execution_policy: Optional[str] = None
    timeout: Optional[float] = None
    version: str = "1.0"
    contract_version: int = 1
    cost: str = "light"
    side_effect: str = "unclassified"
    required_permissions: list[str] = field(default_factory=list)
    param_descriptions: Optional[dict[str, str]] = None
    args_model: Optional[type] = None
    parameters: Optional[dict] = None
    field_extras: Optional[dict[str, dict]] = None
    # ── ADR-0101/0103 描述符扩展字段（与核心 _DESCRIPTOR_KWARGS 同源）──
    status: Optional[str] = None
    deprecation_of: Optional[str] = None
    requires_credentials: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    algorithms: list[str] = field(default_factory=list)
    provider_dependencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    output_semantic_type: Optional[str] = None
    produced_refs: list[str] = field(default_factory=list)
    accepts_ref_types: list[str] = field(default_factory=list)
    network: Optional[bool] = None
    deterministic: Optional[bool] = None
    result_size_policy: Optional[str] = None
    input_artifacts: list[str] = field(default_factory=list)
    required_context: list[str] = field(default_factory=list)
    map_mutations: list[str] = field(default_factory=list)
    data_mutations: list[str] = field(default_factory=list)
    latency_class: Optional[str] = None
    memory_class: Optional[str] = None
    scale_class: Optional[str] = None
    crs_semantics: Optional[str] = None
    unit_semantics: Optional[str] = None
    idempotent: Optional[bool] = None
    security_tier: Optional[str] = None
    required_permission: Optional[str] = None
    examples: list[Any] = field(default_factory=list)
    anti_examples: list[Any] = field(default_factory=list)
    failure_modes: list[Any] = field(default_factory=list)
    fallback_tool: Optional[str] = None

    def _descriptor_kwargs(self) -> dict[str, Any]:
        """一等字段 → 核心 register 的描述符 kwargs（None/空集合省略）。"""
        kwargs: dict[str, Any] = {}
        for f in fields(self):
            if f.name in {"name", "description", "func", "summary", "tier", "domains",
                          "execution_policy", "timeout", "version", "contract_version",
                          "cost", "side_effect", "required_permissions",
                          "param_descriptions", "args_model", "parameters", "field_extras"}:
                continue
            value = getattr(self, f.name)
            if value is None:
                continue
            if isinstance(value, (list, tuple)) and not value:
                continue
            kwargs[f.name] = value
        return kwargs

    def validate(self) -> list[ExtensionDiagnostic]:
        diagnostics: list[ExtensionDiagnostic] = []
        if not self.name or not self.name.replace("_", "").isalnum() or self.name[0].isdigit():
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"tool name {self.name!r} must be snake_case identifier",
                )
            )
        if not callable(self.func):
            diagnostics.append(
                ExtensionDiagnostic.error(DiagnosticCode.MANIFEST_INVALID, "func must be callable")
            )
        if not isinstance(self.tier, int) or isinstance(self.tier, bool):
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"tier must be an int, got {type(self.tier).__name__}",
                )
            )
        elif self.tier < 1 or self.tier > 2:
            # tier 3 是核心安全 chokepoint（list_available_tools + 显式确认），
            # 扩展工具禁入。
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"extension tool tier must be 1 or 2, got {self.tier}",
                )
            )
        if self.side_effect not in _VALID_SIDE_EFFECTS:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"invalid side_effect {self.side_effect!r}",
                )
            )
        if self.side_effect == "destructive":
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    "extension tools cannot declare destructive side_effect "
                    "(map to tier-3 core confirmation flow instead)",
                )
            )
        if self.cost not in _VALID_COSTS:
            diagnostics.append(
                ExtensionDiagnostic.error(DiagnosticCode.MANIFEST_INVALID, f"invalid cost {self.cost!r}")
            )
        if self.execution_policy is not None and self.execution_policy not in _VALID_POLICIES:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"invalid execution_policy {self.execution_policy!r}"
                )
            )
        for attr, vocab in (
            ("latency_class", _VALID_LATENCY), ("memory_class", _VALID_MEMORY),
            ("scale_class", _VALID_SCALE), ("result_size_policy", _VALID_RESULT_SIZE),
        ):
            val = getattr(self, attr)
            if val is not None and val not in vocab:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.MANIFEST_INVALID, f"invalid {attr} {val!r}"
                    )
                )
        # 网络访问必须显式声明 network 权限（宿主授权检查在调用期执行）。
        if self.network and Permission.NETWORK not in self.required_permissions:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.PERMISSION_DECLARATION_INVALID,
                    f"tool {self.name!r} sets network=True but does not require '{Permission.NETWORK}'",
                )
            )
        return diagnostics

    def wrap_with_permissions(self, grants: PermissionGrantSet) -> Callable[..., Any]:
        """返回执行前做权限检查的包裹函数（权限失败 typed）。"""
        if not self.required_permissions:
            return self.func
        required = tuple(self.required_permissions)

        def _perm_error(permission: str) -> ExtensionPermissionDenied:
            return ExtensionPermissionDenied(
                ExtensionDiagnostic.error(
                    DiagnosticCode.PERMISSION_NOT_GRANTED,
                    f"tool {self.name!r} of {grants.extension_id!r} requires "
                    f"permission {permission!r} which was not granted",
                    extension_id=grants.extension_id,
                    permission=permission,
                )
            )

        if inspect.iscoroutinefunction(self.func):
            @functools.wraps(self.func)
            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                for permission in required:
                    if not grants.allows(permission):
                        raise _perm_error(permission)
                return await self.func(*args, **kwargs)

            return _async_wrapper

        @functools.wraps(self.func)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            for permission in required:
                if not grants.allows(permission):
                    raise _perm_error(permission)
            return self.func(*args, **kwargs)

        return _wrapper

    def register_kwargs(self) -> dict[str, Any]:
        """展开为 ToolRegistry.register 的 kwargs（不含 name/description/func）。"""
        kwargs: dict[str, Any] = {
            "tier": self.tier,
            "domains": list(self.domains),
            "version": self.version,
            "contract_version": self.contract_version,
            "cost": self.cost,
            **self._descriptor_kwargs(),
        }
        if self.execution_policy is not None:
            kwargs["execution_policy"] = self.execution_policy
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        if self.param_descriptions is not None:
            kwargs["param_descriptions"] = self.param_descriptions
        if self.args_model is not None:
            kwargs["args_model"] = self.args_model
        if self.parameters is not None:
            kwargs["parameters"] = self.parameters
        if self.field_extras is not None:
            kwargs["field_extras"] = self.field_extras
        return kwargs


def extension_tool(
    name: str,
    description: str,
    *,
    required_permissions: Optional[list[str]] = None,
    **spec_kwargs: Any,
) -> Callable[[Callable[..., Any]], ToolExtensionSpec]:
    """装饰器形态：把函数直接包装为 ToolExtensionSpec。"""

    def decorator(func: Callable[..., Any]) -> ToolExtensionSpec:
        return ToolExtensionSpec(
            name=name,
            description=description,
            func=func,
            required_permissions=list(required_permissions or []),
            **spec_kwargs,
        )

    return decorator
