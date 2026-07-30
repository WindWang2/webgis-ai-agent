import shutil
import uuid
import pytest
from app.services.mapspec_store import mapspec_store, BASE_STORAGE_DIR
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry
from app.tools.cartography_harness import register_cartography_harness_tools


@pytest.fixture
async def clean_session():
  sid = f"test-session-{uuid.uuid4().hex[:8]}"
  await session_data_manager.clear_session(sid)
  yield sid
  await session_data_manager.clear_session(sid)
  session_dir = BASE_STORAGE_DIR / sid
  if session_dir.exists():
    shutil.rmtree(session_dir, ignore_errors=True)


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
async def test_layer_upsert_auto_profiles_and_auto_views(clean_session):
  geojson_data = {
      "type": "FeatureCollection",
      "features": [
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
              "properties": {"val": 10},
          },
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [121.0, 31.0]},
              "properties": {"val": 20},
          },
      ],
  }

  layer = {
      "id": "eq_layer",
      "source": "eq_source",
      "type": "circle",
      "paint": {
          "color": "#ff0000",
          "radius": 5,
      },
  }

  res = await mapspec_store.layer_upsert(clean_session, layer, source_data=geojson_data)
  mapspec = res["mapspec"]

  # Check layer added
  assert len(mapspec["layers"]) == 1
  assert mapspec["layers"][0]["id"] == "eq_layer"

  # Check auto-profiling injected
  assert "profile" in mapspec["sources"]["eq_source"]
  assert mapspec["sources"]["eq_source"]["profile"]["featureCount"] == 2

  # Check first layer auto-view injection (center of [120.0, 30.0] and [121.0, 31.0] -> [120.5, 30.5])
  assert mapspec["view"]["center"] == [120.5, 30.5]

  # Check dual-write to map_state layers
  map_state = await session_data_manager.get_map_state(clean_session)
  assert len(map_state["layers"]) == 1
  assert map_state["layers"][0]["id"] == "eq_layer"


@pytest.mark.asyncio
async def test_layer_remove(clean_session):
  layer = {"id": "test_layer", "source": "s1", "type": "circle"}
  await mapspec_store.layer_upsert(clean_session, layer)

  res = await mapspec_store.layer_remove(clean_session, "test_layer")
  assert res["success"] is True
  assert len(res["mapspec"]["layers"]) == 0

  map_state = await session_data_manager.get_map_state(clean_session)
  assert len(map_state["layers"]) == 0


@pytest.mark.asyncio
async def test_cartography_harness_tools_dispatch(clean_session):
  registry = ToolRegistry()
  register_cartography_harness_tools(registry)

  # webgis_project_init
  init_res = await registry.dispatch("webgis_project_init", {"view": {"center": [100.0, 20.0], "zoom": 5}}, session_id=clean_session)
  assert init_res["success"] is True
  assert init_res["mapspec"]["view"]["center"] == [100.0, 20.0]

  # webgis_layer_upsert
  upsert_res = await registry.dispatch(
      "webgis_layer_upsert",
      {
          "layer": {
              "id": "pts_layer",
              "source": "pts_source",
              "type": "circle",
              "paint": {"color": "#00ff00"},
          }
      },
      session_id=clean_session,
  )
  assert upsert_res["success"] is True
  assert upsert_res["layer_id"] == "pts_layer"

  # webgis_state_get
  get_res = await registry.dispatch("webgis_state_get", {}, session_id=clean_session)
  assert get_res["success"] is True

  # webgis_layer_remove
  remove_res = await registry.dispatch("webgis_layer_remove", {"layer_id": "pts_layer"}, session_id=clean_session)
  assert remove_res["success"] is True
