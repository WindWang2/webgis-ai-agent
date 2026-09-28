"""CartographyComponent 契约 re-export shim（ADR-0216）。

实现本体自本路径下沉至 ``app/contracts/cartography_components``（词表/
Pydantic 模型/工厂/校验/突变全部纯代码，符合 kernel 收纳判据）。本 shim
保留全部既有 import path（36 个引用方零改动）；lib/cartography 等跨层
消费方应直接 import kernel 模块，services 内部消费方暂经此 shim。
"""
from app.contracts.cartography_components import *  # noqa: F401,F403
from app.contracts.cartography_components import (  # noqa: F401
    MAX_CHART_DATA_POINTS,
    MAX_DECISION_ROWS,
    MAX_METHODOLOGY_NOTES,
    MAX_METHODOLOGY_TEXT,
    MAX_STAT_ITEMS,
    MAX_ANNOTATION_TEXT,
    MAX_TABLE_COLUMN_NAME,
    MAX_TABLE_COLUMNS,
    MAX_UNCERTAINTY_ITEMS,
    Position,
    ComponentType,
    _FACTORY_BY_TYPE,
    label_layer_component,
    methodology_note_component,
    uncertainty_panel_component,
    decision_panel_component,
    validate_decision_payload,
    validate_methodology_payload,
    validate_uncertainty_payload,
)

from app.contracts.cartography_components import __all__ as _kernel_all

__all__ = list(_kernel_all)
