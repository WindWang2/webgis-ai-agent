"""xarray adapter — Spatial Lakehouse V7 (ADR-0119, Scope A).

labeled cube 与 xarray 的双向桥（probe-gated：xarray 缺席 = typed 诚实
降级，同 ``ZarrUnavailable`` 族；xarray 与 zarr 一样**不是** requirements
声明依赖 —— ADR-0096 可选依赖纪律不变）。

存储形态（``write`` 侧）遵循 zarr v3 协议：数组经元数据
``dimension_names`` 绑定维度 —— xarray ``open_zarr`` 的 v3 互认机制
（评审 R0-1；``_ARRAY_DIMENSIONS`` attr 仅作 v2 习惯工具的镜像）；
网格契约（crs/transform/nodata）仍在 group attrs（与 V6 foundation
同位，``open_zarr_array`` 的 chunk descriptor 往返不受影响）。

V6 兼容：V6 cube（per-band 数组 + attrs.bands/times，无
``_ARRAY_DIMENSIONS``）经 :func:`open_cube_to_xarray` 合成 Dataset
（零落盘投影）—— 旧修订持续可读，绝不要求重写。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

#: xarray zarr 约定的维度标记 attr。
_ARRAY_DIMENSIONS = "_ARRAY_DIMENSIONS"

#: 写入侧接受的时间坐标类型（datetime64 → ISO-8601 字符串存储）。
_CUBE_GROUP_ATTR_KEYS = ("crs", "transform", "nodata", "times", "bands")


class XarrayUnavailable(RuntimeError):
    """xarray 未安装时的 typed 降级（绝不假装成功）。"""

    code = "XARRAY_UNAVAILABLE"
    correction_hint = "pip install xarray"

    def __init__(self, message: str = "xarray is not installed in this environment"):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict:
        return {
            "success": False,
            "code": self.code,
            "message": self.message,
            "correction_hint": self.correction_hint,
        }


def xarray_available() -> bool:
    try:
        import xarray  # noqa: F401
    except Exception:  # noqa: BLE001 — any import failure = unavailable
        return False
    return True


def _require_xarray() -> Any:
    try:
        import xarray
    except Exception as e:  # noqa: BLE001
        raise XarrayUnavailable() from e
    return xarray


def _require_zarr() -> Any:
    from app.lib.geo_raster.zarr import _require_zarr

    return _require_zarr()


# ── 坐标投影：纯函数在 cube_schema（无环依赖）──────────────────────────


def _labels_to_iso(values: Any) -> List[str]:
    """时间/标签坐标 → 字符串（datetime64 → ISO-8601 UTC）。"""
    import pandas as _pd  # xarray 硬依赖 pandas —— 在场性随 xarray

    if getattr(values, "dtype", None) is not None and np.issubdtype(
        np.asarray(values).dtype, np.datetime64
    ):
        idx = _pd.DatetimeIndex(np.asarray(values))
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        return [t.isoformat().replace("+00:00", "Z") for t in idx]
    return [str(v) for v in np.asarray(values).tolist()]


# ── 写：Dataset → labeled store（写纪律单点 = cube_store）──────────────


def dataset_to_cube_store(
    ds: Any,
    out_store,
    *,
    chunks: Optional[Sequence[int]] = None,
    overwrite: bool = True,
) -> Dict[str, Any]:
    """xarray.Dataset → labeled zarr store（校验与写入单点在 cube_store）。

    返回 schema 投影。数据变量维度共享 (…, y, x) 尾部；坐标显式；CRS
    必须可校验；transform 缺席时从等距坐标推导（不等距 = typed 拒绝）。
    """
    _require_xarray()  # 在场性闸（写入委托 cube_store，本函数不直接用 xr）
    from app.services.lakehouse.cube_schema import (
        CubeSchemaError,
        check_crs,
        transform_from_coords,
    )

    data_vars: Dict[str, List[str]] = {}
    arrays: Dict[str, Any] = {}
    dims: List[str] = []
    shape: List[int] = []
    nodata: Optional[float] = None
    for name, da in ds.data_vars.items():
        data_vars[str(name)] = [str(d) for d in da.dims]
        arrays[str(name)] = np.asarray(da.values)
        if len(da.dims) > len(shape):
            dims = [str(d) for d in da.dims]
            shape = [int(v) for v in da.shape]
            nodata = (
                float(da.attrs["nodata"])
                if getattr(da.attrs, "get", None) and da.attrs.get("nodata") is not None
                else None
            )
        elif [str(d) for d in da.dims][-2:] != dims[-2:]:
            raise CubeSchemaError(
                f"variable {name!r} spatial dims "
                f"{[str(d) for d in da.dims][-2:]} != anchor {dims[-2:]}"
            )
        elif [int(v) for v in da.shape][-2:] != shape[-2:]:
            raise CubeSchemaError(
                f"variable {name!r} spatial shape {da.shape[-2:]} != "
                f"anchor {shape[-2:]}"
            )
    if shape is None:
        raise CubeSchemaError("dataset has no data variables")

    crs = str(ds.attrs.get("crs") or "")
    transform = ds.attrs.get("transform")
    if not crs:
        raise CubeSchemaError(
            "dataset attrs must carry 'crs' (labeled cubes are georeferenced)"
        )
    y_vals = ds.coords["y"].values if "y" in ds.coords else None
    x_vals = ds.coords["x"].values if "x" in ds.coords else None
    if y_vals is None or x_vals is None:
        raise CubeSchemaError("dataset must carry explicit y/x coordinates")
    if transform is None:
        derived = transform_from_coords(
            np.asarray(y_vals).tolist(), np.asarray(x_vals).tolist()
        )
        if derived is None:
            raise CubeSchemaError(
                "dataset attrs lack 'transform' and coordinates are not "
                "equally spaced — cannot anchor the grid (no guessing)"
            )
        transform = derived
    check_crs(crs)

    # 坐标取**全部变量维度并集**（评审 R1-19：多变量不同维度组合时
    # 锚变量维度不足以覆盖 cube 维度并集）。
    all_dims: List[str] = []
    for vd in data_vars.values():
        for d in vd:
            if d not in all_dims:
                all_dims.append(d)
    all_dims += [d for d in ("y", "x") if d not in all_dims]
    coords: Dict[str, Any] = {}
    for dim in all_dims:
        if dim not in ds.coords:
            raise CubeSchemaError(f"dim {dim!r} has no coordinate array")
        vals = ds.coords[dim].values
        coords[dim] = (
            _labels_to_iso(vals) if dim not in ("y", "x") else np.asarray(vals)
        )
    # chunks：序列（对齐锚变量维度）→ {dim: size} 映射（多变量维度并集
    # 下的无歧义形态）。
    chunks_by_dim = None
    if chunks is not None:
        if isinstance(chunks, Mapping):
            chunks_by_dim = dict(chunks)
        else:
            seq = [int(v) for v in chunks]
            if len(seq) != len(dims):
                raise CubeSchemaError(
                    "chunks sequence must align with the anchor variable "
                    f"dims {dims}, got rank {len(seq)}"
                )
            chunks_by_dim = dict(zip(dims, seq))
    from app.services.lakehouse.cube_store import write_labeled_cube

    return write_labeled_cube(
        {name: (data_vars[name], arrays[name]) for name in arrays},
        coords,
        out_store,
        crs=crs,
        transform=transform,
        nodata=nodata,
        chunks=chunks_by_dim,
        overwrite=overwrite,
    )


# ── 读：store → Dataset（V2 labeled 与 V6 per-band 同入口）─────────────


def open_cube_to_xarray(store) -> Any:
    """cube store → xarray.Dataset（V2 labeled 原生；V6 合成投影，零落盘）。

    返回 ``(ds, meta)``；meta 携带 crs/transform/nodata/字符串时间标签
    （xarray 侧 time 坐标已转 datetime64[ns, UTC] 当且仅当可解析）。
    """
    xr = _require_xarray()
    from app.services.lakehouse.cube_schema import coords_from_transform_list
    from app.services.lakehouse.cube_store import CubeError, open_cube

    root = open_cube(store)
    attrs = dict(root.attrs or {})
    if attrs.get("labeled"):
        ds = xr.open_zarr(str(Path(store)), consolidated=False)
        meta = {
            "crs": attrs.get("crs"),
            "transform": attrs.get("transform"),
            "nodata": attrs.get("nodata"),
            "schema_version": attrs.get("cube_schema_version"),
        }
        return ds, meta

    # V6 合成：per-band 数组 (time,y,x) + attrs。V6 的 georeferencing
    # 写在 band 数组 attrs（write_cube 契约），group attrs 仅 bands/times
    # —— 两处都看（root 优先，兼容 v2 工具写出的位置）。
    bands = [str(b) for b in (attrs.get("bands") or [])]
    if not bands:
        raise CubeError("cube declares no bands")
    times = [str(t) for t in (attrs.get("times") or [])]
    first_arr = root[bands[0]]
    first_shape = tuple(int(v) for v in first_arr.shape)  # 元数据级
    if len(first_shape) != 3:
        raise CubeError(
            f"V6 band array must be (time,y,x), got shape {first_shape}"
        )
    band_attrs = dict(root[bands[0]].attrs or {})
    crs = attrs.get("crs") or band_attrs.get("crs")
    transform = attrs.get("transform") or band_attrs.get("transform")
    if not crs or not transform:
        raise CubeError("V6 cube attrs lack georeferencing")
    grid = coords_from_transform_list(
        transform, int(first_shape[1]), int(first_shape[2])
    )
    data_vars: Dict[str, Any] = {}
    for band in bands:
        # zarr 数组经数组协议直传（惰性 —— 读取发生在 xarray 侧按需；
        # 评审 R2-10：绝不在此 np.asarray 全量物化）。
        data_vars[band] = xr.DataArray(
            root[band],
            dims=("time", "y", "x"),
            coords={"time": times, "y": grid["y"], "x": grid["x"]},
        )
    ds = xr.Dataset(data_vars)
    ds.attrs["crs"] = str(crs)
    ds.attrs["transform"] = [float(v) for v in transform]
    meta = {
        "crs": str(crs),
        "transform": [float(v) for v in transform],
        "nodata": attrs.get("nodata"),
        "schema_version": 1,
        "times": times,
    }
    return ds, meta


def labeled_projection_from_store(store) -> Dict[str, Any]:
    """store → labeled schema 投影（不打开 xarray —— 元数据级，供 manifest）。"""
    from app.services.lakehouse.cube_schema import (
        coords_from_transform_list,
        validate_labeled_schema,
    )
    from app.services.lakehouse.cube_store import CubeError, open_cube

    root = open_cube(store)
    attrs = dict(root.attrs or {})
    if not attrs.get("labeled"):
        # V6：合成 time/y/x 单变量投影（bands 以 band 轴标签表达）；
        # georeferencing 在 band 数组 attrs（write_cube 契约），root 兜底。
        bands = [str(b) for b in (attrs.get("bands") or [])]
        times = [str(t) for t in (attrs.get("times") or [])]
        if not bands:
            raise CubeError("cube declares no bands")
        first_shape = tuple(int(v) for v in root[bands[0]].shape)  # 元数据级
        if len(first_shape) != 3:
            raise CubeError(
                f"V6 band array must be (time,y,x), got {first_shape}"
            )
        band_attrs = dict(root[bands[0]].attrs or {})
        transform = attrs.get("transform") or band_attrs.get("transform")
        crs = attrs.get("crs") or band_attrs.get("crs")
        nodata = attrs.get("nodata", band_attrs.get("nodata"))
        if not transform or not crs:
            raise CubeError("V6 cube attrs lack georeferencing")
        grid = coords_from_transform_list(
            transform, int(first_shape[1]), int(first_shape[2])
        )
        return validate_labeled_schema(
            dims=["time", "band", "y", "x"],
            shape=(len(times), len(bands), int(first_shape[1]), int(first_shape[2])),
            coordinates={
                "time": times,
                "band": bands,
                "y": grid["y"],
                "x": grid["x"],
            },
            crs=str(crs),
            dtype=str(root[bands[0]].dtype),
            nodata=nodata,
        )
    dims = [str(d) for d in (attrs.get("dims") or [])]
    if not dims:
        raise CubeError("labeled cube attrs lack dims")
    from app.services.lakehouse.cube_store import _array_dims

    var_dims: Dict[str, List[str]] = {}
    coords: Dict[str, Any] = {}
    for name in root.array_keys():
        arr = root[name]
        adims = _array_dims(arr)
        if adims == [name]:
            coords[name] = np.asarray(arr)
        elif adims:
            var_dims[str(name)] = adims
    if not var_dims:
        raise CubeError("labeled cube declares no data variables")
    if any(d not in coords for d in dims):
        raise CubeError("labeled cube is missing coordinate arrays")
    # 每维度尺寸 = 坐标数组长度（写入器保证坐标长度 == 维度尺寸）。
    shape = [int(np.asarray(coords[d]).shape[0]) for d in dims]
    var_specs: Dict[str, Dict[str, Any]] = {}
    anchor_dtype = ""
    for name, adims in var_dims.items():
        arr = root[name]
        if [int(v) for v in arr.shape] != [
            shape[list(dims).index(d)] for d in adims
        ]:
            raise CubeError(
                f"variable {name!r} shape {arr.shape} inconsistent with "
                f"per-dim sizes {shape}"
            )
        var_specs[name] = {"dims": adims, "dtype": str(arr.dtype)}
        if not anchor_dtype:
            anchor_dtype = str(arr.dtype)
    return validate_labeled_schema(
        dims=dims,
        shape=shape,
        coordinates=coords,
        crs=str(attrs.get("crs") or ""),
        dtype=anchor_dtype,
        variables=var_specs,
        nodata=attrs.get("nodata"),
    )
