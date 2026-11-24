"""carto.paper_scale_plan 检查（GOAL 04 / WP4b）—— 出版冻结计划声明一致性。

与 ``semantic_checks_scale_profile``（zoom 带档案检查）同族但独立成码：
本检查核 ``layer.scale_profile.paper_plans``（paper_scale_plan.v1 投影）
及其与表达窗口/工件源的**跨面一致**。

核验（全部 desired-state 证据，确定性）：
- 计划块可解析、schema_version 识别；
- frozen 状态 ⇒ artifact_ref / artifact_source_id 在场，且该 source 真实
  存在于 spec.sources（防「声明即信」的悬空工件）；
- export_window：mode ∈ ARTIFACT_MODES 词表、source 与 artifact_source_id
  一致、数值域合法（min<max）；
- 统计口径 classification_basis 恒 original_source；
- 表达窗口中存在指向该工件的窗（计划 ↔ 窗口跨面一致）。

无声明（旧 spec / 未 apply 层）→ 零行为变化（不产生检查）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from app.lib.cartography.semantic_checks import CartographyReport

from app.lib.cartography.representation import ARTIFACT_MODES

_PLAN_SCHEMA_VERSIONS = ("paper_scale_plan.v1",)


def check_paper_scale_plan(
    report: "CartographyReport",
    mapspec: Dict[str, Any],
    layer: Dict[str, Any],
    lid: Optional[str],
    sid: Optional[str],
) -> None:
    """carto.paper_scale_plan —— 出版冻结计划声明一致性。"""
    declared = (layer.get("scale_profile") or {}).get("paper_plans")
    if not isinstance(declared, dict) or not declared:
        return  # 无冻结计划：不评（零行为变化）

    sources = mapspec.get("sources") if isinstance(
        mapspec.get("sources"), dict) else {}
    windows = (layer.get("representation") or {}).get("windows") \
        if isinstance(layer.get("representation"), dict) else []
    window_sources = {
        str(w.get("source")) for w in windows
        if isinstance(w, dict) and isinstance(w.get("source"), str)
    }

    problems: List[str] = []
    frozen_count = 0
    sample_evidence: Dict[str, Any] = {}
    for fingerprint, plan in declared.items():
        if not isinstance(plan, dict):
            problems.append(f"{fingerprint}:plan_not_parseable")
            continue
        if plan.get("schema_version") not in _PLAN_SCHEMA_VERSIONS:
            problems.append(f"{fingerprint}:unknown_schema_version")
            continue
        status = plan.get("status")
        if status == "frozen":
            frozen_count += 1
            ref = plan.get("artifact_ref")
            src_id = plan.get("artifact_source_id")
            if not (isinstance(ref, str) and ref):
                problems.append(f"{fingerprint}:artifact_ref_missing")
            if not (isinstance(src_id, str) and src_id):
                problems.append(f"{fingerprint}:artifact_source_missing")
            elif not isinstance(sources.get(src_id), dict):
                problems.append(f"{fingerprint}:artifact_source_absent")
            window = plan.get("export_window")
            if not isinstance(window, dict):
                problems.append(f"{fingerprint}:export_window_missing")
            else:
                if window.get("mode") not in ARTIFACT_MODES:
                    problems.append(f"{fingerprint}:window_mode_not_artifact")
                if str(window.get("source") or "") != (src_id or ""):
                    problems.append(f"{fingerprint}:window_source_mismatch")
                mn, mx = window.get("min_zoom"), window.get("max_zoom")
                if not (isinstance(mn, (int, float))
                        and isinstance(mx, (int, float))
                        and not isinstance(mn, bool)
                        and not isinstance(mx, bool) and float(mn) < float(mx)):
                    problems.append(f"{fingerprint}:window_bounds_invalid")
            # 计划 ↔ 窗口跨面一致：层声明了 representation 时，表达窗中
            # 必须存在指向该工件的窗（完全无 representation 的旧层不追溯）
            if isinstance(src_id, str) and src_id \
                    and isinstance(layer.get("representation"), dict) \
                    and src_id not in window_sources:
                problems.append(f"{fingerprint}:window_not_in_representation")
        elif status != "planned":
            problems.append(f"{fingerprint}:unknown_status")
        stats = plan.get("stats") or {}
        if stats.get("classification_basis") != "original_source":
            problems.append(f"{fingerprint}:classification_basis_not_original")
        if "fingerprint" not in plan:
            problems.append(f"{fingerprint}:fingerprint_missing")
        if not sample_evidence:
            sample_evidence = {
                "schema_version": plan.get("schema_version"),
                "status": status,
                "family": plan.get("family"),
                "target_denominator": (plan.get("contract") or {}).get(
                    "target_denominator"),
                "qa_summary": plan.get("qa_summary"),
            }

    evidence = {
        "plans_declared": len(declared),
        "plans_frozen": frozen_count,
        "sample": sample_evidence,
    }
    if problems:
        report.add_check(
            "carto.paper_scale_plan", "fail",
            f"Layer '{lid}' paper scale plan inconsistent: "
            f"{','.join(sorted(set(problems))[:6])}",
            severity="error", layer_id=lid, source_id=sid,
            evidence={**evidence, "problems": sorted(set(problems))[:12]},
            repairability="not_repairable",
        )
        return
    report.add_check(
        "carto.paper_scale_plan", "pass",
        f"Layer '{lid}' paper scale plans consistent ({frozen_count} frozen)",
        layer_id=lid, source_id=sid,
        evidence=evidence,
    )


__all__ = ["check_paper_scale_plan"]
