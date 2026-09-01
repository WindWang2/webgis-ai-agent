"""Per-facet completion validators（纯函数；输入 gather_completion_inputs 的产物）。

各 facet 校验器在独立子模块；本 ``__init__`` 只做汇聚再导出，无逻辑。
"""
from .artifacts import validate_artifacts
from .components import validate_components
from .execution import validate_execution
from .layers import validate_layers
from .layout import validate_layout
from .semantics import validate_semantics
from .viewport_export import assess_export_parity, derive_result_bbox

__all__ = [
    "assess_export_parity",
    "derive_result_bbox",
    "validate_artifacts",
    "validate_components",
    "validate_execution",
    "validate_layers",
    "validate_layout",
    "validate_semantics",
]
