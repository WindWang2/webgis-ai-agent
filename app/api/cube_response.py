"""Bounded cube JSON conversion; all allocation and encoding runs off-loop."""
from __future__ import annotations

import math
from collections.abc import Mapping
from starlette.responses import JSONResponse

MAX_JSON_CUBE_CELLS = 1_000_000
MAX_JSON_CUBE_BYTES = 32 * 1024 * 1024


class CubeResponseTooLarge(ValueError):
    pass


def _json_value(value: object) -> object:
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None  # nodata/nonfinite is unknown, never a fabricated measurement
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def cube_json_response(result: Mapping[str, object], array_key: str) -> JSONResponse:
    arrays = result.get(array_key)
    if isinstance(arrays, Mapping):
        cells = sum(int(getattr(value, "size", 0)) for value in arrays.values())
        if cells > MAX_JSON_CUBE_CELLS:
            raise CubeResponseTooLarge("cube JSON exceeds one million cells; request a smaller window")
    response = JSONResponse(content=_json_value(dict(result)))
    if len(response.body) > MAX_JSON_CUBE_BYTES:
        raise CubeResponseTooLarge("cube JSON exceeds32MiB; request a smaller window")
    return response
