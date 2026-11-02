"""COG 栅格工件发布纪律（GOAL-02 / M3+M4 共用）。

镜像 ``materialization_service.materialize_geoparquet`` 的工件生命周期
（ADR-0249）：原子写（tmp+replace）→ 流式 sha256 → 预算内 DataObject CAS
发布（``publish_cog_data_object``：grid identity + chunk checksums）→
artifact ledger 注册 → ``ref:fabric-cog/<hex>`` disk-cursor。

差异于 parquet lane：入参是**已写好的 COG 文件路径**（coverage/COG 物化
各自负责生成），本模块只做身份/发布/台账三件事；调用方保留生成期预算
账本（requests/bytes/cells）注入 provenance。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

COG_REF_PREFIX = "ref:fabric-cog/"

#: 会话/工件 id 白名单（与 fabric-parquet 同一 charset 纪律）。
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+\Z")

#: Durable DataObject 发布的字节预算（与 parquet lane 同量级；超限落盘+
#: 台账，durable="oversized" 诚实披露）。
_COG_BLOB_PUBLISH_BUDGET_BYTES = 256 * 1024 * 1024


def mint_cog_ref() -> tuple[str, str]:
    """铸造 (ref, artifact_id) —— 调用方负责写文件到 fabric_cog_path。"""
    artifact_id = secrets.token_hex(8)
    return f"{COG_REF_PREFIX}{artifact_id}", artifact_id


def cog_artifact_path(session_id: str, ref: str) -> Path:
    """ref → 磁盘路径（非法 id/会话 → ValueError，绝不拼出逃逸路径）。"""
    from app.services.artifact_registry import fabric_cog_path

    path = fabric_cog_path(session_id, ref)
    if path is None:
        raise ValueError(f"unresolvable fabric-cog ref: {ref!r}")
    return path


async def publish_cog_artifact(
    *,
    session_id: str,
    source_path: Path,
    title: str,
    producer_tool: str,
    provenance: Dict[str, Any],
    redacted_source_url: Optional[str] = None,
    cancel_token: Any = None,
) -> Dict[str, Any]:
    """已写好的 COG → ref + 指纹 + DataObject + 台账（不修改源文件）。

    返回 dict：``{status, ref, path, content_sha256, data_object_id?,
    durable, fingerprint, provenance}``。``source_path`` 所在文件会被移动
    （os.replace）到会话 fabric-cog 目录 —— 调用方之后不得再写该文件。
    取消：move/publish 前检查令牌（已取消 → OperationCancelled，源文件
    保留由调用方清理）。
    """
    from app.lib.cancellation import OperationCancelled
    from app.services.artifact_registry import register_artifact

    if not isinstance(session_id, str) or not _ID_RE.match(session_id):
        raise ValueError(f"invalid session id for disk artifact: {session_id!r}")

    def _file_sha256(path: Path) -> str:
        from app.lib.data.fingerprints import sha256_of_file

        return sha256_of_file(path)

    content_sha256 = await asyncio.to_thread(_file_sha256, source_path)
    if cancel_token is not None and cancel_token.cancelled:
        raise OperationCancelled(cancel_token.reason or "cancelled before publish")

    ref, artifact_id = mint_cog_ref()
    final_path = cog_artifact_path(session_id, ref)

    def _move() -> int:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        # 原子发布：跨设备场景先复制到同目录 tmp 再 replace。
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{final_path.name}.", suffix=".tmp", dir=str(final_path.parent),
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            if source_path.resolve().parent == final_path.parent.resolve():
                os.replace(source_path, final_path)
            else:
                import shutil

                shutil.copy2(source_path, tmp_path)
                os.replace(tmp_path, final_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return final_path.stat().st_size

    size = await asyncio.to_thread(_move)

    # Durable 发布（预算内）：publish_cog_data_object（grid identity +
    # chunk checksums + owner_scope）。
    data_object_id: Optional[str] = None
    manifest_location: Optional[str] = None
    durable_state = "published"
    deduped = False
    if size <= _COG_BLOB_PUBLISH_BUDGET_BYTES:
        def _publish() -> Dict[str, Any]:
            from app.services.lakehouse.raster_object import publish_cog_data_object

            return publish_cog_data_object(
                final_path,
                session_id=session_id,
                source_refs=[provenance.get("source_url")] if provenance.get("source_url") else None,
                producer={
                    "capability": "fabric.raster_materialize",
                    "tool": producer_tool,
                },
            )

        try:
            identity = await asyncio.to_thread(_publish)
            if identity.get("published"):
                data_object_id = identity["data_object_id"]
                manifest_location = identity.get("manifest")
                deduped = bool(identity.get("deduped"))
            else:
                durable_state = f"skipped:{identity.get('reason', 'unknown')}"
        except Exception as e:  # noqa: BLE001 — durable 发布失败诚实降级
            durable_state = "failed"
            logger.warning(
                "[raster_materialization] data object publish failed for %s: %s",
                ref, e,
            )
    else:
        durable_state = "oversized"

    descriptor = dict(provenance.get("descriptor") or {})
    # 台账注册（best-effort；ref 成为 ledger 一等公民）。
    try:
        await register_artifact(
            session_id,
            artifact_id=ref,
            artifact_type="fabric_cog_raster",
            producer_capability="fabric.raster_materialize",
            producer_tool=producer_tool,
            descriptor=descriptor,
            metadata={
                "content_sha256": content_sha256,
                "byte_size": int(size),
                **({"data_object_id": data_object_id} if data_object_id else {}),
                "storage": "disk-cursor",
                "provenance": provenance,
                **(
                    {"source_url": redacted_source_url}
                    if redacted_source_url else {}
                ),
            },
        )
    except Exception as e:  # noqa: BLE001 — 注册是增值记录，绝不阻断
        logger.warning(
            "[raster_materialization] ledger registration skipped for %s: %s",
            ref, e,
        )

    return {
        "status": "success",
        "success": True,
        "ref": ref,
        "path": str(final_path),
        "title": title,
        "format": "cog",
        "bytes": int(size),
        "content_sha256": content_sha256,
        "fingerprint": f"sha256:{content_sha256}",
        **({"data_object_id": data_object_id} if data_object_id else {}),
        "durable": durable_state,
        "deduped": deduped,
        **({"manifest": manifest_location} if manifest_location else {}),
        "provenance": provenance,
    }


__all__ = [
    "COG_REF_PREFIX",
    "cog_artifact_path",
    "mint_cog_ref",
    "publish_cog_artifact",
]
