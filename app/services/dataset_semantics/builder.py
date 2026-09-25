"""Dataset Semantics Builder —— 推导边界上的 descriptor 铸造（ADR-0215 D1/D6）。

职责边界（不越界）：

- 推导委托既有实现：角色 → ``semantic_profile.derive_semantic_profile``（ADR-0092），
  量纲 → ``measurement.derive_measurement_profile``（ADR-0207，#1488）—— 本模块
  **不复制任何推导规则**，只做有界采样供给 + 结果装配；
- 采样有界且确定：first-N 要素窗口（SAMPLING_FEATURE_CAP），每字段
  ≤MAX_VALUE_SAMPLES（与 #1488 同值同源）—— 正向路径零新增全表扫描；
- descriptor 载荷不携带值样本（只携带采样证据），构建后立即
  ``with_fingerprints()`` —— 同输入恒同指纹。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.lib.gis.dataset_descriptor import (
    DESCRIPTOR_VERSION,
    FieldEntry,
    GISDatasetDescriptor,
    RasterShape,
    SamplingEvidence,
    SourceRef,
    TemporalSemantics,
)
from app.lib.gis.dataset_profile import DatasetProfile, MAX_PROFILE_FIELDS
from app.lib.gis.measurement import (
    MAX_VALUE_SAMPLES,
    derive_measurement_profile,
)
from app.lib.gis.semantic_profile import (
    SemanticDatasetProfile,
    derive_semantic_profile,
)

#: 采样窗口（first-N 要素；有界且确定性 —— ingest/mapspec 正向路径共用）。
SAMPLING_FEATURE_CAP = 200
#: 参与值采样的数值字段上限（防宽表采样放大）。
SAMPLING_FIELD_CAP = 16
#: descriptor 载荷字节上限（store 层强制；契约层同步声明）。
MAX_DESCRIPTOR_BYTES = 96 * 1024

_TIME_DTYPES = ("date", "datetime", "timestamp", "time")


def bounded_value_samples(
    features: Optional[Iterable[Any]],
    *,
    numeric_fields: Optional[Sequence[str]] = None,
    feature_cap: int = SAMPLING_FEATURE_CAP,
    field_cap: int = SAMPLING_FIELD_CAP,
    per_field_cap: int = MAX_VALUE_SAMPLES,
) -> Dict[str, List[Any]]:
    """FC features → 有界值样本（确定性 first-N；先过滤后限额）。

    - 只看前 ``feature_cap`` 个要素（O(cap) 非全表）；
    - 数值字段按给定清单取前 ``field_cap`` 个（宽表不放大采样面）；
    - 字符串字段只对前 ``field_cap`` 个字段采样（供 category/temporal 印证）；
    - None/NaN 不占预算（与 measurement._finite_samples 同纪律）。
    """
    if not features:
        return {}
    sampled_fields: List[str] = []
    if numeric_fields:
        sampled_fields = [str(f) for f in numeric_fields][:field_cap]
    else:
        first = next(iter(features), None)
        props = getattr(first, "properties", None) or (
            first.get("properties") if isinstance(first, dict) else None
        )
        if isinstance(props, dict):
            sampled_fields = [str(k) for k in list(props.keys())[:field_cap]]
    out: Dict[str, List[Any]] = {f: [] for f in sampled_fields}
    seen = 0
    for feat in features:
        if seen >= feature_cap:
            break
        seen += 1
        props = getattr(feat, "properties", None) or (
            feat.get("properties") if isinstance(feat, dict) else None
        )
        if not isinstance(props, dict):
            continue
        for name in sampled_fields:
            bucket = out[name]
            if len(bucket) >= per_field_cap:
                continue
            v = props.get(name)
            if v is None:
                continue
            if isinstance(v, float) and not math.isfinite(v):
                continue
            bucket.append(v)
    return {k: v for k, v in out.items() if v}


def _merge_entries(
    profile: DatasetProfile,
    semantic: Optional[SemanticDatasetProfile],
) -> Dict[str, FieldEntry]:
    entries: Dict[str, FieldEntry] = {}
    for name, dtype in list((profile.fields or {}).items())[:MAX_PROFILE_FIELDS]:
        entries[str(name)] = FieldEntry(
            name=str(name)[:128],
            dtype=str(dtype or ""),
            null_ratio=profile.null_ratios.get(str(name)),
        )
    if semantic is not None:
        for a in (semantic.field_roles or [])[:MAX_PROFILE_FIELDS]:
            entry = entries.get(str(a.field))
            if entry is None:
                continue      # 角色清单不得发明 profile 没有的字段
            entry.roles = [str(r) for r in (a.roles or [])]
            entry.kind_confidence = str(a.confidence or "")
    return entries


def _overlay_measurement(
    entries: Dict[str, FieldEntry],
    measurement: Any,
) -> None:
    if measurement is None:
        return
    for fs in (measurement.fields or [])[:MAX_PROFILE_FIELDS]:
        entry = entries.get(str(fs.field))
        if entry is None:
            continue
        entry.measurement_kind = str(fs.measurement_kind or "")
        entry.unit_dimension = str(fs.unit_dimension or "")
        entry.unit = str(fs.unit or "")
        entry.kind_confidence = str(fs.kind_confidence or "")
        entry.unit_confidence = str(fs.unit_confidence or "")
        entry.domain_hint = list(fs.domain_hint) if fs.domain_hint else None
        entry.center_hint = fs.center_hint
        entry.evidence = [str(e) for e in (fs.evidence or [])]
        entry.checks = [dict(c) for c in (fs.checks or [])]
        if entry.unit and entry.unit_dimension == "" :
            # 兜底：unit 在场而维度缺席 —— 从 CANONICAL_UNITS 补（单一注册表）。
            from app.lib.gis.measurement import CANONICAL_UNITS

            unit_entry = CANONICAL_UNITS.get(entry.unit)
            if unit_entry is not None:
                entry.unit_dimension = unit_entry.dimension.value


def _nullability(entries: Dict[str, FieldEntry], profile: DatasetProfile) -> None:
    """null 证据 → nullable（三态：True/False/None=无证据）。"""
    for name, entry in entries.items():
        ratio = profile.null_ratios.get(name)
        if ratio is None:
            continue
        if entry.nullable is None:
            entry.nullable = bool(ratio > 0.0)


def build_descriptor(
    profile: DatasetProfile,
    *,
    semantic: Optional[SemanticDatasetProfile] = None,
    measurement: Any = None,
    dataset_key: str = "",
    source_refs: Optional[List[SourceRef]] = None,
    provenance: Optional[List[Dict[str, str]]] = None,
    sampling: Optional[SamplingEvidence] = None,
    quality_signals: Optional[List[str]] = None,
    coverage_start: str = "",
    coverage_end: str = "",
    granularity: str = "",
    derived_at: str = "",
) -> GISDatasetDescriptor:
    """DatasetProfile ⊕ Semantic ⊕ Measurement → GISDatasetDescriptor（纯装配）。

    本函数不做任何推导与 I/O；采样/推导由调用方（derive_descriptor 系列）
    完成后传入。输出已填指纹。
    """
    entries = _merge_entries(profile, semantic)
    _overlay_measurement(entries, measurement)
    _nullability(entries, profile)

    kind = "unknown"
    if profile.raster is not None or "raster" in (profile.geometry_types or []):
        kind = "raster"
    elif profile.geometry_types:
        kind = "vector"
    elif profile.fields and profile.geometry_types == []:
        kind = "table"

    raster_shape: Optional[RasterShape] = None
    if profile.raster is not None:
        raster_shape = RasterShape(
            width=profile.raster.width,
            height=profile.raster.height,
            band_count=profile.raster.band_count,
            nodata=profile.raster.nodata,
            pixel_size=profile.raster.pixel_size,
            dtype=str(profile.raster.dtype or ""),
        )

    has_time = profile.has_time_field
    time_field = str(profile.time_field or "")
    if not time_field and has_time and semantic is not None:
        for a in semantic.field_roles:
            if "temporal_dimension" in (a.roles or []):
                time_field = str(a.field)
                break

    temporal = TemporalSemantics(
        has_time_field=has_time,
        time_field=time_field[:MAX_PROFILE_FIELDS],
        coverage_start=str(coverage_start or "")[:64],
        coverage_end=str(coverage_end or "")[:64],
        granularity=str(granularity or "")[:32],
        observation_count=profile.temporal_observation_count,
    )

    lon_convention = ""
    crosses = None
    if isinstance(profile.longitude_facts, dict):
        lon_convention = str(profile.longitude_facts.get("convention") or "")[:16]
        ca = profile.longitude_facts.get("crosses_antimeridian")
        crosses = bool(ca) if isinstance(ca, bool) else None

    null_ratio_max: Optional[float] = None
    if profile.null_ratios:
        try:
            null_ratio_max = round(max(profile.null_ratios.values()), 6)
        except (TypeError, ValueError):
            null_ratio_max = None

    descriptor = GISDatasetDescriptor(
        descriptor_version=DESCRIPTOR_VERSION,
        dataset_key=str(dataset_key or "")[:200],
        kind=kind,
        artifact_type=str(profile.artifact_type or ""),
        geometry_types=list(profile.geometry_types or []),
        feature_count=profile.feature_count,
        bbox=list(profile.bbox) if profile.bbox else None,
        crs=str(profile.crs or ""),
        raster=raster_shape,
        fields=list(entries.values())[:MAX_PROFILE_FIELDS],
        numeric_fields=list(profile.numeric_fields or []),
        categorical_fields=list(profile.categorical_fields or []),
        binary_fields=list(profile.binary_fields or []),
        temporal=temporal,
        source_refs=list(source_refs or [])[:8],
        null_ratio_max=null_ratio_max,
        value_variance=profile.value_variance,
        duplicate_coordinate_count=profile.duplicate_coordinate_count,
        unique_coordinate_count=profile.unique_coordinate_count,
        longitude_convention=lon_convention,
        crosses_antimeridian=crosses,
        quality_signals=[str(s) for s in (quality_signals or [])],
        sampling=sampling or SamplingEvidence(),
        provenance=list(provenance or [])[:8],
        derived_at=str(derived_at or "")[:64],
    )
    return descriptor.with_fingerprints()


def derive_descriptor(
    profile: DatasetProfile,
    *,
    dataset_key: str = "",
    features: Optional[Iterable[Any]] = None,
    value_samples: Optional[Dict[str, Sequence[Any]]] = None,
    unit_overrides: Optional[Dict[str, str]] = None,
    source_refs: Optional[List[SourceRef]] = None,
    provenance: Optional[List[Dict[str, str]]] = None,
    coverage_start: str = "",
    coverage_end: str = "",
    granularity: str = "",
    derived_at: str = "",
    quality_signals: Optional[List[str]] = None,
) -> GISDatasetDescriptor:
    """采样 → 语义/量纲推导（委托 #1488）→ descriptor 铸造（正向路径主入口）。

    ``features``（可选）优先于 ``value_samples``：先做有界 first-N 采样，
    再交给既有 derive_semantic_profile / derive_measurement_profile。
    """
    samples: Dict[str, List[Any]] = {}
    sampling = SamplingEvidence()
    if features is not None:
        numeric_hint = [n for n, t in (profile.fields or {}).items()
                        if str(t) in _NUMERIC_HINT_DTYPES]
        samples = bounded_value_samples(
            features, numeric_fields=numeric_hint or None)
        sampled_values = max((len(v) for v in samples.values()), default=0)
        sampling = SamplingEvidence(
            strategy="first_n_features",
            feature_cap=SAMPLING_FEATURE_CAP,
            fields_capped=bool((profile.fields or {}) and
                               len(profile.fields) >= MAX_PROFILE_FIELDS),
            fields_explicit=(profile.fields_status == "explicit"),
            samples_per_field=MAX_VALUE_SAMPLES,
            notes=[f"numeric_fields_sampled<={len(samples)}",
                   f"max_values_per_field<={sampled_values}"],
        )
    elif value_samples:
        samples = {str(k): list(v)[:MAX_VALUE_SAMPLES]
                   for k, v in value_samples.items() if v}
        sampling = SamplingEvidence(
            strategy="caller_supplied",
            fields_explicit=(profile.fields_status == "explicit"),
            samples_per_field=MAX_VALUE_SAMPLES,
        )
    else:
        sampling = SamplingEvidence(
            strategy="descriptor_projection",
            fields_explicit=(profile.fields_status == "explicit"),
        )

    semantic = derive_semantic_profile(profile, value_samples=samples)
    measurement = derive_measurement_profile(
        profile, semantic, value_samples=samples,
        unit_overrides=unit_overrides,
    )
    return build_descriptor(
        profile,
        semantic=semantic,
        measurement=measurement,
        dataset_key=dataset_key,
        source_refs=source_refs,
        provenance=provenance,
        sampling=sampling,
        quality_signals=quality_signals,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        granularity=granularity,
        derived_at=derived_at,
    )


_NUMERIC_HINT_DTYPES = ("number", "integer", "float", "double", "int")


def descriptor_payload_bytes(descriptor: GISDatasetDescriptor) -> int:
    """canonical 载荷字节数（store 层上限判定的单一口径）。"""
    from app.lib.data.fingerprints import canonical_dumps

    return len(canonical_dumps(descriptor.to_dict()).encode("utf-8"))


__all__ = [
    "SAMPLING_FEATURE_CAP",
    "SAMPLING_FIELD_CAP",
    "MAX_VALUE_SAMPLES",
    "MAX_DESCRIPTOR_BYTES",
    "bounded_value_samples",
    "build_descriptor",
    "derive_descriptor",
    "descriptor_payload_bytes",
]
