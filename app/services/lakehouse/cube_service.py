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
                **({"content_location": durable.get("manifest")}
                   if durable.get("manifest") else {}),
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

    if not is_cube_ref(ref):
        raise CubeServiceError(f"not a cube ref: {str(ref)[:64]!r}")
    path = cube_store_path(session_id, ref)
    if path is None or not cube_ref_exists(session_id, ref):
        raise CubeServiceError(
            f"cube not alive: {ref}", code="CUBE_REF_MISSING"
        )
    # 全 cube 读拒绝（review M-2 OOM DoS）：至少一个有限切片，且读量受
    # 单元预算约束（轴长未知的切片在读前按 cube 形状钳制）。
    if time is None and y is None and x is None:
        raise CubeServiceError(
            "window read requires at least one bounded slice (time/y/x); "
            "whole-cube reads are refused",
            code="CUBE_WINDOW_UNBOUNDED",
        )
    for name, pair in (("time", time), ("y", y), ("x", x)):
        if isinstance(pair, slice) and (
            (pair.start is not None and pair.start < 0)
            or (pair.stop is not None and pair.stop < 0)
        ):
            raise CubeServiceError(
                f"{name} slice must be non-negative", code="CUBE_WINDOW_INVALID",
            )
    result = await asyncio.to_thread(
        _read_window_bounded, path, time, y, x,
    )
    result["ref"] = ref
    return result


#: 单次窗口读的单元预算（bands × time × y × x 的元素总数上限）。
CUBE_WINDOW_MAX_CELLS = 8_000_000


def _read_window_bounded(
    path, time: Optional[Any], y: Optional[Any], x: Optional[Any],
) -> Dict[str, Any]:
    """窗口读 + 形状钳制 + 单元预算（切片越界 = 钳制，负号已在上游拒绝）。"""
    from app.services.lakehouse.cube_store import (
        _require_v1_cube,
        open_cube,
        read_cube_window,
    )

    root = open_cube(path)
    # 版本闸（R0-2）：labeled v2 store 不满足本入口的 (time,y,x) 假设。
    bands = _require_v1_cube(root, what="session cube window read")
    if not bands:
        raise CubeServiceError("cube declares no bands")
    n_t, n_y, n_x = (int(v) for v in root[bands[0]].shape)

    def _clamp(s: Optional[slice], n: int) -> Optional[slice]:
        if s is None:
            return None
        start = 0 if s.start is None else min(int(s.start), n)
        stop = n if s.stop is None else min(int(s.stop), n)
        return slice(start, max(start, stop))

    time_s, y_s, x_s = _clamp(time, n_t), _clamp(y, n_y), _clamp(x, n_x)
    t_len = (time_s.stop - time_s.start) if time_s else n_t
    y_len = (y_s.stop - y_s.start) if y_s else n_y
    x_len = (x_s.stop - x_s.start) if x_s else n_x
    cells = len(bands) * t_len * y_len * x_len
    if cells > CUBE_WINDOW_MAX_CELLS:
        raise CubeServiceError(
            f"window requests {cells} cells, exceeding the bounded budget "
            f"{CUBE_WINDOW_MAX_CELLS} — narrow the slice",
            code="CUBE_WINDOW_TOO_LARGE",
        )
    return read_cube_window(path, time=time_s, y=y_s, x=x_s)


async def revise_session_cube(
    session_id: str,
    ref: str,
    *,
    updates: Sequence[Mapping[str, Any]],
    title: str,
) -> Dict[str, Any]:
    """cube 修订生产路径（fork_cube_revision 的真实调用方）：

    ``updates = [{"band", "time_index", "source"}, ...]`` —— 源 store 逐字节
    不动，指定 (band, time) 片以新源重写，产出新不可变修订（新 ref +
    新 DataObject 身份）。血缘 source_refs 携带全部修订源指纹。
    """
    from app.lib.geo_raster.chunk import iter_chunk_descriptors
    from app.lib.geo_raster.reader import RasterReader
    from app.services.artifact_registry import (
        cube_ref_exists,
        cube_store_path,
        get_artifact,
        is_cube_ref,
        register_artifact,
    )
    from app.services.lakehouse.cube_store import (
        fork_cube_revision,
        publish_cube,
        read_cube_window,
    )

    if not isinstance(session_id, str) or not _CUBE_ID_RE.match(session_id):
        raise CubeServiceError(f"invalid session id: {session_id!r}")
    if not is_cube_ref(ref):
        raise CubeServiceError(f"not a cube ref: {str(ref)[:64]!r}")
    source_dir = cube_store_path(session_id, ref)
    if source_dir is None or not cube_ref_exists(session_id, ref):
        raise CubeServiceError(f"cube not alive: {ref}", code="CUBE_REF_MISSING")

    import numpy as _np

    resolved_updates: Dict[str, list] = {}
    source_prints: Dict[str, str] = {}
    for step in updates[:64]:
        band = str(step.get("band") or "")
        t_index = int(step.get("time_index") or 0)
        source = str(step.get("source") or "")
        path = await asyncio.to_thread(_resolve_time_source, session_id, source)
        reader = await asyncio.to_thread(RasterReader.open, str(path))
        try:
            descriptors = list(await asyncio.to_thread(
                iter_chunk_descriptors, reader,
                identity_extra=f"revise:{band}:t={t_index}",
            ))
        finally:
            reader.close()
        if not descriptors:
            raise CubeServiceError(
                f"no chunks for revision source: {source[:64]}"
            )
        resolved_updates.setdefault(band, []).append(
            (t_index, descriptors, None)
        )
        source_prints[str(path)] = await asyncio.to_thread(_raster_fingerprint, path)

    new_id = secrets.token_hex(8)
    new_ref = f"ref:cube/{new_id}"
    new_dir = cube_store_path(session_id, new_ref)
    if new_dir is None:  # 防御性
        raise CubeServiceError("unresolvable revision id")
    try:
        await asyncio.to_thread(
            lambda: fork_cube_revision(
                source_dir, new_dir, updates=resolved_updates,
            ),
        )
    except Exception as e:
        raise CubeServiceError(f"cube revision failed: {e}") from e

    old_record = await get_artifact(session_id, ref)
    prev_meta = (old_record.metadata if old_record is not None else {}) or {}
    durable: Dict[str, Any] = {"published": False, "reason": "publish_failed"}
    try:
        publication = await asyncio.to_thread(
            publish_cube,
            new_dir,
            session_id=session_id,
            payload_extra={
                "title": title,
                "times": prev_meta.get("times") or [],
                "revision_of": ref,
                "revision_source_fingerprints": source_prints,
            },
            producer={"capability": "lakehouse.cube_revise",
                      "tool": "revise_session_cube"},
            source_refs=[f"fingerprint:{d}" for d in source_prints.values()],
        )
        durable = publication
    except Exception as e:  # noqa: BLE001 — 诚实降级
        logger.warning("[cube_service] revision publish failed: %s", e)

    try:
        await register_artifact(
            session_id,
            artifact_id=new_ref,
            artifact_type="lakehouse_cube",
            producer_capability="lakehouse.cube_revise",
            producer_tool="revise_session_cube",
            inputs=[ref],
            descriptor={"feature_count": len(prev_meta.get("times") or [])},
            metadata={
                "title": title,
                "times": prev_meta.get("times") or [],
                "cube_id": new_id,
                "revision_of": ref,
                **({"data_object_id": durable.get("data_object_id")}
                   if durable.get("data_object_id") else {}),
                **({"content_location": durable.get("manifest")}
                   if durable.get("manifest") else {}),
                "storage": "disk-cursor",
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[cube_service] revision ledger registration skipped: %s", e)

    window = await asyncio.to_thread(
        read_cube_window, new_dir, time=slice(0, 1),
    )
    return {
        "status": "success",
        "success": True,
        "ref": new_ref,
        "cube_id": new_id,
        "revision_of": ref,
        "path": str(new_dir),
        "title": title,
        "times": prev_meta.get("times") or [],
        "first_step_preview": {
            band: _np.asarray(data).tolist()
            for band, data in (window.get("bands") or {}).items()
        },
        **durable,
    }


# ── V7（ADR-0119）：labeled cube 的会话选择读 ──────────────────────────


async def read_session_labeled_window(
    session_id: str,
    ref: str,
    *,
    selection: Mapping[str, Any],
    max_cells: int = CUBE_WINDOW_MAX_CELLS,
) -> Dict[str, Any]:
    """labeled cube 的标签级窗口读（owner = session 域；预算闸同 V6）。"""
    from app.services.artifact_registry import (
        cube_ref_exists,
        cube_store_path,
        is_cube_ref,
    )
    from app.services.lakehouse.cube_store import (
        open_cube,
        read_labeled_window,
    )
    from app.services.lakehouse.labeled_selection import plan_selection
    from app.services.lakehouse.xarray_adapter import (
        labeled_projection_from_store,
    )

    if not is_cube_ref(ref):
        raise CubeServiceError(f"not a cube ref: {str(ref)[:64]!r}")
    path = cube_store_path(session_id, ref)
    if path is None or not cube_ref_exists(session_id, ref):
        raise CubeServiceError(
            f"cube not alive: {ref}", code="CUBE_REF_MISSING"
        )
    import numpy as np_

    root = open_cube(path)
    if not (root.attrs or {}).get("labeled"):
        raise CubeServiceError(
            "ref is a V6 cube — use the v1 window endpoint",
            code="CUBE_SCHEMA_V1",
        )
    projection = await asyncio.to_thread(labeled_projection_from_store, path)
    dims = [str(d) for d in (projection.get("dims") or [])]
    coords = {}
    for dim in dims:
        arr = root[dim] if dim in root else None
        if arr is not None:
            coords[dim] = np_.asarray(arr)
        else:
            raise CubeServiceError(
                f"cube dim {dim!r} has no coordinate array",
                code="CUBE_WINDOW_INVALID",
            )
    plan = await asyncio.to_thread(
        plan_selection,
        projection=projection,
        coordinates=coords,
        selection=selection,
        max_cells=max_cells,
    )
    slices = {
        d: slice(int(a), int(b)) for d, (a, b) in plan["slices"].items()
    }
    result = await asyncio.to_thread(
        read_labeled_window, path, index_slices=slices,
    )
    result["selection_plan"] = {
        "cells": plan["cells"],
        "touched_chunks": plan["plan"]["touched_chunks"],
        "total_chunks": plan["plan"]["total_chunks"],
        "slices": plan["slices"],
    }
    result["ref"] = ref
    return result
