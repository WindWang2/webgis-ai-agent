"""atlas_batch 测试 fixtures —— 合成 12 行政区 spec / job DB / 提交链公共件。

诚实声明：MapSpec 会话存储边界（mapspec_store）在单元/executor 测试中以
monkeypatch 返回**内联合成数据**（标注 simulated）；管线/渲染/组装/QA 全部
真实执行（无 mock）。API 级测试用真实 SQLite + 真实路由 + 真实归属行。
"""
from __future__ import annotations

import contextlib
import os
from typing import Any, Dict, List

import pytest

#: 12 个虚构行政区（成都式命名；合成数据 —— simulated）。
DISTRICTS: List[str] = [
    "锦江区", "青羊区", "金牛区", "武侯区", "成华区",
    "龙泉驿区", "青白江区", "新都区", "温江区", "双流区",
    "郫都区", "新津区",
]


def _district_polygon(cx: float, cy: float, span: float = 0.12) -> Dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [[
            [cx - span, cy - span], [cx + span, cy - span],
            [cx + span, cy + span], [cx - span, cy + span],
            [cx - span, cy - span],
        ]],
    }


def district_document(
    *,
    schools_per_district: int = 3,
    with_legend: bool = True,
    districts: int | None = None,
) -> Dict[str, Any]:
    """12 行政区 × N 小学（点）+ 行政区面（category 驱动目标层）。

    ``districts``：取前 N 个区（executor 快速路径用小子集；旗舰用全量 12）。
    category 属性 = ``district``（面层）；同区学校点落在面内。
    """
    school_feats: List[Dict[str, Any]] = []
    district_feats: List[Dict[str, Any]] = []
    names = DISTRICTS[:districts] if districts else DISTRICTS
    for i, name in enumerate(names):
        cx = 103.9 + (i % 4) * 0.35
        cy = 30.55 + (i // 4) * 0.30
        district_feats.append({
            "type": "Feature",
            "geometry": _district_polygon(cx, cy),
            "properties": {
                "district": name,
                "primaries": schools_per_district + (i % 3),
                "students": 1000 + i * 137,
            },
        })
        for j in range(schools_per_district):
            school_feats.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [cx - 0.04 + j * 0.04, cy - 0.02 + (j % 2) * 0.04],
                },
                "properties": {"district": name, "name": f"{name}第{j + 1}小学"},
            })
    layers: List[Dict[str, Any]] = [
        {"id": "districts", "source": "districts", "type": "fill",
         "paint": {"fill-color": "#1d4ed8", "fill-opacity": 0.35},
         **({"legend_spec": {
             "type": "choropleth", "field": "primaries", "method": "quantile",
             "breaks": [3, 4, 5],
             "colors": ["#eff6ff", "#bfdbfe", "#93c5fd", "#3b82f6"],
         }} if with_legend else {})},
        {"id": "schools", "source": "schools", "type": "circle",
         "paint": {"circle-color": "#dc2626", "circle-radius": 4}},
    ]
    return {
        "version": "1.1",
        "sources": {
            "districts": {"type": "geojson", "inlineData": {
                "type": "FeatureCollection", "features": district_feats}},
            "schools": {"type": "geojson", "inlineData": {
                "type": "FeatureCollection", "features": school_feats}},
        },
        "layers": layers,
        "layout": {"components": [
            {"id": "t", "type": "title", "options": {"text": "成都市各区小学分布"}},
            {"id": "lg", "type": "legend"},
            {"id": "sb", "type": "scale_bar"},
            {"id": "at", "type": "attribution", "options": {"text": "合成数据 · simulated"}},
        ]},
    }


def spec_params(**overrides: Any) -> Dict[str, Any]:
    """AtlasJobSpec 的 job_params 形状（executor 从 job 行读回）。"""
    params: Dict[str, Any] = {
        "spec_version": "atlas_job.v1",
        "session_id": "sess-atlas-test",
        "atlas": {
            "driver": "category",
            "layer_id": "districts",
            "category_property": "district",
            "features_per_page": 50,
            "page_budget": 20,
            "include_cover": True,
            "atlas_title": "成都市各区小学分布图册",
        },
        "classification_scope": "shared",
        "target_dpi": 300,
        "title": "成都小学图册",
        "simulated_data": True,
    }
    params.update(overrides)
    return params


@pytest.fixture()
def atlas_exports(tmp_path, monkeypatch):
    """exports 目录指向 tmp（executor 工作目录/缓存/产物全隔离）。"""
    from app.services import export_paths

    monkeypatch.setattr(export_paths, "exports_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def atlas_job_db(tmp_path, monkeypatch):
    """同步 db_session（executor/_load_job_spec/submit/worker）指向临时 SQLite。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.db_model import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'atlas_jobs.db'}")
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def fake_db_session():
        db = sess()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr("app.tools._utils.db_session", fake_db_session)
    monkeypatch.setattr("app.services.jobs.submit.db_session", fake_db_session)
    monkeypatch.setattr(
        "app.services.jobs.worker._default_session_factory", fake_db_session)
    yield sess
    engine.dispose()


@pytest.fixture()
def atlas_spec_store(monkeypatch):
    """mapspec_store.get_mapspec 返回内联合成 spec（simulated 边界声明）。

    返回句柄带 ``set_document``：单测可换文档（数据变更/异常路径）。
    """
    from app.services import mapspec_store as store_mod

    state: Dict[str, Any] = {"document": district_document()}
    calls: List[str] = []

    async def fake_get_mapspec(session_id: str):
        calls.append(session_id)
        return state["document"]

    monkeypatch.setattr(store_mod.mapspec_store, "get_mapspec", fake_get_mapspec)
    return {
        "calls": calls,
        "set_document": lambda doc: state.update(document=doc),
        "document": state["document"],
    }


@pytest.fixture(autouse=True)
def _atlas_no_lineage(monkeypatch):
    """lineage best-effort 面隔离（防测试写 dev DB；result.lineage_recorded=False 路径）。"""
    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(
        "app.services.export_lineage.record_export_lineage", _none)


@pytest.fixture(autouse=True)
def _atlas_env():
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-atlas-32-chars-okay!")
    os.environ.setdefault("ENV", "development")
