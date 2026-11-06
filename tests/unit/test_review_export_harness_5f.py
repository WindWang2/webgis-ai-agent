"""Boundary regressions for export quality and offline QA audit findings."""
import json
import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import Point, Polygon


def test_signed_charts_stay_inside_plot_domain():
    from app.lib.cartography.svg_charts import _render_bars, _render_line_family
    for horizontal in (False, True):
        for stacked in (False, True):
            fragment = _render_bars(0, 0, "Signed values", [[("a", -5), ("b", 3)], [("c", -2)]], horizontal=horizontal, stacked=stacked)
            assert "Signed values" in fragment
            tree = ET.fromstring(f"<svg>{fragment}</svg>")
            for rect in tree.findall("rect"):
                y, height = float(rect.attrib["y"]), float(rect.attrib["height"])
                assert 0 <= y <= 116 and y + height <= 116.01
                assert float(rect.attrib["width"]) >= 0
    for area in (False, True):
        fragment = _render_line_family(0, 0, "Negative", [[("a", -5), ("b", -2)]], area=area, cumulative=False)
        tree = ET.fromstring(f"<svg>{fragment}</svg>")
        for polyline in tree.findall("polyline"):
            assert all(16 <= float(pair.split(",")[1]) <= 116 for pair in polyline.attrib["points"].split())


def test_concave_polygon_anchor_is_inside():
    from app.lib.cartography.label_engine import LabelFeature, _polygon_candidates
    ring = [[0, 0], [100, 0], [100, 100], [70, 100], [70, 30], [30, 30], [30, 100], [0, 100]]
    polygon = Polygon(ring)
    candidates = _polygon_candidates(LabelFeature(id="u", text="U", kind="polygon", geometry=ring))
    assert candidates
    assert all(polygon.contains(Point(candidate.x, candidate.y)) for candidate in candidates)


def test_story_export_scrubs_secrets_inside_prose():
    from app.lib.storymap.export_packager import sanitize_dict
    out = sanitize_dict({"narrative": "download https://example.com/a?api_key=private-value&mode=map next", "details": ["Bearer secret-token-value"]})
    assert "private-value" not in json.dumps(out)
    assert "secret-token-value" not in json.dumps(out)
    assert "mode=map" in out["narrative"]


def test_strict_schema_accepts_all_fake_samples():
    from jsonschema import validate
    from app.lib.harness.visual_judge.vlm_provider import CRITIC_OUTPUT_SCHEMA
    from app.lib.harness.visual_judge.fake_vlm import GOLDEN_SAMPLES
    item = CRITIC_OUTPUT_SCHEMA["properties"]["critiques"]["items"]
    assert set(item["required"]) == set(item["properties"])
    for sample in GOLDEN_SAMPLES:
        validate(sample.response, CRITIC_OUTPUT_SCHEMA)


def test_provider_endpoint_override_never_inherits_main_key(monkeypatch):
    from app.core.config import settings
    from app.lib.harness.visual_judge.vlm_provider import resolve_provider_config
    monkeypatch.setenv("CARTO_VISUAL_JUDGE_BASE_URL", "https://different.example/v1")
    monkeypatch.delenv("CARTO_VISUAL_JUDGE_API_KEY", raising=False)
    monkeypatch.setattr(settings, "LLM_API_KEY", "main-key-must-stay-private")
    assert resolve_provider_config()["api_key"] == ""


def test_fallback_receipts_do_not_skip_remaining_candidates():
    from app.lib.harness.lab.fakes import ScriptedToolProvider
    from app.lib.harness.lab.spec import ProviderOp
    from app.lib.harness.replay.receipt import _RecordedProviderRegistry
    from app.lib.harness.replay.replayer import ScenarioOp
    provider = ScriptedToolProvider([ProviderOp(call_id=str(i), tool="echo", arguments={"n": i}, result={"n": i}) for i in range(3)])
    assert [provider._next("echo", None).call_id for _ in range(3)] == ["0", "1", "2"]
    receipts = _RecordedProviderRegistry([ScenarioOp(call_id=str(i), tool="echo", result={"n": i}) for i in range(3)], {})
    assert [receipts._next("echo", None).call_id for _ in range(3)] == ["0", "1", "2"]


def test_virtual_clock_requires_explicit_advancement():
    from app.lib.harness.replay.determinism import DeterministicClock
    clock = DeterministicClock()
    assert clock.now_ms() == clock.now_ms() == 0
    assert clock.advance(125) == clock.now_ms() == 125


@pytest.mark.asyncio
async def test_default_calibration_preserves_regression_expectations():
    from app.lib.harness.replay.replayer import Scenario, TurnSpec
    from app.lib.harness.replay.roundtrip import calibrate_recorded_scenario
    scenario = Scenario(scenario_id="preserve", category="test", turns=[TurnSpec(expect={"goal": {"status": "impossible-status"}})])
    calibrated, report = await calibrate_recorded_scenario(scenario)
    assert calibrated.turns[0].expect == scenario.turns[0].expect
    assert report["residual_diffs"]
    assert not report["calibrated"]


def test_pdf_legend_and_scale_text_receive_cjk_font(monkeypatch):
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from app.lib.cartography.pdf_renderer import _draw_legend_box, _draw_scale_bar
    figure = Figure()
    texts = []
    monkeypatch.setattr(figure, "text", lambda *args, **kwargs: texts.append(kwargs))
    font = FontProperties(family=["sans-serif"])
    _draw_legend_box(figure, [{"color": "red", "label": "耕地"}], font)
    _draw_scale_bar(figure, None, {"nice": {"px": 100}, "label": "10公里"}, {"width": 1000}, font)
    assert len(texts) == 2
    assert all(text["fontproperties"] is font for text in texts)


def test_screenshot_candidate_must_be_in_storage_root(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.lib.harness.visual_evaluator import _find_screenshot_path
    from app.services.mapspec import store
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    rejected = tmp_path / "private"
    rejected.mkdir()
    (rejected / "map.png").write_bytes(b"sensitive")
    monkeypatch.setattr(store, "BASE_STORAGE_DIR", allowed)
    result = {"c": {"result": {"mapspec_fingerprint": "fp", "runtime_dir": str(rejected)}}}
    assert _find_screenshot_path(SimpleNamespace(mapspec_fingerprint="fp"), result) is None


def test_core_variants_change_actual_parameters():
    from app.lib.harness.replay.scenarios import _single_turn_scenario
    one = _single_turn_scenario("point_distribution", 1)
    two = _single_turn_scenario("point_distribution", 2)
    a = one.turns[0].cartography["mapspec"]
    b = two.turns[0].cartography["mapspec"]
    assert a["view"]["zoom"] != b["view"]["zoom"]
    assert a["layers"][0]["paint"]["circle-radius"] != b["layers"][0]["paint"]["circle-radius"]


def test_recording_global_quota_and_string_redaction(tmp_path, monkeypatch):
    from app.lib.harness.replay import recorder
    monkeypatch.setattr(recorder, "MAX_RECORDING_SESSIONS", 1)
    first = recorder.write_trace({"user_input": "Bearer first-private-token", "final_text": "token=secret-private-value"}, session_id="old", turn_id="t1", root=tmp_path)
    raw = first.read_text()
    assert "first-private-token" not in raw and "secret-private-value" not in raw
    import os
    os.utime(first, (1, 1))
    second = recorder.write_trace({"user_input": "hello", "final_text": "ready"}, session_id="new", turn_id="t2", root=tmp_path)
    assert second.is_file()
    assert not (tmp_path / "old").exists()
