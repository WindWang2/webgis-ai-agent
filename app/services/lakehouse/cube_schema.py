"""N-D labeled cube schema — Spatial Lakehouse V7 (ADR-0119, Scope A).

V6 cube 是硬编码 ``dims=["time","y","x"]`` + per-band 多数组的隐式契约
（cube_store.py attrs）；V7 把**标签语义升级为一等契约**：维度白名单、
坐标数组、CRS、dtype/nodata、chunk 布局全部显式可验证。

单一事实源边界：本模块是 labeled cube 的**纯校验/投影层** —— 无 IO、
无 zarr 依赖、无全局状态。存储形态见 ``cube_store.write_labeled_cube``，
身份投影（manifest payload）由 :func:`labeled_projection` 给出。

契约（不可协商）：

- **维度白名单** ``{time, band, polarization, vertical, y, x}``；
  ``y``/``x`` 必须存在且恒为最后两轴（网格语义锚定）；
- **坐标数组** 一维、长度 == 该轴 shape；y 严格降序、x 严格升序
  （栅格网格约定）、其余维度严格唯一（time/band/polarization/vertical
  轴身份 = 唯一性，重复时间步 = 不同 cube，绝不静默去重）；
- **网格一致性**：两个 cube/源对齐 = y/x 坐标数组逐元素相等（容差 0）
  —— 不相等 typed 拒绝，**绝不静默重采样**（对齐是调用方职责，V6 红线）；
- **CRS**：rasterio 在场时经 ``CRS.from_user_input`` 真校验；缺席时
  降级为 ``EPSG:`` 前缀校验并如实披露 ``crs_checked: "regex"``；
- **有界**：维度数 ≤8、坐标长度超界/字节超界 typed 拒绝。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: labeled cube 契约版本（V6 cube 隐式 v1；V7 labeled = v2；
#: V8 = v3 —— model/scenario 维度 + 逐变量 nodata，ADR-0130）。
CUBE_SCHEMA_VERSION_V2 = 2
CUBE_SCHEMA_VERSION_V3 = 3

#: V2 维度白名单（V7 契约 —— 兼容基线：维度集 ⊆ 此集 ⇒ 投影恒为 v2，
#: 既有发布路径字节级不变）。
ALLOWED_DIMS_V2 = ("time", "band", "polarization", "vertical", "y", "x")

#: V8 新增标签维度（多模型 × 多情景产物 —— ADR-0130 §3）。
V3_DIMS = ("model", "scenario")

#: 维度白名单（超出 = 契约违例；新增维度 = 升契约版本）。
ALLOWED_DIMS = ALLOWED_DIMS_V2 + V3_DIMS

#: 网格轴（必须存在且为最后两轴）。
SPATIAL_DIMS = ("y", "x")

#: 有界上限。
MAX_DIMS = 8
MAX_COORD_VALUES = 1_000_000
#: 坐标数组可序列化字节上界（入 manifest payload 的投影同样受限）。
MAX_COORD_BYTES = 1 * 1024 * 1024

_EPSG_RE = re.compile(r"^EPSG:\d{1,6}\Z", re.IGNORECASE)


class CubeSchemaError(ValueError):
    """labeled cube 契约违例（typed；诚实拒绝，绝不猜测/静默对齐）。"""

    code = "LAKEHOUSE_CUBE_SCHEMA_INVALID"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict:
        return {"success": False, "code": self.code, "message": self.message}


def _as_1d(values: Any, dim: str) -> list:
    """坐标序列规范化（Sequence / numpy ndarray duck-type；str/dict 拒绝）。"""
    if isinstance(values, (str, bytes, Mapping)) or not hasattr(values, "__len__"):
        raise CubeSchemaError(
            f"coordinate {dim!r} must be a 1-D sequence, got {type(values).__name__}"
        )
    if getattr(values, "ndim", 1) != 1:
        raise CubeSchemaError(
            f"coordinate {dim!r} must be 1-D, got ndim={getattr(values, 'ndim', '?')}"
        )
    return list(values)


def _require_1d_numeric(values: Any, dim: str) -> List[float]:
    """坐标数组规范化：一维、数值可投影、长度有界。"""
    vals = _as_1d(values, dim)
    if len(vals) > MAX_COORD_VALUES:
        raise CubeSchemaError(
            f"coordinate {dim!r} has {len(vals)} values, exceeding the bounded "
            f"cap {MAX_COORD_VALUES}"
        )
    out: List[float] = []
    for v in vals:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            # numpy 数值标量（np.float32 等）接受 —— 有 .item() 投影。
            item = getattr(v, "item", None)
            if item is None:
                raise CubeSchemaError(
                    f"coordinate {dim!r} must be numeric, got {type(v).__name__}"
                )
            v = item()
        out.append(float(v))
    return out


def _require_unique(values: Any, dim: str) -> List[str]:
    """非空间维度坐标：字符串标签 + 唯一性（轴身份）。"""
    labels = [str(v) for v in _as_1d(values, dim)]
    if len(labels) > MAX_COORD_VALUES:
        raise CubeSchemaError(
            f"coordinate {dim!r} has {len(labels)} values, exceeding the bounded "
            f"cap {MAX_COORD_VALUES}"
        )
    seen: set = set()
    for lab in labels:
        if lab in seen:
            raise CubeSchemaError(
                f"coordinate {dim!r} has duplicate value {lab!r} — axis "
                "identity requires uniqueness (never silently deduplicated)"
            )
        seen.add(lab)
    return labels


def _strictly_decreasing(values: List[float], dim: str) -> List[float]:
    for a, b in zip(values, values[1:]):
        if not (a > b):
            raise CubeSchemaError(
                f"coordinate {dim!r} must be strictly decreasing "
                f"(north-up grid); got {a} -> {b}"
            )
    return values


def _strictly_increasing(values: List[float], dim: str) -> List[float]:
    for a, b in zip(values, values[1:]):
        if not (a < b):
            raise CubeSchemaError(
                f"coordinate {dim!r} must be strictly increasing "
                f"(west-east grid); got {a} -> {b}"
            )
    return values


def validate_grid_coords(
    y: Sequence[float], x: Sequence[float]
) -> Tuple[List[float], List[float]]:
    """网格坐标校验：y 严格降序、x 严格升序（栅格北在上/西在东约定）。"""
    yv = _require_1d_numeric(y, "y")
    xv = _require_1d_numeric(x, "x")
    if len(yv) < 1 or len(xv) < 1:
        raise CubeSchemaError("grid coordinates y/x must be non-empty")
    _strictly_decreasing(yv, "y")
    _strictly_increasing(xv, "x")
    return yv, xv


def check_crs(crs: str) -> Dict[str, str]:
    """CRS 校验（rasterio 真校验优先；缺席 = EPSG 正则降级，如实披露）。"""
    text = str(crs or "").strip()
    if not text:
        raise CubeSchemaError("crs is required for a georeferenced cube")
    try:
        from rasterio.crs import CRS

        CRS.from_user_input(text)
        return {"crs": text, "crs_checked": "rasterio"}
    except Exception:  # noqa: BLE001 — rasterio 缺席或拒绝 → 降级校验
        pass
    if _EPSG_RE.match(text):
        return {"crs": text.upper(), "crs_checked": "regex"}
    raise CubeSchemaError(f"invalid CRS: {text[:64]!r}")


def resolve_cube_schema_version(dims: Sequence[str]) -> int:
    """维度集 → 契约版本（⊆ v2 六维 = 2；含 model/scenario = 3）。

    版本按需升级：既有 v2 发布路径的投影（与 manifest 身份）字节级
    不变，只有真正使用新维度的 cube 升 v3 —— 一次性身份冲断仅限
    v3 采用者（ADR-0130 §3）。
    """
    dim_set = {str(d) for d in dims}
    if dim_set & set(V3_DIMS):
        return CUBE_SCHEMA_VERSION_V3
    return CUBE_SCHEMA_VERSION_V2


def validate_labeled_schema(
    *,
    dims: Sequence[str],
    shape: Sequence[int],
    coordinates: Mapping[str, Sequence[Any]],
    crs: str,
    dtype: str,
    variables: Optional[Mapping[str, Any]] = None,
    nodata: Optional[float] = None,
    chunks: Optional[Any] = None,
    nodata_per_variable: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """labeled cube 契约总校验（纯函数；违规 → :class:`CubeSchemaError`）。

    ``dims`` = 全部变量的**有序维度并集**（非空间轴按首次出现序在前，
    y/x 恒在最后）；``shape`` 与 ``dims`` 对齐（每维度一个尺寸 —— 同一
    维度跨变量尺寸必须一致，由写入侧保证）；``variables[name]`` 是该
    变量的维度元组（必须以 (y, x) 结尾的 dims 子序）。返回规范化后的
    schema 投影（同一输入恒得同一 dict —— 可入 manifest payload 参与
    身份）。
    """
    dims = [str(d) for d in dims]
    if not dims:
        raise CubeSchemaError("dims must be non-empty")
    if len(dims) > MAX_DIMS:
        raise CubeSchemaError(
            f"cube declares {len(dims)} dims, exceeding the bounded cap {MAX_DIMS}"
        )
    unknown = [d for d in dims if d not in ALLOWED_DIMS]
    if unknown:
        raise CubeSchemaError(
            f"unknown dims {sorted(unknown)} — allowed: {list(ALLOWED_DIMS)}"
        )
    if len(set(dims)) != len(dims):
        raise CubeSchemaError(f"duplicate dims in {dims}")
    for axis in SPATIAL_DIMS:
        if axis not in dims:
            raise CubeSchemaError(f"spatial dim {axis!r} is required")
    if tuple(dims[-2:]) != SPATIAL_DIMS:
        raise CubeSchemaError(
            f"spatial dims y/x must be the last two axes, got tail {dims[-2:]}"
        )

    shape_t = tuple(int(v) for v in shape)
    if len(shape_t) != len(dims):
        raise CubeSchemaError(
            f"shape {shape_t} rank != dims {dims} rank"
        )
    if any(v < 0 for v in shape_t):
        raise CubeSchemaError(f"negative dim in shape {shape_t}")

    if not str(dtype):
        raise CubeSchemaError("dtype is required")
    if nodata is not None:
        nodata = float(nodata)

    # 坐标数组：空间轴数值 + 单调；标签轴唯一。缺坐标 = 拒绝（labeled
    # 契约要求坐标显式 —— 无坐标即 V6 隐式 cube，不走本入口）。
    coords: Dict[str, Any] = {}
    size_of = dict(zip(dims, shape_t))
    for i, dim in enumerate(dims):
        if dim not in coordinates:
            raise CubeSchemaError(
                f"dim {dim!r} has no coordinate array — labeled cubes "
                "require explicit coordinates"
            )
        vals = coordinates[dim]
        if dim == "y":
            yv = _strictly_decreasing(_require_1d_numeric(vals, "y"), "y")
            if len(yv) != size_of[dim]:
                raise CubeSchemaError(
                    f"coordinate 'y' length {len(yv)} != dim size {size_of[dim]}"
                )
            coords[dim] = yv
        elif dim == "x":
            xv = _strictly_increasing(_require_1d_numeric(vals, "x"), "x")
            if len(xv) != size_of[dim]:
                raise CubeSchemaError(
                    f"coordinate 'x' length {len(xv)} != dim size {size_of[dim]}"
                )
            coords[dim] = xv
        else:
            labels = _require_unique(vals, dim)
            if len(labels) != size_of[dim]:
                raise CubeSchemaError(
                    f"coordinate {dim!r} length {len(labels)} != "
                    f"dim size {size_of[dim]}"
                )
            coords[dim] = labels

    # 变量→维度映射：每个变量的维度必须是 dims 的子序且以 (y, x) 结尾。
    # 变量值兼容两种形态：[dims...]（dtype 继承顶层）或
    # {"dims": [...], "dtype": "..."}（逐变量 dtype —— 如 uint8 mask 与
    # float32 reflectance 同 cube）。
    vars_out: Dict[str, Dict[str, Any]] = {}
    for name, var_spec in (variables or {"data": list(dims)}).items():
        if not str(name):
            raise CubeSchemaError("variable names must be non-empty")
        if isinstance(var_spec, Mapping):
            var_dims_raw = var_spec.get("dims") or []
            var_dtype = str(var_spec.get("dtype") or dtype)
        else:
            var_dims_raw = var_spec
            var_dtype = dtype
        vd = [str(d) for d in var_dims_raw]
        if not vd or len(vd) != len(set(vd)):
            raise CubeSchemaError(f"variable {name!r}: dims {vd} empty/duplicated")
        if tuple(vd[-2:]) != SPATIAL_DIMS:
            raise CubeSchemaError(
                f"variable {name!r}: dims {vd} must end with (y, x)"
            )
        if not var_dtype:
            raise CubeSchemaError(f"variable {name!r}: dtype required")
        for d in vd:
            if d not in dims:
                raise CubeSchemaError(
                    f"variable {name!r} references dim {d!r} not in cube dims"
                )
        vars_out[str(name)] = {"dims": vd, "dtype": var_dtype}

    # 逐变量 nodata（V8 v3 契约）：变量必须存在、值可投影 float；
    # 键序规范化（sorted）—— 投影确定性（manifest 身份前提）。
    # 携带 nodata_per_variable 的投影升 v3（版本字段判别投影形状 ——
    # 旧 reader 不会遇到带该键的 v2 投影）。
    nodata_per_var_out: Optional[Dict[str, float]] = None
    if nodata_per_variable is not None:
        if not isinstance(nodata_per_variable, Mapping):
            raise CubeSchemaError("nodata_per_variable must be a mapping")
        unknown_vars = [
            str(k) for k in nodata_per_variable if str(k) not in vars_out
        ]
        if unknown_vars:
            raise CubeSchemaError(
                f"nodata_per_variable references unknown variables "
                f"{sorted(unknown_vars)}"
            )
        try:
            nodata_per_var_out = {
                str(k): float(v)
                for k, v in sorted(nodata_per_variable.items())
            }
        except (TypeError, ValueError) as exc:
            raise CubeSchemaError(
                f"nodata_per_variable values must be numeric: {exc}"
            ) from exc

    crs_info = check_crs(crs)

    # chunk 布局（可选）：Mapping{dim: size}（每维度必须有值）或与 dims
    # 并集对齐的正数序列。
    chunks_out: Optional[List[int]] = None
    if chunks is not None:
        if isinstance(chunks, Mapping):
            missing = [d for d in dims if d not in chunks]
            if missing:
                raise CubeSchemaError(
                    f"chunks mapping lacks dims {missing}"
                )
            chunks_out = [int(chunks[d]) for d in dims]
        else:
            chunks_out = [int(v) for v in chunks]
            if len(chunks_out) != len(dims):
                raise CubeSchemaError(
                    f"chunks rank {len(chunks_out)} != dims rank {len(dims)}"
                )
        if any(v < 1 for v in chunks_out):
            raise CubeSchemaError(f"chunks must be >= 1, got {chunks_out}")

    projection: Dict[str, Any] = {
        "labeled": True,
        "cube_schema_version": (
            CUBE_SCHEMA_VERSION_V3
            if nodata_per_var_out is not None
            else resolve_cube_schema_version(dims)
        ),
        "dims": dims,
        "shape": list(shape_t),
        "variables": vars_out,
        "dtype": str(dtype),
        "nodata": nodata,
        "chunks": chunks_out,
        **crs_info,
        # 坐标只投影网格轴 + 短标签轴（长轴长度入投影即可 —— 投影受
        # manifest 尺寸闸约束，完整坐标真相在 store 的坐标数组里）。
        "coords_summary": {
            d: (
                {"kind": "grid", "n": len(coords[d]),
                 "start": coords[d][0], "end": coords[d][-1]}
                if d in ("y", "x")
                else {"kind": "labels", "n": len(coords[d]),
                      "first": coords[d][0], "last": coords[d][-1]}
            )
            for d in dims
        },
    }
    if nodata_per_var_out is not None:
        projection["nodata_per_variable"] = nodata_per_var_out
    return projection


def grid_coords_equal(
    a_y: Sequence[float], a_x: Sequence[float],
    b_y: Sequence[float], b_x: Sequence[float],
) -> bool:
    """网格恒等判定（逐元素相等，容差 0 —— 对齐与重采样的分界线）。"""
    if len(a_y) != len(b_y) or len(a_x) != len(b_x):
        return False
    return (
        list(float(v) for v in a_y) == list(float(v) for v in b_y)
        and list(float(v) for v in a_x) == list(float(v) for v in b_x)
    )


# ── transform ↔ 中心坐标（纯函数；无 numpy/rasterio 依赖）──────────────


def coords_from_transform_list(
    transform: Sequence[float], height: int, width: int
) -> Dict[str, List[float]]:
    """affine transform → y/x 像元中心坐标列表（北-up 约定，绝对地理
    坐标：仿射平移 c/f 必须参与 —— 评审 R1-3，丢失平移 = 所有 cube
    相对原点、catalog/STAC 地理位置全错、跨原点对齐失明）。"""
    a, _b, c, _d, e, f = (float(v) for v in transform[:6])
    xs = [c + (j + 0.5) * a for j in range(int(width))]
    ys = [f + (i + 0.5) * e for i in range(int(height))]
    return {"x": xs, "y": ys}


def transform_from_coords(
    y: Sequence[float], x: Sequence[float]
) -> Optional[List[float]]:
    """中心坐标 → transform（等间距前提；不等距 = None，诚实缺席）。"""
    if len(y) < 1 or len(x) < 1:
        return None
    if len(y) > 1:
        deltas = {round(float(b) - float(a), 12) for a, b in zip(y, y[1:])}
        if len(deltas) != 1:
            return None
        e = next(iter(deltas))
        if e >= 0:
            return None
    else:
        e = -1.0
    if len(x) > 1:
        deltas = {round(float(b) - float(a), 12) for a, b in zip(x, x[1:])}
        if len(deltas) != 1:
            return None
        a = next(iter(deltas))
        if a <= 0:
            return None
    else:
        a = 1.0
    # 中心坐标 → 左上角：c = x0 - a/2, f = y0 - e/2。
    return [a, 0.0, float(x[0]) - a / 2.0, 0.0, e, float(y[0]) - e / 2.0]


def require_aligned(
    reference: Mapping[str, Any], candidate: Mapping[str, Any], *, what: str = "source"
) -> None:
    """两份 labeled projection 的网格恒等校验（不一致 typed 拒绝）。"""
    for key in ("crs", "dtype"):
        rv, cv = reference.get(key), candidate.get(key)
        if str(rv or "") != str(cv or ""):
            raise CubeSchemaError(
                f"{what}: {key} {cv!r} != cube reference {rv!r} — "
                "align sources first (no silent resample/reproject)"
            )
    ra, ca = reference.get("coords_summary", {}).get("y"), candidate.get(
        "coords_summary", {}).get("y")
    rx, cx = reference.get("coords_summary", {}).get("x"), candidate.get(
        "coords_summary", {}).get("x")
    for axis, r, c in (("y", ra, ca), ("x", rx, cx)):
        if not r or not c:
            raise CubeSchemaError(f"{what}: missing {axis} coordinate summary")
        if (
            r.get("n") != c.get("n")
            or float(r.get("start", 0)) != float(c.get("start", 0))
            or float(r.get("end", 0)) != float(c.get("end", 0))
        ):
            raise CubeSchemaError(
                f"{what}: {axis} grid differs from cube reference "
                "(count/extent) — align sources first (no silent resample)"
            )
