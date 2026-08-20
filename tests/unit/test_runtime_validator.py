"""Tests for the headless Runtime Validator.

Two layers:
- `compute_eval_scores` is a pure function → fast unit tests (always run).
- The full `validate_runtime` flow drives real headless Chromium over compiled
  output → marked `heavy` (opt-in, needs the Playwright browser + network for
  the MapLibre CDN). This mirrors how Seam C is gated per the spec.

#532: the heavy flow self-skips unless REQUIRE_BROWSER=1 (set by the nightly
`runtime-validator` CI lane, which installs playwright + chromium). In that
lane a missing browser is a hard FAIL, not a green SKIPPED.
"""
import json
import os
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
  assert scores["cartographic_quality_status"] == "heuristic_visual_proxies"
  assert scores["cartographic_quality_evidence_class"] == "heuristic"


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
  assert scores["cartographic_quality_status"] == "heuristic_visual_proxies"
  assert scores["cartographic_quality_evidence_class"] == "heuristic"
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

REQUIRE_BROWSER = os.environ.get("REQUIRE_BROWSER") == "1"

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "runtime"


def _browser_guard(message: str) -> None:
    """#532：浏览器依赖缺失时的处置 —— REQUIRE_BROWSER=1 的 lane 硬失败，
    其余上下文（本地开发 / PR lane）跳过。修复前这里永远 skip：没有任何 lane
    安装 playwright，真实 MapLibre 渲染门在 CI 里从未执行。"""
    if REQUIRE_BROWSER:
        pytest.fail(message)
    pytest.skip(message)


def _discover_runtime_fixtures() -> list[str]:
    if not FIXTURE_ROOT.exists():
        return []
    names: list[str] = []
    for d in sorted(FIXTURE_ROOT.iterdir()):
        if d.is_dir() and (d / "mapspec.json").is_file() and (d / "probes.json").is_file():
            names.append(d.name)
    return names


_FIXTURE_NAMES = _discover_runtime_fixtures()


@pytest.mark.heavy
@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
async def test_runtime_fixture(fixture_name: str, clean_session):
  """Parameterized runtime fixture validation — one param per dir under tests/fixtures/runtime/.

  For each fixture: compile via the Python service (mirroring the old full_flow),
  validate with --probes, assert per expect.
  """
  import subprocess
  from app.services.mapspec_store import PROJECT_ROOT

  if not (PROJECT_ROOT / "frontend" / "node_modules" / "playwright").exists():
    _browser_guard("requires Playwright (run `npm ci` in frontend/ first)")

  try:
    check_proc = subprocess.run(
        [
            "node",
            "-e",
            "const { chromium } = require('playwright'); if (!require('fs').existsSync(chromium.executablePath())) process.exit(1);",
        ],
        cwd=str(PROJECT_ROOT / "frontend"),
        capture_output=True,
        timeout=5,
    )
    if check_proc.returncode != 0:
      _browser_guard("requires Playwright Chromium binary (run `npx playwright install` in frontend/)")
  except Exception:
    _browser_guard("requires Playwright Chromium binary")

  from app.services.runtime_validator import runtime_validator

  fixture_dir = FIXTURE_ROOT / fixture_name
  mapspec = json.loads((fixture_dir / "mapspec.json").read_text(encoding="utf-8"))
  probes_spec = json.loads((fixture_dir / "probes.json").read_text(encoding="utf-8"))
  expect = probes_spec.get("expect", "pass")

  # Persist the fixture MapSpec into the session (mirrors old init_project+layer_upsert path).
  await mapspec_store.save_mapspec(clean_session, mapspec)

  probes_path = fixture_dir / "probes.json"
  res = await runtime_validator.validate_runtime(clean_session, probes_path=probes_path)

  report = res["report"]
  runtime_dir = Path(res["runtime_dir"])

  if expect == "pass":
    assert report["mapLoaded"] is True, f"{fixture_name}: mapLoaded"
    assert report["mapIdle"] is True, f"{fixture_name}: mapIdle"
    assert report["fatalError"] is None, f"{fixture_name}: fatalError={report['fatalError']}"
    assert report["pageErrors"] == [], f"{fixture_name}: pageErrors"
    assert report["consoleErrors"] == [], f"{fixture_name}: consoleErrors"
    # probeResults all pass
    probe_results = report.get("probeResults") or []
    assert len(probe_results) > 0, f"{fixture_name}: expected probeResults"
    assert all(r.get("pass") for r in probe_results), f"{fixture_name}: probeResults should all pass: {probe_results}"
    # Evidence trail persisted
    assert (runtime_dir / "map.png").exists(), f"{fixture_name}: map.png missing"
    assert (runtime_dir / "trace.zip").exists(), f"{fixture_name}: trace.zip missing"
    assert (runtime_dir / "report.json").exists(), f"{fixture_name}: report.json missing"
    # Also check report.json on disk contains probeResults
    disk_report = json.loads((runtime_dir / "report.json").read_text(encoding="utf-8"))
    assert "probeResults" in disk_report, f"{fixture_name}: report.json missing probeResults"
    assert all(r.get("pass") for r in disk_report["probeResults"]), f"{fixture_name}: disk probeResults should all pass"
    assert res["score"] <= 100.0
  else:  # expect == "fail"
    probe_results = report.get("probeResults") or []
    assert len(probe_results) > 0, f"{fixture_name}: expect:fail must have probeResults"
    assert any(not r.get("pass") for r in probe_results), f"{fixture_name}: expect:fail must have at least one failing probe: {probe_results}"
    # 门必须因探针失败而红，而非基础设施失败（浏览器崩溃/编译错误/页面异常）——
    # 否则负例证伪不了"探针会红"这件事本身（#673 评审结论）。
    assert report["fatalError"] is None, f"{fixture_name}: expect:fail 浏览器须正常，fatalError={report['fatalError']}"
    assert report["mapLoaded"] is True and report["mapIdle"] is True, f"{fixture_name}: expect:fail 地图须正常加载"
    assert report["pageErrors"] == [] and report["consoleErrors"] == [], f"{fixture_name}: expect:fail 须无页面/控制台错误"
    assert res.get("valid") is False, f"{fixture_name}: expect:fail should be invalid"
    # report.json should also contain failing probe
    disk_report_path = runtime_dir / "report.json"
    if disk_report_path.exists():
      disk_report = json.loads(disk_report_path.read_text(encoding="utf-8"))
      if "probeResults" in disk_report:
        assert any(not r.get("pass") for r in disk_report["probeResults"]), f"{fixture_name}: disk probeResults should have failing probe"
