"""Unified Finding 契约补全 + 制图/视觉投影接入（方向 5 W-A/W-B）回归锁。

不变式：
1. UnifiedFinding 携带 finding_id / finding_class / user_owned /
   recurrence_fingerprint；id 与指纹稳定、不同 finding 不同；
2. recurrence_fingerprint 与 repair_planner.finding_fingerprint 同构同值
   （一把指纹，不建第二套哈希）；
3. finding_class 推导封闭（FINDING_CLASSES），未知组合保守归
   runtime_display；domain 自带类别语义时优先；
4. 制图 review check（quality loop / runtime lane）fail/warning 入投影
   （semantic_check 域），pass/not_evaluated 与畸形输入不产 finding；
5. collector 接受三种评审形状 + visual seam 产物；visual findings 恒
   degradation_only；dict 形状未经 seam 白名单不入投影；
6. classify_repair 对 semantic_check 域：带建议 → quality_loop 呈现级；
   无建议 → ask_user；user-wins（锁）仍压过一切。
"""

from __future__ import annotations

from typing import Any, Dict

from app.services.gis_harness.completion.contracts import (
    F_CRS_NOT_WGS84,
    F_LAYER_MISSING,
    F_NEEDS_EXECUTION,
    F_SEMANTIC_LEGEND_MISMATCH,
    MapCompletionFinding,
)
from app.services.gis_harness.completion.unified_findings import (
    FINDING_CLASSES,
    collect_unified_findings,
    derive_finding_class,
    from_cartographic_check,
    from_completion_finding,
    from_render_diagnostic,
)
from app.services.gis_harness.repair_planner import (
    classify_repair,
    finding_fingerprint,
)
from app.services.gis_harness.completion.unified_findings import UnifiedFinding


def _finding(
    code: str, severity: str = "error", target: str = "t-1"
) -> MapCompletionFinding:
    return MapCompletionFinding(code=code, severity=severity, target=target, detail="d")


# ── 1. finding_id / recurrence_fingerprint ──────────────────────────────


def test_finding_id_and_fingerprint_stable_and_distinct() -> None:
    a1 = from_completion_finding(_finding(F_LAYER_MISSING, target="lyr-1"))
    a2 = from_completion_finding(_finding(F_LAYER_MISSING, target="lyr-1"))
    b = from_completion_finding(_finding(F_LAYER_MISSING, target="lyr-2"))
    assert a1.finding_id == a2.finding_id
    assert a1.finding_id != b.finding_id
    assert a1.recurrence_fingerprint == a2.recurrence_fingerprint
    assert a1.recurrence_fingerprint != b.recurrence_fingerprint
    # 有界且自描述
    assert len(a1.finding_id) <= 96
    assert a1.finding_id.startswith("harness_fina:layer_missing:")


def test_recurrence_fingerprint_parity_with_w11_ledger() -> None:
    """统一面的指纹与 W11 防循环账本指纹必须同值 —— 跨轮追同因共用一把钥匙。"""
    uf = from_completion_finding(_finding(F_LAYER_MISSING, target="lyr-9"))
    assert finding_fingerprint(uf) == uf.recurrence_fingerprint
    # 与 repair_planner 的原始算法逐字节一致（domain+code+entity 规范哈希）
    from app.services.gis_harness.workflow_instance import canonical_fingerprint

    assert uf.recurrence_fingerprint == canonical_fingerprint(
        {
            "domain": "harness_finalizer",
            "code": F_LAYER_MISSING,
            "entity": "lyr-9",
        }
    )


def test_to_dict_includes_new_fields_bounded() -> None:
    d = from_completion_finding(_finding(F_CRS_NOT_WGS84, target="ref:x")).to_dict()
    assert d["finding_class"] == "gis_correctness"
    assert d["user_owned"] is False
    assert 0 < len(d["finding_id"]) <= 96
    assert len(d["recurrence_fingerprint"]) == 32


# ── 2. finding_class 推导（封闭词表）────────────────────────────────────


def test_finding_class_derivation_table() -> None:
    # code 精确表
    assert (
        derive_finding_class("harness_finalizer", F_NEEDS_EXECUTION, "workflow")
        == "semantic"
    )
    assert (
        derive_finding_class("harness_finalizer", F_CRS_NOT_WGS84, "data")
        == "gis_correctness"
    )
    assert (
        derive_finding_class("harness_finalizer", F_SEMANTIC_LEGEND_MISMATCH, "map")
        == "cartographic"
    )
    assert (
        derive_finding_class("harness_finalizer", F_LAYER_MISSING, "layer")
        == "runtime_display"
    )
    # scope 兜底（未知码）
    assert (
        derive_finding_class("harness_finalizer", "unknown_code_xyz", "data")
        == "gis_correctness"
    )
    assert (
        derive_finding_class("harness_finalizer", "unknown_code_xyz", "workflow")
        == "semantic"
    )
    assert (
        derive_finding_class("harness_finalizer", "unknown_code_xyz", "export")
        == "export"
    )
    # domain 自带类别语义
    assert derive_finding_class("visual", "V_ANY", "map") == "visual"
    assert derive_finding_class("render_diagnostic", "label_x", "export") == "export"
    assert (
        derive_finding_class("semantic_check", "PALETTE_CONTRAST", "layer")
        == "cartographic"
    )
    # 双缺席保守默认
    assert (
        derive_finding_class("harness_finalizer", "weird", "elsewhere")
        == "runtime_display"
    )


def test_finding_class_vocabulary_closed_on_all_projectors() -> None:
    samples = [
        from_completion_finding(_finding(code, severity="error"))
        for code in (
            "needs_execution",
            "crs_not_wgs84",
            "semantic_legend_missing",
            "layer_missing",
            "blank_map_risk",
            "export_component_missing",
        )
    ]
    samples.append(
        from_render_diagnostic({"code": "label_truncated", "severity": "warning"})
    )
    samples.append(
        from_cartographic_check({"rule": "R", "status": "fail", "message": "m"})
    )
    samples.append(UnifiedFinding(domain="visual", code="V", severity="warning"))
    for uf in samples:
        assert uf.finding_class in FINDING_CLASSES, uf.finding_class


# ── 3. user_owned 声明 ──────────────────────────────────────────────────


def test_user_owned_stamped_from_entity_set() -> None:
    f = _finding("layer_hidden", severity="warning", target="lyr-user")
    assert from_completion_finding(f).user_owned is False
    stamped = from_completion_finding(f, user_owned_entities=frozenset({"lyr-user"}))
    assert stamped.user_owned is True
    # 锁声明不改变分类/阻断推导
    assert stamped.finding_class == "runtime_display"
    assert stamped.blocks_completion is False


# ── 4. 制图 review check 投影 ───────────────────────────────────────────


def test_cartographic_fail_with_suggested_fix_projects_repair_class() -> None:
    uf = from_cartographic_check(
        {
            "rule": "PALETTE_SEMANTICS",
            "status": "fail",
            "message": "sequential data on diverging palette",
            "layer_id": "lyr-7",
            "repairability": "auto_safe",
            "suggested_fix": {
                "operation": "change_palette",
                "layer_id": "lyr-7",
                "value": {"colors": ["#000"]},
            },
        }
    )
    assert uf is not None
    assert uf.domain == "semantic_check"
    assert uf.finding_class == "cartographic"
    assert uf.severity == "error"
    assert uf.blocks_completion is True
    assert uf.degradation_only is False
    assert uf.repair_class == "reapply_style"
    assert uf.scope == "layer"
    assert uf.affected_entity == "lyr-7"


def test_cartographic_pass_and_malformed_never_project() -> None:
    assert from_cartographic_check({"rule": "R1", "status": "pass"}) is None
    assert from_cartographic_check({"rule": "R2", "status": "not_evaluated"}) is None
    assert from_cartographic_check({"status": "fail"}) is None  # 无规则名
    assert from_cartographic_check("not-a-dict") is None
    assert from_cartographic_check(None) is None


def test_cartographic_warning_does_not_block() -> None:
    uf = from_cartographic_check(
        {
            "rule": "TITLE_READABILITY",
            "status": "warning",
            "severity": "warning",
            "message": "small title",
        }
    )
    assert uf is not None
    assert uf.severity == "warning"
    assert uf.blocks_completion is False
    assert uf.repair_class == ""  # 无 suggested_fix → 分类走 ask_user


# ── 5. collector 三形状 + visual 接入 ───────────────────────────────────


def _check(rule: str, status: str = "fail", layer: str = "lyr-a") -> Dict[str, Any]:
    return {"rule": rule, "status": status, "message": f"{rule} msg", "layer_id": layer}


def test_collect_accepts_three_review_shapes() -> None:
    checks = [_check("RULE_A"), _check("RULE_B", status="warning")]
    # 形状 1：CartographicLoopResult（review 嵌套）
    r1 = collect_unified_findings(cartographic_review={"review": {"checks": checks}})
    # 形状 2：CartographyReport（checks 直挂）
    r2 = collect_unified_findings(cartographic_review={"checks": checks})
    # 形状 3：_cartographic_review map_state 块（cartography + desired_review）
    r3 = collect_unified_findings(
        cartographic_review={
            "cartography": {"checks": [_check("RULE_A")]},
            "desired_review": None,
            "gate": {},
        }
    )
    assert [u.code for u in r1] == ["RULE_A", "RULE_B"]
    assert [u.code for u in r2] == ["RULE_A", "RULE_B"]
    assert [u.code for u in r3] == ["RULE_A"]
    for u in r1 + r2 + r3:
        assert u.domain == "semantic_check"


def test_collect_dedupes_rules_across_shapes() -> None:
    review = {
        "review": {"checks": [_check("RULE_A")]},
        "cartography": {
            "checks": [_check("RULE_A")],
            "desired_review": {"checks": [_check("RULE_A")]},
        },
    }
    out = collect_unified_findings(cartographic_review=review)
    assert len(out) == 1


def test_collect_accepts_visual_findings_objects_only() -> None:
    vf = UnifiedFinding(
        domain="visual",
        code="V_HIERARCHY",
        severity="warning",
        source="visual_evaluator",
        degradation_only=True,
    )
    out = collect_unified_findings(visual_findings=[vf, {"code": "RAW"}])
    assert [u.code for u in out] == ["V_HIERARCHY"]
    assert out[0].degradation_only is True
    assert out[0].blocks_completion is False
    assert out[0].finding_class == "visual"


# ── 6. classify_repair 对 semantic_check 域 ─────────────────────────────


def test_semantic_check_with_fix_routes_to_quality_loop() -> None:
    uf = from_cartographic_check(
        {
            "rule": "OPACITY_NORMALIZATION",
            "status": "fail",
            "message": "m",
            "layer_id": "lyr-2",
            "suggested_fix": {
                "operation": "normalize_opacity",
                "layer_id": "lyr-2",
                "value": 1.0,
            },
        }
    )
    a = classify_repair(uf)
    assert (a.repair_class, a.safety, a.executor) == (
        "reapply_style",
        "presentation_only",
        "quality_loop",
    )


def test_semantic_check_without_fix_asks_user() -> None:
    uf = from_cartographic_check(
        {"rule": "UNFIXABLE_RULE", "status": "fail", "message": "m"}
    )
    a = classify_repair(uf)
    assert (a.repair_class, a.safety, a.executor) == (
        "ask_user",
        "requires_user_approval",
        "user",
    )


def test_semantic_check_locked_entity_still_user_wins() -> None:
    uf = from_cartographic_check(
        {
            "rule": "OPACITY_NORMALIZATION",
            "status": "fail",
            "message": "m",
            "layer_id": "lyr-locked",
            "suggested_fix": {
                "operation": "normalize_opacity",
                "layer_id": "lyr-locked",
                "value": 1.0,
            },
        }
    )
    a = classify_repair(uf, locked_entities=frozenset({"lyr-locked"}))
    assert a.safety == "not_allowed"
    assert a.executor == "none"
