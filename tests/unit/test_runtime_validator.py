"""Tests for the headless Runtime Validator.

Two layers:
- `compute_eval_scores` is a pure function → fast unit tests (always run).
- The full `validate_runtime` flow drives real headless Chromium over compiled
  output → marked `heavy` (opt-in, needs the Playwright browser + network for
  the MapLibre CDN). This mirrors how Seam C is gated per the spec.
"""
import shutil
import uuid
from pathlib import Path

import pytest

from app.services.mapspec_store import mapspec_store, BASE_STORAGE_DIR
from app.services.runtime_validator import compute_eval_scores
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
  sid = f"test-runtime-session-{uuid.uuid4().hex[:8]}"
  await session_data_manager.clear_session(sid)
  yield sid
  await session_data_manager.clear_session(sid)
  session_dir = BASE_STORAGE_DIR / sid
  if session_dir.exists():
    shutil.rmtree(session_dir, ignore_errors=True)


# ─── pure scoring (fast, always runs) ────────────────────────────────────────

def test_scores_full_marks_for_a_clean_run():
  report = {
      "mapLoaded": True,
      "mapIdle": True,
      "pageErrors": [],
      "consoleErrors": [],
      "failedRequests": [],
      "fatalError": None,
      "_evidenceComplete": True,
  }
  mapspec = {"sources": {"s1": {}}, "layers": [{"id": "l1"}]}
  scores = compute_eval_scores(report, mapspec)
  assert scores["spatial_data_score"] == 25.0
  assert scores["task_completion_score"] == 20.0
  assert scores["browser_runtime_score"] == 15.0
  assert scores["traceability_score"] == 10.0
  assert scores["efficiency_score"] == 10.0
  assert scores["total_score_80_max"] == 80.0
  assert scores["cartographic_quality_status"] in ("deferred_pending_visual_judge", "evaluated_by_visual_judge")


def test_scores_visual_judge_full_marks():
  report = {
      "mapLoaded": True,
      "mapIdle": True,
      "pageErrors": [],
      "consoleErrors": [],
      "failedRequests": [],
      "fatalError": None,
      "_evidenceComplete": True,
      "canvas": {
          "luminanceStdDev": 25.0,
          "dominantRatio": 0.50,
          "transparentRatio": 0.10,
          "blank": False,
      },
      "controls": {"overflow": [], "collisions": []},
  }
  mapspec = {"sources": {"s1": {}}, "layers": [{"id": "l1"}]}
  scores = compute_eval_scores(report, mapspec)
  assert scores["cartographic_quality_score"] == 20.0
  assert scores["total_score_100_max"] == 100.0
  assert scores["cartographic_quality_status"] == "evaluated_by_visual_judge"
  assert scores["visual_judge_details"]["visual_contrast_score"] == 8.0
  assert scores["visual_judge_details"]["label_collision_score"] == 6.0
  assert scores["visual_judge_details"]["layout_balance_score"] == 6.0


def test_scores_visual_judge_penalises_collisions_and_flat_canvas():
  report = {
      "mapLoaded": True,
      "mapIdle": True,
      "pageErrors": [],
      "consoleErrors": [],
      "failedRequests": [],
      "fatalError": None,
      "_evidenceComplete": True,
      "canvas": {
          "luminanceStdDev": 2.0,
          "dominantRatio": 0.99,
          "transparentRatio": 0.85,
          "blank": True,
      },
      "controls": {
          "overflow": ["ctrl outside"],
          "collisions": ["ctrl ↔ ctrl"],
      },
  }
  mapspec = {"sources": {"s1": {}}, "layers": [{"id": "l1"}]}
  scores = compute_eval_scores(report, mapspec)
  assert scores["visual_judge_details"]["visual_contrast_score"] == 2.0
  assert scores["visual_judge_details"]["label_collision_score"] == 2.0
  assert scores["visual_judge_details"]["layout_balance_score"] == 3.0
  assert scores["cartographic_quality_score"] == 7.0
  assert scores["total_score_100_max"] == 87.0


def test_scores_penalise_browser_failures():
  # console errors and a missing screenshot should drop browser + traceability.
  report = {
      "mapLoaded": True,
      "mapIdle": True,
      "pageErrors": [],
      "consoleErrors": ["boom"],
      "failedRequests": [],
      "fatalError": None,
      "_evidenceComplete": False,
  }
  mapspec = {"sources": {"s1": {}}, "layers": [{"id": "l1"}]}
  scores = compute_eval_scores(report, mapspec)
  assert scores["browser_runtime_score"] == 5.0  # console error → not browser_ok
  assert scores["traceability_score"] == 6.0  # no screenshot/trace
  assert scores["total_score_80_max"] < 80.0


def test_scores_penalise_fatal_error():
  report = {
      "mapLoaded": False,
      "mapIdle": False,
      "pageErrors": [],
      "consoleErrors": [],
      "failedRequests": [],
      "fatalError": "chromium missing",
      "_evidenceComplete": False,
  }
  mapspec = {"sources": {"s1": {}}, "layers": [{"id": "l1"}]}
  scores = compute_eval_scores(report, mapspec)
  # fatal → not browser_ok, efficiency penalised
  assert scores["browser_runtime_score"] == 5.0
  assert scores["efficiency_score"] == 4.0


def test_scores_low_for_empty_mapspec():
  report = {
      "mapLoaded": True,
      "mapIdle": True,
      "pageErrors": [],
      "consoleErrors": [],
      "failedRequests": [],
      "fatalError": None,
      "_evidenceComplete": True,
  }
  mapspec = {"sources": {}, "layers": []}
  scores = compute_eval_scores(report, mapspec)
  assert scores["spatial_data_score"] == 10.0  # no sources/layers
  assert scores["task_completion_score"] == 10.0


# ─── full browser-driven flow (heavy, opt-in) ─────────────────────────────────

@pytest.mark.heavy
@pytest.mark.asyncio
async def test_runtime_validator_full_flow(clean_session):
  """End-to-end: init → layer_upsert → validate_runtime drives headless Chromium.

  Requires the Playwright chromium browser and network access to the MapLibre
  CDN (the compiled HTML loads maplibre-gl from unpkg). Run with: pytest -m heavy
  """
  # The validator drives a Node subprocess (npx tsx runtime-validate.ts, cwd=frontend)
  # that launches Playwright Chromium and loads maplibre-gl from a CDN. The Backend
  # Tests CI job never runs `npm ci`, so frontend/node_modules/playwright is absent
  # and the subprocess fails with `mapLoaded: False`. Skip gracefully rather than
  # fail with a misleading assertion. Mirrors the skipif(weasyprint is None) guard
  # in test_report_service_vector_svg.py.
  from app.services.mapspec_store import PROJECT_ROOT

  if not (PROJECT_ROOT / "frontend" / "node_modules" / "playwright").exists():
    pytest.skip("requires Playwright (run `npm ci` in frontend/ first)")

  from app.services.runtime_validator import runtime_validator

  await mapspec_store.init_project(
      clean_session, view={"center": [0.0, 0.0], "zoom": 2.0}
  )
  # A GeoJSON point layer with an interpolated colour (the compiler's `type`
  # contract). Three points spread far enough to avoid a pure-blank canvas.
  layer = {
      "id": "eq",
      "source": "pts",
      "type": "circle",
      "source_spec": {
          "type": "geojson",
          "inlineData": {
              "type": "FeatureCollection",
              "features": [
                  {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
                   "properties": {"mag": 5}},
                  {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10, 10]},
                   "properties": {"mag": 3}},
                  {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-10, -5]},
                   "properties": {"mag": 7}},
              ],
          },
      },
      "paint": {
          "color": {
              "method": "interpolate",
              "field": "mag",
              "stops": [[0, "#2ca25f"], [8, "#de2d26"]],
          },
          "radius": 8,
      },
  }
  await mapspec_store.layer_upsert(clean_session, layer)

  res = await runtime_validator.validate_runtime(clean_session)

  # The browser contract must hold even if the canvas-blank risk signal trips
  # for this minimal fixture (a 3-point map is legitimately near-monochrome).
  report = res["report"]
  assert report["mapLoaded"] is True
  assert report["mapIdle"] is True
  assert report["fatalError"] is None
  assert report["pageErrors"] == []
  assert report["consoleErrors"] == []
  # Evidence trail persisted.
  assert (Path(res["runtime_dir"]) / "map.png").exists()
  assert (Path(res["runtime_dir"]) / "trace.zip").exists()
  assert (Path(res["runtime_dir"]) / "report.json").exists()
  # Score is present and evaluated up to 100.0 max (cartographic quality visual judge included).
  assert res["score"] <= 100.0
