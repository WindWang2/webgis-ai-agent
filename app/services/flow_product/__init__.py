"""G21 OD flow product — pure domain core.

Public API:
- :class:`FlowProductConfig` — declarative build contract.
- :func:`build_flow_product` — deterministic, bounded OD flow product build.
- :class:`FlowProductResult` — value object (product dict + edge/node FCs).
- :class:`FlowProductError` — explicit domain rejection.

No SessionStore, no MapSpec, no geocoding, no I/O in this package.
"""
from .builder import FlowProductResult, build_flow_product
from .contracts import (
    FAMILY_ID,
    MAX_INPUT_ROWS,
    MAX_OUTPUT_EDGES,
    MAX_OUTPUT_NODES,
    SCHEMA_VERSION,
    SUPPORTED_MODES,
    FlowProductConfig,
    FlowProductError,
)
from .geometry import CURVE_VERSION, MAX_EDGE_SEGMENTS

__all__ = [
    "FlowProductConfig",
    "FlowProductError",
    "FlowProductResult",
    "build_flow_product",
    "MAX_INPUT_ROWS",
    "MAX_OUTPUT_EDGES",
    "MAX_OUTPUT_NODES",
    "MAX_EDGE_SEGMENTS",
    "SCHEMA_VERSION",
    "FAMILY_ID",
    "SUPPORTED_MODES",
    "CURVE_VERSION",
]
