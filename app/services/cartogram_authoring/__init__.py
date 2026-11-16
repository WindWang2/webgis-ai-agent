"""G47 Dorling 圆面积 cartogram 作者（领域服务公开面）。

UI 与 Pi 工具共用这里的一切：``inspect → plan → run → status → layout → apply →
export``。核心裁决（数量语义、投影/邻接、位移求解、独立质量门、示意坐标隔离、
CAS 应用）都在本包内，路由与工具只做薄适配。
"""
from __future__ import annotations

from app.services.cartogram_authoring.contract import (
    ALGORITHM_ID,
    ALGORITHM_VERSION,
    AuthoringPlan,
    parse_plan,
)
from app.services.cartogram_authoring.errors import (
    AuthoringError,
    error_payload,
    http_status,
)
from app.services.cartogram_authoring.service import (
    apply_layout,
    export_layout,
    get_layout,
    inspect_layer,
    layout_status,
    list_layouts,
    load_layout,
    plan_layout,
    resolve_layer,
    start_layout,
)

__all__ = [
    "ALGORITHM_ID",
    "ALGORITHM_VERSION",
    "AuthoringError",
    "AuthoringPlan",
    "apply_layout",
    "error_payload",
    "export_layout",
    "get_layout",
    "http_status",
    "inspect_layer",
    "layout_status",
    "list_layouts",
    "load_layout",
    "parse_plan",
    "plan_layout",
    "resolve_layer",
    "start_layout",
]
