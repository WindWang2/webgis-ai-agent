"""Model Provider Extension SDK（ADR-0105 V2 / Wave 10）。

定位（诚实边界）：这里的 model provider 是 **GIS 域推理模型**（分割/
检测/分类等服务）的扩展接入面，投影为一个类型化的调用工具（真实生产
路径：Pi agent 经 ToolRegistry 派发）。LLM chat transport 由 ADR-0102
的配置驱动封闭面管理（ModelDescriptorRegistry 严格拒绝未知来源），扩展
**不得也无法**接入——那不是本类型的语义。

契约：
- ``invoke_fn(request, ctx) -> dict | Iterator[dict]``：返回 dict（聚合
  结果）或事件迭代器（声明 ``streaming`` 能力时）；事件是 JSON-able dict，
  最后一个事件约定为 ``{"type": "final", ...}``；
- ``capabilities ⊆ {streaming, cancellation, batch}``，且 ⊆ manifest 声明；
- ``credentials_ref`` 必须与 manifest 声明一致；值经 secrets 通道
  （供给即授权）获取，永不进入工具结果/日志/LLM 可见面；
- worker 模式下 ``streaming`` 不可用（单帧 RPC；manifest 层 typed 拒绝）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..diagnostics import DiagnosticCode, ExtensionDiagnostic
from ..manifest import MODEL_PROVIDER_CAPABILITIES
from ..permissions import Permission
from .identifier import NAME_RE

InvokeFn = Callable[[dict[str, Any], Any], Any]


@dataclass
class ModelProviderSpec:
    """一个扩展模型 provider 的完整声明。"""

    provider_id: str
    description: str
    invoke_fn: InvokeFn
    capabilities: list[str] = field(default_factory=list)
    credentials_ref: Optional[str] = None
    # 投影工具的入参 schema（invoke 工具的 request 参数描述；缺省给保守
    # 的单 object 参数）。必须可 JSON 序列化。
    parameters: Optional[dict[str, Any]] = None

    def validate(
        self,
        manifest_providers: list[dict[str, Any]],
        declared_permissions: frozenset[str],
    ) -> list[ExtensionDiagnostic]:
        diagnostics: list[ExtensionDiagnostic] = []
        if not NAME_RE.match(self.provider_id or ""):
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"model provider id {self.provider_id!r} must be snake_case identifier",
                )
            )
        unknown_caps = sorted(set(self.capabilities) - MODEL_PROVIDER_CAPABILITIES)
        if unknown_caps:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"model provider {self.provider_id!r}: unknown capabilities "
                    f"{unknown_caps}; valid: {sorted(MODEL_PROVIDER_CAPABILITIES)}",
                )
            )
        declared = {p.get("id"): p for p in manifest_providers if isinstance(p, dict)}
        if self.provider_id not in declared:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.UNDECLARED_REGISTRATION,
                    f"model provider {self.provider_id!r} is not declared in the "
                    "manifest (declaration and activation must match)",
                )
            )
        else:
            declaration = declared[self.provider_id]
            undeclared_caps = sorted(
                set(self.capabilities) - set(declaration.get("capabilities") or [])
            )
            if undeclared_caps:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.MANIFEST_INVALID,
                        f"model provider {self.provider_id!r}: capabilities "
                        f"{undeclared_caps} exceed manifest declaration",
                    )
                )
            declared_ref = declaration.get("credentials_ref")
            if self.credentials_ref != declared_ref:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.MANIFEST_INVALID,
                        f"model provider {self.provider_id!r}: credentials_ref "
                        f"{self.credentials_ref!r} != manifest declaration "
                        f"{declared_ref!r}",
                    )
                )
        if Permission.MODEL_PROVIDER not in declared_permissions:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.PERMISSION_DECLARATION_INVALID,
                    f"model provider {self.provider_id!r} requires the "
                    f"'{Permission.MODEL_PROVIDER}' permission in the manifest",
                )
            )
        if not callable(self.invoke_fn):
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"model provider {self.provider_id!r}: invoke_fn must be callable",
                )
            )
        return diagnostics


def aggregate_stream_events(events: Any) -> dict[str, Any]:
    """把事件迭代器聚合为单个工具结果（投影工具的默认形态）。"""
    if isinstance(events, dict):
        return events
    collected: list[dict[str, Any]] = []
    final: dict[str, Any] = {}
    for event in events:
        if not isinstance(event, dict):
            collected.append({"type": "raw", "value": event})
            continue
        collected.append(event)
        if event.get("type") == "final":
            final = dict(event)
    return {"events": collected, "final": final}
