"""WP02/WP03 时间科学与计划合同：场景 1–4、8–11、24 的纯计划层验收。"""
from __future__ import annotations

import pytest

from app.services.temporal_media.contracts import (
    MAX_OBSERVATIONS, MAX_TOTAL_DISPLAY_MS, MediaPlanArgs, TemporalMediaError,
)
from app.services.temporal_media.planning import (
    assert_plan_hash, build_plan, detect_gaps, plan_hash_of,
)
from app.services.temporal_media.capabilities import probe_capabilities
from app.services.temporal_media.freeze import build_frozen

from .conftest import make_plan, temporal_document


def test_scenario_1_five_real_observations_one_second_each():
    _, _, plan = make_plan(width=640, height=360, dwell_ms=1000)
    display = plan["display"]
    assert len(plan["observations"]) == 5
    assert display["observation_count"] == 5
    assert display["timing"]["total_display_ms"] == 5000
    assert [s["duration_ms"] for s in display["segments"]] == [1000] * 5
    assert display["timing"]["last_segment_hold_ms"] == 1000
    assert display["encoded_frame_count"] == 5
    assert all(s["identity"] == "real_observation" for s in display["segments"])
    assert plan["display"]["timing"]["quantization"] == \
        "integer milliseconds, exact cumulative sum"


def test_scenario_2_missing_years_are_disclosed_not_fabricated():
    _, _, plan = make_plan(temporal_document([2019, 2021, 2024]),
                           width=640, height=360)
    gaps = plan["gaps"]
    assert [g["label"] for g in gaps] == ["2020", "2022", "2023"]
    assert plan["display"]["missing_period_count"] == 3
    assert [s["observation_label"] for s in plan["display"]["segments"]] == \
        ["2019", "2021", "2024"]
    # 缺期不产生新观测，也不把缺的三年压成一帧冒充连续年度资料
    assert all(not s["missing_period"] for s in plan["display"]["segments"])
    assert any("missing_periods=3" in d for d in plan["disclosures"])
    assert f"actual_span_ms={plan['selection']['actual_span_ms']}" in plan["disclosures"]
    assert plan["selection"]["actual_span_ms"] > 0


def test_scenario_3_proportional_keeps_real_interval_ratio():
    _, _, plan = make_plan(temporal_document([2019, 2021, 2024]),
                           width=640, height=360,
                           timing_policy="proportional", dwell_ms=1000)
    timing = plan["display"]["timing"]
    durations = [s["duration_ms"] for s in plan["display"]["segments"]]
    # 间隔 2 年 : 3 年 → 展示时长比 ≈ 2:3（整数毫秒量化）
    assert durations[0] < durations[1]
    assert abs(durations[0] / durations[1] - 2 / 3) < 0.02, durations
    assert timing["rule"].startswith("d_i = clamp(")
    assert timing["compressed_observations"] == []  # 10 年阈值内未压缩
    assert timing["quantization_error_ms"] >= 0


def test_scenario_3b_long_gap_compression_is_disclosed():
    _, _, plan = make_plan(temporal_document([1900, 2000, 2001]),
                           width=640, height=360,
                           timing_policy="proportional", dwell_ms=1000)
    assert plan["display"]["timing"]["compressed_observations"]
    assert any("proportional_gap_compressed" in d for d in plan["disclosures"])


def test_scenario_4_hold_display_keeps_real_observation_identity():
    _, _, plan = make_plan(temporal_document([2019, 2021]),
                           width=640, height=360, missing_display="hold")
    segments = plan["display"]["segments"]
    held = next(s for s in segments if s["kind"] == "held")
    assert held["observation_id"] is None
    assert held["held_from"] == "f-2019"
    assert "沿用 2019 观测" in held["time_label"]
    assert "2020" in held["time_label"]
    assert held["identity"].startswith("held_from:")
    # 沿用段不是观测桶
    assert plan["display"]["observation_count"] == 2
    assert any("held_display_reuses" in d for d in plan["disclosures"])


def test_scenario_4b_empty_display_is_labelled_as_no_observation():
    _, _, plan = make_plan(temporal_document([2019, 2021]),
                           width=640, height=360, missing_display="empty")
    empty = next(s for s in plan["display"]["segments"] if s["kind"] == "empty")
    assert "无观测" in empty["time_label"]
    assert empty["identity"] == "missing_period_empty"


def test_scenario_8_reverse_order_keeps_source_times():
    _, _, asc = make_plan(width=640, height=360)
    _, _, desc = make_plan(width=640, height=360, order="reverse")
    assert [s["observation_label"] for s in desc["display"]["segments"]] == \
        list(reversed([s["observation_label"] for s in asc["display"]["segments"]]))
    assert desc["display"]["direction"] == "source_time_descending_explicit"
    assert desc["temporal_contract"]["extent"] == asc["temporal_contract"]["extent"]
    assert any("explicit_reverse_playback" in d for d in desc["disclosures"])


def test_scenario_9_identical_pictures_retain_distinct_identity():
    _, _, plan = make_plan(temporal_document([2019, 2020, 2021],
                                             identical_picture=True),
                           width=640, height=360, time_display="none")
    segments = plan["display"]["segments"]
    assert len({s["observation_id"] for s in segments}) == 3
    assert [s["time_label"] for s in segments] == ["", "", ""]
    assert plan["display"]["observation_count"] == 3


def test_scenario_10_one_dwell_may_encode_multiple_identical_frames():
    _, _, plan = make_plan(width=640, height=360, dwell_ms=1000, fps=4)
    segments = plan["display"]["segments"]
    assert all(s["encoded_repeat"] == 4 for s in segments)
    assert all(sum(s["encoded_durations_ms"]) == s["duration_ms"]
               for s in segments)
    assert plan["display"]["encoded_frame_count"] == 20
    assert plan["display"]["observation_count"] == 5  # 观测数不增加
    assert any("one_dwell_may_encode" in d for d in plan["disclosures"])


def test_plan_hash_is_stable_and_version_checked():
    _, _, a = make_plan(width=640, height=360)
    _, _, b = make_plan(width=640, height=360)
    assert a["plan_hash"] == b["plan_hash"]
    assert plan_hash_of(a) == a["plan_hash"]
    assert_plan_hash(a, a["plan_hash"])
    with pytest.raises(TemporalMediaError) as exc:
        assert_plan_hash(a, "0" * 64)
    assert exc.value.code == "PLAN_VERSION_MISMATCH"
    _, _, changed = make_plan(width=640, height=360, dwell_ms=2000)
    assert changed["plan_hash"] != a["plan_hash"]


def test_unknown_frame_id_and_selection_disclosure():
    with pytest.raises(TemporalMediaError) as exc:
        make_plan(width=640, height=360, frame_ids=["f-2099"])
    assert exc.value.code == "UNKNOWN_FRAME_ID"
    _, _, plan = make_plan(width=640, height=360,
                           frame_ids=["f-2019", "f-2021"])
    assert plan["selection"]["omitted_observations"] == 3
    assert plan["selection"]["selection"] == "explicit_ids"
    assert plan["display"]["observation_count"] == 2


def test_scenario_24_budgets_are_enforced_not_silently_truncated():
    with pytest.raises(TemporalMediaError) as exc:
        make_plan(temporal_document(list(range(1900, 1900 + MAX_OBSERVATIONS + 2))),
                  width=640, height=360)
    assert exc.value.code == "RESOURCE_BUDGET_EXCEEDED"
    with pytest.raises(TemporalMediaError) as exc2:
        make_plan(width=640, height=360, dwell_ms=10_000, fps=24)
    assert exc2.value.code == "RESOURCE_BUDGET_EXCEEDED"
    _, _, plan = make_plan(width=640, height=360, dwell_ms=1000)
    assert plan["resource_estimate"]["encoded_frames"] == 5
    assert plan["budgets"]["max_total_display_ms"] == MAX_TOTAL_DISPLAY_MS


def test_closed_vocabulary_rejects_unknown_arguments():
    with pytest.raises(Exception):
        MediaPlanArgs(session_id="s", format="mp4",
                      ffmpeg_preset="/etc/passwd")
    with pytest.raises(Exception):
        MediaPlanArgs(session_id="s", format="mp4")


def test_blocking_policy_refuses_non_reproducible_layers():
    doc = temporal_document()
    doc["sources"]["external"] = {"type": "raster",
                                  "tiles": ["https://tiles.example/{z}/{x}/{y}.png"]}
    doc["layers"].append({"id": "external", "source": "external",
                          "type": "raster"})
    frozen = build_frozen("sess-g35", doc, 3)
    with pytest.raises(TemporalMediaError) as exc:
        build_plan(frozen, MediaPlanArgs(session_id="sess-g35", width=640,
                                         height=360),
                   "session:sess-g35", probe_capabilities())
    assert exc.value.code == "BLOCKED_INCOMPLETE"
    assert exc.value.details["omitted_layers"][0]["reason"] == \
        "external_tiles_licence_not_embedded"
    partial = build_plan(
        frozen, MediaPlanArgs(session_id="sess-g35", width=640, height=360,
                              partial_policy="allow_partial"),
        "session:sess-g35", probe_capabilities())
    assert partial["partial"]["is_partial"] is True
    assert len(partial["partial"]["omitted_layers"]) == 1


def test_gap_detection_is_calendar_aware_in_contract_timezone():
    doc = temporal_document([2019, 2021, 2024])
    gaps = detect_gaps(doc["temporal"]["frames"], doc["temporal"])
    assert [g["label"] for g in gaps] == ["2020", "2022", "2023"]
    assert all(g["kind"] == "missing_period" for g in gaps)
