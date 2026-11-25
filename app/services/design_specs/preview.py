"""Design Spec 预览 —— 有效版本解析 / 渲染目标支持面 / 冲突披露（WP1）。

预览是**只读投影**：列出支持的渲染目标（theme output_targets × 真实
字体可用性 × 纸张 profile）、引用校验结果、继承链与 digest。不执行任何
mutation，不产生第二真相。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.design_specs.contract import (
    OUTPUT_TARGETS,
    structural_digest,
    validate_design_spec_payload,
)
from app.services.design_specs.versioning import get_spec, get_version, resolve_payload


def build_design_spec_preview(
    db: Any,
    spec_id: str,
    *,
    version: Optional[int] = None,
) -> Dict[str, Any]:
    """规范 → 预览（有效 payload + 渲染目标 + 冲突 + 继承链 + digest）。"""
    spec = get_spec(db, spec_id)
    row = get_version(db, spec_id, version)
    effective, chain = resolve_payload(db, row)
    report = validate_design_spec_payload(effective)

    # 渲染目标支持面：theme 声明的 output_targets（权威）∩ 规范声明目标；
    # 未声明 theme 时按规范目标 + 字体/纸张真实性校验。
    supported: List[str] = []
    unsupported: List[Dict[str, str]] = []
    declared_targets = list((effective.get("output") or {}).get("targets") or [])
    theme_id = (effective.get("color") or {}).get("theme_id")
    theme_targets: Optional[List[str]] = None
    if theme_id:
        from app.lib.cartography.themes import get_cartographic_theme_registry

        theme = get_cartographic_theme_registry().get_theme(str(theme_id))
        if theme is not None:
            theme_targets = [t for t in theme.output_targets if t in OUTPUT_TARGETS]

    for target in declared_targets:
        if theme_targets is not None and target not in theme_targets:
            unsupported.append(
                {
                    "target": target,
                    "reason": "theme_does_not_declare_target",
                    "theme": str(theme_id),
                }
            )
            continue
        supported.append(target)

    # 纸张 profile 真实性（声明即校验 —— OUTPUT_TARGETS 词表无 "print"，
    # 旧分支恒 False 是死代码，R1-P3）。
    paper = (effective.get("output") or {}).get("paper_profile")
    if paper:
        from app.lib.cartography.publication_ir import PAGE_PROFILES

        if str(paper) not in PAGE_PROFILES:
            unsupported.append(
                {"target": "print", "reason": "unknown_paper_profile", "paper": str(paper)}
            )

    # 字体真实可用性（G34 font_resource_status：vendor 缺席/字形缺失如实披露）。
    font_findings: List[Dict[str, Any]] = []
    font_id = (effective.get("typography") or {}).get("font_resource")
    if font_id:
        try:
            from app.services.print_layout_authoring.fonts import font_resource_status

            status = font_resource_status(str(font_id))
            if status and not status.get("available", True):
                font_findings.append(
                    {
                        "font": str(font_id),
                        "issues": list(status.get("issues") or [])[:8],
                    }
                )
        except Exception:  # noqa: BLE001 —— 字体探测失败如实降级，不阻断预览
            font_findings.append({"font": str(font_id), "issues": ["status_probe_failed"]})

    conflicts: List[Dict[str, str]] = []
    for v in report["violations"]:
        conflicts.append({"field": v["field"], "ref": v.get("ref", ""), "reason": v["reason"]})

    return {
        "spec": {
            "id": spec.id,
            "name": spec.name,
            "status": spec.status,
            "is_builtin": bool(spec.is_builtin),
            "current_version": spec.current_version,
            "retired": spec.retired_at is not None,
            "structural_digest": spec.structural_digest,
        },
        "version": {
            "id": row.id,
            "version": row.version,
            "parent_version_id": row.parent_version_id,
            "deprecated": row.deprecated_at is not None,
            "deprecation_note": row.deprecation_note,
            "structural_digest": row.structural_digest,
        },
        "effective_payload": effective,
        "inheritance_chain": chain,
        "reference_validation": {
            "ok": report["ok"],
            "violations": report["violations"],
            "warnings": report["warnings"],
        },
        "render_targets": {
            "supported": supported,
            "unsupported": unsupported,
            "font_findings": font_findings,
        },
        "conflicts": conflicts[:32],
        "resolved_digest": structural_digest(effective),
    }


__all__ = ["build_design_spec_preview"]
