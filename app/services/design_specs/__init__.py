"""Design Specs —— 可复用制图设计规范权威服务（goal-carto-design-series WP1）。

单一导入面。规范 = 引用与约束文档（design_spec.v1），权威存储在
``cartography_design_specs`` / ``cartography_design_spec_versions``
（migration g50）；MapSpec / 渲染 / 组件 / 资产的执行权威各归其主。
"""
from app.services.design_specs.contract import (
    DESIGN_SPEC_VERSION,
    DesignSpecV1,
    canonical_json,
    structural_digest,
    validate_design_spec_payload,
)
from app.services.design_specs.errors import (
    DesignSpecConflictError,
    DesignSpecError,
    DesignSpecForbiddenError,
    DesignSpecNotFoundError,
    DesignSpecValidationError,
)
from app.services.design_specs.preview import build_design_spec_preview
from app.services.design_specs.versioning import (
    create_spec,
    create_version,
    deprecate_version,
    design_spec_can_write,
    design_spec_scope_clause,
    design_spec_visible,
    get_spec,
    get_version,
    list_specs,
    publish_spec,
    resolve_payload,
    retire_spec,
)

__all__ = [
    "DESIGN_SPEC_VERSION",
    "DesignSpecV1",
    "canonical_json",
    "structural_digest",
    "validate_design_spec_payload",
    "DesignSpecError",
    "DesignSpecNotFoundError",
    "DesignSpecForbiddenError",
    "DesignSpecConflictError",
    "DesignSpecValidationError",
    "build_design_spec_preview",
    "create_spec",
    "create_version",
    "deprecate_version",
    "design_spec_can_write",
    "design_spec_scope_clause",
    "design_spec_visible",
    "get_spec",
    "get_version",
    "list_specs",
    "publish_spec",
    "resolve_payload",
    "retire_spec",
]
