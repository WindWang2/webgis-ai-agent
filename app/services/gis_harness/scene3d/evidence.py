"""垂直证据核实（G3D04 M3）——只认真实数据事实，零编造。

证据源（全部服务端权威状态，非话语/猜测）：

- **elevation_dem**：MapSpec ``sources`` 中已注册的 ``raster-dem`` 源
  （``RasterDemMapSpecSource``）；scene.terrain.source 必须指向其中之一。
- **height_attribute**：MapSpec ``layers[*].extrusion.stats`` 的真实采样
  结论（``valid > 0`` 即存在数值有效高度样本——ADR-0199 extrusion_model
  的写入面）；或调用方提供的已核实采样结论（typed 入参，不重算）。

红线：本模块不读取话语、不推断、不默认 True；证据核实的责任链是
"数据面写入 → 本模块只读核实"。session ref 面的 raster 注册回执
（ref:raster-*）由 mapspec_source.store_data 写入 sources，经同一
mapspec 通道核实，不开第二真相。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.requirement_ir.contracts import VerticalEvidenceRecord

#: mapspec 源字典的 raster-dem 类型字面值（mapspec_schema.RasterDemMapSpecSource）。
_RASTER_DEM_TYPE = "raster-dem"


class EvidenceBundle(BaseModel):
    """一次核实结论（elevation/height 两通道，bounded ≤8/通道）。"""

    model_config = {"extra": "forbid"}

    elevation: List[VerticalEvidenceRecord] = Field(default_factory=list, max_length=8)
    height_attribute: List[VerticalEvidenceRecord] = Field(
        default_factory=list, max_length=8)

    @property
    def has_elevation(self) -> bool:
        return any(e.verified for e in self.elevation)

    @property
    def has_height(self) -> bool:
        return any(e.verified for e in self.height_attribute)

    def dem_source_ids(self) -> List[str]:
        """已核实的 raster-dem 源 id 列表（scene.terrain.source 绑定候选）。"""
        return [e.ref for e in self.elevation if e.verified and e.ref]

    def to_records(self) -> List[VerticalEvidenceRecord]:
        return [*self.elevation, *self.height_attribute]


def verify_vertical_evidence(
    mapspec: Optional[Dict[str, Any]],
    *,
    geometry_kinds: Optional[List[str]] = None,
    extra_height_evidence: Optional[List[Dict[str, Any]]] = None,
) -> EvidenceBundle:
    """从当前 MapSpec（权威 spec dict）核实垂直证据（纯读，零副作用）。

    Args:
        mapspec: 会话当前 MapSpec dict（``mapspec_store.get_mapspec`` 产物）；
            ``None``/空 → 两通道全空（fail-closed）。
        geometry_kinds: 当前图层数据几何类型（polygon 与否影响挤出资格，
            由决策核消费；此处仅透传核实结论的 detail）。
        extra_height_evidence: 调用方已核实的补充高度采样结论
            （``[{ref, valid, detail}]``——如 extrusion 工具 stats 的调用侧
            汇总）；``valid>0`` 才记 verified。

    Returns:
        :class:`EvidenceBundle`——只含已核实事实；无证据不造默认。
    """
    bundle = EvidenceBundle()
    for item in (extra_height_evidence or [])[:8]:
        if not isinstance(item, dict):
            continue
        try:
            valid = int(item.get("valid") or 0)
        except (TypeError, ValueError):
            continue
        if valid > 0:
            bundle.height_attribute.append(VerticalEvidenceRecord(
                kind="height_attribute",
                ref=str(item.get("ref") or "")[:128],
                verified=True,
                detail=str(item.get("detail") or "")[:200],
            ))
    if not isinstance(mapspec, dict):
        return bundle

    sources = mapspec.get("sources")
    if isinstance(sources, dict):
        for source_id, source in sources.items():
            if not isinstance(source, dict):
                continue
            if source.get("type") == _RASTER_DEM_TYPE and source.get("url"):
                bundle.elevation.append(VerticalEvidenceRecord(
                    kind="elevation_dem",
                    ref=str(source_id)[:128],
                    verified=True,
                    detail=f"raster-dem source registered (encoding="
                           f"{source.get('encoding') or 'default'})",
                ))
                if len(bundle.elevation) >= 8:
                    break

    layers = mapspec.get("layers")
    if isinstance(layers, list):
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            extrusion = layer.get("extrusion")
            if not isinstance(extrusion, dict):
                continue
            stats = extrusion.get("stats")
            valid = 0
            if isinstance(stats, dict):
                try:
                    valid = int(stats.get("valid") or 0)
                except (TypeError, ValueError):
                    valid = 0
            if valid > 0:
                bundle.height_attribute.append(VerticalEvidenceRecord(
                    kind="height_attribute",
                    ref=str(layer.get("id") or "")[:128],
                    verified=True,
                    detail=f"height_field={extrusion.get('height_field')!r} "
                           f"valid_samples={valid}"
                           + (f" elevation_ref={extrusion['elevation_ref']}"
                              if extrusion.get("elevation_ref") else ""),
                ))
                if len(bundle.height_attribute) >= 8:
                    break
    return bundle
