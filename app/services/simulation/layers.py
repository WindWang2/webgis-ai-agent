"""时态产物切片与交付（ADR-0192 D7 / spec §2.3）。

时间片产物 = 每 output_stride 步铸一条 :class:`TemporalLayerProduct`：
- :class:`InMemoryTemporalSink`：内存记录 + `ref:simulation/<id>` 虚拟提货券；
- :class:`GeoParquetTemporalSink`：features → GeoArrow → GeoParquet 落盘；
  **pyarrow 缺席时诚实降级**为内存记录（`format="memory"` + degraded_note），
  绝不虚构 parquet 路径（与 vector_carrier 的 VectorCarrierUnavailable 同纪律）；
- :func:`build_mapspec_bundle`：把多时相产物组装为 **MapSpec v1.2 文档骨架**
  ——逐切片一个 geojson source + layer，时间轴用 ``layout.frames`` 表达
  （每帧 title 为模拟时刻、layerOverrides 只点亮当期图层）。帧数受
  ``MAX_SPEC_FRAMES``（与 mapspec_schema 对账）硬约束。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Protocol

from app.services.simulation.contracts import (
    MAX_TEMPORAL_FRAMES,
    SimulationConfigError,
    TemporalLayerProduct,
)

if TYPE_CHECKING:
    from app.services.simulation.runtime import SimulationResult

logger = logging.getLogger(__name__)

# 与 mapspec_schema.MAX_SPEC_FRAMES 对账：charts 保持同口径，漂移即告警。
try:  # pragma: no cover - import 分支
    from app.lib.cartography.mapspec_schema import (
        MAX_SPEC_FRAMES as _MAPSPEC_MAX_FRAMES,
    )
except Exception:  # pragma: no cover - schema 模块缺席时回退字面量
    _MAPSPEC_MAX_FRAMES = MAX_TEMPORAL_FRAMES

if _MAPSPEC_MAX_FRAMES != MAX_TEMPORAL_FRAMES:
    logger.warning(
        "[simulation] MapSpec frame cap drift: mapspec=%s contracts=%s",
        _MAPSPEC_MAX_FRAMES, MAX_TEMPORAL_FRAMES,
    )


@dataclass
class LayerDraft:
    """一个时间片的展平载荷（runtime 铸造，sink 消化）。"""

    product_id: str
    tick: int
    sim_seconds: float
    timestamp: Optional[str]
    layer_kind: str
    crs: Optional[str]
    features: list[dict[str, Any]]
    bbox: Optional[list[float]]
    truncated: bool


class TemporalSink(Protocol):
    """时态产物汇（内存 / GeoParquet / 未来 MVT 的公共缝）。"""

    def emit(self, draft: LayerDraft) -> TemporalLayerProduct: ...


def _product(
    draft: LayerDraft,
    *,
    format: str,
    ref: Optional[str],
    uri: Optional[str] = None,
    degraded_note: Optional[str] = None,
) -> TemporalLayerProduct:
    return TemporalLayerProduct(
        product_id=draft.product_id,
        tick=draft.tick,
        sim_seconds=draft.sim_seconds,
        timestamp=draft.timestamp,
        format=format,  # type: ignore[arg-type]
        ref=ref,
        uri=uri,
        feature_count=len(draft.features),
        truncated=draft.truncated,
        bbox=draft.bbox,
        layer_kind=draft.layer_kind,  # type: ignore[arg-type]
        degraded_note=degraded_note,
    )


class InMemoryTemporalSink:
    """内存产物汇（单测/小规模推演）；载荷按 product_id 可取回。"""

    def __init__(self) -> None:
        self.payloads: dict[str, list[dict[str, Any]]] = {}

    def emit(self, draft: LayerDraft) -> TemporalLayerProduct:
        self.payloads[draft.product_id] = draft.features
        return _product(
            draft,
            format="memory",
            ref=f"ref:simulation/{draft.product_id}",
        )


class GeoParquetTemporalSink:
    """GeoParquet 落盘汇；pyarrow 缺席或编码失败时诚实降级为内存。"""

    def __init__(self, output_dir) -> None:
        self.output_dir = Path(output_dir)
        self.payloads: dict[str, list[dict[str, Any]]] = {}

    def emit(self, draft: LayerDraft) -> TemporalLayerProduct:
        from app.services.data_fabric.vector_carrier import (
            arrow_available,
            features_to_arrow,
            table_to_geoparquet,
        )

        if not arrow_available():
            return self._degrade(
                draft,
                "pyarrow 未安装：GeoParquet 产物降级为内存记录"
                "（诚实降级，见 ADR-0192 D7）",
            )
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / f"{draft.product_id}.parquet"
            table = features_to_arrow(draft.features, crs=draft.crs)
            table_to_geoparquet(table, str(path))
        except Exception as exc:  # noqa: BLE001 — 降级必须留痕，不阻断推演
            logger.warning(
                "[simulation] geoparquet emission failed for %s: %s",
                draft.product_id, exc,
            )
            return self._degrade(
                draft,
                f"GeoParquet 写盘失败（{type(exc).__name__}），"
                "降级为内存记录",
            )
        return _product(
            draft,
            format="geoparquet",
            ref=f"ref:simulation/{draft.product_id}",
            uri=str(path),
        )

    def _degrade(self, draft: LayerDraft, note: str) -> TemporalLayerProduct:
        self.payloads[draft.product_id] = draft.features
        return _product(
            draft,
            format="memory",
            ref=f"ref:simulation/{draft.product_id}",
            degraded_note=note,
        )


def build_mapspec_bundle(result: "SimulationResult") -> dict[str, Any]:
    """多时相产物 → MapSpec v1.2 文档骨架（layout.frames 时间轴）。

    帧语义：每帧只点亮当期切片图层（layerOverrides deep-merge，前端
    frame-composer 直接可播）。超过 ``MAX_SPEC_FRAMES`` 抛配置错误——
    上游 :class:`SimulationRunConfig` 的帧数守卫保证此处不会触达。
    """
    products = result.products
    if len(products) > _MAPSPEC_MAX_FRAMES:
        raise SimulationConfigError(
            f"temporal slices ({len(products)}) exceed MapSpec frame cap "
            f"({_MAPSPEC_MAX_FRAMES})",
            context={"products": len(products)},
        )
    sources: dict[str, Any] = {}
    layers: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    for p in products:
        src_id = f"src-{p.product_id}"
        layer_id = f"layer-{p.product_id}"
        sources[src_id] = {
            "type": "geojson",
            "dataPath": p.uri or p.ref,
        }
        layers.append({
            "id": layer_id,
            "source": src_id,
            "type": "fill" if p.layer_kind == "raster_grid" else "line",
            "visible": False,
        })
        frames.append({
            "id": f"t{p.tick}",
            "title": p.timestamp or f"T+{p.sim_seconds:g}s",
            "layerOverrides": {layer_id: {"visible": True}},
        })
    return {
        "version": "1.2",
        "sources": sources,
        "layers": layers,
        "layout": {"frames": frames},
    }


__all__ = [
    "GeoParquetTemporalSink",
    "InMemoryTemporalSink",
    "LayerDraft",
    "TemporalSink",
    "build_mapspec_bundle",
]
