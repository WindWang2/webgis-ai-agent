import shutil
import uuid
import pytest
from app.services.mapspec_store import mapspec_store, BASE_STORAGE_DIR, view_has_center
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry
from app.tools.cartography_tools import register_mapspec_cartography_tools


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
async def test_layer_upsert_does_not_clobber_explicit_origin(clean_session):
  """Regression: an explicitly-set center=[0,0] must survive auto-view injection.

  Previously the heuristic `center == [0.0, 0.0]` treated the origin as
  "unset" and clobbered it with the profiler's suggestedView. A user wanting
  Null Island (common in demos/tests) lost their view. Now only an *absent*
  center is treated as unset.
  """
  # Init with an explicit origin view.
  await mapspec_store.init_project(
      clean_session, view={"center": [0.0, 0.0], "zoom": 4.0}
  )
  geojson = {
      "type": "FeatureCollection",
      "features": [
          {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
           "properties": {"val": 1}},
      ],
  }
  layer = {"id": "eq2", "source": "src2", "type": "circle", "paint": {"color": "#00f"}}
  await mapspec_store.layer_upsert(clean_session, layer, source_data=geojson)

  mapspec = await mapspec_store.get_mapspec(clean_session)
  # The explicit origin must survive — NOT replaced by the profiler's suggestion.
  assert mapspec["view"]["center"] == [0.0, 0.0]
  assert mapspec["view"]["zoom"] == 4.0


@pytest.mark.asyncio
async def test_init_project_leaves_view_unset_when_not_provided(clean_session):
  """A fresh MapSpec with no view argument should have an empty (unset) view,
  not the magic {center: [0,0], zoom: 2} default that previously masked 'unset'."""
  res = await mapspec_store.init_project(clean_session)
  assert res["mapspec"]["view"] == {}


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


@pytest.mark.asyncio
async def test_layer_upsert_analysis_result_contract(clean_session):
  geojson = {
      "type": "FeatureCollection",
      "features": [
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
              "properties": {"val": 5},
          },
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [121.0, 31.0]},
              "properties": {"val": 15},
          },
      ],
  }
  analysis_data = {
      "success": True,
      "algorithm": "spatial_hotspot",
      "data": geojson,
      "legend_spec": {
          "type": "graduated",
          "field": "val",
          "breaks": [0.0, 10.0, 20.0],
          "palette_colors": ["#0000ff", "#ff0000"],
      },
      "source_ref": "ref:input_1",
      "params": {"radius": 1000},
  }

  layer = {
      "id": "hotspot_layer",
      "source": "hotspot_source",
  }

  res = await mapspec_store.layer_upsert(clean_session, layer, source_data=analysis_data)
  mapspec = res["mapspec"]

  upserted_layer = mapspec["layers"][0]
  assert upserted_layer["id"] == "hotspot_layer"
  assert upserted_layer["type"] == "circle"
  assert upserted_layer["paint"]["color"]["method"] == "step"
  assert upserted_layer["paint"]["color"]["field"] == "val"
  assert upserted_layer["provenance"]["algorithm"] == "spatial_hotspot"
  assert mapspec["sources"]["hotspot_source"]["inlineData"] == geojson


def test_view_has_center_false_when_absent():
  assert view_has_center({}) is False
  assert view_has_center({"view": {}}) is False
  assert view_has_center({"view": {"zoom": 5.0}}) is False


def test_view_has_center_false_when_none():
  # Defensive: a center key carrying None is treated as unset.
  assert view_has_center({"view": {"center": None}}) is False


def test_view_has_center_true_for_origin():
  # The regression: an explicitly-set [0.0, 0.0] must count as set.
  assert view_has_center({"view": {"center": [0.0, 0.0]}}) is True
  assert view_has_center({"view": {"center": [120.0, 30.0]}}) is True


@pytest.mark.asyncio
async def test_layer_upsert_raster_source_contract(clean_session):
  """Raster path (ADR-0011): a source_data payload carrying a numpy array +
  bounds is rendered to a PNG and stored as a `type:"raster"` MapSpec source
  (imageRef + bounds + imageSize), with a continuous legend_spec on the layer.

  Mirrors test_layer_upsert_analysis_result_contract's shape: feed a payload,
  assert the persisted MapSpec source + layer carry the right shape.
  """
  import numpy as np
  from app.services.raster_store import resolve_png_path

  await mapspec_store.init_project(clean_session)
  arr = np.array([[0.1, 0.5, 0.9], [0.2, 0.8, 0.4]])
  payload = {
      "algorithm": "compute_ndvi",
      "item_id": "S2B_tile_xyz",
      "raster_source": {
          "array": arr,
          "bounds": [100.0, 20.0, 101.0, 21.0],
          "band_stats": {"min": 0.1, "max": 0.9},
          "suggested_palette": "Viridis",
      },
  }
  layer = {"id": "ndvi_layer", "source": "ndvi_src"}

  res = await mapspec_store.layer_upsert(clean_session, layer, source_data=payload)
  mapspec = res["mapspec"]

  # The layer became a raster layer with a continuous legend_spec.
  upserted = mapspec["layers"][0]
  assert upserted["id"] == "ndvi_layer"
  assert upserted["type"] == "raster"
  assert upserted["source"] == "ndvi_src"
  assert upserted["legend_spec"]["type"] == "continuous"
  assert upserted["legend_spec"]["palette"] == "Viridis"

  # The source is type:"raster" with imageRef + bounds + imageSize.
  src = mapspec["sources"]["ndvi_src"]
  assert src["type"] == "raster"
  assert src["bounds"] == [100.0, 20.0, 101.0, 21.0]
  assert src["imageRef"].startswith("ref:raster/")
  # imageSize is [width, height] = [cols, rows] of the array.
  assert src["imageSize"] == [3, 2]

  # The PNG actually landed on disk and resolves via the imageRef.
  session_dir = BASE_STORAGE_DIR / clean_session
  png_path = resolve_png_path(session_dir, src["imageRef"])
  assert png_path is not None and png_path.exists()
  assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_layer_upsert_raster_does_not_profile(clean_session):
  """A raster source carries no GeoJSON — the auto-profiler must skip it
  (is_raster_entry guard), leaving no `profile` key on the source entry."""
  import numpy as np
  await mapspec_store.init_project(clean_session)
  payload = {
      "raster_source": {"array": np.zeros((2, 2)), "bounds": [0, 0, 1, 1]},
  }
  await mapspec_store.layer_upsert(clean_session, {"id": "r", "source": "rs"}, source_data=payload)
  persisted = await mapspec_store.get_mapspec(clean_session)
  assert "profile" not in persisted["sources"]["rs"]


