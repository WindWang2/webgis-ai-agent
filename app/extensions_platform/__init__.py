"""GIS Extension Platform（ADR-0104）。

统一、可验证、可版本化、可限制权限的扩展体系：第三方以
``GisExtensionManifest`` 声明 + SDK spec 描述扩展，宿主在既有权威
registry（ToolRegistry / AlgorithmRegistry / AdapterRegistry /
recipe registry / cartography registries）上做投影，绝不建立第二事实源。

公共出口保持最小：扩展作者只需要 ``sdk``；宿主集成点只有
:func:`configure_extension_host` 与 :func:`get_extension_host`。
"""

from .diagnostics import (
    DiagnosticCode,
    DiagnosticSeverity,
    ExtensionDiagnostic,
    ExtensionPlatformError,
)
from .host import ExtensionHost, ExtensionState, HostPolicy
from .manifest import GisExtensionManifest

__all__ = [
    "DiagnosticCode",
    "DiagnosticSeverity",
    "ExtensionDiagnostic",
    "ExtensionHost",
    "ExtensionPlatformError",
    "ExtensionState",
    "GisExtensionManifest",
    "HostPolicy",
]
