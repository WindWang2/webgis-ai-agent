"""G30 分类型目录投影 —— UI/Pi 共用的可解释目录（有界、确定性）。

同一工作台目录里分型展示三类资产（不按同名合并、不互相覆盖）：

- ``gis_skill``：GIS SkillLibrary 契约（version/digest/runnable）；
- ``md_guide``：``app/skills/*.md`` 方法指南（知识资产，无执行面）；
- ``extension_candidate``：扩展包声明的技能（candidate 治理，未入 trusted，
  可发现但不可自动取得执行资格）。

runnability 词表（closed）：``runnable | guidance_only | deprecated |
unavailable``。原因字段必须可解释（资产形状/治理/能力），不猜测。
"""
from __future__ import annotations

from typing import Any, Dict, List

#: 可运行性词表。
RUNNABILITY = ("runnable", "guidance_only", "deprecated", "unavailable")


def gis_skill_entry(skill: Any, *, capability_available=None) -> Dict[str, Any]:
    """单个 GIS 技能的目录条目（有界投影；不泄 procedure 全文）。"""
    runnability = "runnable"
    reasons: List[str] = []
    if skill.deprecated:
        runnability = "deprecated"
        reasons.append(f"已弃用：替代 {skill.deprecated_by or '（未声明）'}")
    elif skill.pack != "core":
        runnability = "guidance_only"
        reasons.append(f"pack={skill.pack}（非 trusted，仅方法指导）")
    elif skill.execution is None or not skill.execution.steps:
        runnability = "guidance_only"
        reasons.append("无执行映射（方法知识资产，经规划路径提供指导）")
    else:
        # 执行映射在但 required hard 能力不可用（阳性事实）→ unavailable
        missing = []
        if capability_available is not None:
            for req in skill.capability_requirements:
                if req.criticality == "required" and req.hard_gate \
                        and not capability_available(req.capability_id):
                    missing.append(req.capability_id)
        if missing:
            runnability = "unavailable"
            reasons.append("必需能力不可用：" + ", ".join(missing[:4]))
    return {
        "asset_type": "gis_skill",
        "id": skill.id,
        "name": skill.name,
        "description": (skill.description or "")[:160],
        "domain": skill.domain,
        "version": skill.version,
        "digest": skill.fingerprint()[:16],
        "deprecated": skill.deprecated,
        "deprecated_by": skill.deprecated_by if skill.deprecated else "",
        "when_to_use": (skill.when_to_use or "")[:200],
        "when_not_to_use": (skill.when_not_to_use or "")[:160],
        "runnability": runnability,
        "runnability_reasons": reasons[:4],
        "result_kind": skill.execution.result_kind if skill.execution else "",
        "parameters": [
            {
                "name": p.name, "label": p.label or p.name, "type": p.type,
                "required": p.required, "unit": p.unit,
                "enum_values": list(p.enum_values),
                "default": p.default, "role": p.role, "expects": p.expects,
                "required_if_param": p.required_if_param,
                "required_if_in": list(p.required_if_in),
                "description": (p.description or "")[:160],
                "max_length": p.max_length,
                "min_value": p.min_value, "max_value": p.max_value,
            }
            for p in skill.parameters
        ],
        "role_bindings": [
            {
                "role": rb.role, "required": rb.required,
                "geometry_kinds": list(rb.geometry_kinds),
                "min_features": rb.min_features,
                "description": (rb.description or "")[:120],
            }
            for rb in skill.role_bindings
        ],
        "steps_summary": [
            {"step_id": s.step_id, "title": s.title, "kind": s.kind}
            for s in skill.procedure.steps[:10]
        ],
        "tools": (
            sorted({st.tool for st in skill.execution.steps})
            if skill.execution else []
        ),
        "examples": [
            {"title": e.title, "query": e.query[:80]}
            for e in skill.examples[:3]
        ],
    }


def build_catalog(*, domain: str = "", include_deprecated: bool = True) -> Dict[str, Any]:
    """分类型目录（GIS 技能 + md 指南 + 扩展 candidate；有界）。"""
    from app.services.gis_harness.skills.loader import get_skill_library

    lib = get_skill_library()

    def capability_available(cap: str) -> bool:
        # 目录级能力核对用 CapabilityRegistry 存在性（结构事实）；运行时
        # 可用性由 prepare/dispatch 的快照闸裁决 —— 目录只做可解释初筛。
        try:
            from app.services.gis_harness.skills.bridges import (
                default_capability_exists,
            )
            return default_capability_exists(cap)
        except Exception:  # noqa: BLE001 — 目录绝不因谓词失败而空转
            return True

    entries: List[Dict[str, Any]] = []
    for skill in lib.skills:
        if domain and skill.domain != domain:
            continue
        if skill.deprecated and not include_deprecated:
            continue
        entries.append(gis_skill_entry(skill, capability_available=capability_available))

    # md 指南（另一资产类型：只读知识，无执行面；与 GIS 技能同名不合并）
    md_guides: List[Dict[str, Any]] = []
    try:
        from app.tools.skills import list_md_skills

        for item in list_md_skills()[:60]:
            if not isinstance(item, dict):
                continue
            md_guides.append({
                "asset_type": "md_guide",
                "id": str(item.get("name") or "")[:80],
                "name": str(item.get("name") or "")[:80],
                "description": str(item.get("description") or "")[:160],
                "category": str(item.get("category") or "")[:40],
                "runnability": "guidance_only",
                "runnability_reasons": ["方法指南（markdown 知识资产），无执行面"],
            })
    except Exception:  # noqa: BLE001 — 指南目录缺席不阻断技能目录
        md_guides = []

    # 扩展 candidate（可发现、不自动晋级）
    extension_candidates: List[Dict[str, Any]] = []
    try:
        from app.extensions_platform.host import get_extension_host
        from app.extensions_platform.pack_catalog import build_pack_catalog

        host = get_extension_host()
        if host is not None:
            catalog = build_pack_catalog(host)
            for ns in catalog.get("namespaces", []):
                for pack in ns.get("packs", []):
                    surface = pack.get("surface") or {}
                    for sk in (surface.get("skills") or []):
                        extension_candidates.append({
                            "asset_type": "extension_candidate",
                            "id": str(sk.get("id") or "")[:96],
                            "pack": str(pack.get("id") or "")[:60],
                            "runnability": "guidance_only",
                            "runnability_reasons": [
                                "扩展包声明技能（candidate 治理）—— "
                                "安装/认证不自动授予执行资格"],
                        })
    except Exception:  # noqa: BLE001 — 扩展目录缺席不阻断
        extension_candidates = []

    runnable_count = sum(1 for e in entries if e["runnability"] == "runnable")
    return {
        "schema_version": "skilljob.catalog/v1",
        "skills": entries,
        "md_guides": md_guides,
        "extension_candidates": extension_candidates[:20],
        "counts": {
            "gis_skills": len(entries),
            "runnable": runnable_count,
            "md_guides": len(md_guides),
            "extension_candidates": len(extension_candidates),
        },
    }
