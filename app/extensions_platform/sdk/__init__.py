"""扩展 SDK 公共出口（ADR-0104）。

扩展入口模块只允许 import 本包（``app.extensions_platform.sdk``）与自身
标准库/三方依赖；禁止 import 核心 internals（ToolRegistry 等由宿主注入，
见 context.py）——这是「扩展不再依赖随意修改内部代码」的契约边界。
"""

from .algorithm import AlgorithmExtensionSpec, NumericalSmokeCase, run_authoring_checks
from .declarations import CartographyItemSpec, WorkflowPackSpec
from .provider import ProviderExtensionSpec
from .tool import ToolExtensionSpec, extension_tool

__all__ = [
    "AlgorithmExtensionSpec",
    "CartographyItemSpec",
    "NumericalSmokeCase",
    "ProviderExtensionSpec",
    "ToolExtensionSpec",
    "WorkflowPackSpec",
    "extension_tool",
    "run_authoring_checks",
]
