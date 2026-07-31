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

  # Check dual-write to map_state: the MapSpec intent is cached under
  # map_state["mapspec"] (the live read path). A separate top-level
  # map_state["view"] key was removed — nothing read it (readers use
  # "viewport"); the canonical view lives inside the cached mapspec.
  map_state = await session_data_manager.get_map_state(clean_session)
  assert "mapspec" in map_state
  assert map_state["mapspec"]["view"]["zoom"] == 10.0

  # Check retrieved MapSpec
  retrieved = await mapspec_store.get_mapspec(clean_session)
  assert retrieved == init_res["mapspec"]


@pytest.mark.asyncio
async def test_mapspec_store_set_view(clean_session):
  await mapspec_store.init_project(clean_session)
  res = await mapspec_store.set_view(clean_session, center=[116.4, 39.9], zoom=12.0)

  assert res["mapspec"]["view"]["center"] == [116.4, 39.9]
  assert res["mapspec"]["view"]["zoom"] == 12.0

  # The view is reachable via the cached mapspec (the live read path),
  # not a separate top-level map_state["view"] key.
  map_state = await session_data_manager.get_map_state(clean_session)
  assert map_state["mapspec"]["view"]["center"] == [116.4, 39.9]


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

  assert len(mapspec["layers"]) == 1
  assert mapspec["layers"][0]["id"] == "eq_layer"
  assert "profile" in mapspec["sources"]["eq_source"]
  assert mapspec["sources"]["eq_source"]["profile"]["featureCount"] == 2
  assert mapspec["view"]["center"] == [120.5, 30.5]


@pytest.mark.asyncio
async def test_validate_and_compile(clean_session):
  layer = {
      "id": "test_layer",
      "source": "s1",
      "type": "circle",
      "paint": {
          "color": {
              "method": "interpolate",
              "field": "val",
              "stops": [
                  [10, "#ff0000"],
                  [20, "#00ff00"],
              ],
          }
      },
  }
  await mapspec_store.layer_upsert(clean_session, layer)

  # Validate
  val_res = await mapspec_store.validate_mapspec(clean_session)
  assert val_res["success"] is True

  # Compile
  comp_res = await mapspec_store.compile_mapspec_cli(clean_session)
  assert comp_res["success"] is True
  assert comp_res["style"]["version"] == 8


@pytest.mark.asyncio
async def test_layout_set(clean_session):
  await mapspec_store.init_project(clean_session)
  res = await mapspec_store.layout_set(
      clean_session,
      legend={"title": "Earthquakes", "position": "top-left", "visible": True},
  )
  assert res["success"] is True
  assert res["layout"]["legend"]["title"] == "Earthquakes"


@pytest.mark.asyncio
async def test_checkpoint_and_rollback(clean_session):
  # 1. Store ref data & layer
  geojson = {"type": "FeatureCollection", "features": []}
  ref_id = await session_data_manager.store(clean_session, geojson, prefix="geojson")

  layer = {
      "id": "pts",
      "source": "s1",
      "type": "circle",
      "paint": {"color": "#0000ff"},
  }

  await mapspec_store.layer_upsert(clean_session, layer)

  # Set source url to ref_id
  mapspec = await mapspec_store.get_mapspec(clean_session)
  mapspec["sources"]["s1"]["url"] = ref_id
  await mapspec_store.save_mapspec(clean_session, mapspec)

  # 2. Create Checkpoint
  ckpt_res = await mapspec_store.checkpoint(clean_session, "ckpt_1")
  assert ckpt_res["success"] is True
  assert ckpt_res["checkpoint_id"] == "ckpt_1"
  assert ckpt_res["ref_count"] == 1

  # 3. Mutate MapSpec (add layer)
  layer2 = {"id": "line_layer", "source": "s1", "type": "line"}
  await mapspec_store.layer_upsert(clean_session, layer2)
  mutated = await mapspec_store.get_mapspec(clean_session)
  assert len(mutated["layers"]) == 2

  # 4. Rollback to ckpt_1
  rb_res = await mapspec_store.rollback(clean_session, "ckpt_1")
  assert rb_res["success"] is True
  restored = rb_res["mapspec"]
  assert len(restored["layers"]) == 1
  assert restored["layers"][0]["id"] == "pts"


@pytest.mark.asyncio
async def test_checkpoint_materializes_inline_data(clean_session):
  """A checkpoint of an inlineData layer must be self-contained (spec Story 31).

  layer_upsert with source_data must persist the data (previously it was
  profiled-then-discarded, leaving the source with no data). With the data
  persisted as source.inlineData, the checkpoint's mapspec.json copy carries
  it — so the snapshot is replayable without the live session store.
  """
  geojson = {
      "type": "FeatureCollection",
      "features": [
          {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
           "properties": {"mag": 5}},
      ],
  }
  layer = {
      "id": "eq",
      "source": "pts",
      "type": "circle",
      "paint": {"color": "#ff0000"},
  }
  # layer_upsert with source_data must persist it into source.inlineData.
  await mapspec_store.layer_upsert(clean_session, layer, source_data=geojson)
  persisted = await mapspec_store.get_mapspec(clean_session)
  assert persisted["sources"]["pts"]["inlineData"] == geojson, (
      "layer_upsert must persist source_data as inlineData"
  )

  # Checkpoint, then prove the snapshot is self-contained: reading ONLY the
  # checkpoint dir (no live session store) recovers the original GeoJSON.
  ckpt_res = await mapspec_store.checkpoint(clean_session, "ckpt_inline")
  assert ckpt_res["success"] is True
  import json as _json
  ckpt_dir = BASE_STORAGE_DIR / clean_session / "checkpoints" / "ckpt_inline"
  snapshot = _json.loads((ckpt_dir / "mapspec.json").read_text())
  assert snapshot["sources"]["pts"]["inlineData"] == geojson, (
      "inlineData must survive into the checkpoint snapshot"
  )
