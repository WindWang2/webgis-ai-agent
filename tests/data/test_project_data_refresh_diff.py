"""G10 UPD-02 / UPD-03 / UPD-04 纯单元测试（PU 层）。

测试先行：这些断言在实现落地前必须失败。正例给出明确数值/状态，
不允许「返回非空」式断言。

- O02：稳定 feature ID 场景的新增/删改数量与 fixture 一致；无 ID / 抽样 /
  预算不足不得伪造精确差异。
- O03：字段单位、CRS、时空范围、分母变化影响正确分析节点。
- O04：只改样式不重算 GIS；无法证明局部安全的方法保守重算。
"""
from __future__ import annotations


from app.services.project_runtime.refresh.contracts import (
    ChangeEvidence,
    DiffBudget,
    EvidenceLevel,
    FeatureIdKind,
    SourceSnapshot,
    SourceState,
)
from app.services.project_runtime.refresh.diff import compute_source_diff
from app.services.project_runtime.refresh.impact import build_refresh_plan


def _feature(props: dict, geom: str = "POINT(1 1)") -> dict:
    return {"properties": props, "geometry": geom}


def _stable_base() -> SourceSnapshot:
    return SourceSnapshot(
        logical_id="ds://schools",
        version_token="dsd-v1:aaa",
        evidence=EvidenceLevel.CONTENT_EXACT,
        state=SourceState.REFRESHABLE,
        fingerprints={"content": "c0", "schema": "s0", "metadata": "m0", "crs": "epsg4326"},
        schema_fields={"name": "str|", "students": "int|person", "year": "int|year"},
        crs="EPSG:4326",
        bbox=[103.9, 30.5, 104.2, 30.8],
        temporal_window=["2024", "2024"],
        denominator="district_population_2024",
        method_version="heat_v1",
        feature_id_kind=FeatureIdKind.STABLE,
        raster_tiles={"t0": "h0", "t1": "h0"},
        features={
            "s1": _feature({"name": "A", "students": 100}),
            "s2": _feature({"name": "B", "students": 200}),
            "s3": _feature({"name": "C", "students": 300}),
            "s4": _feature({"name": "D", "students": 400}),
            "s5": _feature({"name": "E", "students": 500}),
        },
    )


def _dag() -> dict:
    """bounded typed DAG（与 compute_affected_subgraph 输入同形）。"""
    return {
        "nodes": [
            {"node_id": "data:schools", "parameters": []},
            {"node_id": "data:population", "parameters": []},
            {"node_id": "analysis:heat", "parameters": [{"name": "bandwidth"}]},
            {"node_id": "analysis:normalized", "parameters": []},
            {"node_id": "output:legend", "parameters": []},
            {"node_id": "output:map", "parameters": []},
        ],
        "edges": [
            {"from": "data:schools", "to": "analysis:heat"},
            {"from": "data:population", "to": "analysis:normalized"},
            {"from": "analysis:heat", "to": "output:map"},
            {"from": "analysis:normalized", "to": "output:map"},
            {"from": "output:legend", "to": "output:map"},
        ],
    }


# ── O02：精确差异 ──────────────────────────────────────────────────

def test_o02a_stable_feature_id_exact_counts_match_fixture():
    base = _stable_base()
    cur = base.model_copy(deep=True)
    cur.version_token = "dsd-v1:bbb"
    cur.fingerprints = dict(base.fingerprints, content="c1")
    cur.features = {
        "s1": _feature({"name": "A", "students": 100}),
        "s2": _feature({"name": "B", "students": 200}),
        "s3": _feature({"name": "C", "students": 999}),          # 属性改
        "s4": _feature({"name": "D", "students": 400}, "POINT(2 2)"),  # 几何改
        "s6": _feature({"name": "F", "students": 600}),          # 新增
        "s7": _feature({"name": "G", "students": 700}),          # 新增
    }                                                             # s5 删除

    diff = compute_source_diff(baseline=base, current=cur)

    assert diff.evidence is ChangeEvidence.EXACT
    assert diff.alignment == "stable_id"
    assert diff.exact_counts is not None
    assert diff.exact_counts.added == 2
    assert diff.exact_counts.removed == 1
    assert diff.exact_counts.attribute_changed == 1
    assert diff.exact_counts.geometry_changed == 1
    assert sorted(diff.features_added) == ["s6", "s7"]
    assert diff.features_removed == ["s5"]
    assert diff.features_attribute_changed == ["s3"]
    assert diff.features_geometry_changed == ["s4"]
    assert diff.truncated is False


def test_o02b_no_stable_id_never_fakes_exact_counts():
    base = _stable_base()
    cur = base.model_copy(deep=True)
    cur.feature_id_kind = FeatureIdKind.NONE
    cur.fingerprints = dict(base.fingerprints, content="c1")
    cur.features = {"row0": _feature({"name": "A", "students": 100})}

    diff = compute_source_diff(baseline=base, current=cur)

    assert diff.exact_counts is None
    assert diff.alignment in ("unavailable", "positional_rejected")
    assert diff.evidence in (ChangeEvidence.UNKNOWN, ChangeEvidence.CONSERVATIVE)
    assert diff.features_added == []
    assert "identity" in " ".join(diff.warnings).lower()


def test_o02c_sample_never_proves_unchanged_and_budget_shortfall_is_conservative():
    base = _stable_base()
    cur = base.model_copy(deep=True)
    cur.version_token = "dsd-v1:ccc"
    cur.fingerprints = dict(base.fingerprints, content="c1")
    cur.sampled = True
    cur.truncated = True
    cur.features = {"s1": _feature({"name": "A", "students": 100})}

    diff = compute_source_diff(baseline=base, current=cur)
    assert diff.change_class != "none"
    assert diff.evidence in (ChangeEvidence.UNKNOWN, ChangeEvidence.CONSERVATIVE)
    assert diff.exact_counts is None
    assert any("sample" in w.lower() or "truncat" in w.lower() for w in diff.warnings)

    # 预算不足：不得 silently unchanged —— 必须保守整项重算
    tight = base.model_copy(deep=True)
    tight.fingerprints = {}
    tight.evidence = EvidenceLevel.TIMESTAMP_ONLY
    d2 = compute_source_diff(
        baseline=base, current=tight,
        budget=DiffBudget(max_features=1, max_bytes=16),
    )
    assert d2.change_class != "none"
    assert d2.evidence in (ChangeEvidence.UNKNOWN, ChangeEvidence.CONSERVATIVE)


def test_o02d_revision_token_evidence_is_not_exact_content_version():
    base = _stable_base()
    cur = base.model_copy(deep=True)
    cur.evidence = EvidenceLevel.TIMESTAMP_ONLY
    cur.version_token = "etag:2026-11-08T12:00:00Z"
    cur.fingerprints = {}
    diff = compute_source_diff(baseline=base, current=cur)
    assert diff.evidence is not ChangeEvidence.EXACT
    assert any("evidence" in w.lower() for w in diff.warnings)


# ── O03：单位 / CRS / 范围 / 分母 ──────────────────────────────────

def test_o03a_unit_and_crs_changes_are_classified_and_disclosed():
    base = _stable_base()
    cur = base.model_copy(deep=True)
    cur.schema_fields = dict(base.schema_fields, students="int|thousand_person")
    cur.crs = "EPSG:3857"
    cur.bbox = [11500000, 3500000, 11600000, 3600000]
    cur.temporal_window = ["2025", "2025"]
    cur.fingerprints = dict(base.fingerprints, schema="s1", crs="epsg3857")

    diff = compute_source_diff(baseline=base, current=cur)
    assert "students" in diff.fields_unit_changed
    assert diff.crs_changed is True
    assert diff.bbox_changed is True
    assert diff.temporal_changed is True
    assert diff.change_class in ("crs", "schema")


def test_o03b_denominator_change_marks_normalization_nodes_only():
    base = _stable_base()
    cur = base.model_copy(deep=True)
    cur.denominator = "district_population_2025"
    cur.fingerprints = dict(base.fingerprints, metadata="m1")

    diff = compute_source_diff(baseline=base, current=cur)
    assert diff.denominator_changed is True

    plan = build_refresh_plan(diffs=[diff], dag=_dag())
    assert "analysis:normalized" in plan.recompute
    assert "data:schools" not in plan.recompute or plan.conservative is True
    assert plan.style_only is False


def test_o03c_style_only_change_triggers_zero_gis_recompute():
    plan = build_refresh_plan(diffs=[], dag=_dag(), style_only=True)
    assert plan.style_only is True
    assert plan.recompute == []
    assert plan.conservative is False
    assert set(plan.decisions.values()) >= {"reuse"} or plan.decisions == {}


# ── O04：保守重算 ──────────────────────────────────────────────────

def test_o04a_global_raster_dependency_forces_conservative_full_recompute():
    base = _stable_base()
    cur = base.model_copy(deep=True)
    cur.raster_tiles = {"t0": "h1", "t1": "h0"}
    cur.raster_dependencies = ["global_stats", "stencil"]
    cur.fingerprints = dict(base.fingerprints, content="c1")

    diff = compute_source_diff(baseline=base, current=cur)
    assert diff.raster_tiles_changed == ["t0"]
    assert any(d in ("global_stats", "stencil") for d in diff.expanded_dependencies)

    plan = build_refresh_plan(diffs=[diff], dag=_dag())
    assert plan.conservative is True
    assert set(plan.recompute) == {
        "data:schools", "data:population", "analysis:heat",
        "analysis:normalized", "output:legend", "output:map",
    }


def test_o04b_unknown_evidence_forces_conservative_full_recompute():
    base = _stable_base()
    cur = base.model_copy(deep=True)
    cur.feature_id_kind = FeatureIdKind.NONE
    cur.fingerprints = dict(base.fingerprints, content="c1")
    diff = compute_source_diff(baseline=base, current=cur)
    plan = build_refresh_plan(diffs=[diff], dag=_dag())
    assert plan.conservative is True
    assert len(plan.recompute) == len(_dag()["nodes"])


def test_o04c_local_data_change_only_recomputes_proven_subgraph():
    base = _stable_base()
    cur = base.model_copy(deep=True)
    cur.version_token = "dsd-v1:bbb"
    cur.fingerprints = dict(base.fingerprints, content="c1")
    cur.features = dict(base.features)
    cur.features["s8"] = _feature({"name": "H", "students": 800})
    diff = compute_source_diff(baseline=base, current=cur)
    assert diff.evidence is ChangeEvidence.EXACT

    plan = build_refresh_plan(diffs=[diff], dag=_dag())
    assert plan.conservative is False
    assert "data:schools" in plan.recompute
    assert "analysis:heat" in plan.recompute
    assert "data:population" not in plan.recompute
    assert "analysis:normalized" not in plan.recompute
    assert plan.reuse
    assert plan.proposal_fingerprint.startswith("g10:")
