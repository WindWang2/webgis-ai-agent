"""Workflow V4 —— Methodology Family Model 单测（Wave 1+2）。

覆盖：注册表/词表完整性、与既有 registry 的悬空引用对账（fail-loud）、
确定性、资格裁决（硬准则拒绝 + 排序稳定 + unknown 语义）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.workflow_v4.methodology import (
    METHODOLOGY_FAMILIES,
    METHODOLOGY_SCHEMA_VERSION,
    MethodologyRegistry,
    get_methodology_registry,
    qualify_method_candidates,
    reset_methodology_registry,
    resolve_methodology_family,
)


@pytest.fixture()
def registry() -> MethodologyRegistry:
    return get_methodology_registry()


# ── Wave 1：族模型与词表 ─────────────────────────────────────────────────

def test_family_vocabulary_exact(registry: MethodologyRegistry) -> None:
    """Epic 11：12 族 + 纯加法 proximity = 13 族（唯一允许改动的计数断言）。"""
    assert METHODOLOGY_SCHEMA_VERSION == 4
    assert registry.family_count() == 13
    assert {f.family_id for f in registry.families()} == set(METHODOLOGY_FAMILIES)
    assert [f.family_id for f in registry.families()] == list(METHODOLOGY_FAMILIES)


def test_every_family_has_candidates_and_tasks(
    registry: MethodologyRegistry,
) -> None:
    for fam in registry.families():
        assert fam.candidate_methods, fam.family_id
        assert fam.ontology_task_ids, fam.family_id


def test_family_for_task_deterministic(registry: MethodologyRegistry) -> None:
    fams = registry.family_for_task("distribution.point_distribution")
    assert fams, "point_distribution 必须至少被一个方法族覆盖"
    assert [f.family_id for f in fams] == [
        f.family_id for f in registry.family_for_task("distribution.point_distribution")
    ]
    primary = resolve_methodology_family("distribution.point_distribution")
    assert primary is not None and primary.family_id == fams[0].family_id
    assert resolve_methodology_family("no.such_task") is None


def test_registry_references_no_dangling_ids(
    registry: MethodologyRegistry,
) -> None:
    """悬空引用 fatal：候选的 capability/algorithm/artifact 引用必须命中
    真实 registry（单一事实源对账）。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.artifacts import get_artifact_type_registry
    from app.lib.gis.capability_registry import get_capability_registry

    caps, algs, arts = (
        get_capability_registry(), get_algorithm_registry(),
        get_artifact_type_registry(),
    )
    caps.load_builtins()
    algs.load_builtins()
    arts.load_builtins()
    violations = registry.validate(
        capability_exists=caps.has,
        algorithm_exists=algs.has,
        artifact_type_exists=arts.has,
    )
    assert violations == []


def test_registry_fingerprint_stable() -> None:
    reset_methodology_registry()
    fp1 = get_methodology_registry().fingerprint
    reset_methodology_registry()
    fp2 = get_methodology_registry().fingerprint
    assert fp1 == fp2 and len(fp1) == 64


# ── Wave 2：资格裁决与排序 ────────────────────────────────────────────────

_PROFILE_POINTS = {
    "featureCount": 500,
    "geometryTypes": ["Point"],
    "fields": {"school_name": {"type": "string"}},
}


def test_density_family_prefers_quantitative_over_visual() -> None:
    """专业首选（定量核密度）优先于视觉代理热力图 —— 排序不靠 LLM。"""
    q = qualify_method_candidates(
        "distribution_density",
        role_states={"subject": "eligible", "boundary": "unknown",
                     "denominator": "unknown"},
        profile=_PROFILE_POINTS,
    )
    assert q.selected_id == "density.kernel_surface"
    assert q.qualifications[0].status == "selected"
    # 视觉热力图永远 eligible 但垫底 + 代理披露
    heatmap = next(x for x in q.qualifications
                   if x.method_id == "density.visual_heatmap")
    assert heatmap.status == "eligible"
    assert heatmap.disclosures
    assert heatmap.rank == max(x.rank for x in q.qualifications)


def test_insufficient_sample_rejects_kriging_but_selects_idw() -> None:
    """n=8 时克里金被资格拒绝（机器可读理由），IDW 顶上 —— 方法选择由
    资格事实决定，不是自由猜。"""
    q = qualify_method_candidates(
        "interpolation",
        role_states={"subject": "eligible", "measure": "eligible"},
        profile={"featureCount": 8, "geometryTypes": ["Point"],
                 "fields": {"v": {"type": "number"}}},
    )
    assert q.selected_id == "interp.idw"
    kriging = next(x for x in q.qualifications
                   if x.method_id == "interp.ordinary_kriging")
    assert kriging.status == "rejected"
    assert any(c.startswith("METHOD_MIN_SAMPLE_UNMET") for c in kriging.reason_codes)


def test_unknown_facts_never_reject() -> None:
    """画像无事实（unknown ≠ 不满足）：无 featureCount/几何 → 零硬拒绝。"""
    q = qualify_method_candidates(
        "interpolation", role_states={}, profile=None,
    )
    assert q.selected_id
    assert not q.all_rejected
    assert all(x.status in ("selected", "eligible") for x in q.qualifications)


def test_role_blocked_rejects_with_stable_code() -> None:
    q = qualify_method_candidates(
        "distribution_density", role_states={"subject": "blocked"},
        profile=_PROFILE_POINTS,
    )
    assert q.all_rejected and q.selected_id == ""
    rejected = q.qualifications[0]
    assert rejected.status == "rejected"
    assert any(c.startswith("METHOD_ROLE_BLOCKED:subject")
               for c in rejected.reason_codes)


def test_crs_transform_is_soft_not_rejecting() -> None:
    """地理坐标系（度）→ 度量类方法 precondition = REQUIRES_TRANSFORM：
    软惩罚 + 披露（可修复），不硬拒绝 —— 与 data_qualification 的
    transform_required 同哲学（修复链物化为 transform step）。"""
    q = qualify_method_candidates(
        "terrain_hydrology",
        role_states={"elevation": "eligible"},
        profile={"geometryKinds": ["raster"], "crs": "EPSG:4326"},
    )
    slope = next(x for x in q.qualifications
                 if x.method_id == "terrain.slope_aspect")
    assert slope.status in ("selected", "eligible")
    assert not slope.reason_codes
    assert any("先变换" in d or "重投影" in d for d in slope.disclosures)
    assert slope.evidence["preconditions"]["local_metric_crs_required"] == "transform"
    assert not q.all_rejected


def test_precondition_insufficient_data_still_hard_rejects() -> None:
    """事实在场且科学不成立（INSUFFICIENT_DATA/INVALID_METHOD）→ 仍硬拒绝
    （unknown ≠ 不满足 的边界不因 transform 软化而失守）。"""
    q = qualify_method_candidates(
        "interpolation",
        role_states={"subject": "eligible", "measure": "eligible"},
        profile={"featureCount": 5, "geometryTypes": ["Point"],
                 "fields": {"v": {"type": "number"}}},
    )
    kriging = next(x for x in q.qualifications
                   if x.method_id == "interp.ordinary_kriging")
    assert kriging.status == "rejected"
    assert any(c.startswith("METHOD_PRECONDITION_UNSATISFIED")
               for c in kriging.reason_codes)


def test_qualification_deterministic_same_input() -> None:
    kwargs = dict(role_states={"subject": "eligible"},
                  profile=_PROFILE_POINTS)
    a = qualify_method_candidates("distribution_density", **kwargs)
    b = qualify_method_candidates("distribution_density", **kwargs)
    assert a.to_bounded_dict() == b.to_bounded_dict()
    assert [x.method_id for x in a.qualifications] == [
        x.method_id for x in b.qualifications]


def test_unknown_family_returns_empty_set() -> None:
    q = qualify_method_candidates("no.such_family")
    assert q.qualifications == [] and q.selected_id == ""
    assert not q.all_rejected  # 无候选 ≠ 全拒（诚实区分）


def test_bounded_serializable_output() -> None:
    import json
    q = qualify_method_candidates(
        "distribution_density",
        role_states={"subject": "eligible"},
        profile=_PROFILE_POINTS,
    )
    payload = json.dumps(q.to_bounded_dict(), ensure_ascii=False)
    assert len(payload) < 8_000
