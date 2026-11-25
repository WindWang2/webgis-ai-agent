"""map_series 一致性/传播/冻结测试共享 fixtures —— 真实 MapSpec 引擎建会话。

诚实声明：成员会话用真实 lifecycle engine / store / session_data（内存
Redis 钉扎）构建，无 mock 引擎；系列行用真实 SQLite。合成 GeoJSON 标注
simulated（同 tests/cartography/atlas_batch/fixtures.py 的 district
合成法）。
"""
from __future__ import annotations

import shutil
from typing import Any, Dict, List, Optional

import pytest

from app.core.database import Base, Engine, SessionLocal
from app.services.gis_world_state.mutation import apply_gis_mutation
from app.services.mapspec.intents import (
    SetLayoutIntent,
    UpsertLayerIntent,
    UpsertSourceIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager


def _sim_geojson(values: Optional[List[int]] = None) -> Dict[str, Any]:
    """合成 3 行政区面（simulated）；primaries 字段承载分类目标。"""
    values = values or [3, 5, 9]
    feats = []
    for i, v in enumerate(values):
        cx = 103.9 + i * 0.35
        cy = 30.55 + (i % 2) * 0.3
        feats.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [cx - 0.12, cy - 0.12], [cx + 0.12, cy - 0.12],
                        [cx + 0.12, cy + 0.12], [cx - 0.12, cy + 0.12],
                        [cx - 0.12, cy - 0.12],
                    ]],
                },
                "properties": {"district": f"d{i}", "primaries": v},
            }
        )
    return {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": feats}}


async def seed_member_session(
    sid: str,
    *,
    method: str = "quantiles",
    color: str = "#1d4ed8",
    ramp: Optional[List[str]] = None,
    unit: Optional[str] = None,
    with_title: bool = True,
    with_attribution: bool = True,
    with_legend_component: bool = False,
    legend_spec: bool = True,
    with_stops_no_legend: bool = False,
    symbol_asset_revision: Optional[int] = None,
    bounds: Optional[List[float]] = None,
) -> str:
    """真实引擎建成员会话（每次全新 spec；返回 sid）。"""
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)

    legend: Dict[str, Any] = {
        "type": "choropleth",
        "field": "primaries",
        "method": method,
    }
    if legend_spec:
        legend["breaks"] = [3, 4, 6]
        legend["colors"] = list(ramp or ["#eff6ff", "#93c5fd", "#1d4ed8"])
    if unit:
        legend["unit"] = unit
    layer: Dict[str, Any] = {
        "id": "districts",
        "type": "fill",
        "source": "districts",
        "paint": {"fill-color": color},
    }
    if legend_spec:
        layer["legend_spec"] = legend
    if with_stops_no_legend:
        # 分级 paint（后端 stops 形态）但无 legend_spec —— LEGEND_MISSING 现场
        layer["paint"] = {"color": {"stops": [[0, "#eff6ff"], [10, "#1d4ed8"]]}}
    if symbol_asset_revision is not None:
        layer["symbol_asset"] = {
            "asset_id": "cartoasset.builtin.test",
            "revision": symbol_asset_revision,
            "content_digest": "digest-" + str(symbol_asset_revision),
            "kind": "point_symbol",
        }
    result = await apply_gis_mutation(
        sid,
        UpsertLayerIntent(layer=layer, source_data=_sim_geojson()),
        origin="agent",
        actor="map-series-tests",
        reason="seed member session",
    )
    assert not result.is_error, result.error_msg

    components = []
    if with_title:
        components.append({"id": "t", "type": "title", "options": {"text": "系列成员图"}})
    if with_attribution:
        components.append(
            {"id": "at", "type": "attribution", "options": {"text": "合成数据 · simulated"}}
        )
    if with_legend_component:
        components.append({"id": "lg", "type": "legend"})
    if components:
        result = await apply_gis_mutation(
            sid,
            SetLayoutIntent(components=components),
            origin="agent",
            actor="map-series-tests",
            reason="seed components",
        )
        assert not result.is_error, result.error_msg

    if bounds is not None:
        mapspec = await session_data_manager.get_map_state(sid)
        spec = (mapspec or {}).get("mapspec") or {}
        src = dict((spec.get("sources") or {}).get("districts") or {"type": "geojson"})
        src["bounds"] = list(bounds)
        from app.services.mapspec.intents import UpsertSourceIntent

        result = await apply_gis_mutation(
            sid,
            UpsertSourceIntent(source_id="districts", source=src),
            origin="agent",
            actor="map-series-tests",
            reason="seed bounds",
        )
        assert not result.is_error, result.error_msg
    return sid


@pytest.fixture()
async def session_factory():
    """真实引擎会话工厂（teardown 清内存会话 + 磁盘目录）。"""
    sids: List[str] = []

    async def _create(sid: str, **kw: Any) -> str:
        sids.append(sid)
        return await seed_member_session(sid, **kw)

    yield _create
    for sid in sids:
        await session_data_manager.clear_session(sid)
        shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as session:
        yield session
        # 清理本套件创建的领域行（防跨测试命名冲突；合成测试数据）
        from app.models.design_spec import (
            CartographyDesignSpec,
            CartographyDesignSpecVersion,
        )
        from app.models.map_series import (
            CartographyMapSeries,
            CartographyMapSeriesVersion,
        )

        session.rollback()
        session.query(CartographyMapSeriesVersion).delete()
        session.query(CartographyMapSeries).delete()
        session.query(CartographyDesignSpecVersion).delete()
        session.query(CartographyDesignSpec).delete()
        session.commit()


@pytest.fixture(autouse=True)
def _map_series_tables():
    Base.metadata.create_all(bind=Engine)
    yield
