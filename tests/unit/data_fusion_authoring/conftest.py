"""融合工作台单测夹具 —— DB 表、DATA_DIR 隔离、成都小学双源种子。

fixture 坐标为受控合成值（成都语义语境），不含真实用户坐标。
种子覆盖验收旅程 1 的要素：别名、同名校区括号、重复点、冲突地址、
类别同义词表、时间字段、B 独有实体。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from app.core.database import Base
import app.models  # noqa: F401 — 注册全部模型（含 data_fusion_authoring）


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """每测独立 DATA_DIR（data-fusion 终态包/会话存储不落开发目录）。"""
    from app.core.config import settings

    data_dir = tmp_path / "fusion-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir), raising=False)
    yield data_dir


@pytest.fixture()
def db_tables():
    """本域 + 全模型建表（测试库 = 套件级临时 SQLite）。"""
    from app.core.database import Engine

    Base.metadata.create_all(bind=Engine, checkfirst=True)
    yield


def chengdu_pois_a() -> Dict[str, Any]:
    """来源 A：教育局名录（别名/冲突地址/独立实体）。"""
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0650, 30.6520]},
             "properties": {"code": "J001", "name": "成都市第七中学",
                            "addr": "天府大道中段661号", "cat": "中学",
                            "tel": "028-85221"}},
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0700, 30.6550]},
             "properties": {"code": "J002", "name": "锦江区第一小学",
                            "addr": "旧街5号", "cat": "小学",
                            "tel": "028-85331"}},
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0600, 30.6400]},
             "properties": {"code": "J003", "name": "高新区实验小学",
                            "addr": "天府三街199号", "cat": "小学",
                            "tel": "028-85441"}},
        ],
    }


def chengdu_pois_b() -> Dict[str, Any]:
    """来源 B：开放地图（别名对应/括号校区/B 独有）。"""
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0651, 30.6521]},
             "properties": {"osm_id": "O9001", "name": "成都七中",
                            "addr": "天府大道中段661号",
                            "cat": "secondary_school",
                            "checked": "2026-01-12",
                            "tel": "028-85221"}},
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0700, 30.6551]},
             "properties": {"osm_id": "O9002",
                            "name": "锦江区第一小学（琉璃场校区）",
                            "addr": "新街9号", "cat": "primary_school",
                            "checked": "2026-02-01", "tel": None}},
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0550, 30.6480]},
             "properties": {"osm_id": "O9003", "name": "芳草街便民幼儿园",
                            "addr": "芳草东街55号", "cat": "kindergarten",
                            "checked": "2026-03-08", "tel": None}},
        ],
    }


async def seed_session_source(session_id: str, payload: Dict[str, Any],
                              prefix: str = "fusion") -> str:
    """payload → session ref（测试种子；会话存储内存实现）。"""
    from app.services.session_data import session_data_manager

    return await session_data_manager.store(session_id, payload, prefix=prefix)


def fusion_plan(a_ref: str, b_ref: str, session_id: str,
                **over: Any) -> Dict[str, Any]:
    """成都小学旗舰计划（remove_bracketed 归一；pair 语义见 WP1/WP3）。"""
    plan: Dict[str, Any] = {
        "schema_version": 1,
        "title": "成都小学实体融合",
        "entity_kind": "point_poi",
        "source_a": {
            "kind": "session_ref", "session_id": session_id, "ref": a_ref,
            "label": "教育局名录", "priority": 1,
            "priority_reason": "官方审校名录（2024 年审）",
            "license": "合成测试数据（无真实许可）",
            "attribution": "受控 fixture",
            "external_id_field": "code", "id_space": None,
            "name_field": "name", "address_field": "addr",
            "category_field": "cat",
            "category_map": {"小学": "school_primary",
                             "中学": "school_middle"},
            "temporal_field": None,
            "field_mappings": [
                {"source_field": "name", "output_field": "name",
                 "rule": "same_value"},
                {"source_field": "addr", "output_field": "address",
                 "rule": "trust_priority"},
                {"source_field": "tel", "output_field": "phone",
                 "rule": "trust_priority"},
            ],
        },
        "source_b": {
            "kind": "session_ref", "session_id": session_id, "ref": b_ref,
            "label": "开放地图", "priority": 2,
            "priority_reason": "众包数据（无官方审校）",
            "license": "ODbL（合成映射）",
            "attribution": "受控 fixture",
            "external_id_field": "osm_id", "id_space": None,
            "name_field": "name", "address_field": "addr",
            "category_field": "cat",
            "category_map": {"primary_school": "school_primary",
                             "secondary_school": "school_middle"},
            "temporal_field": "checked",
            "field_mappings": [
                {"source_field": "name", "output_field": "name",
                 "rule": "same_value"},
                {"source_field": "addr", "output_field": "address",
                 "rule": "trust_priority"},
                {"source_field": "tel", "output_field": "phone",
                 "rule": "trust_priority"},
            ],
        },
        "strategy": {
            "weights": {"external_id": 0.0, "name": 0.40, "address": 0.15,
                        "category": 0.15, "distance": 0.30},
            "auto_match_min": 0.85, "review_min": 0.60,
            "ambiguous_margin": 0.05,
            "max_match_distance_m": 150.0, "close_distance_m": 30.0,
            "name_normalizers": ["trim", "casefold", "collapse_spaces",
                                 "remove_bracketed"],
            "category_unknown_factor": 0.5,
            "id_mismatch_blocks_match": True,
        },
        "geometry": {"adoption": "primary_source"},
        "commit_policy": "require_resolved",
        "invalid_geometry_policy": "exclude_count",
        "output_name": "成都小学融合成果",
    }
    plan.update(over)
    return plan


@pytest.fixture()
async def seeded_fusion_sources() -> Dict[str, Any]:
    session_id = "sess-fusion-unit"
    a_ref = await seed_session_source(
        session_id, chengdu_pois_a(), prefix="fusiona")
    b_ref = await seed_session_source(
        session_id, chengdu_pois_b(), prefix="fusionb")
    return {
        "session_id": session_id,
        "a_ref": a_ref,
        "b_ref": b_ref,
        "actor": {"trusted_session_id": session_id},
        "plan": fusion_plan(a_ref, b_ref, session_id),
    }
