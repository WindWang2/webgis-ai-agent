"""Session cube service — Spatial Lakehouse V6 (Wave 7, ADR-0118).

时空 cube 的**会话生产路径**：一组网格一致的时间片栅格 → zarr cube
（``ref:cube/<id>`` disk-cursor + 内容寻址 DataObject）。

单一事实源边界：写入/读取语义全部在 ``lakehouse/cube_store``（其校验
在 geo_raster foundation）；本模块只做：源解析（路径白名单）、chunk
descriptor 组装（``iter_chunk_descriptors`` —— 唯一分区权威的投影）、
台账注册、发布与血缘接线（source_refs = 输入栅格的内容指纹）。

网格纪律：时间片网格不一致 → typed 拒绝（cube_store 的网格契约），
**绝不静默重采样** —— 对齐是调用方/上游工具的职责。
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

#: cube 输入路径白名单（会话域路径校验复用 upload/COG 的同一目录闸）。
_CUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")


class CubeServiceError(ValueError):
    """cube 生产失败（typed；真实失败，绝不返回死 ref）。"""

    code = "CUBE_BUILD_FAILED"

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code

    def to_dict(self) -> dict:
        return {"success": False, "code": self.code, "message": self.message}


def _resolve_time_source(session_id: str, source: str) -> Path:
    """时间片源解析：``ref:fabric-parquet/*`` 走会话派生；其余按
    DATA_DIR 域内路径（validate_data_path 同级纪律：越界即拒绝）。"""
    from app.services.artifact_registry import fabric_parquet_path
    from app.services.data_fabric.materialization_service import (
        GEOPARQUET_REF_PREFIX,
    )

    if not isinstance(source, str) or not source:
        raise CubeServiceError(f"invalid time source: {source!r}")
    if source.startswith("ref:cube/") or source.startswith("ref:raster/"):
        raise CubeServiceError(
            f"unsupported time source ref: {source[:64]!r} (cubes build "
            "from raster paths / fabric-parquet refs)"
        )
    if source.startswith(GEOPARQUET_REF_PREFIX):
        path = fabric_parquet_path(session_id, source)
        if path is None or not path.is_file():
            raise CubeServiceError(
                f"fabric-parquet source not alive: {source[:64]}",
                code="CUBE_SOURCE_MISSING",
            )
        return path
    from app.core.config import settings
    from app.utils.path import validate_data_path

    try:
        resolved = Path(validate_data_path(source, data_dir=str(settings.DATA_DIR)))
    except ValueError as e:
        raise CubeServiceError(f"time source outside data dir: {e}") from e
    if not resolved.is_file():
        raise CubeServiceError(
            f"time source not found: {source[:64]}", code="CUBE_SOURCE_MISSING"
        )
    return resolved


def _raster_fingerprint(path: Path) -> str:
    """有界栅格内容指纹（V5 corners 机制，零全量读）。"""
    try:
        from app.lib.geo_raster.fingerprint import raster_content_fingerprint_v5

        return str(raster_content_fingerprint_v5(str(path)))
    except Exception:  # noqa: BLE001 — 指纹失败退化为文件 stat 投影（诚实弱证据）
        import hashlib

        st = path.stat()
        return hashlib.sha256(
            f"{path.name}:{st.st_size}:{st.st_mtime_ns}".encode()
        ).hexdigest()


async def build_session_cube(
    session_id: str,
    *,
    time_sources: Sequence[Mapping[str, str]],
    title: str,
    window_side: Optional[int] = None,
) -> Dict[str, Any]:
    """时间片栅格 → 会话 cube（zarr store + ref:cube/<id> + DataObject）。

    ``time_sources = [{"time": "<ISO-8601>", "source": "<path|ref:fabric-parquet/*>"}, ...]``
    （时间升序由调用方保证 —— cube 时间轴身份即给定顺序）。返回 ref、
    路径、数据对象身份与血缘证据；任一环节真实失败 = typed 错误。
    """
    if not isinstance(session_id, str) or not _CUBE_ID_RE.match(session_id):
        raise CubeServiceError(f"invalid session id: {session_id!r}")
    if not time_sources or len(time_sources) > 512:
        raise CubeServiceError(
            f"time_sources must be 1..512 steps, got {len(time_sources)}"
        )
    from app.lib.geo_raster.chunk import iter_chunk_descriptors
    from app.lib.geo_raster.reader import RasterReader
    from app.services.lakehouse.cube_store import publish_cube, write_cube

    resolved: List[Any] = []
    for step in time_sources:
        time_label = str(step.get("time") or "").strip()
        if not time_label:
            raise CubeServiceError("every time_sources entry needs 'time'")
        resolved.append((time_label, await asyncio.to_thread(
            _resolve_time_source, session_id, str(step.get("source") or ""),
        )))

    # 组装 descriptor（chunk 分区走唯一权威 iter_bounded_windows 的投影）。
    band_groups: List[List[Any]] = []
    fingerprints: Dict[str, str] = {}
    times: List[str] = []
    for time_label, path in resolved:
        times.append(time_label)
        reader = await asyncio.to_thread(RasterReader.open, str(path))
        try:
            descriptors = list(await asyncio.to_thread(
                iter_chunk_descriptors, reader,
                window_side=window_side, identity_extra=f"cube:{time_label}",
            ))
        finally:
            reader.close()
        if not descriptors:
            raise CubeServiceError(
                f"no chunks for time {time_label}", code="CUBE_BUILD_FAILED"
            )
        band_groups.append(descriptors)
        fingerprints[str(path)] = await asyncio.to_thread(_raster_fingerprint, path)

    # 网格一致性预检（fail-fast，错误消息带时间步 —— 写入侧还会再验一次）。
    from app.services.lakehouse.cube_store import _validate_band_groups

    try:
        _validate_band_groups({"b1": band_groups}, times)
    except Exception as e:
        raise CubeServiceError(f"cube grid validation failed: {e}") from e

    cube_id = secrets.token_hex(8)  # 16 hex（会话内唯一 cursor id）
    from app.services.artifact_registry import cube_store_path

    store_dir = cube_store_path(session_id, f"ref:cube/{cube_id}")
    if store_dir is None:  # 防御性
        raise CubeServiceError("unresolvable cube id")
    input_fingerprint = await asyncio.to_thread(
        _cube_input_fingerprint, fingerprints, times, window_side,
    )
    try:
        await asyncio.to_thread(
            write_cube, {"b1": band_groups}, times, store_dir,
        )
    except Exception as e:
        raise CubeServiceError(f"cube write failed: {e}") from e

    # 发布（身份/去重/血缘）—— best-effort：失败降级披露，ref 仍指真实 store。
    durable: Dict[str, Any] = {"published": False, "reason": "publish_failed"}
    try:
        publication = await asyncio.to_thread(
            publish_cube,
            store_dir,
            session_id=session_id,
            payload_extra={"title": title, "times": times,
                           "input_fingerprints": fingerprints},
            producer={"capability": "lakehouse.cube_build",
                      "tool": "build_session_cube"},
            source_refs=[f"fingerprint:{d}" for d in fingerprints.values()],
            input_fingerprint=input_fingerprint,
        )
        durable = publication
    except Exception as e:  # noqa: BLE001 — 诚实降级
        logger.warning("[cube_service] publish failed for %s: %s", cube_id, e)

    # 台账注册（best-effort —— ref 是 cursor 真相；注册使 GC/probe 可见）。
    try:
        from app.services.artifact_registry import register_artifact

        await register_artifact(
            session_id,
            artifact_id=f"ref:cube/{cube_id}",
            artifact_type="lakehouse_cube",
            producer_capability="lakehouse.cube_build",
            producer_tool="build_session_cube",
            descriptor={"feature_count": len(times)},
            metadata={
                "title": title,
                "times": times,
                "cube_id": cube_id,
                **({"data_object_id": durable.get("data_object_id")}
                   if durable.get("data_object_id") else {}),
                "storage": "disk-cursor",
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[cube_service] ledger registration skipped: %s", e)

    logger.info(
        "[cube_service] cube '%s' -> ref:cube/%s (%d steps, durable=%s)",
        title, cube_id, len(times), durable.get("durable", "none"),
    )
    return {
        "status": "success",
        "success": True,
        "ref": f"ref:cube/{cube_id}",
        "cube_id": cube_id,
        "path": str(store_dir),
        "title": title,
        "times": times,
        "steps": len(times),
        "content_fingerprints": fingerprints,
        **durable,
    }


def _cube_input_fingerprint(
    fingerprints: Mapping[str, str], times: Sequence[str], window_side: Optional[int],
) -> str:
    """复用键：同输入（顺序敏感）+ 同时间轴 + 同 chunking ⇒ 同 cube 身份。"""
    from app.services.lakehouse.data_object import compute_object_reuse_fingerprint

    return compute_object_reuse_fingerprint(
        operation="lakehouse.cube_build",
        operation_version="v6",
        input_fingerprints={
            str(i): fp for i, fp in enumerate(fingerprints.values())
        },
        normalized_args={"times": list(times), "window_side": window_side},
    )


async def read_session_cube_window(
    session_id: str,
    ref: str,
    *,
    time: Optional[Any] = None,
    y: Optional[Any] = None,
    x: Optional[Any] = None,
) -> Dict[str, Any]:
    """会话 cube 窗口读（chunk 粒度；owner = session 域）。"""
    from app.services.artifact_registry import cube_ref_exists, cube_store_path, is_cube_ref
    from app.services.lakehouse.cube_store import read_cube_window

    if not is_cube_ref(ref):
        raise CubeServiceError(f"not a cube ref: {str(ref)[:64]!r}")
    path = cube_store_path(session_id, ref)
    if path is None or not cube_ref_exists(session_id, ref):
        raise CubeServiceError(
            f"cube not alive: {ref}", code="CUBE_REF_MISSING"
        )
    result = await asyncio.to_thread(
        read_cube_window, path, time=time, y=y, x=x,
    )
    result["ref"] = ref
    return result
