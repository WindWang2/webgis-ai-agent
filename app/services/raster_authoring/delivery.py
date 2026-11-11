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
        _persist_export_file,
        _set_export_owner,
    )
    from app.core.signing import sign_path

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
    # 恒签发能力 URL：登录用户的 Bearer 不会随 window.open/<img> 导航携带，
    # 裸路径必然 401（既有 /export 的匿名会话已用同款 HMAC 能力）。
    download_path = f"/api/v1/export/download/{filename}"
    exp, sig = sign_path(download_path, ttl_seconds=3600)
    return {
        "success": True,
        "filename": filename,
        "download_url": f"{download_path}?exp={exp}&sig={sig}",
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
