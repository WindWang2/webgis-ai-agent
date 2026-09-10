"""版本/构建信息端点（Platform V4，ADR-0131 D7）。

``GET /api/v1/version``：极简构建身份（version/commit/python/extensions
API）。字段全部非敏感、无环境细节（防侦察纪律同 SEC-11）——运维/前端
探测"这个 Pod 跑的是什么"从此有权威出处。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.build_info import build_info

router = APIRouter()

#: 扩展平台宿主 API 版本（与 extensions_platform.api_version 对齐的展示面）
_EXTENSIONS_HOST_API = "1.2.0"


@router.get("/version")
def version():
    """构建身份（公开、极简、无环境细节）。"""
    info = build_info()
    return {
        "version": info["version"],
        "commit": info["commit"],
        "python": info["python"],
        "extensions_api": _EXTENSIONS_HOST_API,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


__all__ = ["router"]
