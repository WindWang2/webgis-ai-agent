"""Remote sensing cube — Spatial Lakehouse V7 (ADR-0119, Scope B).

光学/SAR/掩膜多源 **对齐 labeled cube** 的会话生产路径（通用能力，
无项目硬编码 —— 源角色由调用方声明）：

- ``sources``: ``[{time, source, role, band|polarization}]``，role ∈
  ``optical``（→ reflectance(time,band,y,x)）/ ``sar``（→ sigma0(
  time,polarization,y,x)）/ ``cloud_mask``、``quality_mask``（→
  (time,y,x) uint8）；
- **网格纪律**：全部源过网格恒等校验（header 投影逐键相等），不一致
  typed 拒绝 —— **绝不静默重采样**（V6 红线；对齐是上游职责）；
- **完整时间轴**：每个变量覆盖全部时间步（部分 cube = 诚实性问题，
  typed 拒绝）；
- **有界**：源数 ≤512、总单元 ≤``RS_MAX_CELLS``（组装期内存上界）；
- 单一事实源边界：源解析/指纹复用 ``cube_service`` 通道；写入走
  ``write_labeled_cube`` 唯一写入器；发布/台账与 V6 cube 同通道
  （disk-cursor ref + DataObject 身份 + labeled 投影入 manifest）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: 源角色 → 变量语义（通用，非项目特化）。
RS_ROLES = ("optical", "sar", "cloud_mask", "quality_mask")

#: 有界上限。
RS_MAX_SOURCES = 512
#: 组装期总单元预算（全部变量合计；float32 下 64M cells ≈ 256MiB）。
RS_MAX_CELLS = 64_000_000

_RS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")

#: 变量维度布局。
_RS_VAR_DIMS = {
    "reflectance": ("time", "band", "y", "x"),
    "sigma0": ("time", "polarization", "y", "x"),
    "cloud_mask": ("time", "y", "x"),
    "quality_mask": ("time", "y", "x"),
}


class RSCubeError(ValueError):
    """遥感 cube 生产失败（typed；真实失败，绝不返回死 ref）。"""

    code = "RS_CUBE_BUILD_FAILED"

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code

    def to_dict(self) -> dict:
        return {"success": False, "code": self.code, "message": self.message}


def _validate_source_entry(entry: Mapping[str, Any], index: int) -> Dict[str, Any]:
    """单源条目契约校验（边界即拒绝；错误消息带序号便于定位）。"""
    where = f"sources[{index}]"
    time_label = str(entry.get("time") or "").strip()
    if not time_label:
        raise RSCubeError(f"{where}: 'time' is required")
    source = str(entry.get("source") or "").strip()
    if not source:
        raise RSCubeError(f"{where}: 'source' is required")
    role = str(entry.get("role") or "").strip()
    if role not in RS_ROLES:
        raise RSCubeError(f"{where}: role {role!r} not in {RS_ROLES}")
    out: Dict[str, Any] = {"time": time_label, "source": source, "role": role}
    band_index = entry.get("band_index")
    if band_index is not None:
        idx = int(band_index)
        if idx < 1:
            raise RSCubeError(f"{where}: band_index is 1-based (got {idx})")
        out["band_index"] = idx
    if role == "optical":
        band = str(entry.get("band") or "").strip()
        if not band:
            raise RSCubeError(f"{where}: optical sources need 'band'")
        out["band"] = band
    elif role == "sar":
        pol = str(entry.get("polarization") or "").strip().upper()
        if not pol:
            raise RSCubeError(f"{where}: sar sources need 'polarization'")
        out["polarization"] = pol
    return out


def _geometry_identity(grid: Any) -> Dict[str, Any]:
    """RS 对齐的网格恒等 = 纯几何（width/height/crs/transform）。

    V6 ``_grid_identity_dict_local`` 含 dtype —— 那是单变量 cube 的契约；
    RS cube 的 mask（uint8）与影像（float32）合法共存，dtype 是逐变量
    属性，不参与跨源对齐判定。
    """
    from app.lib.geo_raster.chunk import _grid_identity_dict

    raw = _grid_identity_dict(grid)
    return {k: raw[k] for k in ("width", "height", "crs", "transform")}


def _variable_of(entry: Mapping[str, Any]) -> str:
    role = str(entry["role"])
    if role == "optical":
        return "reflectance"
    if role == "sar":
        return "sigma0"
    return role


def _label_of(entry: Mapping[str, Any]) -> str:
    """变量内标签（optical=band / sar=polarization / mask=空）。"""
    return str(entry.get("band") or entry.get("polarization") or "")


async def build_rs_cube(
    session_id: str,
    *,
    sources: Sequence[Mapping[str, Any]],
    title: str,
) -> Dict[str, Any]:
    """多源时间片 → 对齐 labeled cube（``ref:cube/<id>`` + DataObject）。

    返回 ref/路径/投影/发布与血缘证据；任一环节真实失败 = typed 错误。
    """
    from app.services.lakehouse.cube_service import (
        _resolve_time_source,
        _raster_fingerprint,
    )

    if not isinstance(session_id, str) or not _RS_ID_RE.match(session_id):
        raise RSCubeError(f"invalid session id: {session_id!r}")
    if not sources or len(sources) > RS_MAX_SOURCES:
        raise RSCubeError(
            f"sources must be 1..{RS_MAX_SOURCES} entries, got {len(sources)}"
        )
    entries = [
        _validate_source_entry(entry, i) for i, entry in enumerate(sources)
    ]

    # 解析 + 逐源 header 校验（网格恒等在读取前 —— fail-fast）。
    from app.lib.geo_raster.reader import RasterReader

    resolved: List[Dict[str, Any]] = []
    ref_identity: Optional[Dict[str, Any]] = None
    ref_grid: Any = None
    fingerprints: Dict[str, str] = {}
    for i, entry in enumerate(entries):
        path = await asyncio.to_thread(
            _resolve_time_source, session_id, entry["source"]
        )

        def _open_and_identity(p=path) -> Tuple[Any, Dict[str, Any]]:
            reader = RasterReader.open(str(p))
            try:
                # 网格消费唯一口径 = metadata.grid_profile（V3 恒等投影，
                # 绝不重新推导 —— reader.py 契约）。
                grid = reader.metadata().grid_profile
                if grid is None:
                    raise RSCubeError(
                        f"source {str(p)[:64]!r} metadata lacks grid_profile"
                    )
                return grid, _geometry_identity(grid)
            finally:
                reader.close()

        grid, identity = await asyncio.to_thread(_open_and_identity)
        if ref_identity is None:
            ref_identity, ref_grid = identity, grid
        elif identity != ref_identity:
            raise RSCubeError(
                f"sources[{i}] grid differs from the cube reference grid — "
                "align sources first (no silent resample/reproject)",
                code="RS_CUBE_GRID_MISMATCH",
            )
        resolved.append({**entry, "path": path, "grid": grid})
        fingerprints[str(path)] = await asyncio.to_thread(_raster_fingerprint, path)

    # 轴标签（时间 = 给定顺序的身份；band/polarization 字典序稳定）。
    times: List[str] = []
    for e in resolved:
        if e["time"] not in times:
            times.append(e["time"])
    time_index = {t: i for i, t in enumerate(times)}
    bands = sorted({e["band"] for e in resolved if e["role"] == "optical"})
    pols = sorted(
        {e["polarization"] for e in resolved if e["role"] == "sar"}
    )

    # 唯一性 + 完整时间轴覆盖（先于任何读取/分配 —— fail-fast 纪律）。
    seen: set = set()
    for e in resolved:
        key = (_variable_of(e), e["time"], _label_of(e))
        if key in seen:
            raise RSCubeError(
                f"duplicate source for ({key[0]}, {key[1]}, {key[2]}) — "
                "each (time, variable, label) must appear exactly once"
            )
        seen.add(key)
    for var in {v for v, _d, _l in seen}:
        var_times = {t for v, t, _l in seen if v == var}
        missing = [t for t in times if t not in var_times]
        if missing:
            raise RSCubeError(
                f"variable {var} is missing time steps {missing[:8]} — "
                "every time step must be present (no silent gaps)",
                code="RS_CUBE_INCOMPLETE",
            )

    height, width = int(ref_grid.height), int(ref_grid.width)
    label_index = {
        "reflectance": {b: i for i, b in enumerate(bands)},
        "sigma0": {p: i for i, p in enumerate(pols)},
    }
    filled: Dict[str, np.ndarray] = {}
    var_dtypes: Dict[str, str] = {}
    total_cells = 0
    for e in resolved:
        var = _variable_of(e)

        def _read(entry=e) -> np.ndarray:
            reader = RasterReader.open(str(entry["path"]))
            try:
                # 多波段源必须显式声明 band_index（评审 R1-12：静默读
                # band 1 = 任意标签挂错数据）。
                count = int(getattr(reader.metadata(), "count", 1) or 1)
                band_index = entry.get("band_index")
                if band_index is None and count > 1:
                    raise RSCubeError(
                        f"source {str(entry['source'])[:64]!r} has "
                        f"{count} bands — declare band_index explicitly "
                        "(no silent band-1 ingestion)"
                    )
                chosen = int(band_index or 1)
                if chosen > count:
                    raise RSCubeError(
                        f"band_index {chosen} out of range (source has "
                        f"{count} bands)"
                    )
                return np.asarray(reader.read_window(
                    (0, 0, int(entry["grid"].width), int(entry["grid"].height)),
                    band=chosen,
                ))
            finally:
                reader.close()

        data = await asyncio.to_thread(_read)
        if data.shape != (height, width):
            raise RSCubeError(
                f"source {str(e['source'])[:64]!r} read {data.shape}, "
                f"expected {(height, width)}"
            )
        arr = filled.get(var)
        if arr is None:
            dims = _RS_VAR_DIMS[var]
            shape = tuple(
                len(times)
                if d == "time"
                else len(bands)
                if d == "band"
                else len(pols)
                if d == "polarization"
                else height
                if d == "y"
                else width
                for d in dims
            )
            cells = 1
            for v in shape:
                cells *= v
            total_cells += cells
            if total_cells > RS_MAX_CELLS:
                raise RSCubeError(
                    f"cube assembly would hold {total_cells} cells, exceeding "
                    f"the bounded budget {RS_MAX_CELLS}",
                    code="RS_CUBE_TOO_LARGE",
                )
            # 首源 dtype 定变量 dtype；后续源混 dtype 在下方校验拒绝。
            arr = np.zeros(shape, dtype=data.dtype)
            filled[var] = arr
            var_dtypes[var] = str(data.dtype)
        elif str(data.dtype) != var_dtypes[var]:
            raise RSCubeError(
                f"source {str(e['source'])[:64]!r} dtype {data.dtype} != "
                f"variable {var} dtype {var_dtypes[var]}"
            )
        t = time_index[e["time"]]
        if var in ("reflectance", "sigma0"):
            filled[var][t, label_index[var][_label_of(e)], :, :] = data
        else:
            filled[var][t, :, :] = data

    variables_spec = {name: (_RS_VAR_DIMS[name], arr) for name, arr in filled.items()}
    transform = [float(v) for v in ref_grid.transform]
    from app.services.lakehouse.cube_schema import coords_from_transform_list

    grid_vals = coords_from_transform_list(transform, height, width)

    cube_id = secrets.token_hex(8)
    from app.services.artifact_registry import cube_store_path

    store_dir = cube_store_path(session_id, f"ref:cube/{cube_id}")
    if store_dir is None:
        raise RSCubeError("unresolvable cube id")

    from app.services.lakehouse.cube_store import write_labeled_cube

    coords: Dict[str, Any] = {"time": times, "y": grid_vals["y"], "x": grid_vals["x"]}
    if bands:
        coords["band"] = bands
    if pols:
        coords["polarization"] = pols
    try:
        projection = await asyncio.to_thread(
            write_labeled_cube,
            variables_spec,
            coords,
            store_dir,
            crs=str(ref_grid.crs),
            transform=transform,
            nodata=float(ref_grid.nodata) if ref_grid.nodata is not None else None,
            chunks={"time": 1, "band": 1, "polarization": 1,
                    "y": min(height, 512), "x": min(width, 512)},
        )
    except Exception as e:
        raise RSCubeError(f"rs cube write failed: {e}") from e

    input_fingerprint = await asyncio.to_thread(
        _rs_input_fingerprint, fingerprints, times,
    )
    from app.services.lakehouse.cube_store import publish_cube

    durable: Dict[str, Any] = {"published": False, "reason": "publish_failed"}
    try:
        publication = await asyncio.to_thread(
            publish_cube,
            store_dir,
            session_id=session_id,
            payload_extra={
                "title": title,
                "labeled": projection,
                "times": times,
                "input_fingerprints": fingerprints,
                "roles": sorted({e["role"] for e in resolved}),
            },
            producer={"capability": "lakehouse.rs_cube_build",
                      "tool": "build_rs_cube"},
            source_refs=[f"fingerprint:{d}" for d in fingerprints.values()],
            input_fingerprint=input_fingerprint,
        )
        durable = publication
    except Exception as e:  # noqa: BLE001 — 诚实降级（ref 仍指真实 store）
        logger.warning("[rs_cube] publish failed for %s: %s", cube_id, e)

    try:
        from app.services.artifact_registry import register_artifact

        await register_artifact(
            session_id,
            artifact_id=f"ref:cube/{cube_id}",
            artifact_type="lakehouse_cube",
            producer_capability="lakehouse.rs_cube_build",
            producer_tool="build_rs_cube",
            descriptor={"feature_count": len(times)},
            metadata={
                "title": title,
                "times": times,
                "cube_id": cube_id,
                "labeled": True,
                "variables": sorted(filled),
                **({"data_object_id": durable.get("data_object_id")}
                   if durable.get("data_object_id") else {}),
                **({"content_location": durable.get("manifest")}
                   if durable.get("manifest") else {}),
                "storage": "disk-cursor",
            },
        )
    except Exception as e:  # noqa: BLE001 — 台账 best-effort
        logger.warning("[rs_cube] ledger registration skipped: %s", e)

    logger.info(
        "[rs_cube] '%s' -> ref:cube/%s (%d steps, vars=%s, durable=%s)",
        title, cube_id, len(times), sorted(filled), durable.get("durable", "none"),
    )
    return {
        "status": "success",
        "success": True,
        "ref": f"ref:cube/{cube_id}",
        "cube_id": cube_id,
        "path": str(store_dir),
        "title": title,
        "times": times,
        "variables": sorted(filled),
        "labeled_projection": projection,
        **durable,
    }


def _rs_input_fingerprint(
    fingerprints: Mapping[str, str], times: Sequence[str]
) -> str:
    """复用键：同输入（顺序敏感）+ 同时间轴 ⇒ 同 rs-cube 身份。"""
    from app.services.lakehouse.data_object import compute_object_reuse_fingerprint

    return compute_object_reuse_fingerprint(
        operation="lakehouse.rs_cube_build",
        operation_version="v7",
        input_fingerprints={
            str(i): fp for i, fp in enumerate(fingerprints.values())
        },
        normalized_args={"times": list(times)},
    )
