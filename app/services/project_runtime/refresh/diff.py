"""G10 UPD-02：版本差异判定（字段 / 要素 / 几何 / 栅格 / 覆盖范围）。

判定纪律（契约 UPD-02 / O02）：
- 精确 feature ID 对齐才给精确计数；无 ID 或只有行位置 → 拒绝按位置当身份，
  退化为整体内容变化 / 需要重算，并披露「无法逐要素对齐」。
- 几何按**已知 CRS** 做规范化后比较；不简化顶点、不设大容差；CRS 改变时
  几何值整体失义，不逐要素比（原始值与判定依据可在 snapshot 追溯）。
- 栅格按块/瓦片内容哈希判断候选范围；存在 stencil/kernel/全局统计/重采样/
  拓扑依赖时必须扩大依赖域（由 impact 层转保守整项重算）。
- 抽样差异只叫 sample/unknown，绝不证明「没有变化」；预算不足 →
  conservative full recompute，绝不 silently unchanged。
- 证据级别：ETag / 更新时间不构成精确内容版本，只能降证据级别。

本模块是纯函数（零 I/O），生产 reader 与测试 fixture 共用同一套判定代码。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.lib.data.fingerprints import (
    ChangeClass,
    FingerprintSet,
    canonical_dumps,
    canonical_fingerprint,
    classify_change,
)

from .contracts import (
    ChangeEvidence,
    DiffBudget,
    EvidenceLevel,
    FeatureCounts,
    FeatureIdKind,
    SourceDiff,
    SourceSnapshot,
)

#: 栅格方法依赖词表：任一命中即不能声称「改一个像元只影响同一块」。
_GLOBAL_RASTER_DEPENDENCIES = frozenset({
    "stencil", "kernel", "global_stats", "global_stat",
    "resample", "topology", "network", "classification_breaks", "kde_bandwidth",
})

#: 只影响呈现、不影响科学数值的维度（由 impact 层消费）
_STYLE_DIMENSION = "style"


def _split_type_unit(spec: str) -> Tuple[str, str]:
    """``"int|person"`` → ``("int", "person")``；无 ``|`` 时单位为空。"""
    head, sep, tail = str(spec or "").partition("|")
    return head.strip(), tail.strip() if sep else ""


def _geom_digest(geometry: Any, crs: str) -> Optional[str]:
    """按已知 CRS 规范化几何并取 canonical 指纹。

    方法声明：只做「空白去除 + 大小写归一 + canonical JSON 键排序」；
    **不简化顶点、不做坐标抽稀、不设容差**。CRS 缺失时不猜坐标系，
    但仍可在同 CRS 内比较原始值（并在调用方披露 crs 未知）。
    """
    if geometry is None:
        return None
    if isinstance(geometry, str):
        normalized = " ".join(geometry.strip().lower().split())
        return canonical_fingerprint({"crs": str(crs or ""), "wkt": normalized})
    if isinstance(geometry, dict):
        try:
            return canonical_fingerprint({"crs": str(crs or ""), "geojson": geometry})
        except (TypeError, ValueError):
            return None
    try:
        return canonical_fingerprint({"crs": str(crs or ""), "raw": canonical_dumps(geometry)})
    except (TypeError, ValueError):
        return None


def _props_digest(properties: Any) -> Optional[str]:
    try:
        return canonical_fingerprint(properties if properties is not None else {})
    except (TypeError, ValueError):
        return None


def _derive_roles(snapshot: SourceSnapshot, warnings: List[str]) -> List[str]:
    """来源 → 分析图 data role 节点。

    优先用读侧显式提供的 ``analysis_roles``；缺省时按 logical_id 末段作
    **确定性、可披露**的推导（不猜测语义，warnings 明示推导依据）。
    """
    if snapshot.analysis_roles:
        return list(dict.fromkeys(str(x) for x in snapshot.analysis_roles if x))[:16]
    tail = snapshot.logical_id.rsplit("://", 1)[-1].rstrip("/").rsplit("/", 1)[-1]
    tail = tail.split(".")[0] or "source"
    warnings.append(
        f"analysis role derived from logical_id '{snapshot.logical_id}' -> 'data:{tail}' "
        "(explicit analysis_roles not supplied)"
    )
    return [f"data:{tail}"]


def _approx_bytes(snapshot: SourceSnapshot) -> int:
    """有界估算（不序列化整个大对象；按字段数 × 上界）。"""
    n_features = len(snapshot.features)
    n_tiles = len(snapshot.raster_tiles)
    n_fields = len(snapshot.schema_fields)
    return (
        n_features * 512 + n_tiles * 64 + n_fields * 96
        + sum(len(str(k)) + len(str(v)) for k, v in list(snapshot.features.items())[:64])
    )


def _fingerprints_complete(s: SourceSnapshot) -> bool:
    fp = FingerprintSet.from_dict(s.fingerprints)
    return all(v is not None for v in (fp.content, fp.schema, fp.metadata, fp.crs))


def compute_source_diff(
    *,
    baseline: SourceSnapshot,
    current: SourceSnapshot,
    budget: Optional[DiffBudget] = None,
) -> SourceDiff:
    """基线 vs 当前 → 四态差异报告（纯函数）。

    ``baseline`` 是冻结/上次采纳时的来源快照，``current`` 是本次检查读到的
    快照；两者必须共享同一 ``logical_id``（逻辑身份由调用方保证）。
    """
    warnings: List[str] = []
    b = budget or DiffBudget()

    if baseline.logical_id != current.logical_id:
        raise ValueError("logical identity mismatch: refusing to compare two different sources")

    # ── 1. 指纹变更类别（单一事实源：app.lib.data.fingerprints） ──────
    old_fp = FingerprintSet.from_dict(baseline.fingerprints)
    new_fp = FingerprintSet.from_dict(current.fingerprints)
    change = classify_change(old_fp, new_fp)
    change_class = change.value

    exact_evidence_capable = (
        baseline.evidence is EvidenceLevel.CONTENT_EXACT
        and current.evidence is EvidenceLevel.CONTENT_EXACT
        and _fingerprints_complete(baseline)
        and _fingerprints_complete(current)
        and not baseline.truncated and not current.truncated
        and not baseline.sampled and not current.sampled
    )
    if not exact_evidence_capable:
        low = []
        if baseline.evidence is not EvidenceLevel.CONTENT_EXACT:
            low.append(f"baseline={baseline.evidence.value}")
        if current.evidence is not EvidenceLevel.CONTENT_EXACT:
            low.append(f"current={current.evidence.value}")
        if not (_fingerprints_complete(baseline) and _fingerprints_complete(current)):
            low.append("fingerprint dimensions incomplete")
        warnings.append(
            "evidence downgraded: " + ", ".join(low or ["sampled/truncated payload"])
            + "; exact content change is not provable"
        )

    # ── 2. 字段：增 / 删 / 类型 / 单位 ───────────────────────────────
    old_fields, new_fields = baseline.schema_fields, current.schema_fields
    fields_added = sorted(set(new_fields) - set(old_fields))
    fields_removed = sorted(set(old_fields) - set(new_fields))
    fields_type_changed: List[str] = []
    fields_unit_changed: List[str] = []
    for name in sorted(set(old_fields) & set(new_fields)):
        ot, ou = _split_type_unit(old_fields[name])
        nt, nu = _split_type_unit(new_fields[name])
        if ot != nt:
            fields_type_changed.append(name)
        if ou != nu:
            fields_unit_changed.append(name)

    if (fields_added or fields_removed or fields_type_changed or fields_unit_changed) \
            and change_class not in (ChangeClass.CRS.value, ChangeClass.SCHEMA.value):
        change_class = ChangeClass.SCHEMA.value

    # ── 3. 覆盖范围 / 分母 / 方法版本 / CRS ──────────────────────────
    crs_changed = bool(baseline.crs and current.crs and baseline.crs != current.crs) \
        or bool(baseline.crs != current.crs)
    bbox_changed = baseline.bbox != current.bbox
    temporal_changed = baseline.temporal_window != current.temporal_window
    denominator_changed = baseline.denominator != current.denominator
    method_version_changed = baseline.method_version != current.method_version

    # ── 4. 要素级精确对齐（仅稳定 ID + 完整证据 + 预算内） ───────────
    n_features = max(len(baseline.features), len(current.features))
    est_bytes = max(_approx_bytes(baseline), _approx_bytes(current),
                    baseline.available_bytes, current.available_bytes)
    within_budget = n_features <= b.max_features and est_bytes <= b.max_bytes
    stable_ids = (
        baseline.feature_id_kind is FeatureIdKind.STABLE
        and current.feature_id_kind is FeatureIdKind.STABLE
    )

    exact_counts: Optional[FeatureCounts] = None
    features_added: List[str] = []
    features_removed: List[str] = []
    features_attribute_changed: List[str] = []
    features_geometry_changed: List[str] = []
    alignment = "unavailable"
    evidence = ChangeEvidence.UNKNOWN

    if not stable_ids:
        alignment = ("positional_rejected" if FeatureIdKind.POSITIONAL in
                     (baseline.feature_id_kind, current.feature_id_kind) else "unavailable")
        warnings.append(
            "feature identity unavailable: stable feature id missing; "
            "row position is never used as identity; exact per-feature counts withheld"
        )
    if baseline.sampled or current.sampled:
        alignment = "sample"
        warnings.append("sample detected: sampled diff can never prove 'no change'")
    if baseline.truncated or current.truncated:
        warnings.append("truncated payload: diff is partial, exact counts withheld")
    if not within_budget:
        warnings.append(
            f"detection budget exceeded (features={n_features}/{b.max_features}, "
            f"bytes={est_bytes}/{b.max_bytes}); conservative full recompute required"
        )

    if stable_ids and exact_evidence_capable and within_budget \
            and not (baseline.sampled or current.sampled) \
            and not (baseline.truncated or current.truncated):
        alignment = "stable_id"
        evidence = ChangeEvidence.EXACT
        old_ids, new_ids = set(baseline.features), set(current.features)
        features_added = sorted(new_ids - old_ids)
        features_removed = sorted(old_ids - new_ids)
        # CRS 改变时几何值整体失义 —— 不把坐标系差异误报成逐要素几何变化
        geometries_comparable = not crs_changed
        for fid in sorted(old_ids & new_ids):
            o, n = baseline.features[fid], current.features[fid]
            if _props_digest(o.get("properties")) != _props_digest(n.get("properties")):
                features_attribute_changed.append(fid)
            if geometries_comparable and \
                    _geom_digest(o.get("geometry"), baseline.crs) != \
                    _geom_digest(n.get("geometry"), current.crs):
                features_geometry_changed.append(fid)
        exact_counts = FeatureCounts(
            added=len(features_added),
            removed=len(features_removed),
            attribute_changed=len(features_attribute_changed),
            geometry_changed=len(features_geometry_changed),
        )
        if any((features_added, features_removed,
                features_attribute_changed, features_geometry_changed)) \
                and change_class == ChangeClass.NONE.value:
            change_class = ChangeClass.CONTENT.value
    else:
        # 无法逐要素对齐：整体内容变化或未知，绝不伪造精确计数
        evidence = (
            ChangeEvidence.CONSERVATIVE
            if change_class in (
                ChangeClass.CONTENT.value, ChangeClass.SCHEMA.value,
                ChangeClass.CRS.value,
            ) else ChangeEvidence.UNKNOWN
        )
        if change_class == ChangeClass.NONE.value:
            change_class = ChangeClass.UNKNOWN.value
            warnings.append("no-change claim withheld: alignment/evidence insufficient")

    if change_class == ChangeClass.UNKNOWN.value and evidence is ChangeEvidence.EXACT:
        evidence = ChangeEvidence.UNKNOWN

    # ── 5. 栅格块指纹与依赖域扩展 ───────────────────────────────────
    old_tiles, new_tiles = baseline.raster_tiles, current.raster_tiles
    raster_tiles_changed = sorted(
        k for k in set(old_tiles) | set(new_tiles)
        if old_tiles.get(k) != new_tiles.get(k)
    )
    deps = [str(d) for d in (current.raster_dependencies or baseline.raster_dependencies)]
    expanded = sorted({d for d in deps if d in _GLOBAL_RASTER_DEPENDENCIES})
    if expanded:
        warnings.append(
            "raster method dependencies " + ", ".join(expanded)
            + " invalidate block-local assumption; dependency domain expanded"
        )

    # ── 6. 影响图种子目标（由 diff 事实推导，不猜测分析图结构） ─────
    roles = _derive_roles(baseline, warnings)
    seed_targets: List[str] = []
    scope_change = crs_changed or bbox_changed or temporal_changed or method_version_changed
    content_change = bool(
        exact_counts and any((
            exact_counts.added, exact_counts.removed,
            exact_counts.attribute_changed, exact_counts.geometry_changed,
        ))
    ) or bool(raster_tiles_changed) or bool(expanded) or bool(fields_added or fields_removed)
    pure_denominator = denominator_changed and not (content_change or scope_change or
                                                    fields_unit_changed or fields_type_changed)

    if scope_change or expanded:
        seed_targets.append("recipe")   # 全图失效（保守正确）
    elif pure_denominator:
        seed_targets.extend(baseline.denominator_nodes or ["analysis:normalized"])
    else:
        if content_change:
            seed_targets.extend(roles)
        if fields_unit_changed or fields_type_changed:
            seed_targets.extend(baseline.denominator_nodes or ["analysis:normalized"])
    seed_targets = list(dict.fromkeys(seed_targets))[:16]

    reason_bits = [
        f"change_class={change_class}", f"evidence={evidence.value}",
        f"alignment={alignment}",
    ]
    if exact_counts:
        reason_bits.append(
            f"counts=+{exact_counts.added}/-{exact_counts.removed}/"
            f"attr{exact_counts.attribute_changed}/geom{exact_counts.geometry_changed}"
        )
    if expanded:
        reason_bits.append("expanded=" + ",".join(expanded))

    return SourceDiff(
        logical_id=baseline.logical_id,
        change_class=change_class,
        evidence=evidence,
        alignment=alignment,
        exact_counts=exact_counts,
        fields_added=fields_added[:256],
        fields_removed=fields_removed[:256],
        fields_type_changed=fields_type_changed[:256],
        fields_unit_changed=fields_unit_changed[:256],
        features_added=features_added[:256],
        features_removed=features_removed[:256],
        features_attribute_changed=features_attribute_changed[:256],
        features_geometry_changed=features_geometry_changed[:256],
        crs_changed=bool(crs_changed),
        bbox_changed=bool(bbox_changed),
        temporal_changed=bool(temporal_changed),
        denominator_changed=bool(denominator_changed),
        method_version_changed=bool(method_version_changed),
        raster_tiles_changed=raster_tiles_changed[:256],
        expanded_dependencies=expanded,
        seed_targets=seed_targets,
        warnings=warnings[:16],
        truncated=bool(baseline.truncated or current.truncated or not within_budget),
        sampled=bool(baseline.sampled or current.sampled),
        reason="; ".join(reason_bits),
    )


__all__ = ["compute_source_diff", "_geom_digest", "_GLOBAL_RASTER_DEPENDENCIES"]
