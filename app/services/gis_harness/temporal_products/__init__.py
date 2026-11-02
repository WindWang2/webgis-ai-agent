"""temporal_products —— 地图产品时态控制层（GOAL 03）。

分层：
- ``frames``：真实数据 → 时间表示探测 + 粒度分桶（唯一帧推导源，纯函数）；
- ``time_filter``：帧窗口 → where AST / MapLibre filter / 指纹（纯函数）；
- ``service``：会话级 bind/control/compare/unbind（经 apply_gis_mutation CAS）。

纪律：分析语义复用 app/services/temporal/*（profiler/filter/aggregate），
本包不重写任何时序算法；帧只来自真实观测桶，绝不内插。
"""
from app.services.gis_harness.temporal_products.frames import (
    Bucket,
    TimeBindingProbe,
    detect_time_binding,
    granularity_from_span,
    probe_fc_time_field,
)
from app.services.gis_harness.temporal_products.time_filter import (
    compile_maplibre_filter,
    compile_where_ast,
    normalize_boundary_value,
    time_filter_fingerprint,
)

__all__ = [
    "Bucket",
    "TimeBindingProbe",
    "detect_time_binding",
    "granularity_from_span",
    "probe_fc_time_field",
    "compile_maplibre_filter",
    "compile_where_ast",
    "normalize_boundary_value",
    "time_filter_fingerprint",
]
