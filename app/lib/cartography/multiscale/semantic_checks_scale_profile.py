"""carto.scale.profile 检查（GOAL 04 / M5）—— 多尺度表达档案声明一致性。

与 ``semantic_checks_representation``（v1.6 窗口检查）同族但独立成码：
窗口检查核 ``layer.representation``；本检查核 ``layer.scale_profile``
（ScaleRepresentationProfile.v1 投影）及其与窗口/统计口径的**跨面一致**。

核验（全部 desired-state 证据，确定性）：
- 档案结构合法（multiscale.validate_profile：带覆盖/词表/统计自洽）；
- generalized 窗口必须携带 source 覆盖，且该 source 真实存在于
  spec.sources（工件源装配在场 —— 防止「声明即信」的悬空窗口）；
- 统计口径：displayed ≤ original、classification_basis 恒 original_source
  （分类/图例计算不得建立在渲染副本上 —— D7）；
- 取舍证据：kept_values 非空 ⇔ importance_evidence ≠ none（D4 对偶）。

无声明（旧 spec / 未 apply 层）→ not_evaluated（零行为变化）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from app.lib.cartography.semantic_checks import CartographyReport


def check_scale_profile(
    report: "CartographyReport",
    mapspec: Dict[str, Any],
    layer: Dict[str, Any],
    lid: Optional[str],
    sid: Optional[str],
) -> None:
    """carto.scale.profile —— 多尺度表达档案声明一致性（GOAL 04 / M5）。"""
    declared = layer.get("scale_profile")
    if not isinstance(declared, dict) or not declared:
        return  # 未 apply 的层：不评（零行为变化）

    problems: List[str] = []
    sources = mapspec.get("sources") if isinstance(
        mapspec.get("sources"), dict) else {}

    # 1) 结构合法性（权威 = multiscale 档案契约的 validate）
    from app.lib.cartography.multiscale.profile import (
        ScaleRepresentationProfile,
        validate_profile,
    )

    try:
        profile = ScaleRepresentationProfile.model_validate(declared)
        struct_errors = validate_profile(profile)
    except Exception as exc:  # noqa: BLE001 — 坏声明可诊断不抛
        report.add_check(
            "carto.scale.profile", "fail",
            f"Layer '{lid}' scale_profile is not parseable: {exc}",
            severity="error", layer_id=lid, source_id=sid,
            evidence={"schema_version": declared.get("schema_version")},
            repairability="not_repairable",
        )
        return
    if struct_errors:
        problems.extend(struct_errors)

    # 2) generalized 窗口 ↔ 工件源装配一致（跨面：representation × sources）
    windows = (layer.get("representation") or {}).get("windows") \
        if isinstance(layer.get("representation"), dict) else []
    dangling = []
    if isinstance(windows, list):
        for w in windows:
            if isinstance(w, dict) and w.get("mode") in (
                    "generalized", "aggregate_grid"):
                override = w.get("source")
                if not (isinstance(override, str) and override):
                    dangling.append(f"{w.get('min_zoom')}:no_source_override")
                elif not isinstance(sources.get(override), dict):
                    dangling.append(f"{override}:source_absent")
    if dangling:
        problems.append("artifact_window_source_missing")

    # 3) 统计口径（D7）
    if declared.get("classification_basis") != "original_source":
        problems.append("classification_basis_not_original")
    for band in declared.get("bands") or []:
        if not isinstance(band, dict):
            continue
        stats = band.get("stats") or {}
        if stats.get("displayed_feature_count", 0) > stats.get(
                "original_feature_count", 0):
            problems.append("displayed_exceeds_original")

    # 4) 取舍证据对偶（D4：无证据 ⇒ 无取舍）
    for band in declared.get("bands") or []:
        sel = band.get("selection") or {}
        if sel.get("importance_evidence") == "none" and sel.get("kept_values"):
            problems.append("selection_without_evidence")

    tier_stats = {
        b.get("scale", {}).get("tier", "?"): {
            "displayed": (b.get("stats") or {}).get("displayed_feature_count"),
            "original": (b.get("stats") or {}).get("original_feature_count"),
        }
        for b in declared.get("bands") or [] if isinstance(b, dict)
    }
    evidence = {
        "schema_version": declared.get("schema_version"),
        "profile_id": declared.get("profile_id"),
        "tier_stats": tier_stats,
        "classification_basis": declared.get("classification_basis"),
    }
    if problems:
        report.add_check(
            "carto.scale.profile", "fail",
            f"Layer '{lid}' scale profile inconsistent: "
            f"{','.join(sorted(set(problems))[:6])}",
            severity="error", layer_id=lid, source_id=sid,
            evidence={**evidence, "problems": sorted(set(problems))[:12],
                      "dangling": dangling[:6]},
            repairability="not_repairable",
        )
        return
    report.add_check(
        "carto.scale.profile", "pass",
        f"Layer '{lid}' scale profile consistent"
        f" (classification_basis=original_source)",
        layer_id=lid, source_id=sid,
        evidence=evidence,
    )


__all__ = ["check_scale_profile"]
