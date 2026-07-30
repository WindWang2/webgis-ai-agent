import pytest
from pathlib import Path
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry
from app.tools.cartography_harness import register_cartography_harness_tools


@pytest.fixture
async def clean_session():
  sid = "test-mapspec-store-session"
  await session_data_manager.clear_session(sid)
  yield sid
  await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_mapspec_store_init_and_get(clean_session):
  init_res = await mapspec_store.init_project(clean_session, view={"center": [120.0, 30.0], "zoom": 10.0})
  assert init_res["mapspec"]["version"] == "1.0"
  assert init_res["mapspec"]["view"]["center"] == [120.0, 30.0]

  # Check dual-write to map_state
  map_state = await session_data_manager.get_map_state(clean_session)
  assert "mapspec" in map_state
  assert map_state["mapspec"]["view"]["zoom"] == 10.0
  assert "view" in map_state

  # Check retrieved MapSpec
  retrieved = await mapspec_store.get_mapspec(clean_session)
  assert retrieved == init_res["mapspec"]


@pytest.mark.asyncio
async def test_mapspec_store_set_view(clean_session):
  await mapspec_store.init_project(clean_session)
  res = await mapspec_store.set_view(clean_session, center=[116.4, 39.9], zoom=12.0)

  assert res["mapspec"]["view"]["center"] == [116.4, 39.9]
  assert res["mapspec"]["view"]["zoom"] == 12.0

  map_state = await session_data_manager.get_map_state(clean_session)
  assert map_state["view"]["center"] == [116.4, 39.9]


@pytest.mark.asyncio
async def test_cartography_harness_tools_dispatch(clean_session):
  registry = ToolRegistry()
  register_cartography_harness_tools(registry)

  # webgis_project_init
  init_res = await registry.dispatch("webgis_project_init", {"view": {"center": [100.0, 20.0], "zoom": 5}}, session_id=clean_session)
  assert init_res["success"] is True
  assert init_res["mapspec"]["view"]["center"] == [100.0, 20.0]

  # webgis_state_get
  get_res = await registry.dispatch("webgis_state_get", {}, session_id=clean_session)
  assert get_res["success"] is True
  assert get_res["mapspec"]["view"]["center"] == [100.0, 20.0]

  # webgis_view_set
  view_res = await registry.dispatch("webgis_view_set", {"zoom": 8.0}, session_id=clean_session)
  assert view_res["success"] is True
  assert view_res["view"]["zoom"] == 8.0
