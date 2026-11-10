"""G20 导出成果落盘（UI 路由与 Pi 工具共用，单一受权下载入口）。"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict


async def persist_snapshot_artifact(
    session_id: str, artifact: Dict[str, Any], *, principal: str
) -> Dict[str, Any]:
    """复用既有 export 目录 / 所有权侧车 / 签名下载 URL，不新建交付系统。"""
    from app.api.routes.map import (
        _export_download_url,
        _persist_export_file,
        _set_export_owner,
    )

    filename = (
        f"raster_expression_{int(time.time())}_{uuid.uuid4().hex[:10]}"
        f"{artifact['extension']}"
    )
    await asyncio.to_thread(
        _persist_export_file, filename, artifact["bytes"], artifact["extension"]
    )
    sidecar = json.dumps(
        {**artifact["sidecar"], "filename": filename},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")
    await asyncio.to_thread(
        _persist_export_file, f"{filename}.sidecar.json", sidecar, ".json"
    )
    await asyncio.to_thread(_set_export_owner, filename, principal)
    return {
        "success": True,
        "filename": filename,
        "download_url": _export_download_url(filename, principal),
        "media_type": artifact["media_type"],
        "bytes": len(artifact["bytes"]),
        "frozen_revision": artifact["frozen_revision"],
        "frozen_style_ids": [
            entry["style_id"] for entry in artifact["sidecar"]["layers"]
        ],
        "omitted_layers": artifact["frozen"]["omitted_layers"],
        "embedded": artifact["embedded"],
        "expression_summary": artifact["sidecar"]["layers"][0]["statement"],
        "limits": artifact["sidecar"]["limits"],
    }
