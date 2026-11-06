"""Typed 衔接门（ADR-0284 D2）—— 工作流缝的几何/CRS 纵深防御。

与既有守门的分工（不复制语义）：
- 权威仍是工具级 SpatialSemanticContract preflight（ADR-0279）与
  crs_safety 词表——本门只消费同一词表做**步骤边界**核验；
- plan 期：``binding.qualifier`` 的几何画像事实（上游数据画像写入）与族
  角色需求对照——事实在且冲突 → typed blocked（AWF_ROLE_GEOMETRY_MISMATCH）；
  事实缺 → 警告披露（不阻断——不虚构事实也不假装校验过）；
- run 期：水合后的 FC 实测几何类型（envelope_facts 有界扫描）与步骤
  ``semantic_contract``（param → "key:value"）对照；``step:`` 上游按
  envelope.geometry_types 对照。违例 → AWF_INPUT_SEMANTIC_MISMATCH。

几何类归一：Multi* 折叠为基类（MultiPoint→point）；族需求词表里的
"network" 归一为 "line"（同一物理几何形态）。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Optional, Tuple

from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_INPUT_SEMANTIC_MISMATCH,
    AWF_ROLE_GEOMETRY_MISMATCH,
    DataRoleBinding,
)

#: GeoJSON 几何类型 → 归一几何类。
_GEOMETRY_KIND_BY_TYPE = {
    "Point": "point", "MultiPoint": "point",
    "LineString": "line", "MultiLineString": "line",
    "Polygon": "polygon", "MultiPolygon": "polygon",
}


class SemanticFinding:
    """typed 发现（code + 人类可读 message）。"""

    __slots__ = ("code", "message")

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover — 调试便利
        return f"SemanticFinding(code={self.code!r})"


def _finding(code: str, message: str) -> SemanticFinding:
    return SemanticFinding(code, message)


def normalize_geometry_kind(kind: str) -> str:
    """需求词表归一：小写 + network→line（同一物理几何形态）。"""
    k = str(kind or "").strip().lower()
    return "line" if k == "network" else k


def geometry_types_to_kinds(geometry_types: Tuple[str, ...]
                            ) -> Tuple[str, ...]:
    kinds = []
    for gtype in geometry_types:
        kind = _GEOMETRY_KIND_BY_TYPE.get(str(gtype))
        if kind and kind not in kinds:
            kinds.append(kind)
    return tuple(kinds)


def _kinds_of_requirement(geometry_kinds: object) -> Tuple[str, ...]:
    out = []
    if not isinstance(geometry_kinds, (list, tuple)):
        return ()
    for kind in geometry_kinds:
        norm = normalize_geometry_kind(str(kind))
        if norm and norm not in out:
            out.append(norm)
    return tuple(out)


def check_role_qualifier(
    requirement: Mapping[str, object],
    binding: DataRoleBinding,
) -> Optional[SemanticFinding]:
    """plan 期：角色几何画像 vs 需求（事实缺失=无发现——由调用方披露）。"""
    req_kinds = _kinds_of_requirement(requirement.get("geometry_kinds"))
    if not req_kinds:
        return None
    qualifier = binding.qualifier
    if not isinstance(qualifier, Mapping):
        return None
    measured: Tuple[str, ...] = ()
    raw_types = qualifier.get("geometry_types")
    if isinstance(raw_types, (list, tuple)) and raw_types:
        measured = geometry_types_to_kinds(tuple(map(str, raw_types)))
    else:
        raw_kind = qualifier.get("geometry_kind")
        if isinstance(raw_kind, str):
            measured = (normalize_geometry_kind(raw_kind),)
    if not measured:
        return None
    mismatched = [k for k in measured if k not in req_kinds]
    if not mismatched:
        return None
    role = str(getattr(binding, "role", "") or requirement.get("role", ""))
    return _finding(
        f"{AWF_ROLE_GEOMETRY_MISMATCH}:{role.upper()}",
        f"角色 {role} 几何画像 {sorted(measured)} 与需求 "
        f"{sorted(req_kinds)} 不符（画像来自上游数据事实，非猜测）")


def parse_semantic_contract(contract: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    """param → "key:value" 串 → {param: {key: value}}（非法格式忽略——
    契约校验期已挡，防御性双保险）。"""
    parsed: Dict[str, Dict[str, str]] = {}
    for param, spec in (contract or {}).items():
        if isinstance(spec, str) and ":" in spec:
            key, _, value = spec.partition(":")
            parsed[str(param)] = {key.strip(): value.strip()}
    return parsed


def check_payload_semantics(
    param: str,
    payload: Any,
    semantic_contract: Dict[str, str],
) -> Optional[SemanticFinding]:
    """run 期：水合载荷（FC dict）实测几何 vs 步骤语义合同。

    非 FC 载荷 / 无合同 / 无几何 → 无发现（权威守门在工具 preflight）。
    """
    req = parse_semantic_contract(semantic_contract).get(param)
    if not req:
        return None
    kind_req = req.get("geometry_kind")
    if not kind_req:
        return None  # crs_class 等其他键由工具级 preflight 权威裁决
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        return None
    features = payload.get("features")
    if not isinstance(features, (list, tuple)) or not features:
        return None
    from app.services.gis_harness.analysis_workflows.envelope_facts import (
        collect_fc_facts,
    )
    kinds = geometry_types_to_kinds(
        collect_fc_facts(payload).geometry_types)
    if not kinds:
        return None
    allowed = _kinds_of_requirement([kind_req])
    mismatched = [k for k in kinds if k not in allowed]
    if not mismatched:
        return None
    return _finding(
        f"{AWF_INPUT_SEMANTIC_MISMATCH}:{param}",
        f"输入 {param} 实测几何 {sorted(kinds)} 与步骤语义合同 "
        f"{kind_req} 不符——typed 拒绝，不产出错误科学")


def check_envelope_semantics(
    param: str,
    envelope: Any,
    semantic_contract: Dict[str, str],
) -> Optional[SemanticFinding]:
    """run 期：step: 上游 envelope（geometry_types/result_semantics）vs 合同。

    envelope 实测事实缺失（v1 旧产物）→ 无发现（披露不阻断）。
    """
    req = parse_semantic_contract(semantic_contract).get(param)
    if not req:
        return None
    kind_req = req.get("geometry_kind")
    if not kind_req:
        return None
    upstream_types = tuple(getattr(envelope, "geometry_types", ()) or ())
    if not upstream_types:
        return None
    kinds = geometry_types_to_kinds(upstream_types)
    allowed = _kinds_of_requirement([kind_req])
    mismatched = [k for k in kinds if k not in allowed]
    if not mismatched:
        return None
    return _finding(
        f"{AWF_INPUT_SEMANTIC_MISMATCH}:{param}",
        f"上游产物实测几何 {sorted(kinds)} 与本步骤语义合同 "
        f"{kind_req} 不符——typed 拒绝")


__all__ = [
    "SemanticFinding",
    "check_role_qualifier",
    "check_payload_semantics",
    "check_envelope_semantics",
    "geometry_types_to_kinds",
    "normalize_geometry_kind",
    "parse_semantic_contract",
]
