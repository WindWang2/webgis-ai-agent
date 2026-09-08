"""扩展 SDK 公共出口（ADR-0104）。

import 契约（Round-1 审计 M7 修订）：
- **允许**：本包（``app.extensions_platform.sdk``）、自身标准库/三方
  依赖，以及 workflow/cartography 声明所需的**核心领域模型**
  （如 ``app.services.gis_harness.recipes.CartographyRecipe``、
  cartography 描述符模型）——声明需要类型，读模型不等于改注册表。
- **禁止**：import 并直接调用核心可变 registry（ToolRegistry /
  AlgorithmRegistry / AdapterRegistry / RecipeRegistry / cartography
  registries）。一切注册必须经 ExtensionContext 的 register_* 门面——
  这是「扩展不再依赖随意修改内部代码」的强制边界（host 只把 registry
  引用注入 context，扩展拿不到）。
"""

from .algorithm import AlgorithmExtensionSpec, NumericalSmokeCase, run_authoring_checks
from .declarations import CartographyItemSpec, WorkflowPackSpec
from .model import ModelProviderSpec
from .provider import (
    ProviderExtensionSpec,
    RasterWindowProvider,
    StreamingVectorProvider,
    TilePayload,
    TileProvider,
    extended_provider_capabilities,
)
from .tool import ToolExtensionSpec, extension_tool

__all__ = [
    "AlgorithmExtensionSpec",
    "CartographyItemSpec",
    "ModelProviderSpec",
    "NumericalSmokeCase",
    "ProviderExtensionSpec",
    "RasterWindowProvider",
    "StreamingVectorProvider",
    "TilePayload",
    "TileProvider",
    "ToolExtensionSpec",
    "WorkflowPackSpec",
    "extension_tool",
    "extended_provider_capabilities",
    "run_authoring_checks",
]
