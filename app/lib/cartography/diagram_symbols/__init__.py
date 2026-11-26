"""地理锚定统计图符（diagram symbols）—— 公共出口。

权威形状 = contracts.py 合同 + geometry.py 几何 oracle；schema 层
（mapspec_schema.MapSpecLayerDiagramSymbol）只承载不计算；渲染通道
（live GL / canvas / SVG twin / PDF）消费 oracle 输出，禁止自行重算。
"""
from app.lib.cartography.diagram_symbols.contracts import (
    DIAGRAM_ERROR_CODES,
    DIAGRAM_SYMBOL_KINDS,
    EXTREME_RADIUS_RATIO,
    EXTREME_VALUE_RANGE_RATIO,
    MAX_CATEGORIES,
    MAX_GLYPH_RADIUS_PX,
    MISSING_POLICIES,
    MIN_CATEGORIES,
    MIN_COMPOSITION_CATEGORIES,
    SERIES_BAR_LAYOUTS,
    DiagramSymbolError,
    canonical_json,
    compute_glyph_scale,
    diagram_block_fingerprint,
    normalize_size_policy,
    validate_category_spec,
)
from app.lib.cartography.diagram_symbols.geometry import (
    GEOMETRY_DECIMALS,
    build_glyph_geometry,
    geometry_digest,
    glyph_bound_radius,
)
from app.lib.cartography.diagram_symbols.legend import (
    DEFAULT_GLYPH_PALETTE,
    build_glyph_legend,
)
from app.lib.cartography.diagram_symbols.qualification import (
    DEFAULT_ID_FIELD,
    qualify_features,
)

__all__ = [
    "DIAGRAM_ERROR_CODES",
    "DIAGRAM_SYMBOL_KINDS",
    "EXTREME_RADIUS_RATIO",
    "EXTREME_VALUE_RANGE_RATIO",
    "MAX_CATEGORIES",
    "MAX_GLYPH_RADIUS_PX",
    "MISSING_POLICIES",
    "MIN_CATEGORIES",
    "MIN_COMPOSITION_CATEGORIES",
    "SERIES_BAR_LAYOUTS",
    "DiagramSymbolError",
    "canonical_json",
    "compute_glyph_scale",
    "diagram_block_fingerprint",
    "normalize_size_policy",
    "validate_category_spec",
    "GEOMETRY_DECIMALS",
    "build_glyph_geometry",
    "geometry_digest",
    "glyph_bound_radius",
    "DEFAULT_GLYPH_PALETTE",
    "build_glyph_legend",
    "DEFAULT_ID_FIELD",
    "qualify_features",
]
