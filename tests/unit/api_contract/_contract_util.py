"""V9 契约基石测试工具（ADR-0138）。

`response_model` 覆盖率门禁的单一事实来源：
- 全部 JSON 端点必须挂 response_model；
- 流式（SSE）/二进制/静态文件端点不可能挂（响应体非 JSON schema 可描述），
  进入 EXCLUSIONS 并注明理由；该清单在 PR 附表与 ADR-0138 中镜像。
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Iterator

from fastapi.routing import APIRoute

REPO = Path(__file__).resolve().parents[3]

#: (method, path) -> 排除理由。只允许「非 JSON 响应体」类端点进入。
#: 清单由 P0 勘察的函数级源码扫描实证（scripts/api_contract_recon.py）。
EXCLUSIONS: dict[tuple[str, str], str] = {
    # ── SSE / 流式（text/event-stream，非 JSON schema 可描述）──
    ("POST", "/api/v1/chat/stream"): "SSE streaming",
    ("GET", "/api/v1/explorer/stream/{task_id}"): "SSE streaming",
    # ── 二进制 / 文件下发 ──
    ("GET", "/api/v1/static/{file_path:path}"): "static file bytes",
    ("GET", "/api/v1/sessions/{session_id}/raster/{raster_id}.png"): "PNG bytes",
    ("GET", "/api/v1/reports/{report_id}/download"): "file download bytes",
    ("GET", "/api/v1/reports/shared/{share_code}/view"): "file view bytes",
    ("GET", "/api/v1/export/download/{filename}"): "file download bytes",
    ("GET", "/api/v1/layers/data/{ref_id}/raster-tiles/{z}/{x}/{y}.png"): "PNG tile bytes",
    ("GET", "/api/v1/layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt"): "MVT tile bytes",
    ("GET", "/api/v1/extensions/marketplace/packages/{package_id}/versions/{version}/download"): "package blob stream",
    # ── 定制 media type 的数据面透传（直接构造 Response，response_model 无法介入）──
    ("GET", "/api/v1/layers/data/{ref_id}"): "zero-copy layer data passthrough (media_type)",
    ("GET", "/api/v1/layers/data/{ref_id}/feature/{feature_id}"): "single feature passthrough (media_type)",
    ("GET", "/api/v1/uploads/{upload_id}/geojson"): "GeoJSON passthrough (application/geo+json)",
    ("GET", "/api/v1/data-fabric/catalog/{item_id}/tiles/{z}/{x}/{y}.pbf"): "vector tile bytes",
    ("GET", "/metrics"): "Prometheus exposition format",
}


def iter_routes_by_file(file_suffix: str) -> Iterator[tuple[str, APIRoute]]:
    """按端点实现所在文件名过滤，产出 (full_path, route)。"""
    from app.main import app

    seen: set[int] = set()

    def walk(routes, prefix=""):
        for r in routes:
            if id(r) in seen:
                continue
            seen.add(id(r))
            if isinstance(r, APIRoute):
                try:
                    f = inspect.getfile(r.endpoint)
                except (TypeError, OSError):
                    continue
                if str(f).replace("\\", "/").endswith(file_suffix):
                    yield prefix + r.path, r
            elif hasattr(r, "original_router") and hasattr(r, "include_context"):
                p = getattr(r.include_context, "prefix", "") or ""
                yield from walk(r.original_router.routes, prefix + p)
            elif hasattr(r, "routes"):
                yield from walk(r.routes, prefix)

    yield from walk(app.routes)


def unguarded_routes(file_suffix: str | None = None):
    """返回缺 response_model 且不在豁免规则内的端点列表。

    豁免：EXCLUSIONS（流式/二进制/定制 media type）+ 204/304 无体状态码。
    """
    missing = []
    for path, route in iter_routes_by_file(file_suffix or ""):
        if route.response_model is not None:
            continue
        if route.status_code in (204, 304):
            continue
        for method in route.methods:
            if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                continue
            if (method, path) in EXCLUSIONS:
                continue
            missing.append((method, path, route.response_model))
    return missing
