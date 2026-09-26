"""Descriptor Projections —— GISDatasetDescriptor 的单向投影出口（ADR-0215 D2）。

纪律（不可妥协）：

- **单向**：只允许 descriptor → 既有形状；任何调用方不得把原始数据重新
  "猜"成 descriptor 已有的语义（禁止第二推导面）；
- **委托而非复制**：resolver camelCase 词表的唯一出口仍是
  ``DatasetProfile.to_resolver_profile``（V4 完整事实词表，ADR-0104）——
  本模块重建 DatasetProfile 后直接复用它，不产生第二个 camelCase 词表；
  measurement 重建复用 ``DatasetMeasurementProfile``；D1 视图产出
  ``D1DatasetDescriptor`` 兼容 kwargs（contracts.py 冻结面零改动）；
- **投影等价**：同一 descriptor 重建的 DatasetProfile 的 resolver 投影与
  构建时源 profile 的 resolver 投影逐键相等（V2 验收矩阵锁定）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lib.gis.dataset_descriptor import (
    FieldEntry,
    GISDatasetDescriptor,
)
from app.lib.gis.dataset_profile import DatasetProfile, RasterProfile


def _entry_dtype(entry: FieldEntry) -> str:
    return str(entry.dtype or "unknown")


def descriptor_to_dataset_profile(descriptor: GISDatasetDescriptor) -> DatasetProfile:
    """descriptor → DatasetProfile（消费面：qualification/symbology/resolver）。

    fields_status 还原规则：sampling.fields_explicit=True（构建时源 profile
    声明过权威 schema）→ "explicit"，否则 "unknown" —— 诚实降级（投影不
    放大证据）。
    """
    fields: Dict[str, str] = {}
    null_ratios: Dict[str, float] = {}
    time_field = ""
    for entry in descriptor.fields:
        name = entry.name
        fields[name] = _entry_dtype(entry)
        if entry.null_ratio is not None:
            null_ratios[name] = float(entry.null_ratio)
        if "temporal_dimension" in entry.roles and not time_field:
            time_field = name
    # 清单成员是构建期显式证据的冻结投影 —— 直接还原，不从 dtype 重推
    # （dtype 与清单是两路证据；重推会放大证据，见 ADR-0215 D2）。
    numeric = [str(x) for x in descriptor.numeric_fields]
    categorical = [str(x) for x in descriptor.categorical_fields]
    binary = [str(x) for x in descriptor.binary_fields]

    raster_profile: Optional[RasterProfile] = None
    if descriptor.raster is not None:
        raster_profile = RasterProfile(
            width=descriptor.raster.width,
            height=descriptor.raster.height,
            band_count=descriptor.raster.band_count,
            nodata=descriptor.raster.nodata,
            pixel_size=descriptor.raster.pixel_size,
            dtype=str(descriptor.raster.dtype or ""),
        )

    has_time: Optional[bool] = descriptor.temporal.has_time_field

    longitude_facts: Optional[Dict[str, Any]] = None
    if descriptor.longitude_convention:
        longitude_facts = {"convention": descriptor.longitude_convention}
        if descriptor.crosses_antimeridian is not None:
            longitude_facts["crosses_antimeridian"] = bool(
                descriptor.crosses_antimeridian)

    return DatasetProfile(
        source="ref_descriptor",
        artifact_type=str(descriptor.artifact_type or ""),
        feature_count=descriptor.feature_count,
        geometry_types=list(descriptor.geometry_types),
        bbox=list(descriptor.bbox) if descriptor.bbox else None,
        crs=str(descriptor.crs or ""),
        fields=fields,
        numeric_fields=numeric,
        categorical_fields=categorical,
        binary_fields=binary,
        null_ratios=null_ratios,
        estimated_bytes=None,     # 载荷体积不属于语义契约（不虚构）
        fields_status=("explicit" if descriptor.sampling.fields_explicit else "unknown"),
        raster=raster_profile,
        time_field=time_field,
        has_time_field=has_time,
        temporal_observation_count=descriptor.temporal.observation_count,
        value_variance=descriptor.value_variance,
        duplicate_coordinate_count=descriptor.duplicate_coordinate_count,
        unique_coordinate_count=descriptor.unique_coordinate_count,
        longitude_facts=longitude_facts,
    )


def descriptor_resolver_profile(descriptor: GISDatasetDescriptor) -> Dict[str, Any]:
    """descriptor → resolver camelCase 事实词表（经 DatasetProfile 唯一出口）。"""
    return descriptor_to_dataset_profile(descriptor).to_resolver_profile()


def descriptor_to_measurement_profile(descriptor: GISDatasetDescriptor):
    """descriptor → DatasetMeasurementProfile（#1488 契约的重建投影）。

    只重建「构建时真的推导过量纲」的字段（measurement_kind 非空）——
    不为无证据字段虚构 FieldSemantics（与 derive_measurement_profile 的
    输出域一致：无 kind 字段仍会有 entries，但其 kind="" —— 这里保留
    同一形状，让消费方 by_field 语义一致）。
    """
    from app.lib.gis.measurement import FieldSemantics, DatasetMeasurementProfile

    out: List[FieldSemantics] = []
    for entry in descriptor.fields:
        out.append(FieldSemantics(
            field=entry.name,
            measurement_kind=entry.measurement_kind,
            unit_dimension=entry.unit_dimension,
            unit=entry.unit,
            kind_confidence=entry.kind_confidence or "unknown",
            unit_confidence=entry.unit_confidence or "unknown",
            domain_hint=list(entry.domain_hint) if entry.domain_hint else None,
            center_hint=entry.center_hint,
            evidence=list(entry.evidence),
            checks=[dict(c) for c in entry.checks],
        ))
    return DatasetMeasurementProfile(fields=out)


def descriptor_semantic_view(descriptor: GISDatasetDescriptor) -> Dict[str, Any]:
    """descriptor → 有界语义视图（角色/量纲证据的只读消费面）。

    消费方：template applicability / cartographic grammar / goal satisfaction
    等需要「字段角色+量纲」而不需要完整 profile 的调用点。值域复用既有
    词表（SemanticFieldRole / MeasurementKind / UnitDimension 的冻结字符串）。
    """
    fields: List[Dict[str, Any]] = []
    for entry in descriptor.fields:
        if not (entry.roles or entry.measurement_kind or entry.unit):
            continue      # 无语义证据的字段不进入语义视图（诚实缺席）
        fields.append({
            "field": entry.name,
            "roles": list(entry.roles),
            "measurement_kind": entry.measurement_kind,
            "unit_dimension": entry.unit_dimension,
            "unit": entry.unit,
            "kind_confidence": entry.kind_confidence,
            "unit_confidence": entry.unit_confidence,
            "domain_hint": list(entry.domain_hint) if entry.domain_hint else None,
            "center_hint": entry.center_hint,
        })
    return {
        "descriptor_version": descriptor.descriptor_version,
        "descriptor_fingerprint": descriptor.descriptor_fingerprint,
        "schema_fingerprint": descriptor.schema_fingerprint,
        "kind": descriptor.kind,
        "crs": descriptor.crs,
        "fields": fields,
        "temporal": descriptor.temporal.model_dump(),
        "quality_signals": list(descriptor.quality_signals),
    }


def descriptor_to_d1_kwargs(descriptor: GISDatasetDescriptor) -> Dict[str, Any]:
    """descriptor → D1DatasetDescriptor 兼容 kwargs（supply-side 投影）。

    D1 契约（data_fabric/contracts.py，ADR-0170）冻结面零改动：本函数只
    产出其既有字段名；调用方 ``D1DatasetDescriptor(**kwargs)`` 或经
    ``from_fabric_descriptor`` 消费。descriptor 侧没有的证据（license/
    freshness/cost）如实缺席 —— 绝不虚构 declared_only=False。
    """
    temporal = descriptor.temporal
    kwargs: Dict[str, Any] = {
        "geometry_type": (descriptor.geometry_types[0]
                          if descriptor.geometry_types else "Unknown"),
        "feature_type": "raster" if descriptor.kind == "raster" else "vector",
        "crs": descriptor.crs or None,
        "srs": descriptor.crs or None,
        "bbox": list(descriptor.bbox) if descriptor.bbox else None,
        "feature_count": descriptor.feature_count,
        "schema_fields": {
            entry.name: _entry_dtype(entry) for entry in descriptor.fields
        },
        "contract_version": "1.0",
    }
    if temporal.coverage_start or temporal.coverage_end or temporal.granularity:
        kwargs["temporal_coverage"] = {
            "start": temporal.coverage_start or None,
            "end": temporal.coverage_end or None,
            "granularity": temporal.granularity or None,
            # descriptor 的时间证据来自实测 profile（非源声明）。
            "declared_only": False,
        }
        kwargs["granularity"] = temporal.granularity or None
    quality_signals: Dict[str, Any] = {"declared_crs": descriptor.crs or None}
    if descriptor.null_ratio_max is not None:
        # 完整度代理：1 - 最大 null 率（有界 [0,1]；无 null 证据时缺席）。
        try:
            quality_signals["declared_completeness"] = round(
                max(0.0, min(1.0, 1.0 - float(descriptor.null_ratio_max))), 6)
        except (TypeError, ValueError):
            pass
    if descriptor.quality_signals:
        quality_signals["known_issues"] = list(descriptor.quality_signals)
    kwargs["quality_signals"] = quality_signals
    return kwargs


__all__ = [
    "descriptor_to_dataset_profile",
    "descriptor_resolver_profile",
    "descriptor_to_measurement_profile",
    "descriptor_semantic_view",
    "descriptor_to_d1_kwargs",
]
