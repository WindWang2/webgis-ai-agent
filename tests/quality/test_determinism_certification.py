"""Determinism 红线（ADR-0104 Wave 12）。

行为认证：same input → same output（规划双跑、算法双跑、artifact
round-trip）。声明认证：seed policy 自洽 + 派生表字节一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from gen_determinism_certification import DEFAULT_OUT, generate  # noqa: E402

from app.lib.quality.determinism import (  # noqa: E402
    algorithm_determinism_rows,
    determinism_summary,
)


QUERIES = [
    "成都市小学分布图",
    "帮我算一下从 A 到 B 的最短路径",
    "对比成都各区县的医院分布热点",
]


@pytest.mark.parametrize("query", QUERIES)
def test_plan_double_run_is_identical(query):
    """规划确定性：同 query 双跑 plan dump 逐位一致（离线零 LLM）。"""
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    p1 = MapProductPlanner().plan_from_intent(
        resolve_map_request_intent(query), use_memo=False)
    p2 = MapProductPlanner().plan_from_intent(
        resolve_map_request_intent(query), use_memo=False)
    assert p1.model_dump(mode="json") == p2.model_dump(mode="json")


def test_ripley_k_double_run_identical():
    """固定种子 CSR 包络：双跑一致（输出按模块声明的 4 位小数精度；
    更细粒度的流漂移由 golden pin 后续承接）。"""
    from app.lib.geo_analysis.point_pattern import ripley_k

    rng = np.random.default_rng(7)
    xy = np.column_stack([rng.uniform(0, 1, 60), rng.uniform(0, 1, 60)])
    out1 = ripley_k(xy, envelopes=20)
    out2 = ripley_k(xy, envelopes=20)
    assert out1 == out2


def test_kde_surface_double_run_identical():
    """KDE 表面（无随机成分）：双跑逐位一致。"""
    from app.lib.geo_analysis.density import kde_surface

    rng = np.random.default_rng(11)
    features = [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [float(rng.uniform(104, 104.2)),
                                      float(rng.uniform(30.5, 30.8))]},
         "properties": {}}
        for _ in range(50)
    ]
    fc = {"type": "FeatureCollection", "features": features}
    a1 = kde_surface(fc)
    a2 = kde_surface(fc)
    np.testing.assert_array_equal(np.asarray(a1), np.asarray(a2))


def test_classify_values_double_run_identical():
    from app.lib.cartography.classify import classify_values

    values = [float(i % 17) * 1.5 for i in range(100)]
    assert classify_values(values, method="quantiles", k=5) == \
        classify_values(values, method="quantiles", k=5)


def test_artifact_descriptor_roundtrip_stable():
    """artifact 契约 round-trip：dict→descriptor→dict 两次一致。"""
    from app.lib.gis.artifacts import ArtifactDescriptor

    base = {
        "artifact_type": "point_feature_set",
        "geometry_kind": "point",
        "data_ref": "ref:layer-1",
        "source_capability": "poi_query",
        "feature_count": 3,
        "fields": ["name", "level"],
        "crs": "EPSG:4326",
    }
    d1 = ArtifactDescriptor(**base).model_dump(mode="json")
    d2 = ArtifactDescriptor(**ArtifactDescriptor(**base).model_dump()).model_dump(mode="json")
    assert d1 == d2


# ── 声明认证 ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_registries():
    """声明认证基于 builtins 口径（防同进程 registry 污染）。"""
    from app.lib.gis.algorithm_registry import reset_algorithm_registry
    from app.lib.gis.artifacts import reset_artifact_type_registry
    from app.lib.gis.capability_registry import reset_capability_registry

    reset_algorithm_registry()
    reset_capability_registry()
    reset_artifact_type_registry()
    yield


def test_no_inconsistent_seed_policy_declarations():
    """deterministic=True 且 unseeded 是自相矛盾声明——必须为零。"""
    rows = algorithm_determinism_rows()
    inconsistent = [r["id"] for r in rows if r["class"] == "INCONSISTENT"]
    assert inconsistent == [], f"自相矛盾的确定性声明: {inconsistent}"


def test_seed_policy_vocabulary_fully_classified():
    rows = algorithm_determinism_rows()
    unknown = [r["id"] for r in rows if r["class"] == "unknown-policy"]
    assert unknown == [], f"未知 seed policy: {unknown}"
    assert rows, "algorithm registry 为空？"


def test_summary_has_reasonable_shape():
    summary = determinism_summary()
    assert summary["total"] > 50
    assert summary.get("deterministic", 0) > 0


def test_certification_table_current():
    content = generate()
    assert DEFAULT_OUT.exists()
    assert DEFAULT_OUT.read_text(encoding="utf-8") == content
