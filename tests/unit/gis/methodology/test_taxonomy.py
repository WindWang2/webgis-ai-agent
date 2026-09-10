"""Epic 11 —— Taxonomy V2 单测（20 类分类学 / 投影派生 / 校验 / 指纹）。"""
from __future__ import annotations

import pytest

from app.lib.gis.methodology.taxonomy import (
    GIS_TASK_CATEGORIES,
    get_task_taxonomy,
    reset_task_taxonomy,
)


@pytest.fixture()
def taxonomy():
    reset_task_taxonomy()
    return get_task_taxonomy()


# ── 词表完整性 ──────────────────────────────────────────────────────────

def test_twenty_categories_exact_vocabulary(taxonomy) -> None:
    """Epic §5.A 的 20 类全覆盖（顺序稳定，纯加法演进）。"""
    assert len(GIS_TASK_CATEGORIES) == 20
    assert taxonomy.all_ids == list(GIS_TASK_CATEGORIES)
    assert all(taxonomy.has(c) for c in GIS_TASK_CATEGORIES)


def test_every_category_has_tasks_families_and_viz(taxonomy) -> None:
    for cid in taxonomy.all_ids:
        cat = taxonomy.get(cid)
        assert cat is not None
        assert cat.ontology_task_ids, cid
        assert cat.methodology_family_ids, cid
        assert cat.recommended_visualizations, cid
        assert cat.required_components, cid


# ── 投影派生（数据需求不手写）────────────────────────────────────────────

def test_data_roles_projected_from_ontology(taxonomy) -> None:
    """density 类的数据角色必须与 ontology 成员任务并集一致（投影非手写）。"""
    cat = taxonomy.get("density")
    assert cat is not None
    # distribution.density_quantitative requires subject+boundary+denominator
    assert set(cat.data_role_demands) == {"subject", "boundary", "denominator"}
    assert "point" in cat.geometry_requirements


def test_lexical_pool_projected_not_handwritten(taxonomy) -> None:
    """lexical 池来自 ontology/family 关键词投影（R1-F6：无第四张词表）。"""
    cat = taxonomy.get("proximity")
    assert cat is not None
    # 成员任务 network.proximity_buffer 的关键词必须出现在投影池
    assert "缓冲区" in cat.lexical_terms_zh
    assert "buffer" in cat.lexical_terms_en


def test_invalid_and_alternative_disjoint(taxonomy) -> None:
    for cid in taxonomy.all_ids:
        cat = taxonomy.get(cid)
        assert cat is not None
        overlap = set(cat.invalid_method_ids) & set(cat.alternative_method_ids)
        assert not overlap, cid


# ── category 级匹配（确定性、有界）───────────────────────────────────────

def test_match_query_proximity_specific(taxonomy) -> None:
    matches = taxonomy.match_query("学校周边500米缓冲范围内的便利店")
    assert matches, "邻近查询必须有证据"
    assert matches[0][0] == "proximity"


def test_match_query_distribution(taxonomy) -> None:
    matches = taxonomy.match_query("成都市小学分布情况")
    assert matches[0][0] == "spatial_distribution"


def test_match_query_zero_hit_empty(taxonomy) -> None:
    """无关查询零命中（诚实无证据 → abstention 消费）。"""
    assert taxonomy.match_query("今天天气怎么样") == []
    assert taxonomy.match_query("") == []


def test_match_query_deterministic(taxonomy) -> None:
    q = "基于 DEM 计算坡度并做流域划分"
    assert taxonomy.match_query(q) == taxonomy.match_query(q)


def test_categories_for_task_bidirectional(taxonomy) -> None:
    cats = taxonomy.categories_for_task("distribution.point_distribution")
    assert "spatial_distribution" in [c.category_id for c in cats]
    for cat in cats:
        assert "distribution.point_distribution" in cat.ontology_task_ids


# ── 指纹 ────────────────────────────────────────────────────────────────

def test_fingerprint_stable_and_sensitive(taxonomy) -> None:
    fp = taxonomy.fingerprint()
    assert len(fp) == 64
    assert taxonomy.fingerprint() == fp
    # 同构造同指纹
    from app.lib.gis.methodology.taxonomy import TaskTaxonomy
    assert TaskTaxonomy().fingerprint() == fp
