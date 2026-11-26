"""Map Series（WP3）—— 系列地图作者计划 / 跨产品一致性 / 传播 / 冻结计划。

权威纪律：不建第二地图状态 —— 成员真实地图状态 = 各自 session 的权威
MapSpec（``app/services/mapspec`` store + lifecycle engine CAS）；本包只存
**引用与计划**，并经既有 mutation 通道逐图传播（绝不宣称原子批量）。
"""
from app.services.map_series.contract import (  # noqa: F401
    MAP_SERIES_VERSION,
    MapSeriesV1,
    SeriesMember,
    validate_map_series_payload,
)
from app.services.map_series.errors import (  # noqa: F401
    MapSeriesConflictError,
    MapSeriesError,
    MapSeriesForbiddenError,
    MapSeriesNotFoundError,
    MapSeriesValidationError,
)

__all__ = [
    "MAP_SERIES_VERSION",
    "MapSeriesV1",
    "SeriesMember",
    "validate_map_series_payload",
    "MapSeriesError",
    "MapSeriesNotFoundError",
    "MapSeriesForbiddenError",
    "MapSeriesConflictError",
    "MapSeriesValidationError",
]
