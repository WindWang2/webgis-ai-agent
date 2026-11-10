"""Raster Data Plane 基础契约（C5，ADR-0075）。

目标：**样式改动 ≠ 重新计算**。当前栅格有两条渲染路径互不相通——瓦片流
（raster_tile_service，单波段恒灰度、cmap_name 死参数）与 colormap 烘焙
PNG（raster_cartography_converter，换色=重跑转换=新 PNG + 新 imageRef）。
本模块定义两个契约对象：

- ``RasterArtifactDescriptor`` —— 栅格工件注册期描述子：band schema / dtype /
  nodata / per-band stats / CRS / bounds / overview 有无。登记时一次算好
  （调用方负责 to_thread），消费方（瓦片路由、图例、Agent 感知）零 IO 读取。
- ``RasterStyleSpec`` —— 样式契约：band 组合 / stretch / colormap / 透明度 /
  重采样。存 MapSpec 图层 paint 侧（``paint.raster_style``），数据平面按
  (数据, 样式) 二元组缓存瓦片——换样式只换缓存键，绝不重跑遥感计算。

Zero Big Data in Context：两者都是有界元数据，可安全进 LLM 上下文。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class RasterBandInfo(BaseModel):
    """单波段结构化描述。"""

    index: int  # 1-based
    dtype: str = ""
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    description: str = ""


# 时间轴有界上限（Wave 6）：描述子是进 LLM 上下文的有界元数据，时间
# 刻度必须封顶（1024 期 ≈ 日尺度 3 年，远超当前时序引擎的使用形态）。
_MAX_TIMES_ENTRIES = 1024


def _is_iso8601(value: str) -> bool:
    """ISO-8601 判定（容 Z 后缀；拒绝空串/非时间戳字符串）。"""
    from datetime import datetime

    if not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


class RasterArtifactDescriptor(BaseModel):
    """栅格工件描述子（登记期一次计算；消费期零 IO）。

    Runtime V3（ADR-0089）补齐网格身份字段：``transform`` / ``resolution_x``
    / ``resolution_y`` / ``driver`` / ``block_size`` —— 网格契约（对齐裁决、
    逐像元可运算判定、产物检视）不再需要重开文件。只登记真实使用的字段。
    """

    file_path: str
    width: int = 0
    height: int = 0
    crs: Optional[str] = None
    bounds: Optional[List[float]] = None  # [minx, miny, maxx, maxy]（源 CRS）
    dtype: str = ""
    nodata: Optional[float] = None
    band_count: int = 0
    bands: List[RasterBandInfo] = Field(default_factory=list)
    has_overviews: bool = False
    # ── 网格身份（V3）：仿射 6 参数 [a, b, c, d, e, f] 与派生分辨率 ──
    transform: Optional[List[float]] = None
    resolution_x: Optional[float] = None
    resolution_y: Optional[float] = None
    driver: str = ""
    block_size: Optional[List[int]] = None  # [blockxsize, blockysize]（tiled 时）
    # ── 时间轴（Wave 6，audit 05 §7.6，additive）：时序立方体描述 ──
    # times：ISO-8601 时间戳（≤1024 个）；stack_ref：物化立方体
    # （如 zarr store）的引用。两者都是可缺席元数据 —— 单期产物两者
    # 皆 None，序列化形状与既有登记完全兼容。
    times: Optional[List[str]] = None
    stack_ref: Optional[str] = None

    @field_validator("times")
    @classmethod
    def _validate_times(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        if len(v) > _MAX_TIMES_ENTRIES:
            raise ValueError(
                f"times carries {len(v)} entries; the bounded descriptor "
                f"caps at {_MAX_TIMES_ENTRIES}"
            )
        for raw in v:
            if not _is_iso8601(str(raw)):
                raise ValueError(
                    f"times entries must be ISO-8601 timestamps, got {raw!r}"
                )
        return list(v)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    @property
    def grid_identity(self) -> str:
        """网格身份键（对齐/复用披露用；不含像元内容）。"""
        return "|".join(
            str(x) for x in (
                self.width, self.height, self.crs,
                tuple(round(v, 12) for v in (self.transform or ())),
                self.dtype, self.nodata, self.band_count,
            )
        )


def inspect_raster_artifact(file_path: str) -> Optional[RasterArtifactDescriptor]:
    """读取栅格头信息 + 有界统计（重 IO——调用方必须 asyncio.to_thread）。

    统计与瓦片服务同策略：降采样到最长边 2048，跳过 nodata/非有限值。
    返回 None（文件缺失/非栅格）由调用方决定降级语义。
    """
    try:
        import numpy as np
        import rasterio
        from rasterio.enums import Resampling

        with rasterio.open(file_path) as src:
            band_infos: List[RasterBandInfo] = []
            scale = min(1.0, 2048.0 / float(max(src.width, src.height)))
            out_shape = (
                src.count,
                max(1, int(round(src.height * scale))),
                max(1, int(round(src.width * scale))),
            )
            data = src.read(out_shape=out_shape, masked=True, resampling=Resampling.average)
            arr = np.ma.getdata(data)
            mask = np.ma.getmaskarray(data)
            for b in range(src.count):
                valid = np.isfinite(arr[b]) & ~mask[b]
                if valid.any():
                    vmin = float(arr[b][valid].min())
                    vmax = float(arr[b][valid].max())
                else:
                    vmin = vmax = None
                band_infos.append(
                    RasterBandInfo(
                        index=b + 1,
                        dtype=str(src.dtypes[b]),
                        vmin=vmin,
                        vmax=vmax,
                        description=(src.descriptions[b] or "") if src.descriptions else "",
                    )
                )
            return RasterArtifactDescriptor(
                file_path=file_path,
                width=src.width,
                height=src.height,
                crs=str(src.crs) if src.crs else None,
                bounds=list(src.bounds) if src.bounds else None,
                dtype=str(src.dtypes[0]) if src.dtypes else "",
                nodata=(float(src.nodata) if src.nodata is not None else None),
                band_count=src.count,
                bands=band_infos,
                has_overviews=bool(src.overviews(1)),
                transform=[
                    float(src.transform.a), float(src.transform.b), float(src.transform.c),
                    float(src.transform.d), float(src.transform.e), float(src.transform.f),
                ],
                resolution_x=abs(float(src.transform.a)),
                resolution_y=abs(float(src.transform.e)),
                driver=str(src.driver),
                block_size=(
                    [int(src.block_shapes[0][1]), int(src.block_shapes[0][0])]
                    if getattr(src, "block_shapes", None) else None
                ),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[raster_spec] inspect failed for %s: %s", file_path, e)
        return None


# 内容指纹的有界采样边长：指纹覆盖 grid 身份 + 全部波段的降采样样本
# （同 inspect/stats 同数量级）。绝不为了 hash 重新读整幅几十 GB 栅格
# （ADR-0089 §内容身份）。
_FINGERPRINT_MAX_SIDE = 1024


def raster_content_fingerprint(file_path: str) -> Optional[str]:
    """栅格文件的**有界**内容指纹（sha256，十六进制）。

    输入覆盖（§32）：grid（宽高/CRS/transform/dtype/nodata/band 数）+
    像元内容（各波段降采样到 ≤1024 边的样本字节）。同内容不同
    path/mtime → 同指纹；内容变 → 指纹变。成本 O(1024²·bands)，与一次
    inspect 同级。文件缺失/不可读 → None（调用方降级，不阻塞）。

    Runtime V5 指纹统一说明：本函数是**工件平面**的持久化指纹——值已进
    analysis-reuse 键与产物记录（app/lib/gis/analysis_reuse.py），因此
    格式与取值**冻结**，不得随读侧实现漂移。它与 V3 写者流式摘要
    （WindowedRasterWriter，摘要种子=网格身份 + 窗口字节）以及 V5 读侧
    统一指纹（app/lib/geo_raster/fingerprint.content_fingerprint，sha256[:16]
    = 结构身份 + 角块采样）同属 §31/§32「有界、内容敏感、path/mtime 无关」
    的同一契约族，但三者的摘要格式各自独立——这是持久化兼容的刻意选择，
    不是未完成的迁移。新读侧消费方一律用 V5 入口。
    """
    import hashlib

    try:
        import numpy as np
        import rasterio
        from rasterio.enums import Resampling
    except Exception:  # noqa: BLE001
        return None
    try:
        with rasterio.open(file_path) as src:
            digest = hashlib.sha256()
            grid_key = (
                src.width, src.height, str(src.crs),
                round(src.transform.a, 12), round(src.transform.e, 12),
                str(src.dtypes[0]) if src.dtypes else "",
                src.nodata, src.count,
            )
            digest.update(repr(grid_key).encode())
            scale = min(1.0, _FINGERPRINT_MAX_SIDE / float(max(src.width, src.height)))
            out_shape = (
                src.count,
                max(1, int(round(src.height * scale))),
                max(1, int(round(src.width * scale))),
            )
            data = src.read(out_shape=out_shape, resampling=Resampling.average)
            digest.update(np.ascontiguousarray(data).tobytes())
            return digest.hexdigest()
    except Exception as e:  # noqa: BLE001
        logger.debug("[raster_spec] fingerprint failed for %s: %s", file_path, e)
        return None


class RasterStyleSpec(BaseModel):
    """栅格样式契约（MapSpec paint 侧；样式 ≠ 数据）。

    - ``bands``：1-based 波段组合（RGB 三元组或单波段）；缺省 = 前 min(3, n)。
    - ``stretch``：(min, max) 覆盖数据集全局 stretch；缺省 = 描述子统计。
    - ``colormap``：单波段着色名（matplotlib 合法名，如 viridis/rdylgn）；
      三波段组合忽略。缺省 = 灰度。
    - ``opacity`` / ``resampling``：渲染旋钮。
    """

    bands: Optional[List[int]] = None
    stretch: Optional[Tuple[float, float]] = None
    colormap: Optional[str] = None
    opacity: float = Field(1.0, ge=0.0, le=1.0)
    resampling: str = "bilinear"

    def normalized_bands(self, band_count: int) -> Tuple[int, ...]:
        """校验并归一波段索引（1-based，≤3 个，越界剔除，空则默认前 N）。"""
        if not self.bands:
            n = min(3, max(1, band_count))
            return tuple(range(1, n + 1))
        picked = tuple(
            b for b in self.bands if isinstance(b, int) and 1 <= b <= band_count
        )[:3]
        if not picked:
            n = min(3, max(1, band_count))
            return tuple(range(1, n + 1))
        return picked

    def cache_key(self) -> str:
        """样式缓存键（数据平面 (数据, 样式) 二元组缓存键的样式侧）。"""
        return ",".join(
            str(x)
            for x in (
                self.bands or (),
                self.stretch or (),
                self.colormap or "",
                self.opacity,
                self.resampling,
            )
        )


_COLORMAP_LUT_CACHE: Dict[str, Any] = {}


def colormap_rgb_lut(colormap: str, size: int = 256):
    """colormap 名 → (size, 3) uint8 LUT（进程内缓存）。

    matplotlib colormap 归一化采样；未知名返回 None（调用方回退灰度）。
    """
    import numpy as np

    cache_key = f"{colormap}:{size}"
    if cache_key in _COLORMAP_LUT_CACHE:
        return _COLORMAP_LUT_CACHE[cache_key]
    try:
        # matplotlib ≥3.9 移除了 cm.get_cmap；注册表访问是现行 API
        from matplotlib import colormaps

        cmap = colormaps[colormap]
        lut = (cmap(np.linspace(0.0, 1.0, size))[:, :3] * 255).astype(np.uint8)
    except Exception:  # noqa: BLE001
        return None
    if len(_COLORMAP_LUT_CACHE) > 64:
        _COLORMAP_LUT_CACHE.clear()
    _COLORMAP_LUT_CACHE[cache_key] = lut
    return lut


def apply_colormap_u8(gray: Any, colormap: str) -> Optional[Any]:
    """把归一化 uint8 灰度经 LUT 映射为 (..., 3) RGB。未知 colormap → None。"""

    lut = colormap_rgb_lut(colormap)
    if lut is None:
        return None
    return lut[gray]


# ─────────────────────────────────────────────────────────────────────────────
# G20 / raster_expression.v1 —— 可执行的栅格表达契约
#
# 与旧 ``RasterStyleSpec`` 的关系：旧类保持逐字兼容（``tests/unit/test_raster_spec.py``
# 是它的消费者）。新类是**唯一**进入 MapSpec 与数据面的规范表达：
#   - 严格（不静默剔除用户选择，越界/NaN/反向/未知色带/2 波段 RGB 都返回具体错误）；
#   - 可序列化（``model_dump`` 即 MapSpec 中的 ``layer.raster_style``）；
#   - 与数据身份解耦（``data_revision`` 只记录绑定，不重算遥感数据指纹）；
#   - ``style_id`` 是规范样式 + 数据修订的内容哈希，瓦片缓存/导出快照都用它。
#
# 统计口径（manual / bounded_sample / descriptor + method + sample_count +
# approximate）随样式冻结，缩放与导出绝不悄悄重估自动范围。
# ─────────────────────────────────────────────────────────────────────────────

import hashlib
import json
import math
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

RASTER_EXPRESSION_SCHEMA_VERSION = "raster_expression.v1"
RASTER_EXPRESSION_MODES = ("single", "rgb")
RASTER_RESAMPLINGS = ("nearest", "bilinear", "cubic")
RASTER_CLASSIFICATIONS = ("continuous", "classified")
RASTER_STRETCH_SOURCES = ("manual", "bounded_sample", "descriptor")

#: 表达域预算（窗口/波段/点数/直方图桶的其余上限见 raster_authoring/budgets.py）
MAX_EXPRESSION_BANDS = 3
MAX_RASTER_PROBE_POINTS = 16
MAX_RASTER_PROBE_BANDS = 16
MAX_RASTER_HISTOGRAM_BUCKETS = 64
MAX_RASTER_STATS_SIDE = 2048
MAX_RASTER_SNAPSHOT_SIDE = 2048

#: 稳定错误码（UI 与 Pi 同源；errors.py 是唯一分类表）
RASTER_ERROR_CODES = (
    "SCHEMA_VERSION_UNSUPPORTED",
    "EMPTY_BANDS",
    "BAND_COUNT_UNSUPPORTED",
    "BAND_OUT_OF_RANGE",
    "BAND_NOT_INTEGER",
    "STRETCH_NON_FINITE",
    "STRETCH_REVERSED",
    "STRETCH_CHANNEL_MISMATCH",
    "STRETCH_SAMPLE_UNBOUNDED",
    "UNKNOWN_COLORMAP",
    "UNSUPPORTED_RESAMPLING",
    "CLASSIFIED_INTERPOLATION",
    "OPACITY_OUT_OF_RANGE",
    "COLORMAP_ON_RGB",
    "SOURCE_UNAVAILABLE",
    "SOURCE_NOT_NUMERIC_RASTER",
    "SOURCE_PATH_REJECTED",
    "NO_CRS",
    "INVALID_CRS",
    "OUTSIDE_EXTENT",
    "MASKED_PIXEL",
    "PARTIALLY_MASKED",
    "STALE_BINDING",
    "STALE_STYLE",
    "BUDGET_EXCEEDED",
    "RENDER_FAILED",
    "EXPORT_UNSUPPORTED",
    "CAS_SUPERSEDED",
)


class RasterExpressionError(Exception):
    """带稳定 code / 字段 / 修复指引的领域错误（HTTP 与工具回执共用）。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: Optional[str] = None,
        correction_hint: str = "",
        status_code: int = 422,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code if code in RASTER_ERROR_CODES else "RENDER_FAILED"
        self.message = message
        self.field = field
        self.correction_hint = correction_hint
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": False,
            "code": self.code,
            "message": self.message,
            "correction_hint": self.correction_hint,
        }
        if self.field:
            out["field"] = self.field
        if self.details:
            out["details"] = self.details
        return out


class RasterStretchBounds(BaseModel):
    """已解析（冻结）的拉伸端点与统计口径。

    ``per_channel`` 长度必须等于表达的波段数；每项 ``[min, max]``。
    ``source="bounded_sample"`` 时 ``approximate`` 必须为 True 且带
    ``sample_count`` —— 近似分位数绝不能被写成精确全量结论。
    """

    model_config = ConfigDict(extra="forbid")

    min: StrictFloat
    max: StrictFloat
    source: Literal[RASTER_STRETCH_SOURCES] = "manual"  # type: ignore[valid-type]
    method: StrictStr = "manual"
    sample_count: Optional[StrictInt] = None
    approximate: StrictBool = False
    per_channel: Optional[List[List[StrictFloat]]] = None

    @model_validator(mode="after")
    def _validate_bounds(self) -> "RasterStretchBounds":
        for label, value in (("min", self.min), ("max", self.max)):
            if not math.isfinite(float(value)):
                raise RasterExpressionError(
                    "STRETCH_NON_FINITE",
                    f"stretch.{label} must be finite, got {value!r}",
                    field=f"stretch.{label}",
                    correction_hint="Provide finite numeric stretch endpoints.",
                )
        if self.max < self.min:
            raise RasterExpressionError(
                "STRETCH_REVERSED",
                f"stretch.max ({self.max}) is below stretch.min ({self.min})",
                field="stretch.max",
                correction_hint="Set stretch.max >= stretch.min.",
            )
        if self.source == "bounded_sample":
            if not self.approximate or not self.sample_count or self.sample_count <= 0:
                raise RasterExpressionError(
                    "STRETCH_SAMPLE_UNBOUNDED",
                    "bounded_sample stretch requires approximate=True and a positive sample_count",
                    field="stretch.source",
                    correction_hint="Disclose the sampling method and sample size.",
                )
        for ch in self.per_channel or []:
            if len(ch) != 2 or any(not math.isfinite(float(v)) for v in ch):
                raise RasterExpressionError(
                    "STRETCH_NON_FINITE",
                    "each per_channel entry must be a finite [min, max] pair",
                    field="stretch.per_channel",
                )
            if ch[1] < ch[0]:
                raise RasterExpressionError(
                    "STRETCH_REVERSED",
                    f"per-channel stretch is reversed: {ch!r}",
                    field="stretch.per_channel",
                )
        return self


class RasterExpressionSpec(BaseModel):
    """规范栅格表达（MapSpec ``layer.raster_style`` 的唯一形状）。

    - ``mode="single"`` → 恰好 1 个 1-based 波段 + 可选 colormap；
    - ``mode="rgb"`` → 恰好 3 个 1-based 波段（允许重复波段做灰度复合）；
      2 波段数据集显式请求 RGB 必须由 ``contract.resolve_expression`` 拒绝，
      绝不产生渲染器不支持的二元 RGB。
    - ``classification="classified"`` 时 ``resampling`` 必须为 ``nearest``。
    - ``style_id`` 由 ``finalize()`` 计算，不参与哈希自身。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[RASTER_EXPRESSION_SCHEMA_VERSION] = RASTER_EXPRESSION_SCHEMA_VERSION  # type: ignore[valid-type]
    mode: Literal[RASTER_EXPRESSION_MODES]  # type: ignore[valid-type]
    bands: List[StrictInt]
    stretch: RasterStretchBounds
    colormap: Optional[StrictStr] = None
    opacity: StrictFloat = 1.0
    resampling: Literal[RASTER_RESAMPLINGS] = "bilinear"  # type: ignore[valid-type]
    classification: Literal[RASTER_CLASSIFICATIONS] = "continuous"  # type: ignore[valid-type]
    #: 绑定的数据修订（字符串，来源 = ref descriptor 的 content_revision 或内容指纹）
    data_revision: StrictStr = ""
    #: 内容哈希（见 finalize）；空串 = 尚未 final 的候选
    style_id: StrictStr = ""

    @model_validator(mode="after")
    def _validate_shape(self) -> "RasterExpressionSpec":
        expected = 1 if self.mode == "single" else 3
        if not self.bands:
            raise RasterExpressionError(
                "EMPTY_BANDS", "bands must not be empty", field="bands",
                correction_hint="Choose one band (single) or three bands (RGB).",
            )
        if len(self.bands) != expected:
            raise RasterExpressionError(
                "BAND_COUNT_UNSUPPORTED",
                f"mode={self.mode!r} requires exactly {expected} 1-based band(s), got {len(self.bands)}",
                field="bands",
                correction_hint=(
                    "Use mode='single' with one band, or mode='rgb' with exactly three bands "
                    "(repeating a band index is allowed for a grey composite)."
                ),
            )
        if len(self.bands) > MAX_EXPRESSION_BANDS:
            raise RasterExpressionError(
                "BAND_COUNT_UNSUPPORTED", "at most 3 bands can be rendered",
                field="bands",
            )
        for b in self.bands:
            if b < 1:
                raise RasterExpressionError(
                    "BAND_OUT_OF_RANGE", f"band index must be 1-based and >= 1, got {b}",
                    field="bands",
                )
        if self.mode == "rgb" and self.colormap:
            raise RasterExpressionError(
                "COLORMAP_ON_RGB",
                "colormap applies to single-band expressions only",
                field="colormap",
                correction_hint="Remove colormap for an RGB band combination.",
            )
        if not (0.0 <= float(self.opacity) <= 1.0):
            raise RasterExpressionError(
                "OPACITY_OUT_OF_RANGE", f"opacity must be within [0,1], got {self.opacity}",
                field="opacity",
            )
        if self.classification == "classified" and self.resampling != "nearest":
            raise RasterExpressionError(
                "CLASSIFIED_INTERPOLATION",
                "classified rasters must use nearest resampling; interpolation invents intermediate classes",
                field="resampling",
                correction_hint="Set resampling='nearest' or declare classification='continuous'.",
            )
        if self.stretch.per_channel is not None and len(self.stretch.per_channel) != len(self.bands):
            raise RasterExpressionError(
                "STRETCH_CHANNEL_MISMATCH",
                f"stretch.per_channel has {len(self.stretch.per_channel)} entries for {len(self.bands)} bands",
                field="stretch.per_channel",
                correction_hint="Provide one [min, max] pair per selected band.",
            )
        return self

    def canonical_payload(self) -> Dict[str, Any]:
        """稳定 JSON 载荷（``style_id`` 剔除，避免自引用）。"""
        data = self.model_dump(mode="json", exclude={"style_id"})
        # dict 键序由 model_dump 声明序固定；json.dumps 不重排
        return data

    def compute_style_id(self) -> str:
        raw = json.dumps(
            self.canonical_payload(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def finalize(self) -> "RasterExpressionSpec":
        """回填内容哈希（幂等；已存在且不一致 → 视为被篡改）。"""
        expected = self.compute_style_id()
        if self.style_id and self.style_id != expected:
            raise RasterExpressionError(
                "STALE_STYLE",
                "raster_style.style_id does not match the canonical style payload",
                field="style_id",
                correction_hint="Re-submit the expression through the authoritative commit path.",
                status_code=409,
            )
        return self.model_copy(update={"style_id": expected})

    def channel_stretch(self, channel: int) -> Tuple[float, float]:
        """渲染/图例/导出共用的逐通道端点（单一样式侧真相）。"""
        if self.stretch.per_channel is not None:
            pair = self.stretch.per_channel[channel]
            return float(pair[0]), float(pair[1])
        return float(self.stretch.min), float(self.stretch.max)
