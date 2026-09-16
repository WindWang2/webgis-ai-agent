"""Pack catalog：认证状态 + 有界能力面投影（ADR-0199）。

把已发现扩展整理为确定性 catalog：每个 pack 的声明面（tools/algorithms/
providers/skills…）、命名空间化投影名、持久化认证报告状态
（missing / stale / valid / invalid）。``certified_only=True`` 时只保留
「报告存在且 certified 且指纹匹配」的 pack —— 这是给上层（运维盘点、
有界 LLM surface 投影的消费方）的过滤视图。

边界（诚实性）：
- 本模块只读：不激活、不探针、不写任何 registry；
- catalog 不改变 ToolSurface 的 tier 语义（扩展工具 ≤ tier 2 由
  manifest/SDK 层强制，这里只是如实转述）；
- pack 技能在 catalog 中以 ``governance_tier: "candidate"`` 呈现——
  它们尚未进入 SkillPolicy 的 trusted 面（core 包专属），目录如实标注。
"""

from __future__ import annotations

from typing import Any

from .diagnostics import DiagnosticCode
from .discovery import CERTIFICATION_FILENAME
from .host import ExtensionHost, ExtensionRecord


def certification_status_for(record: ExtensionRecord) -> dict[str, Any]:
    """单包认证报告状态（只读；evidence 模式指纹校验，不验 HMAC）。"""
    from .capability_certification import load_certification_report

    base: dict[str, Any] = {"report_file": CERTIFICATION_FILENAME}
    if record.fingerprint is None:
        base.update(state="invalid", detail="pack fingerprint unavailable")
        return base
    report, diag = load_certification_report(record.path, record.fingerprint, mode="evidence")
    if report is not None:
        base.update(
            state="valid",
            certified=True,
            signed="hmac" in report,
            execution_mode=report.get("execution_mode"),
        )
        return base
    code = diag.code if diag is not None else DiagnosticCode.CERTIFICATION_INVALID
    detail = diag.message if diag is not None else "unknown"
    if code is DiagnosticCode.CERTIFICATION_REQUIRED:
        base.update(state="missing", certified=False, detail=detail)
    elif code is DiagnosticCode.CERTIFICATION_STALE:
        base.update(state="stale", certified=False, detail=detail)
    else:
        base.update(state="invalid", certified=False, detail=detail)
    return base


def _extension_entry(host: ExtensionHost, record: ExtensionRecord) -> dict[str, Any]:
    manifest = record.manifest
    return {
        "id": manifest.id,
        "version": manifest.version,
        "api_version": manifest.api_version,
        "state": record.state.value,
        "trust": record.trust.value,
        "description": manifest.description,
        "execution_mode": manifest.execution.mode if manifest.execution else "in_process",
        "certification": certification_status_for(record),
        "surface": {
            "tools": [
                {
                    "name": manifest.namespaced_tool_name(t.name),
                    "tier": t.tier,
                    "side_effect": t.side_effect,
                    "domains": list(t.domains),
                }
                for t in manifest.tools
            ],
            "algorithms": [
                {
                    "id": manifest.namespaced_algorithm_id(a.id),
                    "scientific_status": a.scientific_status,
                }
                for a in manifest.algorithms
            ],
            "data_providers": [
                {"source_type": manifest.namespaced_source_type(p.source_type)}
                for p in manifest.data_providers
            ],
            "skills": [
                {
                    "id": manifest.namespaced_skill_id(s.skill_id),
                    # 诚实标注：pack 技能不在 SkillPolicy trusted 面。
                    "governance_tier": "candidate",
                }
                for s in (getattr(manifest, "skills", []) or [])
            ],
            "cartography_items": [
                {"kind": c.kind, "id": f"{manifest.namespace}_{c.id}"}
                for c in manifest.cartography_items
            ],
            "workflow_packs": [
                {"pack_id": f"{manifest.namespace}_{w.pack_id}"}
                for w in manifest.workflow_packs
            ],
            "model_providers": [
                {"id": manifest.namespaced_model_provider_tool(m.id)}
                for m in manifest.model_providers
            ],
        },
    }


def build_pack_catalog(
    host: ExtensionHost,
    *,
    certified_only: bool = False,
) -> dict[str, Any]:
    """构建确定性 pack catalog（按 namespace 分组、条目排序）。

    ``certified_only=True``：只保留认证报告 valid 且 certified 的 pack
    （过滤发生在 pack 级——能力级的细粒度证据见认证报告本身）。
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    skipped: list[dict[str, Any]] = []
    for extension_id in host.extension_ids():
        record = host.get_record(extension_id)
        if record is None:  # pragma: no cover - list/get 同源
            continue
        entry = _extension_entry(host, record)
        if certified_only and entry["certification"].get("state") != "valid":
            skipped.append({"id": extension_id, "reason": entry["certification"]["state"]})
            continue
        grouped.setdefault(record.manifest.namespace, []).append(entry)
    return {
        "catalog_schema_version": 1,
        "namespaces": [
            {"namespace": ns, "packs": grouped[ns]} for ns in sorted(grouped)
        ],
        **({"skipped": skipped} if certified_only else {}),
    }


__all__ = [
    "build_pack_catalog",
    "certification_status_for",
]
