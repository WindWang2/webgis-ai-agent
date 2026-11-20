"""G20 表达请求 → 规范表达（严格校验 + 冻结统计口径 + style_id）。

所有入口（UI 提交、Pi 工具、预览）都经此模块，因此同一非法输入在两条
通道得到同一个 code / field / correction_hint。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.schemas.raster_spec import (
    RASTER_EXPRESSION_SCHEMA_VERSION,
    RASTER_RESAMPLINGS,
    RasterExpressionError,
    RasterExpressionSpec,
    RasterStretchBounds,
)
from app.services.raster_authoring.descriptor import band_stats
from app.services.raster_authoring.palettes import canonical_colormap, colormap_lut
from app.services.raster_authoring.source import RasterSource

AUTO_STRETCH_METHOD = "minmax_decimated_2048"


def default_expression_request(source: RasterSource) -> Dict[str, Any]:
    """明确且受支持的默认表达：2 波段数据集绝不退化成二元 RGB。"""
    if source.band_count < 3:
        return {
            "schema_version": RASTER_EXPRESSION_SCHEMA_VERSION,
            "mode": "single",
            "bands": [1],
            "stretch": "auto",
            "colormap": "gray",
            "opacity": 1.0,
            "resampling": "bilinear",
            "classification": "continuous",
        }
    return {
        "schema_version": RASTER_EXPRESSION_SCHEMA_VERSION,
        "mode": "rgb",
        "bands": [1, 2, 3],
        "stretch": "auto",
        "opacity": 1.0,
        "resampling": "bilinear",
        "classification": "continuous",
    }


def _bands_from_request(request: Mapping[str, Any], source: RasterSource) -> tuple[str, List[int]]:
    raw = request.get("bands")
    if raw is None:
        default = default_expression_request(source)
        return str(default["mode"]), list(default["bands"])
    if not isinstance(raw, (list, tuple)) or not raw:
        raise RasterExpressionError(
            "EMPTY_BANDS", "bands must be a non-empty array of 1-based integers",
            field="bands",
        )
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            raise RasterExpressionError(
                "BAND_NOT_INTEGER", f"band index {value!r} is not a 1-based integer",
                field="bands",
            )
    values = [int(v) for v in raw]
    if len(values) == 2:
        raise RasterExpressionError(
            "BAND_COUNT_UNSUPPORTED",
            "two-band RGB is not supported; use one band (single) or repeat a band three times",
            field="bands",
            correction_hint=(
                f"This source has {source.band_count} band(s). Choose mode='single' with one band, "
                "or mode='rgb' with exactly three 1-based band indexes (repeats allowed)."
            ),
            details={"band_count": source.band_count},
        )
    if any(v < 1 or v > source.band_count for v in values):
        raise RasterExpressionError(
            "BAND_OUT_OF_RANGE",
            f"band indexes must be within 1..{source.band_count}, got {values}",
            field="bands",
            correction_hint=f"Choose 1-based indexes within 1..{source.band_count}.",
            details={"band_count": source.band_count},
        )
    mode = "single" if len(values) == 1 else "rgb"
    return mode, values


def _classification(request: Mapping[str, Any], source: RasterSource) -> str:
    declared = request.get("classification")
    if declared is None:
        # 只有真实元数据显式声明才自动采用 classified；整数 dtype 不证明类别。
        tag = str(source.dataset_tags.get("classification") or "").strip().lower()
        return "classified" if tag in ("classified", "categorical", "thematic") else "continuous"
    if declared not in ("continuous", "classified"):
        raise RasterExpressionError(
            "CLASSIFIED_INTERPOLATION",
            f"unknown classification {declared!r}; expected continuous|classified",
            field="classification",
        )
    return str(declared)


def _resolve_stretch(
    request: Mapping[str, Any], source: RasterSource, bands: Sequence[int]
) -> RasterStretchBounds:
    raw = request.get("stretch")
    if raw is None or raw == "auto":
        stats = band_stats(source, bands)
        pairs = [[float(lo), float(hi)] for lo, hi in stats.per_band]
        lows = [p[0] for p in pairs]
        highs = [p[1] for p in pairs]
        return RasterStretchBounds(
            min=min(lows), max=max(highs),
            source="bounded_sample", method=AUTO_STRETCH_METHOD,
            sample_count=stats.sample_count, approximate=True,
            per_channel=pairs,
        )
    if not isinstance(raw, Mapping):
        raise RasterExpressionError(
            "STRETCH_NON_FINITE", "stretch must be 'auto' or an object with min/max",
            field="stretch",
        )
    try:
        lo = float(raw["min"])
        hi = float(raw["max"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RasterExpressionError(
            "STRETCH_NON_FINITE", "explicit stretch requires finite min and max",
            field="stretch",
        ) from exc
    if not math.isfinite(lo) or not math.isfinite(hi):
        raise RasterExpressionError(
            "STRETCH_NON_FINITE", "stretch endpoints must be finite (NaN/Inf rejected)",
            field="stretch",
        )
    per_channel = raw.get("per_channel")
    normalized_channels: Optional[List[List[float]]] = None
    if per_channel is not None:
        if not isinstance(per_channel, (list, tuple)):
            raise RasterExpressionError(
                "STRETCH_CHANNEL_MISMATCH", "per_channel must be an array of [min,max] pairs",
                field="stretch.per_channel",
            )
        normalized_channels = []
        for pair in per_channel:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise RasterExpressionError(
                    "STRETCH_CHANNEL_MISMATCH", "each per_channel entry must be [min,max]",
                    field="stretch.per_channel",
                )
            try:
                normalized_channels.append([float(pair[0]), float(pair[1])])
            except (TypeError, ValueError) as exc:
                raise RasterExpressionError(
                    "STRETCH_NON_FINITE", "per_channel entries must be finite numbers",
                    field="stretch.per_channel",
                ) from exc
    # 显式端点 = 用户手动锁定，口径恒为 manual（不把用户声明伪装成采样统计；
    # 自动口径只由上面的 bounded_sample 分支产生并冻结 sample_count）。
    return RasterStretchBounds(
        min=lo, max=hi,
        source="manual", method="manual",
        sample_count=None, approximate=False,
        per_channel=normalized_channels,
    )


def resolve_expression(request: Mapping[str, Any], source: RasterSource) -> RasterExpressionSpec:
    """严格解析 + 冻结统计口径 + 生成 style_id；任何非法输入不产生写入。"""
    if not isinstance(request, Mapping):
        raise RasterExpressionError(
            "SCHEMA_VERSION_UNSUPPORTED", "expression request must be an object",
        )
    version = request.get("schema_version")
    if version is not None and version != RASTER_EXPRESSION_SCHEMA_VERSION:
        raise RasterExpressionError(
            "SCHEMA_VERSION_UNSUPPORTED",
            f"unsupported raster expression schema {version!r}",
            field="schema_version",
        )
    mode, bands = _bands_from_request(request, source)
    declared_mode = request.get("mode")
    if declared_mode is not None and declared_mode != mode:
        raise RasterExpressionError(
            "BAND_COUNT_UNSUPPORTED",
            f"mode={declared_mode!r} conflicts with {len(bands)} selected band(s)",
            field="mode",
            correction_hint="mode is derived from band count: 1 → single, 3 → rgb.",
        )
    classification = _classification(request, source)
    resampling = str(request.get("resampling") or ("nearest" if classification == "classified" else "bilinear"))
    if resampling not in RASTER_RESAMPLINGS:
        raise RasterExpressionError(
            "UNSUPPORTED_RESAMPLING",
            f"unsupported resampling {resampling!r}; expected one of {list(RASTER_RESAMPLINGS)}",
            field="resampling",
        )
    if classification == "classified" and resampling != "nearest":
        raise RasterExpressionError(
            "CLASSIFIED_INTERPOLATION",
            "classified rasters must use nearest resampling; interpolation invents intermediate classes",
            field="resampling",
            correction_hint="Set resampling='nearest' or declare classification='continuous'.",
        )
    colormap = request.get("colormap")
    if mode == "single":
        if colormap is None:
            colormap = "gray"
        canonical = canonical_colormap(colormap)
        if canonical is None:
            raise RasterExpressionError(
                "UNKNOWN_COLORMAP",
                f"unknown colormap {colormap!r}",
                field="colormap",
                correction_hint="Choose a name from the descriptor's supported palette list.",
            )
        # 提交期就证明 LUT 可解析；绝不把「通过校验但瓦片渲染 502」的
        # 假成功写进 MapSpec（图例色样与像素共用这一个 LUT）。
        if colormap_lut(canonical, 8) is None:
            raise RasterExpressionError(
                "UNKNOWN_COLORMAP",
                f"colormap {colormap!r} resolves to no color LUT",
                field="colormap",
            )
        colormap = canonical
    elif colormap:
        raise RasterExpressionError(
            "COLORMAP_ON_RGB", "colormap applies to single-band expressions only",
            field="colormap",
        )
    try:
        opacity = float(request.get("opacity", 1.0))
    except (TypeError, ValueError) as exc:
        raise RasterExpressionError(
            "OPACITY_OUT_OF_RANGE", "opacity must be a number in [0,1]", field="opacity",
        ) from exc
    if not 0.0 <= opacity <= 1.0:
        raise RasterExpressionError(
            "OPACITY_OUT_OF_RANGE", f"opacity must be within [0,1], got {opacity}",
            field="opacity",
        )
    # 便宜/确定性的校验全部先行，非法输入绝不触发有界统计读或写 MapSpec。
    stretch = _resolve_stretch(request, source, bands)
    spec = RasterExpressionSpec(
        schema_version=RASTER_EXPRESSION_SCHEMA_VERSION,
        mode=mode,  # type: ignore[arg-type]
        bands=bands,
        stretch=stretch,
        colormap=colormap if mode == "single" else None,
        opacity=opacity,
        resampling=resampling,  # type: ignore[arg-type]
        classification=classification,  # type: ignore[arg-type]
        data_revision=source.data_revision,
    )
    return spec.finalize()


def parse_committed_style(layer: Mapping[str, Any]) -> RasterExpressionSpec:
    """从权威 MapSpec 层读回并复验（篡改 / 旧版本 → STALE_STYLE）。"""
    raw = layer.get("raster_style") if isinstance(layer, Mapping) else None
    if not isinstance(raw, Mapping):
        raise RasterExpressionError(
            "SOURCE_NOT_NUMERIC_RASTER",
            "layer has no numeric raster_style; baked PNG layers cannot be re-styled",
            status_code=422,
        )
    try:
        spec = RasterExpressionSpec.model_validate(dict(raw))
    except RasterExpressionError:
        raise
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError → 领域码
        raise RasterExpressionError(
            "STALE_STYLE", "layer raster_style does not match raster_expression.v1",
            status_code=409,
        ) from exc
    return spec.finalize()


def bounded_projection(spec: RasterExpressionSpec, source: RasterSource,
                       *, band_limit: int = 6) -> Dict[str, Any]:
    """给 Pi 上下文的有界投影：只放本次操作相关的有限波段，不放直方图桶/点样本。"""
    return {
        "schema_version": spec.schema_version,
        "style_id": spec.style_id,
        "data_revision": spec.data_revision,
        "mode": spec.mode,
        "bands": [
            {
                "index": int(b),
                "label": source.band(b).get("label") or f"band {b}",
                "description": str(source.band(b).get("description") or "")[:80],
                "unit": source.band(b).get("unit"),
                "role": source.band(b).get("role"),
                "stretch": list(spec.channel_stretch(i)),
            }
            for i, b in enumerate(spec.bands[:band_limit])
        ],
        "band_count": source.band_count,
        "colormap": spec.colormap,
        "resampling": spec.resampling,
        "classification": spec.classification,
        "opacity": spec.opacity,
        "stretch_provenance": {
            "source": spec.stretch.source,
            "method": spec.stretch.method,
            "sample_count": spec.stretch.sample_count,
            "approximate": spec.stretch.approximate,
        },
        "committed": False,
    }
