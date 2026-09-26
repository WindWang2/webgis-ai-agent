"""Declared vs derived capability binding 治理（F06, ADR-0215 候选）.

#1482 把「声明但算法链不可达」的分歧定为**合法态**（聚合 warning 披露），
并把数据治理登记为 follow-up —— 本模块补上治理面：逐对分类、migration
hint、确定性报告与回归门禁，**不做一刀切删除**。

三类处置（分类规则全确定性，同输入同序同输出）：

- ``legal_multi_provider``：声明工具 ABI 元数据完备、输出契约与该
  capability 的派生面一致 —— 「声明先到、算法链未注册」的合法额外
  provider。处置 = 保留披露；若需参与解析排序则注册算法链。
- ``metadata_missing``：ABI 元数据欠缺（network/deterministic/side_effect/
  result_size_policy）—— 无从验证契约。处置 = 先补 descriptor 元数据。
- ``suspected_misdeclaration``：``output_semantic_type`` 与该 capability
  派生 provider 面冲突 —— 真实错误候选。处置 = 修声明或补算法链。

附 ``durable_label_honesty`` 节：execution_policy=celery 的工具在当前部署
（broker 未配置 / required 未满足）下的 durable 取消标签诚实性披露
（#1482 登记 follow-up；标签本体的声明面投影不改动）。

纯函数核心 + 只读 live 采集 helper；报告 ≤512 条、JSON 可序列化。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from app.lib.gis.capability_conformance import (
    CODE_BINDING_UNBACKED,
    CODE_ID_DANGLING,
    CODE_METADATA_INCOMPLETE,
    MAX_FINDINGS,
    ConformanceIssue,
    _metadata_gaps,
    validate_capability_conformance,
)

GOVERNANCE_POLICY_VERSION = "capability_binding_governance.v1"

CLASS_LEGAL_MULTI_PROVIDER = "legal_multi_provider"
CLASS_METADATA_MISSING = "metadata_missing"
CLASS_SUSPECTED_MISDECLARATION = "suspected_misdeclaration"

_CLASS_ORDER = (
    CLASS_SUSPECTED_MISDECLARATION,
    CLASS_METADATA_MISSING,
    CLASS_LEGAL_MULTI_PROVIDER,
)

#: 报告逐条目上限（与 conformance MAX_FINDINGS 同量级，bounded）。
MAX_REPORT_ENTRIES = 512
_STR_MAX = 128


@dataclass(frozen=True)
class DivergenceEntry:
    """一条 declared vs derived 分歧的治理条目。"""

    capability: str
    tool: str
    classification: str
    output_semantic_type: str
    derived_output_face: Tuple[str, ...] = ()
    metadata_gaps: Tuple[str, ...] = ()
    migration_hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability[:_STR_MAX],
            "tool": self.tool[:_STR_MAX],
            "classification": self.classification,
            "output_semantic_type": self.output_semantic_type[:64],
            "derived_output_face": list(self.derived_output_face[:4]),
            "metadata_gaps": list(self.metadata_gaps[:8]),
            "migration_hint": self.migration_hint[:160],
        }


@dataclass
class CapabilityBindingGovernanceReport:
    """治理报告（确定性排序；JSON 可序列化；有界）。"""

    policy_version: str = GOVERNANCE_POLICY_VERSION
    entries: List[DivergenceEntry] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    dangling_fatal: List[Dict[str, str]] = field(default_factory=list)
    metadata_incomplete_total: int = 0
    output_divergence_total: int = 0
    durable_label_honesty: Dict[str, Any] = field(default_factory=dict)
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "counts": dict(self.counts),
            "metadata_incomplete_total": self.metadata_incomplete_total,
            "output_divergence_total": self.output_divergence_total,
            "dangling_fatal": list(self.dangling_fatal[:32]),
            "durable_label_honesty": dict(self.durable_label_honesty),
            "truncated": self.truncated,
            "entries": [e.to_dict() for e in self.entries],
        }


def _derived_face(algorithms: Iterable[Any]) -> Tuple[
    Dict[str, set], Dict[str, Dict[str, str]]
]:
    """capability → {algorithm-backed tool set} 与 capability → {tool: out_type}."""
    derived_tools: Dict[str, set] = {}
    derived_out: Dict[str, Dict[str, str]] = {}
    for algo in algorithms:
        caps = [str(c).strip() for c in (getattr(algo, "capabilities", None) or [])]
        tools = [str(t).strip() for t in (getattr(algo, "tool_candidates", None) or [])]
        for cap in caps:
            if not cap:
                continue
            derived_tools.setdefault(cap, set()).update(tools)
    return derived_tools, derived_out


def _majority_output_face(
    tool_metadata: Mapping[str, Mapping[str, Any]],
    tools: Iterable[str],
) -> Dict[str, str]:
    """派生 provider 面的 output_semantic_type 投影（tool → out type）。"""
    face: Dict[str, str] = {}
    for t in tools:
        meta = tool_metadata.get(t) or {}
        out = str(meta.get("output_semantic_type") or "").strip()
        if out:
            face[t] = out[:64]
    return face


def govern_capability_bindings(
    *,
    capability_ids: Iterable[str],
    algorithms: Iterable[Any],
    tool_metadata: Iterable[Tuple[str, Mapping[str, Any]]],
    durable_honesty: Optional[Dict[str, Any]] = None,
) -> CapabilityBindingGovernanceReport:
    """分歧逐对分类（纯函数；确定性排序；有界）。

    ``tool_metadata``：``(tool_name, metadata_dict)`` 迭代（与
    :func:`validate_capability_conformance` 同形）。
    """
    meta_map: Dict[str, Mapping[str, Any]] = {}
    for name, meta in tool_metadata:
        meta_map[str(name)[:_STR_MAX]] = meta
    caps = {str(c) for c in capability_ids}
    derived_tools, _ = _derived_face(algorithms)

    issues = validate_capability_conformance(
        capability_ids=caps,
        algorithms=algorithms,
        tool_metadata=sorted(meta_map.items()),
    )

    report = CapabilityBindingGovernanceReport()
    entries: List[DivergenceEntry] = []

    # capability → 派生 provider 输出面（含声明面 out type，供冲突判定）
    derived_face_cache: Dict[str, Dict[str, str]] = {}

    def _derived_face_for(cap: str) -> Dict[str, str]:
        if cap not in derived_face_cache:
            derived_face_cache[cap] = _majority_output_face(
                meta_map, derived_tools.get(cap, ()))
        return derived_face_cache[cap]

    for issue in issues:
        if issue.code == CODE_ID_DANGLING:
            report.dangling_fatal.append({
                "tool": issue.tool[:_STR_MAX],
                "capability": issue.capability[:_STR_MAX],
                "detail": issue.detail[:160],
            })
            continue
        if issue.code == CODE_METADATA_INCOMPLETE:
            report.metadata_incomplete_total += 1
            continue
        if issue.code == "provider_output_contract_divergence":
            report.output_divergence_total += 1
            continue
        if issue.code != CODE_BINDING_UNBACKED:
            continue

        meta = meta_map.get(issue.tool) or {}
        out_type = str(meta.get("output_semantic_type") or "").strip()
        gaps = tuple(_metadata_gaps(meta))
        face = _derived_face_for(issue.capability)
        face_variants = tuple(sorted(set(face.values())))

        if out_type and face and out_type not in face.values():
            classification = CLASS_SUSPECTED_MISDECLARATION
            hint = (
                f"输出契约 {out_type} 与派生 provider 面 {list(face_variants)[:3]} "
                "冲突 —— 修 capabilities 声明或为该工具注册对应算法链")
        elif gaps:
            classification = CLASS_METADATA_MISSING
            hint = "补 descriptor ABI 元数据（network/deterministic/side_effect/result_size_policy）后再分类"
        else:
            classification = CLASS_LEGAL_MULTI_PROVIDER
            hint = "合法声明面 provider；若需参与解析排序则注册算法链（tool_candidates）"

        entries.append(DivergenceEntry(
            capability=issue.capability,
            tool=issue.tool,
            classification=classification,
            output_semantic_type=out_type,
            derived_output_face=face_variants,
            metadata_gaps=gaps,
            migration_hint=hint,
        ))

    entries.sort(key=lambda e: (
        _CLASS_ORDER.index(e.classification), e.capability, e.tool))
    if len(entries) > MAX_REPORT_ENTRIES:
        entries = entries[:MAX_REPORT_ENTRIES]
        report.truncated = True
    report.entries = entries
    report.counts = {
        c: sum(1 for e in entries if e.classification == c)
        for c in _CLASS_ORDER
    }
    if durable_honesty:
        report.durable_label_honesty = dict(durable_honesty)
    return report


def durable_label_honesty_facts(
    tool_metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """durable 取消标签的部署诚实性披露（只读 settings/env；零 DB I/O）。

    ABI profile 按 ``execution_policy=="celery"`` 投影 ``cancellation=
    "durable"``（声明面）；本节披露该标签在当前部署是否成立：broker 未
    配置（USE_REDIS=False）时 celery 策略回落 THREAD，durable 标签不生效。
    """
    facts: Dict[str, Any] = {
        "policy_version": GOVERNANCE_POLICY_VERSION,
        "broker_configured": None,
        "celery_required": False,
        "label_effective": None,
        "affected_tools": [],
    }
    try:
        from app.core.config import settings

        broker = bool(getattr(settings, "USE_REDIS", False))
    except Exception:  # noqa: BLE001 — settings 缺席按 unknown
        broker = None
    required = os.environ.get("GIS_CELERY_REQUIRED", "").strip().lower() in (
        "1", "true", "yes")
    facts["broker_configured"] = broker
    facts["celery_required"] = required
    if broker is True:
        facts["label_effective"] = True
    elif broker is False:
        facts["label_effective"] = False
    else:
        facts["label_effective"] = None

    if tool_metadata and facts["label_effective"] is False:
        affected = []
        for name in sorted(tool_metadata.keys()):
            meta = tool_metadata[name] or {}
            policy = str(getattr(meta.get("execution_policy"), "value",
                                 meta.get("execution_policy") or "")).lower()
            if policy == "celery":
                affected.append(str(name)[:_STR_MAX])
        facts["affected_tools"] = affected[:32]
        facts["affected_count"] = len(affected)
    return facts


def collect_live_inputs(
    tool_registry: Optional[Any] = None,
) -> Dict[str, Any]:
    """live registry → 治理输入（只读；与 runtime_manifest 采集同构）。"""
    caps_reg_ids: List[str] = []
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        caps_reg_ids = list(get_capability_registry().all_ids)
    except Exception:  # noqa: BLE001 — 词表缺席 → 空（报告如实披露 fatal 0）
        caps_reg_ids = []

    algorithms: List[Any] = []
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        registry = get_algorithm_registry()
        for d in getattr(registry, "_by_id", {}).values():
            algorithms.append(SimpleNamespace(
                capabilities=list(getattr(d, "capabilities", None) or []),
                tool_candidates=list(getattr(d, "tool_candidates", None) or []),
            ))
    except Exception:  # noqa: BLE001
        algorithms = []

    meta_map: Dict[str, Mapping[str, Any]] = {}
    try:
        reg = tool_registry
        if reg is None:
            from app.tools import init_tools
            from app.tools.registry import ToolRegistry

            reg = ToolRegistry()
            init_tools(reg)
        all_meta = getattr(reg, "all_metadata", None)
        names = sorted(all_meta().keys()) if callable(all_meta) else sorted(
            getattr(reg, "_tools", {}).keys())
        getter = getattr(reg, "metadata", None)
        for name in names:
            meta = getter(name) if callable(getter) else None
            meta_map[str(name)[:_STR_MAX]] = meta or {}
    except Exception:  # noqa: BLE001
        meta_map = {}

    return {
        "capability_ids": caps_reg_ids,
        "algorithms": algorithms,
        "tool_metadata": sorted(meta_map.items()),
        "tool_metadata_map": meta_map,
    }


def govern_live_registry(
    tool_registry: Optional[Any] = None,
) -> CapabilityBindingGovernanceReport:
    """live registry 治理报告（CLI/canary 入口）。"""
    inputs = collect_live_inputs(tool_registry)
    honesty = durable_label_honesty_facts(inputs["tool_metadata_map"])
    return govern_capability_bindings(
        capability_ids=inputs["capability_ids"],
        algorithms=inputs["algorithms"],
        tool_metadata=inputs["tool_metadata"],
        durable_honesty=honesty,
    )


def format_governance_report(report: CapabilityBindingGovernanceReport) -> str:
    """markdown 报告（有界；linter/审计面板友好）。"""
    lines = [
        "# Capability Binding Governance Report",
        "",
        f"- policy: `{report.policy_version}`",
        f"- counts: " + ", ".join(
            f"{k}={report.counts.get(k, 0)}" for k in _CLASS_ORDER),
        f"- dangling fatal: {len(report.dangling_fatal)}",
        f"- metadata_incomplete (all tools): {report.metadata_incomplete_total}",
        f"- provider_output_contract_divergence: {report.output_divergence_total}",
    ]
    honesty = report.durable_label_honesty
    if honesty:
        lines += [
            "",
            "## Durable label honesty",
            "",
            f"- broker_configured: {honesty.get('broker_configured')}",
            f"- celery_required: {honesty.get('celery_required')}",
            f"- durable cancellation label effective: "
            f"{honesty.get('label_effective')}",
        ]
        affected = honesty.get("affected_tools") or []
        if affected:
            lines.append(
                f"- affected tools (label NOT effective in this deployment): "
                f"{', '.join(affected[:12])}"
                + (f" … (+{len(affected) - 12})" if len(affected) > 12 else ""))
    if report.truncated:
        lines += ["", f"> truncated at {MAX_REPORT_ENTRIES} entries"]
    lines += ["", "| classification | capability | tool | out | derived face | hint |",
              "|---|---|---|---|---|---|"]
    for e in report.entries[:200]:
        lines.append(
            f"| {e.classification} | {e.capability} | {e.tool} "
            f"| {e.output_semantic_type or '—'} "
            f"| {','.join(e.derived_output_face[:3]) or '—'} "
            f"| {e.migration_hint[:96]} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover
    """CLI：``python -m app.lib.gis.capability_binding_governance [--json]``."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="capability binding divergence governance report")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of markdown")
    args = parser.parse_args(argv)
    report = govern_live_registry()
    if args.json:
        sys.stdout.write(json.dumps(report.to_dict(), ensure_ascii=False,
                                    indent=2, default=str) + "\n")
    else:
        sys.stdout.write(format_governance_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "GOVERNANCE_POLICY_VERSION",
    "CLASS_LEGAL_MULTI_PROVIDER",
    "CLASS_METADATA_MISSING",
    "CLASS_SUSPECTED_MISDECLARATION",
    "MAX_REPORT_ENTRIES",
    "ConformanceIssue",
    "DivergenceEntry",
    "CapabilityBindingGovernanceReport",
    "govern_capability_bindings",
    "govern_live_registry",
    "durable_label_honesty_facts",
    "collect_live_inputs",
    "format_governance_report",
    "main",
]
