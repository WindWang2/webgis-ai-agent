"""ExecutionCatalog Discovery —— 有界 capability-first 候选查询（F07）。

agent / LLM 面的统一发现入口：按 capability + data descriptor + situation +
resource envelope 查询**有界、确定性、带证据**的候选（capability →
algorithm → tool 链），替代「把整个 registry 塞给 LLM」与 prompt 中的
工具名字面量。

纪律：
- **纯函数**：同 catalog + 同 query 必同序同输出（tie-break (score, kind, id)）;
- **有界**：默认 limit=5，硬上限 MAX_LIMIT=16；被硬过滤排除的候选只以
  计数与原因码披露，不展开条目（防元数据泄洪）;
- **证据**：每个候选携带 reasons（词表码）与 score 明细 —— 排序可解释;
- **只读**：消费 catalog 投影，不解析 provider 运行态（在线/离线判定用
  声明面 network/offline_capable，不探测真实服务 —— 运行期 provider
  选择权威仍是 capability_resolution）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.lib.gis.execution_catalog import (
    KIND_ALGORITHM,
    KIND_TOOL,
    CatalogEntry,
    ExecutionCatalog,
)

#: 返回条目硬上限（防泄洪；调用方显式要求更多也会被截到本值）。
MAX_LIMIT = 16
DEFAULT_LIMIT = 5

#: 查询 capability 预算。
MAX_QUERY_CAPABILITIES = 8

#: latency/memory 词表序（用于 envelope 比较；unknown 视为最宽松）。
_LATENCY_ORDER = {"unknown": 0, "fast": 1, "medium": 2, "slow": 3}
_MEMORY_ORDER = {"unknown": 0, "light": 1, "medium": 2, "heavy": 3}
_SCALE_ORDER = {"unknown": 0, "small": 1, "medium": 2, "large": 3}

#: 原因码（稳定词表；agent 面依赖它做机器可读决策）。
REASON_ALGORITHM_PRIORITY = "algorithm_priority"
REASON_DEPRECATED = "provider_deprecated"
REASON_UNCLASSIFIED_SIDE_EFFECT = "side_effect_unclassified"
REASON_UNKNOWN_CLASS = "resource_class_unknown"
REASON_OFFLINE_EXCLUDED = "excluded_offline_violation"
REASON_DESTRUCTIVE_EXCLUDED = "excluded_destructive"
REASON_RESOURCE_EXCLUDED = "excluded_resource_envelope"
REASON_SCALE_MISMATCH = "scale_mismatch"
REASON_CERTIFIED_BONUS = "certified_provider"
REASON_UNCERTIFIED = "uncertified_provider"
REASON_TOOL_MISSING = "excluded_tool_not_registered"
REASON_GEOMETRY_MATCH = "geometry_requirements_matched"
REASON_GEOMETRY_UNKNOWN = "geometry_requirements_undeclared"
REASON_CRS_PROJECTED_REQUIRED = "projected_crs_required"

#: 数据规模档（approx_features → scale 档；有界合成阈值）。
_SCALE_SMALL_MAX = 5_000
_SCALE_MEDIUM_MAX = 200_000


@dataclass(frozen=True)
class DiscoveryQuery:
    """一次能力发现查询（全部字段可选除 capabilities）。"""

    capabilities: Sequence[str] = ()
    #: 数据描述面（声明式；unknown = 不判）。
    geometry: str = ""                 # point/line/polygon/raster/table/unknown
    approx_features: Optional[int] = None
    crs_class: str = ""                # geographic/projected/unknown
    #: situation 面。
    offline_required: bool = False
    allow_destructive: bool = False
    #: resource envelope 面。
    max_latency_class: str = ""        # fast/medium/slow（空 = 不限）
    max_memory_class: str = ""         # light/medium/heavy（空 = 不限）
    #: 结果预算。
    limit: int = DEFAULT_LIMIT

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise ValueError("discovery query requires at least one capability")
        if len(self.capabilities) > MAX_QUERY_CAPABILITIES:
            raise ValueError(
                f"discovery query supports at most {MAX_QUERY_CAPABILITIES} "
                f"capabilities (got {len(self.capabilities)})")
        if not 1 <= int(self.limit) <= MAX_LIMIT:
            raise ValueError(
                f"discovery limit must be in [1, {MAX_LIMIT}] (got {self.limit})")


@dataclass(frozen=True)
class DiscoveryCandidate:
    """一个候选执行链（capability → algorithm → tool）+ 证据。"""

    capability: str
    algorithm: str
    tool: str
    tool_label: str = ""
    score: float = 0.0
    reasons: Tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability,
            "algorithm": self.algorithm,
            "tool": self.tool,
            "label": self.tool_label[:96],
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
            "evidence": dict(self.evidence),
        }


@dataclass
class DiscoveryResult:
    """有界发现结果 + 排除披露（排序确定）。"""

    candidates: List[DiscoveryCandidate] = field(default_factory=list)
    excluded: Dict[str, int] = field(default_factory=dict)
    query_capabilities: List[str] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "excluded": dict(sorted(self.excluded.items())),
            "query_capabilities": list(self.query_capabilities),
            "truncated": self.truncated,
        }


def _data_scale_band(approx_features: Optional[int]) -> str:
    if not approx_features or approx_features <= 0:
        return "unknown"
    if approx_features <= _SCALE_SMALL_MAX:
        return "small"
    if approx_features <= _SCALE_MEDIUM_MAX:
        return "medium"
    return "large"


def _tool_executable_reason(
    tool: CatalogEntry, query: DiscoveryQuery,
) -> Optional[str]:
    """situation / envelope 硬过滤：返回排除原因码；可执行返回 None。"""
    if tool.status in ("planned", "hidden"):
        return REASON_TOOL_MISSING
    network = tool.detail.get("network")
    if query.offline_required and network is True:
        return REASON_OFFLINE_EXCLUDED
    if (not query.allow_destructive) and tool.side_effect == "destructive":
        return REASON_DESTRUCTIVE_EXCLUDED
    # tier>=3 视为破坏性闸（descriptor.destructive_level 同语义）。
    if (not query.allow_destructive) and int(tool.detail.get("tier", 1) or 1) >= 3:
        return REASON_DESTRUCTIVE_EXCLUDED
    if query.max_latency_class:
        cap = _LATENCY_ORDER.get(query.max_latency_class, 0)
        value = _LATENCY_ORDER.get(tool.resource_class.get("latency", "unknown"), 0)
        if value > cap:
            return REASON_RESOURCE_EXCLUDED
    if query.max_memory_class:
        cap = _MEMORY_ORDER.get(query.max_memory_class, 0)
        value = _MEMORY_ORDER.get(tool.resource_class.get("memory", "unknown"), 0)
        if value > cap:
            return REASON_RESOURCE_EXCLUDED
    # 资源包络硬上限 × 数据描述（算法层声明的 hard_max_*）。
    return None


def _algorithm_executable_reason(
    algo: CatalogEntry, query: DiscoveryQuery,
) -> Optional[str]:
    if algo.status != "native":
        return REASON_TOOL_MISSING
    band = _data_scale_band(query.approx_features)
    env = algo.resource_envelope
    if band != "unknown" and env:
        hard_max = env.get("hard_max_features")
        if hard_max and query.approx_features and int(hard_max) < int(query.approx_features):
            return REASON_RESOURCE_EXCLUDED
    if query.crs_class == "geographic" and algo.crs_class in (
            "PROJECTED_REQUIRED", "LOCAL_METRIC_REQUIRED"):
        # 数据在地理 CRS 而算法硬门要求投影 —— 预期不可执行（resolver
        # 会拒），发现层如实前置披露。
        return REASON_CRS_PROJECTED_REQUIRED
    return None


def _candidate_score(
    algo: CatalogEntry, tool: CatalogEntry, query: DiscoveryQuery,
) -> Tuple[float, List[str]]:
    """确定性打分（越小越优）+ 显式原因码。"""
    reasons: List[str] = []
    score = float(algo.priority)
    reasons.append(f"{REASON_ALGORITHM_PRIORITY}:{algo.priority}")
    if tool.deprecated:
        score += 2.0
        reasons.append(REASON_DEPRECATED)
    if not tool.side_effect or tool.side_effect == "unclassified":
        score += 0.5
        reasons.append(REASON_UNCLASSIFIED_SIDE_EFFECT)
    unknown_classes = sum(
        1 for v in tool.resource_class.values() if v in ("", "unknown"))
    if unknown_classes:
        score += 0.25 * unknown_classes
        reasons.append(f"{REASON_UNKNOWN_CLASS}:{unknown_classes}")
    if tool.certification.get("provider_kind") == "extension":
        if tool.certification.get("certified"):
            score -= 0.25
            reasons.append(REASON_CERTIFIED_BONUS)
        else:
            score += 0.5
            reasons.append(REASON_UNCERTIFIED)
    # scale 适配披露（软信号：不匹配不排除，但显式入证据）。
    band = _data_scale_band(query.approx_features)
    if band != "unknown":
        scale = tool.resource_class.get("scale", "unknown")
        if scale not in ("", "unknown") and scale != band:
            score += 0.25
            reasons.append(f"{REASON_SCALE_MISMATCH}:{scale}!={band}")
    return score, reasons


def discover(
    catalog: ExecutionCatalog,
    query: DiscoveryQuery,
) -> DiscoveryResult:
    """capability-first 有界候选发现（纯函数、确定性）。"""
    limit = min(int(query.limit), MAX_LIMIT)
    result = DiscoveryResult(query_capabilities=list(query.capabilities)[:MAX_QUERY_CAPABILITIES])
    seen: set = set()
    rows: List[DiscoveryCandidate] = []

    for cap in query.capabilities[:MAX_QUERY_CAPABILITIES]:
        cap_entry = catalog.get("capability", cap)
        if cap_entry is None:
            result.excluded["capability_not_in_catalog"] = (
                result.excluded.get("capability_not_in_catalog", 0) + 1)
            continue
        algorithms = [
            e for e in catalog.entries_of_kind(KIND_ALGORITHM)
            if cap in e.capabilities
        ]
        if not algorithms:
            result.excluded["capability_no_algorithm"] = (
                result.excluded.get("capability_no_algorithm", 0) + 1)
            continue
        for algo in algorithms:
            algo_block = _algorithm_executable_reason(algo, query)
            if algo_block:
                result.excluded[algo_block] = result.excluded.get(algo_block, 0) + 1
                continue
            candidates = [str(t) for t in algo.detail.get("tool_candidates", ())]
            if not candidates:
                result.excluded[REASON_TOOL_MISSING] = (
                    result.excluded.get(REASON_TOOL_MISSING, 0) + 1)
                continue
            for tool_name in candidates:
                tool = catalog.get(KIND_TOOL, tool_name)
                if tool is None:
                    result.excluded[REASON_TOOL_MISSING] = (
                        result.excluded.get(REASON_TOOL_MISSING, 0) + 1)
                    continue
                pair = (cap, algo.id, tool.id)
                if pair in seen:
                    continue
                seen.add(pair)
                tool_block = _tool_executable_reason(tool, query)
                if tool_block:
                    result.excluded[tool_block] = (
                        result.excluded.get(tool_block, 0) + 1)
                    continue
                score, reasons = _candidate_score(algo, tool, query)
                evidence: Dict[str, Any] = {
                    "algorithm_priority": algo.priority,
                    "tool_status": tool.status,
                    "side_effect": tool.side_effect,
                    "resource_class": dict(sorted(tool.resource_class.items())),
                    "algorithm_crs_class": algo.crs_class,
                    "certified": bool(tool.certification.get("certified", False)),
                }
                if cap_entry.geometry_requirements:
                    evidence["capability_geometry"] = list(cap_entry.geometry_requirements)
                    reasons.append(REASON_GEOMETRY_MATCH)
                rows.append(DiscoveryCandidate(
                    capability=cap,
                    algorithm=algo.id,
                    tool=tool.id,
                    tool_label=tool.name,
                    score=score,
                    reasons=tuple(reasons),
                    evidence=evidence,
                ))

    rows.sort(key=lambda c: (c.score, c.capability, c.algorithm, c.tool))
    if len(rows) > limit:
        result.truncated = True
        result.excluded["truncated_over_limit"] = len(rows) - limit
        rows = rows[:limit]
    result.candidates = rows
    return result


def discover_to_payload(
    catalog: ExecutionCatalog,
    query: DiscoveryQuery,
) -> Dict[str, Any]:
    """LLM 面有界 payload（reason codes + 证据；不携带大描述体）。"""
    return discover(catalog, query).to_dict()


__all__ = [
    "MAX_LIMIT",
    "DEFAULT_LIMIT",
    "MAX_QUERY_CAPABILITIES",
    "DiscoveryQuery",
    "DiscoveryCandidate",
    "DiscoveryResult",
    "REASON_ALGORITHM_PRIORITY",
    "REASON_DEPRECATED",
    "REASON_UNCLASSIFIED_SIDE_EFFECT",
    "REASON_UNKNOWN_CLASS",
    "REASON_OFFLINE_EXCLUDED",
    "REASON_DESTRUCTIVE_EXCLUDED",
    "REASON_RESOURCE_EXCLUDED",
    "REASON_SCALE_MISMATCH",
    "REASON_CERTIFIED_BONUS",
    "REASON_UNCERTIFIED",
    "REASON_TOOL_MISSING",
    "REASON_GEOMETRY_MATCH",
    "REASON_CRS_PROJECTED_REQUIRED",
    "discover",
    "discover_to_payload",
]
