import shutil
import uuid
import pytest
from pathlib import Path

from app.services.mapspec_store import mapspec_store, BASE_STORAGE_DIR
from app.services.runtime_validator import runtime_validator, compute_eval_scores
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


@pytest.mark.asyncio
async def test_runtime_validator_flow(clean_session):
  # 1. Init project & add a layer
  await mapspec_store.init_project(clean_session, view={"center": [120.0, 30.0], "zoom": 10.0})

  layer = {
      "id": "circle_layer",
      "source": "s1",
      "type": "circle",
      "paint": {"color": "#ff0000"},
  }
  await mapspec_store.layer_upsert(clean_session, layer)

  # 2. Run runtime validator
  res = await runtime_validator.validate_runtime(clean_session)

  assert res["success"] is True
  assert "report" in res
  assert res["report"]["mapLoaded"] is True

  scores = res["eval_scores"]
  assert "total_score_80_max" in scores
  assert scores["total_score_80_max"] == 80.0
  assert scores["cartographic_quality_status"] == "deferred_pending_visual_judge"


def test_compute_eval_scores():
  report = {"mapLoaded": True, "pageErrors": []}
  mapspec = {"sources": {"s1": {}}, "layers": [{"id": "l1"}]}

  scores = compute_eval_scores(report, mapspec)
  assert scores["spatial_data_score"] == 25.0
  assert scores["task_completion_score"] == 20.0
  assert scores["browser_runtime_score"] == 15.0
  assert scores["total_score_80_max"] == 80.0
