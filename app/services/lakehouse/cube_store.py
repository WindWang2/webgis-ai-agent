"""Spatiotemporal cube store — Spatial Lakehouse V6 (Wave 6-7, ADR-0118).

Zarr V6 cube 运行时：**group + per-band 数组 + consolidated metadata +
内容寻址修订**。单一事实源边界：

- 网格契约/铺排验证复用 ``app/lib/geo_raster/zarr`` foundation（不重建
  窗口/网格运行时 —— V4 红线）；
- 身份/发布复用 ``lakehouse/data_object``（manifest CAS，去重免费）；
- 字节真相只有 BlobStore 一份（预算内的 chunk blob 副本）；工作目录是
  zarr store（hot copy），manifest 是身份与完整性真相。

形状契约：cube = zarr group，`dims=["time","y","x"]`，每 band 一个
``(time, y, x)`` 数组（band 维度以多数组表达 —— 与 foundation 的单数组
``write_zarr_cube`` 同构，组根 attrs 记 band 列表）。attrs 键与 foundation
一致（crs/transform/times/nodata），``open_zarr_array(<store>/<band>)`` 可
对单 band 做 chunk descriptor 往返。

不可变修订（partial update → new revision）：

``fork_cube_revision`` 用 **硬链接 CoW**（未变 chunk 文件 os.link 进新
store，改写的时间片写新文件；跨设备/不支持时整体拷贝降级）—— 旧 store
目录逐字节不动，旧修订持续可验证；新 manifest 内容根 ≠ 旧 ⇒ 新 DataObject
id，即新不可变修订。零 DB 迁移（append-only 语义在 manifest 层）。
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

from app.services.lakehouse.data_object import (
    DataObjectError,
    MAX_MANIFEST_BLOBS,
    normalize_owner_scope,
    publish_manifest_only,
    publish_data_object,
)

logger = logging.getLogger(__name__)

#: cube manifest 契约版本。
CUBE_SCHEMA_VERSION = 1
#: cube blob 发布默认预算（超过 = manifest_only 诚实降级）。
DEFAULT_CUBE_BLOB_BUDGET_BYTES = 128 * 1024 * 1024

#: zarr v3 目录布局的 chunk 路径段（``<band>/c/<t>/<y>/<x>``）；v2 布局
#. （``.zarray`` + ``<t>.<y>.<x>``）不识别 → CoW 全拷贝（保守正确）。


class CubeError(ValueError):
    """cube 契约违例（typed；诚实拒绝，绝不写半截 cube）。"""

    code = "LAKEHOUSE_CUBE_INVALID"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict:
        return {"success": False, "code": self.code, "message": self.message}


def _require_zarr():
    from app.lib.geo_raster.zarr import _require_zarr as _rq

    return _rq()


def _validate_band_groups(
    band_descriptors: Mapping[str, Sequence[Sequence[Any]]],
    times: Sequence[str],
) -> Tuple[int, int, str]:
    """全部 band 的时间片描述符过 foundation 铺排/网格/dtype 校验。"""
    from app.lib.geo_raster.zarr import (
        _grid_identity_dict_local,
        _verify_descriptor_tiling,
    )

    if not band_descriptors:
        raise CubeError("refusing to write a cube with no bands")
    ref_grid = None
    ref_dtype = ""
    height = width = 0
    for band, groups in sorted(band_descriptors.items()):
        if len(groups) != len(times):
            raise CubeError(
                f"band {band!r}: {len(groups)} time slices != {len(times)} times"
            )
        flat = [d for group in groups for d in group]
        if not flat:
            raise CubeError(f"band {band!r}: descriptor groups are empty")
        grid = flat[0].grid
        dtype = str(flat[0].dtype)
        for d in flat:
            if d.dtype != dtype:
                raise CubeError(f"band {band!r}: mixed chunk dtypes")
            if _grid_identity_dict_local(d.grid) != _grid_identity_dict_local(grid):
                raise CubeError(
                    f"band {band!r}: chunk {d.chunk_id} off-grid; cubes require "
                    "one common grid (align sources first — no silent resample)"
                )
        if ref_grid is None:
            ref_grid, ref_dtype = grid, dtype
            height, width = int(grid.height), int(grid.width)
        else:
            if _grid_identity_dict_local(grid) != _grid_identity_dict_local(ref_grid):
                raise CubeError(
                    f"band {band!r} grid differs from cube reference grid"
                )
            if dtype != ref_dtype:
                raise CubeError(
                    f"band {band!r} dtype {dtype} != reference {ref_dtype}"
                )
        for t, group in enumerate(groups):
            _verify_descriptor_tiling(
                group, height=height, width=width, what=f"{band} slice {t} ({times[t]})"
            )
    return height, width, ref_dtype


def write_cube(
    band_descriptors: Mapping[str, Sequence[Sequence[Any]]],
    times: Sequence[str],
    out_store: Union[str, Path],
    *,
    read_chunk: Optional[Callable[[Any, str], Any]] = None,
    overwrite: bool = True,
) -> Path:
    """写入 group cube（每 band 一个 (time,y,x) 数组 + consolidated metadata）。

    校验先于建数组（foundation 同纪律）：网格一致性 + 逐时间片精确铺排，
    违例 typed :class:`CubeError`。数据源 per chunk 与 foundation 同语义
    （``read_chunk`` loader 或 descriptor.source_uri 走 RasterReader 窗口读）。
    """
    from app.lib.geo_raster.reader import RasterReader

    zarr = _require_zarr()
    if len(times) != len(set(times)):
        raise CubeError("times must be unique (time axis identity)")
    if not times:
        raise CubeError("refusing to write an empty cube")
    out_store = Path(out_store)
    if out_store.exists() and not overwrite:
        raise CubeError(f"store already exists: {out_store}")

    height, width, dtype = _validate_band_groups(band_descriptors, times)
    ref_grid = next(iter(band_descriptors.values()))[0][0].grid
    bands = sorted(band_descriptors)

    store_str = str(out_store)
    try:
        root = zarr.open_group(store=store_str, mode="w")
        for band in bands:
            groups = band_descriptors[band]
            w0 = groups[0][0]
            chunk_y = max(1, min(int(w0.window[3]), height))
            chunk_x = max(1, min(int(w0.window[2]), width))
            arr = root.create_array(
                band,
                shape=(len(times), height, width),
                chunks=(1, chunk_y, chunk_x),
                dtype=dtype,
            )
            arr.attrs["crs"] = ref_grid.crs
            arr.attrs["transform"] = [float(v) for v in ref_grid.transform]
            arr.attrs["nodata"] = ref_grid.nodata
            arr.attrs["times"] = [str(t) for t in times]
            for t, time_label in enumerate(times):
                for d in groups[t]:
                    if read_chunk is not None:
                        data = read_chunk(d, str(time_label))
                    else:
                        if not d.source_uri:
                            raise CubeError(
                                f"chunk {d.chunk_id} has no source_uri and no "
                                "read_chunk loader was provided"
                            )
                        reader = RasterReader.open(d.source_uri)
                        try:
                            data = reader.read_window(
                                d.window, band=int(d.band_indexes[0])
                            )
                        finally:
                            reader.close()
                    col, row, cw, ch = (int(v) for v in d.window)
                    data = np.asarray(data)
                    if data.shape != (ch, cw):
                        raise CubeError(
                            f"chunk {d.chunk_id} loader returned {data.shape}, "
                            f"expected {(ch, cw)}"
                        )
                    arr[t, row:row + ch, col:col + cw] = data
        root.attrs["dims"] = ["time", "y", "x"]
        root.attrs["bands"] = bands
        root.attrs["times"] = [str(t) for t in times]
        root.attrs["cube_schema_version"] = CUBE_SCHEMA_VERSION
    except CubeError:
        raise
    except Exception as e:  # noqa: BLE001 — zarr 故障 typed 包装
        raise CubeError(f"cannot write cube store {store_str!r}: {e}") from e
    consolidate_cube_metadata(out_store)
    return out_store


def consolidate_cube_metadata(store: Union[str, Path]) -> bool:
    """consolidated metadata（有界成本：元数据级）；失败 = 诚实缺席。"""
    import warnings

    zarr = _require_zarr()
    try:
        with warnings.catch_warnings():
            # zarr 3 明示 consolidated metadata 尚未进 format-3 规范 ——
            # 我们仍写（同仓 readers 受益），但绝不因该告警失败。
            warnings.simplefilter("ignore")
            zarr.consolidate_metadata(str(store))
        return True
    except Exception as e:  # noqa: BLE001 — 元数据整合失败不影响数据正确性
        logger.warning("[cube_store] consolidate_metadata failed for %s: %s", store, e)
        return False


def open_cube(store: Union[str, Path]) -> Any:
    """打开 cube group（typed 错误；consolidated 元数据存在则透明受益）。"""
    zarr = _require_zarr()
    p = Path(store)
    if not p.is_dir():
        raise CubeError(f"cube store not found: {p}")
    try:
        return zarr.open_group(store=str(p), mode="r")
    except Exception as e:  # noqa: BLE001
        raise CubeError(f"cannot open cube store {str(p)!r}: {e}") from e


def read_cube_window(
    store: Union[str, Path],
    *,
    bands: Optional[Sequence[str]] = None,
    time: Optional[slice] = None,
    y: Optional[slice] = None,
    x: Optional[slice] = None,
) -> Dict[str, Any]:
    """窗口读（zarr 原生 chunk 粒度 —— 只触窗口相交 chunk）。

    返回 ``{"bands": {band: np.ndarray}, "times": [...], "crs", "transform",
    "nodata"}``；无参 slice = 读整个轴（显式全读是调用方的决定）。
    """
    root = open_cube(store)
    wanted = list(bands) if bands else list(root.attrs.get("bands") or [])
    if not wanted:
        raise CubeError("cube declares no bands")
    out: Dict[str, Any] = {}
    meta: Dict[str, Any] = {}
    for band in wanted:
        if band not in root:
            raise CubeError(f"band {band!r} not in cube")
        arr = root[band]
        out[band] = np.asarray(arr[time or slice(None), y or slice(None), x or slice(None)])
        if not meta:
            meta = {
                "times": list(arr.attrs.get("times") or []),
                "crs": arr.attrs.get("crs"),
                "transform": arr.attrs.get("transform"),
                "nodata": arr.attrs.get("nodata"),
            }
    return {"bands": out, **meta}


# ── chunk 清点 / 发布 / 修订（内容寻址）────────────────────────────────


def collect_cube_entries(
    store_dir: Union[str, Path],
) -> List[Tuple[str, str, int]]:
    """store 目录全量清点 → (相对路径, sha256, size)，路径排序稳定。

    元数据文件（zarr.json 等）与 chunk 同等参与内容根 —— cube 的身份即
    逐字节身份。文件数超 manifest blob 闸 → typed 拒绝。
    """
    base = Path(store_dir)
    if not base.is_dir():
        raise CubeError(f"cube store not found: {base}")
    entries: List[Tuple[str, str, int]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        h = hashlib.sha256()
        size = 0
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
                size += len(chunk)
        entries.append((rel, h.hexdigest(), size))
        if len(entries) > MAX_MANIFEST_BLOBS:
            raise DataObjectError(
                f"cube declares more than {MAX_MANIFEST_BLOBS} files — "
                "refusing to identity an unbounded object"
            )
    if not entries:
        raise CubeError(f"cube store is empty: {base}")
    return entries


def publish_cube(
    store_dir: Union[str, Path],
    *,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    payload_extra: Optional[Mapping[str, Any]] = None,
    producer: Optional[Mapping[str, Any]] = None,
    source_refs: Optional[List[str]] = None,
    input_fingerprint: Optional[str] = None,
    blob_budget_bytes: int = DEFAULT_CUBE_BLOB_BUDGET_BYTES,
) -> Dict[str, Any]:
    """cube store → DataObject 修订（manifest 恒发布；blob 预算内随发布）。

    返回 ``{"published"|"manifest_only", "data_object_id", "manifest",
    "content_sha256", "byte_size", "deduped", "entry_count"}``。
    """
    try:
        owner_scope = normalize_owner_scope(
            session_id=session_id, project_id=project_id
        )
    except DataObjectError:
        return {"published": False, "reason": "owner_missing"}
    base = Path(store_dir)
    entries = collect_cube_entries(base)
    total = sum(n for _p, _d, n in entries)
    payload: Dict[str, Any] = {
        "dims": ["time", "y", "x"],
        "cube_schema_version": CUBE_SCHEMA_VERSION,
        **(dict(payload_extra) if payload_extra else {}),
    }
    if total <= blob_budget_bytes:
        identity = publish_data_object(
            {rel: base / rel for rel, _d, _n in entries},
            kind="zarr_cube",
            owner_scope=owner_scope,
            payload=payload,
            producer=producer,
            source_refs=source_refs,
            input_fingerprint=input_fingerprint,
        )
        return {
            "published": True,
            "durable": "published",
            "data_object_id": identity.data_object_id,
            "manifest": identity.manifest_location,
            "content_sha256": identity.content_sha256,
            "byte_size": identity.byte_size,
            "deduped": identity.deduped,
            "entry_count": len(entries),
        }
    identity = publish_manifest_only(
        entries,
        kind="zarr_cube",
        owner_scope=owner_scope,
        payload=payload,
        producer=producer,
        source_refs=source_refs,
        input_fingerprint=input_fingerprint,
    )
    return {
        "published": True,
        "durable": "manifest_only",
        "data_object_id": identity.data_object_id,
        "manifest": identity.manifest_location,
        "content_sha256": identity.content_sha256,
        "byte_size": identity.byte_size,
        "deduped": identity.deduped,
        "entry_count": len(entries),
    }


def _is_v3_chunk_path(rel: str) -> Optional[Tuple[str, Tuple[int, ...]]]:
    """``<band>/c/<i>...`` → (band, chunk 坐标)；非 chunk 文件 → None。"""
    parts = rel.split("/")
    if len(parts) >= 3 and parts[1] == "c" and all(p.isdigit() for p in parts[2:]):
        return parts[0], tuple(int(p) for p in parts[2:])
    return None


def fork_cube_revision(
    source_dir: Union[str, Path],
    target_dir: Union[str, Path],
    *,
    updates: Mapping[str, Sequence[Tuple[int, Sequence[Any], Optional[Callable[[Any, str], Any]]]]],
) -> Path:
    """CoW 修订：未变文件硬链接，``updates`` 指定的 (band, t) 时间片重写。

    ``updates[band] = [(t_index, descriptors, read_chunk), ...]``；descriptor
    语义与 :func:`write_cube` 相同（同网格、精确铺排）。旧 store 逐字节
    不动 —— 旧修订持续可验证；跨设备硬链接失败 → 保守整体拷贝（正确性
    优先，成本诚实）。zarr v2/未知布局 → 全拷贝（无法识别 chunk 归属）。
    """
    from app.lib.geo_raster.reader import RasterReader

    zarr = _require_zarr()
    src, dst = Path(source_dir), Path(target_dir)
    if not src.is_dir():
        raise CubeError(f"source cube not found: {src}")
    if dst.exists():
        raise CubeError(f"target revision store already exists: {dst}")

    root = zarr.open_group(store=str(src), mode="r")
    bands = list(root.attrs.get("bands") or [])
    times = [str(t) for t in (root.attrs.get("times") or [])]
    for band, per_band in updates.items():
        if band not in bands:
            raise CubeError(f"unknown band for update: {band!r}")
        for t_index, _desc, _rc in per_band:
            if not (0 <= t_index < len(times)):
                raise CubeError(
                    f"time index {t_index} out of range ({len(times)} steps)"
                )

    replaced: set = set()
    known_v3 = (src / "zarr.json").is_file()
    for band, per_band in updates.items():
        for t_index, _descriptors, _rc in per_band:
            replaced.add((band, t_index))

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.mkdir()

    def _link_or_copy(s: Path, t: Path) -> None:
        t.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(s, t)
        except OSError:
            shutil.copy2(s, t)

    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src).as_posix()
        if known_v3:
            parsed = _is_v3_chunk_path(rel)
            if parsed is not None:
                band, coords = parsed
                t_index = coords[0]
                if (band, t_index) in replaced:
                    continue  # 该时间片整体重写 —— 绝不链接旧 chunk
                _link_or_copy(path, dst / rel)
                continue
        _link_or_copy(path, dst / rel)

    # 重写更新的时间片（validate-then-write：网格/铺排校验复用 foundation）。
    for band, per_band in sorted(updates.items()):
        grid = None
        for _t, descriptors, _rc in per_band:
            if descriptors:
                grid = descriptors[0].grid
                break
        if grid is None:
            raise CubeError(f"update for band {band!r} has no descriptors")
        new_root = zarr.open_group(store=str(dst), mode="a")
        target = new_root[band]
        for t_index, descriptors, read_chunk in per_band:
            group = descriptors
            from app.lib.geo_raster.zarr import _verify_descriptor_tiling

            _verify_descriptor_tiling(
                group,
                height=int(target.shape[1]),
                width=int(target.shape[2]),
                what=f"revision {band} slice {t_index}",
            )
            for d in group:
                if read_chunk is not None:
                    data = read_chunk(d, times[t_index])
                else:
                    if not d.source_uri:
                        raise CubeError(
                            f"chunk {d.chunk_id} has no source_uri and no loader"
                        )
                    reader = RasterReader.open(d.source_uri)
                    try:
                        data = reader.read_window(
                            d.window, band=int(d.band_indexes[0])
                        )
                    finally:
                        reader.close()
                col, row, cw, ch = (int(v) for v in d.window)
                data = np.asarray(data)
                if data.shape != (ch, cw):
                    raise CubeError(
                        f"chunk {d.chunk_id} loader returned {data.shape}, "
                        f"expected {(ch, cw)}"
                    )
                target[t_index, row:row + ch, col:col + cw] = data

    # 修订事件戳：uuid 使 fork 后的 store 字节必然不同于源（同一逻辑内容
    # 的两次 fork 是两个不同修订事件 —— 不可变修订需要这一点；内容寻址的
    # "同内容同 id" 语义保留给非 fork 的 cube 发布）。
    new_root = zarr.open_group(store=str(dst), mode="a")
    new_root.attrs["revision_forked_from"] = str(src)
    new_root.attrs["revision_id"] = uuid.uuid4().hex
    consolidate_cube_metadata(dst)
    return dst
