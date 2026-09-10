"""Workflow Runtime V5 —— 运行时 typed port 校验（Binding Gate）。

编译期 ``ports_compatible`` 只裁决类型声明；本模块对**实际 artifact
descriptor** 逐维度校验 TypedPort 契约（架构 §5）。单一事实源纪律：

- artifact 类型词表/几何族 ← ArtifactTypeRegistry；
- CRS 兼容 ← ``crs_safety.crs_class_allows``（resolver 硬门同谓词）；
- 科学义务复验 ← data_qualification 既有谓词（不新造科学语义）；
- descriptor 缺键 = 诚实缺证（unknown 放行并披露），绝不虚构违规。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.workflow_runtime.contracts import (
    BindingVerdict,
    PortViolation,
)

#: 几何类型词 → 几何族（descriptor geometry_types 投影）。
_GEOMETRY_FAMILY: Dict[str, str] = {
    "point": "point", "multipoint": "point",
    "linestring": "line", "multilinestring": "line",
    "polygon": "polygon", "multipolygon": "polygon",
}

#: descriptor 几何族 → artifact category 的 feature_set 判定所需的族集合。
_ANY_FEATURE_CATEGORIES = frozenset({"feature_set"})


def _artifact_desc(artifact_type: str):
    from app.lib.gis.artifacts import get_artifact_type_registry

    reg = get_artifact_type_registry()
    return reg.get(artifact_type) if artifact_type and hasattr(reg, "get") else None


def _descriptor_geometry_family(descriptor: Dict[str, Any]) -> str:
    """live descriptor → 几何族（geometry_types 主族；raster → raster）。"""
    if not isinstance(descriptor, dict):
        return "unknown"
    if descriptor.get("raster_capable"):
        return "raster"
    types = descriptor.get("geometry_types") or []
    for t in types:
        fam = _GEOMETRY_FAMILY.get(str(t).lower().replace("st_", ""))
        if fam:
            return fam
    if types:
        return "table"
    if descriptor.get("feature_count"):
        return "unknown"
    return "unknown"


def verify_port(
    port: Any,
    descriptor: Optional[Dict[str, Any]],
    *,
    port_name: str = "",
) -> List[PortViolation]:
    """单端口校验：实际 descriptor × TypedPort → 违规列表（空 = PASS）。

    缺 descriptor（required 端口）→ MISSING_INPUT；optional 端口缺席不违规。
    """
    violations: List[PortViolation] = []
    name = port_name or str(getattr(port, "name", "") or "input")
    required = bool(getattr(port, "required", True))

    if not isinstance(descriptor, dict) or not descriptor:
        if required:
            violations.append(PortViolation(
                port=name, code="MISSING_INPUT",
                detail="required 端口无 artifact descriptor"))
        return violations

    # 1) artifact_type：注册表成员 + 相等（宽端口接受任意 feature_set 类）
    want_type = str(getattr(port, "artifact_type", "") or "")
    got_type = str(descriptor.get("artifact_type") or "")
    if want_type:
        want_desc = _artifact_desc(want_type)
        if want_desc is None:
            violations.append(PortViolation(
                port=name, code="ARTIFACT_TYPE_UNKNOWN",
                detail=f"端口要求的类型 {want_type!r} 不在 ArtifactTypeRegistry"))
        else:
            got_desc = _artifact_desc(got_type) if got_type else None
            if got_type and got_desc is None:
                violations.append(PortViolation(
                    port=name, code="ARTIFACT_TYPE_UNKNOWN",
                    detail=f"实际类型 {got_type!r} 不在 ArtifactTypeRegistry"))
            elif got_type and got_type != want_type:
                # 宽端口收窄：dst 为 feature_collection 时接受 feature_set 类
                if not (want_type == "feature_collection"
                        and got_desc is not None
                        and got_desc.category in _ANY_FEATURE_CATEGORIES):
                    violations.append(PortViolation(
                        port=name, code="ARTIFACT_TYPE_MISMATCH",
                        detail=f"want={want_type} got={got_type}"))

    # 2) geometry_kind：两端已知才比（unknown 诚实放行）
    want_geo = str(getattr(port, "geometry_kind", "") or "unknown")
    got_geo = _descriptor_geometry_family(descriptor)
    if want_geo not in ("unknown", "") and got_geo not in ("unknown", "") \
            and want_geo != got_geo:
        violations.append(PortViolation(
            port=name, code="GEOMETRY_MISMATCH",
            detail=f"want={want_geo} got={got_geo}"))

    # 3) CRS class：crs_safety 单一谓词（要求类 vs 数据分类）
    crs_req = str(getattr(port, "crs_requirement", "") or "")
    if crs_req:
        data_class = "unknown"
        crs = descriptor.get("crs")
        if crs:
            from app.lib.gis.crs_safety import classify_crs

            data_class = classify_crs(str(crs))
        from app.lib.gis.crs_safety import crs_class_allows

        if not crs_class_allows(crs_req, data_class):
            violations.append(PortViolation(
                port=name, code="CRS_CLASS_MISMATCH",
                detail=f"requirement={crs_req} data_class={data_class}"))

    # 4) unit：声明时做存在性词表比对（缺证披露，不虚构违规）
    unit_req = str(getattr(port, "unit_requirement", "") or "")
    if unit_req and not descriptor.get("unit"):
        violations.append(PortViolation(
            port=name, code="UNIT_UNVERIFIED",
            detail=f"端口要求单位 {unit_req!r} 而 descriptor 无单位事实"))

    # 5) cardinality / nullability：required 输入不得为空集
    if required:
        fc = descriptor.get("feature_count")
        rc = descriptor.get("row_count")
        if fc == 0 or rc == 0:
            violations.append(PortViolation(
                port=name, code="EMPTY_OUTPUT",
                detail=f"required 输入为空（features={fc} rows={rc}）"))

    # 6) temporal：端口声明时间语义时检查 descriptor 时间键存在性
    temporal = bool(getattr(port, "temporal", False)) if hasattr(
        port, "temporal") else False
    if temporal:
        has_time = any(k in descriptor for k in (
            "time_range", "temporal_extent", "timestamp", "time_key"))
        if not has_time:
            violations.append(PortViolation(
                port=name, code="TEMPORAL_UNVERIFIED",
                detail="端口声明时间语义而 descriptor 无时间事实"))
    return violations


def verify_node_bindings(
    node: Any,
    input_descriptors: Dict[str, Optional[Dict[str, Any]]],
    *,
    output_descriptors: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
) -> BindingVerdict:
    """节点级绑定校验：inputs（+已产出的 outputs）→ BindingVerdict。"""
    node_id = str(getattr(node, "node_id", "") or "")
    violations: List[PortViolation] = []
    for port in (getattr(node, "inputs", None) or [])[:6]:
        name = str(getattr(port, "name", "") or "input")
        violations.extend(verify_port(
            port, input_descriptors.get(name), port_name=name))
    for port in (getattr(node, "outputs", None) or [])[:6]:
        name = str(getattr(port, "name", "") or "output")
        if output_descriptors and name in output_descriptors:
            violations.extend(verify_port(
                port, output_descriptors.get(name), port_name=name))
    ok = not violations
    return BindingVerdict(node_id=node_id, ok=ok,
                          action="pass" if ok else "blocked",
                          violations=violations[:8])


def violations_to_disclosure(verdict: BindingVerdict) -> str:
    """BindingVerdict → 一行人读披露（投影/解释面用）。"""
    if verdict.ok:
        return ""
    return "; ".join(
        f"{v.port}:{v.code}" for v in verdict.violations[:4])[:200]
