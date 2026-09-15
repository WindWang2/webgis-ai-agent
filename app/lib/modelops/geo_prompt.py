"""GeoPrompt artifact 契约（Platform 11 / ADR-0198 §A；GOAL WP-A）。

分层（与 :mod:`app.lib.modelops.promptable` 的关系）::

    GeoPromptArtifact（本模块） = 授权/互换契约：版本、CRS 身份、几何、
        mask sidecar 引用、reference-layer 引用、时间语义、目标模型/波段
        绑定、provenance、内容寻址 artifact_id。
    PromptSpec（promptable.py） = 运行时 prompt 契约（窗口像素坐标 +
        prior mask 数组），provider 能力门照旧生效。

``compile_prompt`` 把 artifact 编译为 :class:`CompiledPrompt`（运行时
PromptSpec + 转换审计）。编译是**确定性纯函数 + 注入式 IO**（mask 加载与
reference-layer 解析经 callable 注入，本模块不触磁盘/网络）——失败全部
typed（:class:`PromptArtifactError` / :class:`PlanningError`），绝不静默
丢弃几何。

坐标语义（fail-closed）：
- ``artifact.crs is None`` ⇒ 几何为**目标栅格像素坐标**（左上原点）；
- ``artifact.crs`` 声明 ⇒ 几何为该 CRS 的地图坐标，编译期经仿射逆变换
  落到像素；变换矩阵退化（行列式为 0）或往返误差超容差 ⇒ typed 拒绝；
- mask sidecar / reference-layer 产物假定与目标栅格同网格（像素对齐），
  由编译审计记录来源，不对齐责任在调用方（文档化契约）。

栅格化语义（确定性，known-answer 可测）：
- polygon：像元中心包含（rasterio 默认 ``all_touched=False``）；
- polyline：触及像元（``all_touched=True``，名义 1px 宽）；
- reference-layer：``nonzero``（非零/非 nodata 即真）或 ``threshold``
  （>= 阈值），可选 ``invert``。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.lib.modelops.errors import PlanningError, PromptArtifactError
from app.lib.modelops.promptable import PromptSpec, np_isfinite

#: artifact schema 版本（破坏性变更必须 bump 并提供迁移）。
GEO_PROMPT_SCHEMA_VERSION = 1

#: 地图 CRS ↔ 像素往返容差（像素单位）。仿射正逆是精确数学，此容差只吸收
#: 浮点舍入；任何超容差（退化/病态变换）= typed 拒绝，不产出漂移几何。
PROMPT_ROUNDTRIP_TOL_PX = 1e-6

#: 编译期物化的先验掩膜像素上限（与 engine 掩膜画布同界：超过 = typed
#: 拒绝，绝不无界分配）。
MAX_COMPILED_MASK_PIXELS = 256 * 1024 * 1024

#: 每类几何的数量上限（与 PromptSpec.MAX_PROMPTS_PER_KIND 同口径）。
MAX_GEOMETRY_PER_KIND = 64

#: polyline 最少顶点数 / polygon 环最少顶点数（闭合前）。
MIN_POLYLINE_VERTICES = 2
MIN_POLYGON_RING_VERTICES = 3

REF_STRATEGY_NONZERO = "nonzero"
REF_STRATEGY_THRESHOLD = "threshold"
REFERENCE_STRATEGIES = (REF_STRATEGY_NONZERO, REF_STRATEGY_THRESHOLD)

#: mask/reference 解析器协议：返回 (height, width) 形状的 bool 数组。
#: lib 层不 import numpy —— 类型以 duck-typing 约束（.shape/.astype/.dtype）。
MaskLoader = Callable[[str, int], Any]
ReferenceReader = Callable[[str, int], Any]

_Point = Tuple[float, float]


def _canonical_json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _validate_xy(x: float, y: float, what: str) -> None:
    if not (np_isfinite(float(x)) and np_isfinite(float(y))):
        raise PromptArtifactError(f"{what} contains non-finite coordinate ({x!r}, {y!r})")


def _validate_iso(value: str, what: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PromptArtifactError(
            f"{what} must be ISO-8601 (got {value!r})"
        ) from exc


@dataclass(frozen=True)
class GeoPromptTime:
    """prompt 的时间语义（授权/选择层；运行时时间栈走 TemporalStackSpec）。

    - ``acquisition``：prompt 描述目标的获取时刻；
    - ``valid_from``/``valid_until``：prompt 语义有效窗口（闭区间）。
    至少一项必填；字段只做 ISO-8601 词法校验（时区/历法语义由消费方裁决）。
    """

    acquisition: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None

    def __post_init__(self) -> None:
        if not (self.acquisition or self.valid_from or self.valid_until):
            raise PromptArtifactError(
                "GeoPromptTime requires at least one of acquisition/valid_from/valid_until"
            )
        if self.acquisition:
            _validate_iso(self.acquisition, "time.acquisition")
        if self.valid_from:
            _validate_iso(self.valid_from, "time.valid_from")
        if self.valid_until:
            _validate_iso(self.valid_until, "time.valid_until")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "acquisition": self.acquisition,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
        }


@dataclass(frozen=True)
class GeoPromptTarget:
    """prompt 与目标模型/波段的绑定（advisory 身份；编译期校验冲突）。

    ``band_names`` 是**语义命名**（如 ["B04","B03","B02"]）：声明 prompt
    制作时的波段语义，供引擎核对 descriptor 波段语义的一致性；不做重排
    （重排是 preprocess plan 的职责，避免第二真值）。
    """

    model_id: Optional[str] = None
    model_version: Optional[str] = None
    band_names: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (self.model_id or self.model_version or self.band_names):
            raise PromptArtifactError(
                "GeoPromptTarget requires at least one of model_id/model_version/band_names"
            )
        if self.model_id is not None and (
            not self.model_id.strip() or any(ch in self.model_id for ch in "\r\n\t")
        ):
            raise PromptArtifactError(f"invalid model_id {self.model_id!r}")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "band_names": list(self.band_names),
        }


@dataclass(frozen=True)
class MaskReference:
    """mask sidecar 引用（内容寻址；加载时 digest 校验 fail-closed）。"""

    path: str
    sha256: str
    band: int = 1

    def __post_init__(self) -> None:
        if not self.path or any(ch in self.path for ch in "\r\n\t"):
            raise PromptArtifactError(f"invalid mask path {self.path!r}")
        if len(self.sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in self.sha256.lower()
        ):
            raise PromptArtifactError(
                f"mask sha256 must be a 64-hex digest (got {self.sha256!r})"
            )
        if int(self.band) < 1:
            raise PromptArtifactError(f"mask band must be >= 1 (got {self.band})")

    def as_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "band": int(self.band)}


@dataclass(frozen=True)
class ReferenceLayer:
    """参考图层 prompt：既有图层/栅格 → 布尔先验（战略显式、确定性）。"""

    uri: str
    band: int = 1
    strategy: str = REF_STRATEGY_NONZERO
    threshold: Optional[float] = None
    invert: bool = False

    def __post_init__(self) -> None:
        if not self.uri or any(ch in self.uri for ch in "\r\n\t"):
            raise PromptArtifactError(f"invalid reference uri {self.uri!r}")
        if self.strategy not in REFERENCE_STRATEGIES:
            raise PromptArtifactError(
                f"reference strategy must be one of {list(REFERENCE_STRATEGIES)} "
                f"(got {self.strategy!r})"
            )
        if self.strategy == REF_STRATEGY_THRESHOLD:
            if self.threshold is None or not np_isfinite(float(self.threshold)):
                raise PromptArtifactError(
                    "threshold strategy requires a finite threshold"
                )
        if int(self.band) < 1:
            raise PromptArtifactError(f"reference band must be >= 1 (got {self.band})")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "uri": self.uri,
            "band": int(self.band),
            "strategy": self.strategy,
            "threshold": self.threshold,
            "invert": bool(self.invert),
        }


@dataclass(frozen=True)
class GeoPromptArtifact:
    """版本化地理 prompt artifact（身份 = 内容寻址；见 ``artifact_id``）。

    身份语义（进 ``artifact_id``）：schema_version、crs、全部几何、text、
    mask 引用 digest、reference-layer 战略、combine、labels、time、target。
    **不进身份**：provenance 元数据（created_by/note/source_refs）——出处
    不改变 prompt 对推理的语义；它们随 artifact 存储并在 inspect 面呈现。
    """

    schema_version: int = GEO_PROMPT_SCHEMA_VERSION
    #: None ⇒ 像素坐标；否则 CRS 字符串（"EPSG:xxxx"/WKT；词法非空无控制符，
    #: 解析由编译期/服务层负责——lib 层不依赖 rasterio CRS 解析器）。
    crs: Optional[str] = None
    points: Tuple[Tuple[float, float], ...] = ()
    #: (x, y, w, h)，坐标系同 points。
    boxes: Tuple[Tuple[float, float, float, float], ...] = ()
    #: 每条折线是 ≥2 顶点的序列。
    polylines: Tuple[Tuple[_Point, ...], ...] = ()
    #: 每个多边形是 ≥3 顶点的外环（v1 无洞；编译期自动闭合）。
    polygons: Tuple[Tuple[_Point, ...], ...] = ()
    text: Optional[str] = ""
    mask_ref: Optional[MaskReference] = None
    reference_layer: Optional[ReferenceLayer] = None
    time: Optional[GeoPromptTime] = None
    target: Optional[GeoPromptTarget] = None
    combine: str = "union"
    labels: Tuple[int, ...] = ()
    # ── provenance（元数据，不进身份）───────────────────────────────
    created_by: str = ""
    note: str = ""
    source_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != GEO_PROMPT_SCHEMA_VERSION:
            raise PromptArtifactError(
                f"unsupported prompt artifact schema_version {self.schema_version} "
                f"(supported: {GEO_PROMPT_SCHEMA_VERSION})"
            )
        if self.crs is not None and (
            not self.crs.strip() or any(ch in self.crs for ch in "\r\n\t\x00")
        ):
            raise PromptArtifactError(f"invalid crs declaration {self.crs!r}")
        has_geometry = bool(
            self.points or self.boxes or self.polylines or self.polygons
        )
        if not has_geometry and not self.text and not self.mask_ref \
                and not self.reference_layer:
            raise PromptArtifactError(
                "prompt artifact requires at least one prompt input "
                "(geometry/text/mask/reference-layer)"
            )
        for kind, count in (
            ("points", len(self.points)), ("boxes", len(self.boxes)),
            ("polylines", len(self.polylines)), ("polygons", len(self.polygons)),
        ):
            if count > MAX_GEOMETRY_PER_KIND:
                raise PromptArtifactError(
                    f"too many {kind} prompts (max {MAX_GEOMETRY_PER_KIND} per kind)"
                )
        if self.combine not in ("union", "intersect"):
            raise PromptArtifactError(f"unknown prompt combine policy {self.combine!r}")
        for x, y in self.points:
            _validate_xy(x, y, "point")
        for bx, by, bw, bh in self.boxes:
            _validate_xy(bx, by, "box origin")
            if float(bw) <= 0 or float(bh) <= 0:
                raise PromptArtifactError(
                    f"box prompt must have positive extent (got w={bw!r}, h={bh!r})"
                )
        for line in self.polylines:
            if len(line) < MIN_POLYLINE_VERTICES:
                raise PromptArtifactError(
                    f"polyline requires >= {MIN_POLYLINE_VERTICES} vertices "
                    f"(got {len(line)})"
                )
            for x, y in line:
                _validate_xy(x, y, "polyline vertex")
        for ring in self.polygons:
            if len(ring) < MIN_POLYGON_RING_VERTICES:
                raise PromptArtifactError(
                    f"polygon ring requires >= {MIN_POLYGON_RING_VERTICES} vertices "
                    f"(got {len(ring)})"
                )
            for x, y in ring:
                _validate_xy(x, y, "polygon vertex")
        if self.mask_ref is not None and self.reference_layer is not None:
            raise PromptArtifactError(
                "mask sidecar and reference-layer are mutually exclusive prior "
                "sources (combine semantics would be ambiguous); compile one and "
                "pass the derived mask explicitly if both are needed"
            )

    # ── 身份 ─────────────────────────────────────────────────────────
    def identity_payload(self) -> Dict[str, Any]:
        """进 artifact_id 的具名字段（排序 canonical JSON；不含 provenance）。"""
        return {
            "schema_version": self.schema_version,
            "crs": self.crs,
            "points": [[float(x), float(y)] for x, y in self.points],
            "boxes": [[float(b[0]), float(b[1]), float(b[2]), float(b[3])]
                      for b in self.boxes],
            "polylines": [[[float(x), float(y)] for x, y in line]
                          for line in self.polylines],
            "polygons": [[[float(x), float(y)] for x, y in ring]
                         for ring in self.polygons],
            "text": self.text or None,
            "mask_ref": self.mask_ref.as_dict() if self.mask_ref else None,
            "reference_layer": self.reference_layer.as_dict()
            if self.reference_layer else None,
            "time": self.time.as_dict() if self.time else None,
            "target": self.target.as_dict() if self.target else None,
            "combine": self.combine,
            "labels": [int(v) for v in self.labels],
        }

    @property
    def artifact_id(self) -> str:
        return hashlib.sha256(_canonical_json_bytes(self.identity_payload())).hexdigest()

    # ── JSON 往返（几何/引用全 JSON 安全；先验数组不在此通道）─────────
    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = self.identity_payload()
        payload["artifact_id"] = self.artifact_id
        payload["provenance"] = {
            "created_by": self.created_by,
            "note": self.note,
            "source_refs": list(self.source_refs),
        }
        return payload

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, Any]]) -> "GeoPromptArtifact":
        if not payload:
            raise PromptArtifactError("prompt artifact payload required")
        if not isinstance(payload, dict):
            raise PromptArtifactError("prompt artifact payload must be a mapping")
        declared = payload.get("artifact_id")
        time_raw = payload.get("time")
        target_raw = payload.get("target")
        mask_raw = payload.get("mask_ref")
        ref_raw = payload.get("reference_layer")
        prov = payload.get("provenance") or {}
        artifact = cls(
            schema_version=int(payload.get("schema_version", GEO_PROMPT_SCHEMA_VERSION)),
            crs=payload.get("crs"),
            points=tuple(tuple(map(float, p)) for p in payload.get("points", [])),
            boxes=tuple(tuple(map(float, b)) for b in payload.get("boxes", [])),
            polylines=tuple(
                tuple(tuple(map(float, v)) for v in line)
                for line in payload.get("polylines", [])
            ),
            polygons=tuple(
                tuple(tuple(map(float, v)) for v in ring)
                for ring in payload.get("polygons", [])
            ),
            text=payload.get("text") or None,
            mask_ref=MaskReference(**mask_raw) if mask_raw else None,
            reference_layer=ReferenceLayer(**ref_raw) if ref_raw else None,
            time=GeoPromptTime(**time_raw) if time_raw else None,
            target=GeoPromptTarget(**target_raw) if target_raw else None,
            combine=payload.get("combine", "union"),
            labels=tuple(int(v) for v in payload.get("labels", [])),
            created_by=str(prov.get("created_by", "")),
            note=str(prov.get("note", "")),
            source_refs=tuple(str(r) for r in prov.get("source_refs", [])),
        )
        if declared and declared != artifact.artifact_id:
            raise PromptArtifactError(
                f"prompt artifact id mismatch: declared {declared!r} != "
                f"computed {artifact.artifact_id!r} (payload tampered or hand-edited)"
            )
        return artifact


@dataclass(frozen=True)
class GeoPromptAudit:
    """编译审计（进 manifest；每步可追溯）。"""

    artifact_id: str
    crs: Optional[str]
    coordinate_space: str                     # "pixel" | "map→pixel"
    roundtrip_max_error_px: float
    derived_mask_source: Optional[str]        # mask_sidecar | reference_layer | polygon/polyline rasterized | None
    derived_mask_pixels: int
    anchor_box: Optional[Tuple[int, int, int, int]]  # (x0, y0, x1, y1) 像素
    time: Optional[Dict[str, Any]]
    target: Optional[Dict[str, Any]]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "crs": self.crs,
            "coordinate_space": self.coordinate_space,
            "roundtrip_max_error_px": self.roundtrip_max_error_px,
            "derived_mask_source": self.derived_mask_source,
            "derived_mask_pixels": self.derived_mask_pixels,
            "anchor_box": list(self.anchor_box) if self.anchor_box else None,
            "time": self.time,
            "target": self.target,
        }


@dataclass(frozen=True)
class CompiledPrompt:
    """编译产物：运行时 PromptSpec + 审计（两者同生同灭）。"""

    prompt: PromptSpec
    audit: GeoPromptAudit


def _transform_inverse(transform: Any) -> Any:
    """仿射逆（typed 拒绝退化矩阵：|det|==0 不可逆）。"""
    a, b, _, d, e, _ = tuple(transform)[:6]
    det = a * e - b * d
    if det == 0 or not np_isfinite(det):
        raise PlanningError(
            "geographic prompt requires an invertible affine transform "
            "(degenerate/singular grid)",
            correction_hint="check the raster georeferencing (transform)",
        )
    return ~transform


def _roundtrip_error_px(transform: Any, inverse: Any, px: float, py: float) -> float:
    """像素 → 地图 → 像素 的闭合误差（像素单位）。"""
    mx, my = transform * (float(px), float(py))
    bx, by = inverse * (mx, my)
    return max(abs(bx - px), abs(by - py))


def _map_points_to_pixel(
    points: Tuple[_Point, ...], transform: Any
) -> Tuple[List[_Point], float]:
    inverse = _transform_inverse(transform)
    out: List[_Point] = []
    worst = 0.0
    for mx, my in points:
        col, row = inverse * (float(mx), float(my))
        out.append((float(col), float(row)))
        worst = max(worst, _roundtrip_error_px(transform, inverse, col, row))
    return out, worst


def _map_boxes_to_pixel(
    boxes: Tuple[Tuple[float, float, float, float], ...], transform: Any
) -> Tuple[List[Tuple[float, float, float, float]], float]:
    inverse = _transform_inverse(transform)
    out: List[Tuple[float, float, float, float]] = []
    worst = 0.0
    for bx, by, bw, bh in boxes:
        x0, y0 = inverse * (float(bx), float(by))
        x1, y1 = inverse * (float(bx + bw), float(by + bh))
        px0, py0 = min(x0, x1), min(y0, y1)
        out.append((px0, py0, abs(x1 - x0), abs(y1 - y0)))
        worst = max(worst, _roundtrip_error_px(transform, inverse, px0, py0),
                    _roundtrip_error_px(transform, inverse, x1, y1))
    return out, worst


def _map_lines_to_pixel(
    lines: Tuple[Tuple[_Point, ...], ...], transform: Any
) -> Tuple[List[List[_Point]], float]:
    inverse = _transform_inverse(transform)
    out: List[List[_Point]] = []
    worst = 0.0
    for line in lines:
        pixel_line: List[_Point] = []
        for mx, my in line:
            col, row = inverse * (float(mx), float(my))
            pixel_line.append((float(col), float(row)))
            worst = max(worst, _roundtrip_error_px(transform, inverse, col, row))
        out.append(pixel_line)
    return out, worst


def _rasterize_geometry_mask(
    polylines_px: List[List[_Point]],
    polygons_px: List[List[_Point]],
    raster_height: int,
    raster_width: int,
) -> Any:
    """折线（触及像元）+ 多边形（中心包含）→ 单张 bool 先验（确定性）。"""
    from rasterio import features as _features
    from shapely.geometry import LineString, Polygon

    if raster_height * raster_width > MAX_COMPILED_MASK_PIXELS:
        raise PromptArtifactError(
            f"compiled prior mask exceeds pixel cap {MAX_COMPILED_MASK_PIXELS} "
            f"({raster_width}x{raster_height}); narrow the prompt with an anchor "
            "or pre-compile the mask"
        )
    shapes: List[Tuple[Any, int]] = []
    for ring in polygons_px:
        # shapely 自动闭合开放环；自交/退化环修复为有效多边形（确定性；
        # 修复后 0 面积 = typed 拒绝）。
        geom = Polygon(ring)
        if not geom.is_valid:
            geom = geom.buffer(0)
            if geom.is_empty:
                raise PromptArtifactError(
                    "polygon prompt is degenerate (zero area after repair)"
                )
        shapes.append((geom, 1))
    for line in polylines_px:
        geom = LineString(line)
        if geom.length == 0:
            raise PromptArtifactError("polyline prompt has zero length")
        shapes.append((geom, 1))
    if not shapes:
        return None
    mask = _features.rasterize(
        ((geom, val) for geom, val in shapes),
        out_shape=(raster_height, raster_width),
        fill=0,
        dtype="uint8",
        all_touched=False,
    ).astype(bool)
    if polylines_px:
        # 折线单独以触及语义烧录（与多边形中心包含语义分开，均 known-answer）。
        line_mask = _features.rasterize(
            ((LineString(line), 1) for line in polylines_px),
            out_shape=(raster_height, raster_width),
            fill=0,
            dtype="uint8",
            all_touched=True,
        ).astype(bool)
        mask = mask | line_mask
    return mask


def _as_2d_band(array: Any, *, what: str, raster_height: int, raster_width: int) -> Any:
    """单波段读取结果 (1,H,W) → (H,W)；网格对齐校验（fail-closed）。"""
    if getattr(array, "ndim", 0) == 3 and array.shape[0] == 1:
        array = array[0]
    if tuple(array.shape)[-2:] != (raster_height, raster_width):
        raise PromptArtifactError(
            f"{what} shape {tuple(array.shape)} is not grid-aligned with the "
            f"target raster ({raster_height}x{raster_width})"
        )
    return array


def _load_mask_sidecar(mask_ref: MaskReference, loader: MaskLoader, raster_height: int, raster_width: int) -> Any:
    array = loader(mask_ref.path, int(mask_ref.band))
    if array is None:
        raise PromptArtifactError(
            f"mask sidecar {mask_ref.path!r} could not be loaded",
            correction_hint="provide a mask_loader or fix the path",
        )
    array = _as_2d_band(
        array, what=f"mask sidecar {mask_ref.path!r}",
        raster_height=raster_height, raster_width=raster_width,
    )
    return array.astype(bool)


def _resolve_reference_mask(
    ref: ReferenceLayer, reader: ReferenceReader, raster_height: int, raster_width: int
) -> Any:
    import numpy as np

    array = reader(ref.uri, int(ref.band))
    if array is None:
        raise PromptArtifactError(
            f"reference layer {ref.uri!r} could not be resolved",
            correction_hint="provide a reference_reader or check the layer uri",
        )
    array = _as_2d_band(
        array, what=f"reference layer {ref.uri!r}",
        raster_height=raster_height, raster_width=raster_width,
    )
    data = np.asarray(array)
    if data.dtype.kind == "f":
        valid = np.isfinite(data)
    else:
        valid = np.ones(data.shape, dtype=bool)
    if ref.strategy == REF_STRATEGY_THRESHOLD:
        mask = (data >= float(ref.threshold)) & valid
    else:
        mask = (data != 0) & valid
    if ref.invert:
        mask = ~mask
    return mask


def _mask_bounds(mask: Any) -> Optional[Tuple[int, int, int, int]]:
    """bool 掩膜的整数包围盒 (x0, y0, x1, y1)（无真值 → None）。"""
    import numpy as np

    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def _geometry_bounds(
    points: List[_Point],
    boxes: List[Tuple[float, float, float, float]],
    extra: Optional[Tuple[int, int, int, int]],
) -> Optional[Tuple[int, int, int, int]]:
    """全部几何的整数半开包围盒 (x0, y0, x1, y1)；``extra`` 已是半开区间。"""
    xs: List[float] = [x for x, _ in points]
    ys: List[float] = [y for _, y in points]
    for bx, by, bw, bh in boxes:
        xs.extend([bx, bx + bw])
        ys.extend([by, by + bh])
    if extra is not None:
        # extra 半开 → 折成闭区间参与合并（避免二次 +1）。
        xs.extend([extra[0], max(extra[2] - 1, extra[0])])
        ys.extend([extra[1], max(extra[3] - 1, extra[1])])
    if not xs:
        return None
    return (
        int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1,
    )


def compile_prompt(
    artifact: GeoPromptArtifact,
    *,
    transform: Any,
    raster_height: int,
    raster_width: int,
    mask_loader: Optional[MaskLoader] = None,
    reference_reader: Optional[ReferenceReader] = None,
    prior_masks: Tuple[Any, ...] = (),
    tolerance_px: float = PROMPT_ROUNDTRIP_TOL_PX,
) -> CompiledPrompt:
    """artifact → 运行时 PromptSpec + 审计（确定性；IO 全注入）。

    - 像素坐标 artifact：几何直通（不触仿射）；
    - 地图坐标 artifact：仿射逆变换 + 往返容差断言（超容差 typed 拒绝）；
    - polygon/polyline：确定性栅格化 → prior mask（像元中心/触及语义）；
    - mask sidecar / reference-layer：经注入 loader/reader 解析并网格校验；
      digest/解析失败 typed 拒绝；
    - anchor_box：全部几何（含派生 mask 包围盒）的整数包围盒——只用于窗口
      放置（prompt_anchored tile 策略），**不是**语义 box prompt。
    """
    if raster_height <= 0 or raster_width <= 0:
        raise PlanningError(
            f"compile_prompt requires a positive-shape grid (got {raster_width}x{raster_height})"
        )
    worst_err = 0.0
    space = "pixel"
    if artifact.crs is not None:
        if transform is None:
            raise PlanningError(
                "map-CRS prompt artifact requires a georeferenced raster "
                "(no transform)",
                correction_hint="use a GeoTIFF/COG source or a pixel-CRS artifact",
            )
        space = "map→pixel"
        pts, e1 = _map_points_to_pixel(artifact.points, transform)
        bxs, e2 = _map_boxes_to_pixel(artifact.boxes, transform)
        lines, e3 = _map_lines_to_pixel(
            tuple(artifact.polylines) + tuple(artifact.polygons), transform
        )
        worst_err = max(e1, e2, e3)
        poly_lines_px = lines[: len(artifact.polylines)]
        rings_px = lines[len(artifact.polylines):]
        if worst_err > tolerance_px:
            raise PlanningError(
                f"prompt coordinate roundtrip error {worst_err:.3e}px exceeds "
                f"tolerance {tolerance_px:.1e}px (ill-conditioned transform)"
            )
        points: List[_Point] = pts
        boxes: List[Tuple[float, float, float, float]] = bxs
        polylines_px: List[List[_Point]] = [
            [(float(x), float(y)) for x, y in line] for line in poly_lines_px
        ]
        polygons_px: List[List[_Point]] = [
            [(float(x), float(y)) for x, y in ring] for ring in rings_px
        ]
    else:
        points = [(float(x), float(y)) for x, y in artifact.points]
        boxes = [
            (float(b[0]), float(b[1]), float(b[2]), float(b[3])) for b in artifact.boxes
        ]
        polylines_px = [
            [(float(x), float(y)) for x, y in line] for line in artifact.polylines
        ]
        polygons_px = [
            [(float(x), float(y)) for x, y in ring] for ring in artifact.polygons
        ]

    derived_mask = None
    mask_source: Optional[str] = None
    if polygons_px or polylines_px:
        derived_mask = _rasterize_geometry_mask(
            polylines_px, polygons_px, raster_height, raster_width
        )
        mask_source = "polygon/polyline rasterized"
    if artifact.mask_ref is not None:
        if mask_loader is None:
            raise PromptArtifactError(
                "mask sidecar prompt requires a mask_loader (services layer must "
                "supply content-addressed loading)",
                correction_hint="pass mask_loader or drop mask_ref",
            )
        derived_mask = _load_mask_sidecar(
            artifact.mask_ref, mask_loader, raster_height, raster_width
        )
        mask_source = "mask_sidecar"
    elif artifact.reference_layer is not None:
        if reference_reader is None:
            raise PromptArtifactError(
                "reference-layer prompt requires a reference_reader (services "
                "layer must supply bounded resolution)",
                correction_hint="pass reference_reader or drop reference_layer",
            )
        derived_mask = _resolve_reference_mask(
            artifact.reference_layer, reference_reader, raster_height, raster_width
        )
        mask_source = "reference_layer"

    mask_bounds: Optional[Tuple[int, int, int, int]] = None
    mask_pixels = 0
    if derived_mask is not None:
        if derived_mask.size > MAX_COMPILED_MASK_PIXELS:
            raise PromptArtifactError(
                f"compiled prior mask exceeds pixel cap {MAX_COMPILED_MASK_PIXELS} "
                f"({derived_mask.size} px)"
            )
        mask_pixels = int(derived_mask.size)
        mask_bounds = _mask_bounds(derived_mask)
        if mask_bounds is None and not points and not boxes:
            raise PromptArtifactError(
                "compiled prior mask is empty and no point/box geometry exists "
                "(prompt would select nothing)"
            )

    priors: Tuple[Any, ...] = tuple(prior_masks)
    if derived_mask is not None:
        priors = priors + (derived_mask,)

    anchor = _geometry_bounds(points, boxes, mask_bounds)
    anchor_spec: Optional[Tuple[float, float, float, float]] = None
    if anchor is not None:
        ax0, ay0, ax1, ay1 = anchor
        anchor_spec = (float(ax0), float(ay0), float(ax1 - ax0), float(ay1 - ay0))
    prompt = PromptSpec(
        points=tuple(points),
        boxes=tuple(boxes),
        prior_masks=priors,
        text=artifact.text,
        combine=artifact.combine,
        labels=artifact.labels,
        anchor_box=anchor_spec,
    )
    audit = GeoPromptAudit(
        artifact_id=artifact.artifact_id,
        crs=artifact.crs,
        coordinate_space=space,
        roundtrip_max_error_px=float(worst_err),
        derived_mask_source=mask_source,
        derived_mask_pixels=mask_pixels,
        anchor_box=anchor,
        time=artifact.time.as_dict() if artifact.time else None,
        target=artifact.target.as_dict() if artifact.target else None,
    )
    return CompiledPrompt(prompt=prompt, audit=audit)


__all__ = [
    "GEO_PROMPT_SCHEMA_VERSION",
    "PROMPT_ROUNDTRIP_TOL_PX",
    "MAX_COMPILED_MASK_PIXELS",
    "MAX_GEOMETRY_PER_KIND",
    "REFERENCE_STRATEGIES",
    "GeoPromptArtifact",
    "GeoPromptAudit",
    "GeoPromptTarget",
    "GeoPromptTime",
    "MaskReference",
    "ReferenceLayer",
    "CompiledPrompt",
    "compile_prompt",
]
