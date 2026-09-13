"""ads-v1 DS7 local asset index tests (ADR-0177).

Covers: normalized meta schema, honest scan (available / unavailable with
ingest hints), assets→cards merge into retrieval, sources-scan command
smoke, and the 10 mixed local/online retrieval cases (local wins on cost
only when relevance agrees — never force-topped).
"""
from __future__ import annotations


import pytest

from app.services.data_fabric.local_index import (
    META_SCHEMA,
    normalize_meta,
    scan_local_assets,
)


# ── meta schema ──────────────────────────────────────────────────────────────


def test_meta_schema_keys_documented():
    assert META_SCHEMA == {
        "crs", "bbox", "rows", "temporal_start", "temporal_end",
        "fields", "source", "ingested_at",
    } or set(META_SCHEMA) == {
        "crs", "bbox", "rows", "temporal_start", "temporal_end",
        "fields", "source", "ingested_at",
    }


def test_normalize_meta_maps_sidecar_aliases():
    out = normalize_meta({"srs": "EPSG:4326", "total_rows": 100, "generated_at": "2026-01-01"})
    assert out["normalized"]["crs"] == "EPSG:4326"
    assert out["normalized"]["rows"] == 100
    assert out["normalized"]["ingested_at"] == "2026-01-01"
    assert "rows" not in out["missing"] and "ingested_at" not in out["missing"]
    assert "bbox" in out["missing"]  # honest: unknown stays missing, not faked


# ── scan: unavailable is explicit ────────────────────────────────────────────


def test_scan_empty_dir_is_explicit_unavailable(tmp_path):
    report = scan_local_assets(str(tmp_path))
    assert not report.available
    assert {u.library for u in report.unavailable} == {"local_osm", "local_poi", "local_yearbook"}
    for u in report.unavailable:
        assert "manage.py" in u.ingest_hint  # 灌数指引必须可执行


def test_scan_unconfigured_root_reports_all_unavailable():
    report = scan_local_assets(None)  # LOCAL_GEODATA_DIR not set in tests
    assert report.unavailable and not report.all_available


def test_scan_available_osm_themes(tmp_path):
    """A real GPKG under <root>/osm_gpkg → available asset with meta."""
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point

    gpkg_dir = tmp_path / "osm_gpkg"
    gpkg_dir.mkdir(parents=True)
    gdf = gpd.GeoDataFrame(
        pd.DataFrame({"name": ["a"]}),
        geometry=[Point(116.4, 39.9)],
        crs="EPSG:4326",
    )
    gdf.to_file(gpkg_dir / "pois.gpkg", driver="GPKG", layer="pois")

    report = scan_local_assets(str(tmp_path))
    ids = {a.asset_id for a in report.available}
    assert "local_osm/pois" in ids
    asset = next(a for a in report.available if a.asset_id == "local_osm/pois")
    assert asset.layers == ["pois"]
    assert asset.meta["rows"] == 1
    assert any(u.library in {"local_poi", "local_yearbook"} for u in report.unavailable)


def test_assets_to_cards_are_local_and_verified(tmp_path):
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point

    gpkg_dir = tmp_path / "osm_gpkg"
    gpkg_dir.mkdir(parents=True)
    gpd.GeoDataFrame(pd.DataFrame({"name": ["a"]}), geometry=[Point(0, 0)], crs="EPSG:4326").to_file(
        gpkg_dir / "roads.gpkg", driver="GPKG", layer="roads"
    )
    report = scan_local_assets(str(tmp_path))
    from app.services.data_fabric.local_index import assets_to_cards

    cards = assets_to_cards(report.available)
    assert cards and all(c.local and c.verified for c in cards)
    assert all(c.card_id.startswith("local_") for c in cards)


# ── sources-scan command smoke ───────────────────────────────────────────────


def test_sources_scan_command_runs(capsys):
    """Command function smoke: unconfigured root → explicit unavailable report."""

    from manage import cmd_sources_scan

    cmd_sources_scan()
    out = capsys.readouterr().out
    assert "sources-scan" in out and "不可用" in out


# ── mixed local/online retrieval: 10 cases ───────────────────────────────────


@pytest.fixture(scope="module")
def mixed_service():
    from app.services.data_fabric.retrieval import get_retrieval_service

    return get_retrieval_service()


MIXED_CASES = [
    # (query, expect_local_in_top3, expect_online_in_results)
    ("本地道路路网数据", True, False),
    ("本地POI兴趣点设施", True, False),
    ("县域统计年鉴经济指标", True, False),
    ("卫星遥感影像", False, True),
    ("全球高程DEM", False, True),
    ("各国GDP宏观数据", False, True),
    ("物种出现记录", False, True),
    ("北京空气质量监测", False, True),
    ("道路交通流量在线查询", None, True),   # online matches; local roads also relevant
    ("人口统计", None, True),              # both sides may surface
]


def test_mixed_local_online_ten_cases(mixed_service):
    assert len(MIXED_CASES) == 10
    for query, expect_local, expect_online in MIXED_CASES:
        resp = mixed_service.retrieve(query, top_k=5)
        local_ids = [h.source_id for h in resp.hits if h.source_id.startswith("local_")]
        online_ids = [h.source_id for h in resp.hits if not h.source_id.startswith("local_")]
        if expect_local is True:
            assert local_ids, f"{query!r}: expected a local asset in top-5"
        if expect_online:
            assert online_ids, f"{query!r}: expected an online dataset in top-5"
        if expect_local is False:
            assert not local_ids, f"{query!r}: local must not displace relevant online hits"


def test_local_never_force_topped_over_relevance(mixed_service):
    """Relevance dominates: a purely-online query must rank online datasets
    first even though local assets carry cost=1.0."""
    resp = mixed_service.retrieve("哨兵2号卫星影像", top_k=3)
    assert resp.hits
    assert not resp.hits[0].source_id.startswith("local_"), (
        "cost must not force a local card above a clearly-relevant online dataset"
    )


def test_local_wins_when_relevance_agrees(mixed_service):
    """When the query explicitly asks for the local library, the local asset
    ranks first (cost breaks the tie only after relevance agrees)."""
    resp = mixed_service.retrieve("本地OSM道路数据", top_k=3)
    assert resp.hits and resp.hits[0].source_id.startswith("local_")
