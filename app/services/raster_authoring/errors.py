"""G20 领域错误分类（UI HTTP 与 Pi 工具回执的唯一码表）。"""
from __future__ import annotations

from typing import Any, Dict

from app.schemas.raster_spec import (  # noqa: F401
    RASTER_ERROR_CODES,
    RasterExpressionError,
)

#: HTTP 状态映射（未列出者 422）。权限/陈旧/服务端失败单独区分，
#: 绝不把所有失败压成 200 + success=false。
STATUS_BY_CODE: Dict[str, int] = {
    "SOURCE_UNAVAILABLE": 404,
    "SOURCE_NOT_NUMERIC_RASTER": 422,
    "SOURCE_PATH_REJECTED": 400,
    "NO_CRS": 422,
    "OUTSIDE_EXTENT": 422,
    "MASKED_PIXEL": 422,
    "PARTIALLY_MASKED": 422,
    "STALE_BINDING": 409,
    "STALE_STYLE": 409,
    "CAS_SUPERSEDED": 409,
    "BUDGET_EXCEEDED": 413,
    "RENDER_FAILED": 502,
    "EXPORT_UNSUPPORTED": 422,
}

#: UI 字段聚焦提示（错误码 → 建议聚焦的面板字段）。
FIELD_BY_CODE: Dict[str, str] = {
    "BAND_COUNT_UNSUPPORTED": "bands",
    "BAND_OUT_OF_RANGE": "bands",
    "BAND_NOT_INTEGER": "bands",
    "STRETCH_NON_FINITE": "stretch",
    "STRETCH_REVERSED": "stretch",
    "STRETCH_CHANNEL_MISMATCH": "stretch.per_channel",
    "STRETCH_SAMPLE_UNBOUNDED": "stretch.source",
    "UNKNOWN_COLORMAP": "colormap",
    "COLORMAP_ON_RGB": "colormap",
    "UNSUPPORTED_RESAMPLING": "resampling",
    "CLASSIFIED_INTERPOLATION": "resampling",
    "OPACITY_OUT_OF_RANGE": "opacity",
    "MUTATION_REJECTED": "expected_revision",
}

#: 永不进入 UI / Pi 回执的详情键（服务端路径、原始异常文本）。
_PRIVATE_DETAIL_KEYS = frozenset({"path", "file_path", "exception", "traceback", "uri"})


def error_payload(err: RasterExpressionError) -> Dict[str, Any]:
    """统一回执（不泄漏服务器路径或像元）。"""
    payload = err.to_dict()
    safe_details = {
        k: v for k, v in err.details.items()
        if k.lower() not in _PRIVATE_DETAIL_KEYS and isinstance(v, (str, int, float, bool, list))
    }
    if safe_details:
        payload["details"] = safe_details
    else:
        payload.pop("details", None)
    if not payload.get("field") and err.code in FIELD_BY_CODE:
        payload["field"] = FIELD_BY_CODE[err.code]
    return payload


def http_status(err: RasterExpressionError) -> int:
    if err.status_code != 422:
        return err.status_code
    return STATUS_BY_CODE.get(err.code, 422)
