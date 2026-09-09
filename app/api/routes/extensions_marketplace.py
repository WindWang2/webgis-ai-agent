"""Extension Marketplace 只读 HTTP API（ADR-0119 / Wave 5）。

只读服务面：search / package detail / versions / blob download。
**写路径（publish/deprecate/revoke/yank）不开 HTTP 端点**——发布需要
签名私钥与文件系统权限，天然是运维 CLI 面（`python -m
app.extensions_platform ...`）；不给攻击者一个带 auth 复杂度的写入口 =
最小攻击面。

下载端点：blob digest 形状校验（`^[0-9a-f]{64}$`）+ 流式响应 + 尺寸
硬顶；路径穿越面为零（文件名即 digest）。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.extensions_platform.diagnostics import ExtensionPlatformError
from app.extensions_platform.marketplace.models import DIGEST_RE
from app.extensions_platform.marketplace.service import RegistryService
from app.extensions_platform.marketplace.store import BLOB_MAX_BYTES

logger = logging.getLogger(__name__)

router = APIRouter()

_SEARCH_LIMIT_MAX = 100


def get_registry_service() -> Optional[RegistryService]:
    """settings 桥：EXTENSION_REGISTRY_DIR 为空 → marketplace 关闭（404 语义）。"""
    from app.extensions_platform.marketplace.bootstrap import registry_service_from_settings

    return registry_service_from_settings()


def _service_or_404() -> RegistryService:
    service = get_registry_service()
    if service is None:
        raise HTTPException(
            status_code=404,
            detail="extension marketplace is not configured (EXTENSION_REGISTRY_DIR is empty)",
        )
    return service


@router.get("/extensions/marketplace/packages")
def search_packages(
    q: str = Query("", max_length=200, description="搜索词（id/title/description 子串）"),
    tag: str = Query("", max_length=64),
    publisher: str = Query("", max_length=64),
    include_revoked: bool = Query(False),
    offset: int = Query(0, ge=0, le=1_000_000),
    limit: int = Query(20, ge=1, le=_SEARCH_LIMIT_MAX),
) -> dict:
    service = _service_or_404()
    result = service.search(
        query=q,
        tag=tag,
        publisher=publisher,
        include_revoked=include_revoked,
        offset=offset,
        limit=limit,
    )
    return {
        "total": result.total,
        "offset": offset,
        "limit": limit,
        "items": list(result.items),
    }


@router.get("/extensions/marketplace/packages/{package_id}")
def get_package(package_id: str) -> dict:
    service = _service_or_404()
    record = service.get_package(package_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown package {package_id!r}")
    latest = record.latest_version()
    return {
        "id": record.id,
        "title": record.title,
        "description": record.description,
        "publisher": record.publisher,
        "status": record.status,
        "deprecation_note": record.deprecation_note,
        "latest_version": latest,
        "versions": sorted(record.versions, reverse=True),
        "tags": list(record.tags),
    }


@router.get("/extensions/marketplace/packages/{package_id}/versions/{version}")
def get_package_version(package_id: str, version: str) -> dict:
    service = _service_or_404()
    record = service.get_package(package_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown package {package_id!r}")
    version_record = record.versions.get(version)
    if version_record is None:
        raise HTTPException(status_code=404, detail=f"unknown version {version!r}")
    return {
        "package_id": package_id,
        **version_record.model_dump(),
        "download": f"/api/v1/extensions/marketplace/packages/{package_id}"
        f"/versions/{version}/download",
    }


@router.get(
    "/extensions/marketplace/packages/{package_id}/versions/{version}/download"
)
def download_package(package_id: str, version: str) -> StreamingResponse:
    service = _service_or_404()
    record = service.get_package(package_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown package {package_id!r}")
    if record.status == "revoked":
        raise HTTPException(status_code=410, detail=f"package {package_id!r} is revoked")
    version_record = record.versions.get(version)
    if version_record is None:
        raise HTTPException(status_code=404, detail=f"unknown version {version!r}")
    if not DIGEST_RE.match(version_record.digest):
        raise HTTPException(status_code=500, detail="registry digest shape invalid")
    store = service.store
    try:
        blob_path = store.blob_path(version_record.digest)
    except ExtensionPlatformError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if not blob_path.is_file():
        raise HTTPException(status_code=503, detail="package blob unavailable (GC?)")
    try:
        stream = store.iter_blob_stream(version_record.digest)
        first = next(stream, b"")
    except ExtensionPlatformError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if version_record.size_bytes > BLOB_MAX_BYTES:
        raise HTTPException(status_code=500, detail="registry blob exceeds size cap")

    def _chunks():
        yield first
        yield from stream

    return StreamingResponse(
        _chunks(),
        media_type="application/gzip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{package_id}-{version}.tar.gz"'
            ),
            "X-Content-Digest": version_record.digest,
        },
    )
