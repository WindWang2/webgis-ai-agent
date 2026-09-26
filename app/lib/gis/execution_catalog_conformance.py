"""ExecutionCatalog Conformance —— 目录级跨层一致性校验（F07）。

``capability_conformance``（ADR-0204 D2）只覆盖 capability↔tool 绑定面；
本模块把对账提升到完整执行链：recipe → capability → algorithm → tool →
successor。**纯函数**、fatal/warning 分级、产出排序确定条数有界、只读
catalog 投影，绝不反写 registry。

分级哲学（与 capability_conformance / runtime_manifest 同语义）：

- **fatal** = 引用破损：catalog 词表内永远不可解析的引用（悬空 capability /
  算法 / fallback / 替代者）；
- **warning** = 完整度 / 部署态 / 等价性风险：候选工具缺席（部署退化）、
  输出契约分歧、单位/几何语义矛盾（双方**已声明**才判，绝不猜）、弃用
  无后继、扩展条目认证契约缺口。

错误码前缀 ``catalog_``；capability_conformance 的既有码原样链入
（单一绑定闸语义，不复制实现）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from app.lib.gis.execution_catalog import (
    CATALOG_KINDS,
    KIND_ALGORITHM,
    KIND_CAPABILITY,
    KIND_RECIPE,
    KIND_TOOL,
    CatalogEntry,
    ExecutionCatalog,
)

SEVERITY_FATAL = "fatal"
SEVERITY_WARNING = "warning"

#: 校验发现预算（巨型 registry 下截断并在 summary 披露，不静默）。
MAX_FINDINGS = 512
#: 单条 issue 细节列表上界。
MAX_DETAIL_ITEMS = 16

# ── 错误码 ─────────────────────────────────────────────────────────────
CODE_CAPABILITY_DANGLING = "catalog_capability_dangling"
CODE_FALLBACK_DANGLING = "catalog_fallback_dangling"
CODE_DEPRECATION_TARGET_DANGLING = "catalog_deprecation_target_dangling"
CODE_ALGORITHM_TOOL_MISSING = "catalog_algorithm_tool_missing"
CODE_RECIPE_CAPABILITY_UNREACHABLE = "catalog_recipe_capability_unreachable"
CODE_OUTPUT_CONTRACT_MISMATCH = "catalog_output_contract_mismatch"
CODE_UNIT_GEOMETRY_MISMATCH = "catalog_unit_geometry_mismatch"
CODE_DEPRECATED_NO_SUCCESSOR = "catalog_deprecated_no_successor"
CODE_DEPRECATED_PROVIDER_ONLY = "catalog_deprecated_provider_only"
CODE_EXTENSION_CONTRACT_INCOMPLETE = "catalog_extension_contract_incomplete"

#: 链入 capability_conformance 的既有码（透传，同一闸语义）。
CHAINED_CONFORMANCE_CODES = (
    "capability_id_dangling",            # fatal
    "capability_binding_unbacked",       # warning
    "descriptor_metadata_incomplete",    # warning
    "provider_output_contract_divergence",  # warning
)

_UNCLASSIFIED_SIDE_EFFECT = "unclassified"

#: 单位词族（unit 冲突判定的封闭归并；词表外 = unknown 不判）。
_UNIT_FAMILIES: Dict[str, str] = {
    "meters": "distance", "kilometers": "distance",
    "degrees": "angular", "pixels": "pixel", "seconds": "time",
}
#: 地理绑定 CRS 语义（工具 crs_semantics 声明面；小写）。
_GEOGRAPHIC_BOUND_CRS = frozenset({"wgs84", "gcj02", "bd09", "gcj02/bd09"})


@dataclass(frozen=True)
class CatalogConformanceIssue:
    """一条目录级一致性发现（排序键 = (code, kind, entry_id)）。"""

    code: str
    severity: str
    kind: str
    entry_id: str
    peer: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code, "severity": self.severity,
            "kind": self.kind, "id": self.entry_id[:128],
            "peer": self.peer[:128], "detail": self.detail[:240],
        }


def facet_gaps(entry: CatalogEntry) -> List[str]:
    """扩展条目认证契约缺口（7 facet；纯函数、按 kind 确定适用面）。

    facet 不适用于该 kind 时视为满足（core 条目整体豁免 —— 认证契约
    只约束扩展投影）。缺口 = 声明面缺席，不虚构判定。
    """
    if entry.certification.get("provider_kind") not in ("extension", "unknown"):
        return []
    if entry.kind == KIND_TOOL:
        applicable = ("metadata", "schema", "cancellation",
                      "side_effects", "security", "resource_estimate")
    elif entry.kind == KIND_ALGORITHM:
        applicable = ("metadata", "schema", "cancellation",
                      "resource_estimate", "tests")
    else:  # recipe / capability：只约束身份元数据。
        applicable = ("metadata",)
    gaps: List[str] = []
    for facet in applicable:
        if facet == "metadata":
            ok = bool(entry.name.strip()) and bool(entry.version.strip())
        elif facet == "schema":
            ok = bool(entry.output_semantic_types)
        elif facet == "cancellation":
            if entry.kind == KIND_TOOL:
                ok = entry.detail.get("timeout") is not None
            else:
                ok = bool(entry.cancellation_profile)
        elif facet == "side_effects":
            ok = bool(entry.side_effect) and entry.side_effect != _UNCLASSIFIED_SIDE_EFFECT
        elif facet == "security":
            ok = entry.kind != KIND_TOOL or entry.detail.get("security_tier") is not None
        elif facet == "resource_estimate":
            ok = bool(entry.resource_envelope) or any(
                v not in ("", "unknown") for v in entry.resource_class.values())
        elif facet == "tests":
            ok = int(entry.detail.get("conformance_tests", 0) or 0) > 0
        else:
            ok = True
        if not ok:
            gaps.append(facet)
    return gaps


def _unit_family(unit: str) -> str:
    return _UNIT_FAMILIES.get(str(unit or "").strip().lower(), "")


def _validate_references(catalog: ExecutionCatalog) -> List[CatalogConformanceIssue]:
    """词表内引用完整性（fatal 面；有界、排序确定）。"""
    issues: List[CatalogConformanceIssue] = []
    ids_by_kind = {k: set() for k in CATALOG_KINDS}
    for entry in catalog.entries.values():
        ids_by_kind.setdefault(entry.kind, set()).add(entry.id)

    def _emit(code: str, kind: str, eid: str, peer: str, detail: str,
              severity: str = SEVERITY_FATAL) -> None:
        if len(issues) < MAX_FINDINGS:
            issues.append(CatalogConformanceIssue(
                code, severity, kind, eid, peer, detail))

    for entry in sorted(catalog.entries.values(), key=lambda e: (e.kind, e.id)):
        if entry.kind == KIND_ALGORITHM:
            for cap in entry.capabilities:
                if cap not in ids_by_kind[KIND_CAPABILITY]:
                    _emit(CODE_CAPABILITY_DANGLING, KIND_ALGORITHM,
                          entry.id, cap, "algorithm capability id not in catalog")
        elif entry.kind == KIND_TOOL:
            for cap in entry.capabilities:
                if cap not in ids_by_kind[KIND_CAPABILITY]:
                    _emit(CODE_CAPABILITY_DANGLING, KIND_TOOL,
                          entry.id, cap, "declared capability id not in catalog")
        elif entry.kind == KIND_RECIPE:
            for cap in entry.capabilities:
                if cap not in ids_by_kind[KIND_CAPABILITY]:
                    _emit(CODE_CAPABILITY_DANGLING, KIND_RECIPE,
                          entry.id, cap, "recipe capability id not in catalog")
        for target in entry.fallback_targets:
            if target not in ids_by_kind.get(entry.kind, set()):
                _emit(CODE_FALLBACK_DANGLING, entry.kind,
                      entry.id, target, "fallback target missing in catalog")
        if entry.is_deprecated and entry.superseded_by:
            if entry.superseded_by not in ids_by_kind.get(entry.kind, set()):
                _emit(CODE_DEPRECATION_TARGET_DANGLING, entry.kind,
                      entry.id, entry.superseded_by,
                      "deprecation successor missing in catalog")
    return issues


def _validate_chains(catalog: ExecutionCatalog) -> List[CatalogConformanceIssue]:
    """跨层链完整性 / 契约等价性（warning 面）。"""
    issues: List[CatalogConformanceIssue] = []

    def _emit(code: str, kind: str, eid: str, peer: str, detail: str) -> None:
        if len(issues) < MAX_FINDINGS:
            issues.append(CatalogConformanceIssue(
                code, SEVERITY_WARNING, kind, eid, peer, detail))

    tools = {e.id: e for e in catalog.entries.values() if e.kind == KIND_TOOL}
    algorithms = [e for e in sorted(
        catalog.entries.values(),
        key=lambda e: (e.priority, e.id)) if e.kind == KIND_ALGORITHM]
    algorithms_by_cap: Dict[str, List[CatalogEntry]] = {}
    for algo in algorithms:
        for cap in algo.capabilities:
            algorithms_by_cap.setdefault(cap, []).append(algo)

    for algo in algorithms:
        candidates = [str(t) for t in algo.detail.get("tool_candidates", ())]
        if algo.status == "native":
            if not candidates:
                _emit(CODE_ALGORITHM_TOOL_MISSING, KIND_ALGORITHM, algo.id,
                      "", "native algorithm declares no tool candidates")
            else:
                missing = [t for t in candidates if t not in tools]
                if missing:
                    detail = "candidate tools not registered: " + ",".join(
                        missing[:MAX_DETAIL_ITEMS])
                    if len(missing) > MAX_DETAIL_ITEMS:
                        detail += f" (+{len(missing) - MAX_DETAIL_ITEMS} more)"
                    _emit(CODE_ALGORITHM_TOOL_MISSING, KIND_ALGORITHM,
                          algo.id, ",".join(missing[:4]), detail)
        # 输出契约（诚实边界）：工具 output_semantic_type 是 LLM 响应通道
        # 词表，produced_refs 全库基本未声明 —— channel × artifact kind 的
        # 结构判定会建立在未声明事实上（系统性误报），本层不做。可判定的
        # 缺口是**声明完备性**：native 算法声明了输出工件，但全部已注册
        # 候选工具都未声明任何输出通道 → 链的输出契约不可验证（提富化，
        # 不判分歧）。同 capability 多 provider 通道分歧由链入的
        # capability_conformance（provider_output_contract_divergence）负责。
        if algo.status == "native" and algo.output_semantic_types:
            registered = [tools[t] for t in candidates if t in tools]
            if registered and not any(
                    t.output_semantic_types for t in registered):
                _emit(CODE_OUTPUT_CONTRACT_MISMATCH, KIND_ALGORITHM, algo.id,
                      ",".join(t.id for t in registered[:4]),
                      f"algorithm output {list(algo.output_semantic_types)} "
                      f"unverifiable: no candidate tool declares an output "
                      f"channel (enrich output_semantic_type)")
        # 单位/几何语义矛盾（双方已声明才判；保守单向规则）。
        algo_unit = _unit_family(algo.unit_requirements)
        algo_crs = str(algo.crs_class or "")
        for t in candidates:
            tool = tools.get(t)
            if tool is None:
                continue
            if algo_unit:
                tool_unit = _unit_family(tool.unit_semantics)
                if tool_unit and tool_unit != algo_unit:
                    _emit(CODE_UNIT_GEOMETRY_MISMATCH, KIND_ALGORITHM,
                          algo.id, t,
                          f"unit family conflict: algorithm requires "
                          f"'{algo.unit_requirements}' ({algo_unit}), tool "
                          f"declares '{tool.unit_semantics}' ({tool_unit})")
            if algo_crs in ("PROJECTED_REQUIRED", "LOCAL_METRIC_REQUIRED"):
                tool_crs = str(tool.crs_semantics or "").strip().lower()
                if tool_crs in _GEOGRAPHIC_BOUND_CRS:
                    _emit(CODE_UNIT_GEOMETRY_MISMATCH, KIND_ALGORITHM,
                          algo.id, t,
                          f"crs conflict: algorithm requires {algo_crs}, "
                          f"tool crs_semantics='{tool.crs_semantics}' is "
                          f"geography-bound")
        # 弃用无后继（registry validate 同规则，catalog 面独立可查）。
        if algo.is_deprecated and not algo.fallback_targets:
            _emit(CODE_DEPRECATED_NO_SUCCESSOR, KIND_ALGORITHM, algo.id,
                  "", "DEPRECATED algorithm without fallback_algorithms")
        if (algo.is_deprecated and algo.fallback_targets
                and not algo.superseded_by):
            # fallback 有但替代者未指定（多 fallback 时合法）——不判。
            pass

    for tool in tools.values():
        if tool.is_deprecated and not tool.superseded_by:
            _emit(CODE_DEPRECATED_NO_SUCCESSOR, KIND_TOOL, tool.id, "",
                  "deprecated tool without deprecation_of successor")

    # capability 级：recipe 可达性 + 弃用 provider 独占。
    executable_cache: Dict[str, bool] = {}
    for recipe in catalog.entries_of_kind(KIND_RECIPE):
        for cap in recipe.capabilities:
            reachable = executable_cache.get(cap)
            if reachable is None:
                algos = algorithms_by_cap.get(cap, [])
                reachable = any(
                    a.status == "native"
                    and any(str(t) in tools
                            for t in a.detail.get("tool_candidates", ()))
                    for a in algos)
                executable_cache[cap] = reachable
            if not reachable:
                _emit(CODE_RECIPE_CAPABILITY_UNREACHABLE, KIND_RECIPE,
                      recipe.id, cap,
                      "recipe references capability with no executable "
                      "algorithm→tool chain")
    for cap, algos in sorted(algorithms_by_cap.items()):
        native = [a for a in algos if a.status == "native"]
        if native and all(a.is_deprecated for a in native):
            _emit(CODE_DEPRECATED_PROVIDER_ONLY, KIND_CAPABILITY, cap,
                  ",".join(a.id for a in native[:4]),
                  "capability only served by deprecated algorithms")

    # 扩展认证契约缺口（聚合到单条目一条，防噪音）。
    for entry in sorted(catalog.entries.values(), key=lambda e: (e.kind, e.id)):
        gaps = facet_gaps(entry)
        if gaps:
            _emit(CODE_EXTENSION_CONTRACT_INCOMPLETE, entry.kind, entry.id,
                  str(entry.certification.get("namespace", "")),
                  "certification contract gaps: " + ",".join(gaps))
    return issues


def _chain_capability_conformance(
    catalog: ExecutionCatalog,
) -> List[CatalogConformanceIssue]:
    """链入 ADR-0204 绑定闸（同一闸语义；码透传、kind=tool）。"""
    from types import SimpleNamespace

    from app.lib.gis.capability_conformance import (
        validate_capability_conformance,
    )

    algo_view = [
        SimpleNamespace(
            capabilities=list(e.capabilities),
            tool_candidates=list(e.detail.get("tool_candidates", ())),
        )
        for e in catalog.entries_of_kind(KIND_ALGORITHM)
    ]
    tool_metadata = []
    for e in catalog.entries_of_kind(KIND_TOOL):
        tool_metadata.append((e.id, {
            "capabilities": list(e.capabilities),
            "network": e.detail.get("network"),
            "deterministic": e.deterministic,
            "side_effect": e.side_effect,
            "result_size_policy": e.detail.get("result_size_policy", "unknown"),
            "output_semantic_type": (list(e.output_semantic_types)[0]
                                     if e.output_semantic_types else ""),
        }))
    raw = validate_capability_conformance(
        capability_ids=[e.id for e in catalog.entries_of_kind(KIND_CAPABILITY)],
        algorithms=algo_view,
        tool_metadata=sorted(tool_metadata),
    )
    return [
        CatalogConformanceIssue(
            i.code, i.severity, KIND_TOOL, i.tool, i.capability, i.detail)
        for i in raw
    ]


def validate_execution_catalog_conformance(
    catalog: ExecutionCatalog,
    *,
    chain_capability_conformance: bool = True,
) -> List[CatalogConformanceIssue]:
    """目录级一致性校验（纯函数；同 catalog 同序同输出）。"""
    issues: List[CatalogConformanceIssue] = []
    issues.extend(_validate_references(catalog))
    issues.extend(_validate_chains(catalog))
    if chain_capability_conformance:
        issues.extend(_chain_capability_conformance(catalog))
    issues.sort(key=lambda i: (i.code, i.kind, i.entry_id, i.peer))
    return issues[:MAX_FINDINGS]


def conformance_summary(issues: Sequence[CatalogConformanceIssue]) -> Dict[str, Any]:
    """有界摘要（文档 / manifest 投影用）。"""
    by_sev: Dict[str, int] = {SEVERITY_FATAL: 0, SEVERITY_WARNING: 0}
    by_code: Dict[str, int] = {}
    for i in issues:
        by_sev[i.severity] = by_sev.get(i.severity, 0) + 1
        by_code[i.code] = by_code.get(i.code, 0) + 1
    return {
        "total": len(issues),
        "by_severity": {k: v for k, v in sorted(by_sev.items())},
        "by_code": dict(sorted(by_code.items())),
        "truncated": len(issues) >= MAX_FINDINGS,
    }


__all__ = [
    "SEVERITY_FATAL",
    "SEVERITY_WARNING",
    "MAX_FINDINGS",
    "MAX_DETAIL_ITEMS",
    "CODE_CAPABILITY_DANGLING",
    "CODE_FALLBACK_DANGLING",
    "CODE_DEPRECATION_TARGET_DANGLING",
    "CODE_ALGORITHM_TOOL_MISSING",
    "CODE_RECIPE_CAPABILITY_UNREACHABLE",
    "CODE_OUTPUT_CONTRACT_MISMATCH",
    "CODE_UNIT_GEOMETRY_MISMATCH",
    "CODE_DEPRECATED_NO_SUCCESSOR",
    "CODE_DEPRECATED_PROVIDER_ONLY",
    "CODE_EXTENSION_CONTRACT_INCOMPLETE",
    "CHAINED_CONFORMANCE_CODES",
    "CatalogConformanceIssue",
    "facet_gaps",
    "validate_execution_catalog_conformance",
    "conformance_summary",
]
