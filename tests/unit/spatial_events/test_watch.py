"""SpatialWatch 求值引擎测试（纯函数 + 状态机：阈值/连续N/进出AOI/时间窗/版本变化/冷却）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.spatial_events import contracts as C
from app.services.spatial_events.watch import (
    WatchEvalResult,
    evaluate_watch,
    watch_matches_event,
)

NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)


def _evt(kind="dataset.version_changed", payload=None, occurred_at=None,
         org="org-a", subject="dataset:ndvi", **over):
    kw = dict(
        kind=kind,
        org_id=org,
        subject_type="dataset",
        subject_key=subject,
        occurred_at=occurred_at or NOW,
        payload=payload if payload is not None else {},
    )
    kw.update(over)
    env = C.SpatialEventEnvelope(**kw)
    return env


def _watch(condition=None, actions=("notify_only",), **over):
    kw = dict(
        watch_id="w1",
        org_id="org-a",
        name="t",
        kinds=["dataset.version_changed", "observation.spatial"],
        condition=condition or C.WatchCondition(),
        actions=list(actions),
        cooldown_s=60.0,
    )
    kw.update(over)
    return C.SpatialWatch(**kw)


class TestMatchFilter:
    def test_kind_subject_org_match(self):
        w = _watch(subject_key_prefix="dataset:ndvi")
        assert watch_matches_event(w, _evt())
        assert not watch_matches_event(w, _evt(subject="dataset:other"))
        assert not watch_matches_event(w, _evt(kind="job.completed"))
        # 租户红线：org 不匹配绝不求值
        assert not watch_matches_event(w, _evt(org="org-b"))

    def test_session_project_filters(self):
        w = _watch(session_id="s1")
        assert watch_matches_event(w, _evt(session_id="s1"))
        assert not watch_matches_event(w, _evt(session_id="s2"))
        w2 = _watch(project_id="p1")
        assert not watch_matches_event(w2, _evt(project_id="p2"))


class TestMetricPredicates:
    def test_lt_threshold_fires(self):
        w = _watch(C.WatchCondition(
            metric_name="metric.ndvi", metric_op="lt", metric_value=0.2))
        r = evaluate_watch(w, C.WatchState(),
                           _evt(payload={"metric": {"ndvi": 0.15}}))
        assert r.fired and r.state.last_metric_value == 0.15

    def test_not_met_resets_consecutive(self):
        w = _watch(C.WatchCondition(
            metric_name="metric.ndvi", metric_op="lt", metric_value=0.2))
        st = C.WatchState(consecutive_hits=2)
        r = evaluate_watch(w, st, _evt(payload={"metric": {"ndvi": 0.9}}))
        assert not r.fired
        assert r.state.consecutive_hits == 0

    def test_missing_metric_not_met(self):
        w = _watch(C.WatchCondition(
            metric_name="metric.ndvi", metric_op="lt", metric_value=0.2))
        r = evaluate_watch(w, C.WatchState(), _evt(payload={}))
        assert not r.fired and "metric" in r.reason

    def test_delta_pct_ge_needs_baseline(self):
        w = _watch(C.WatchCondition(
            metric_name="metric.ndvi", metric_op="delta_pct_ge",
            metric_value=10.0))
        r1 = evaluate_watch(w, C.WatchState(),
                            _evt(payload={"metric": {"ndvi": 0.40}}))
        assert not r1.fired and r1.reason == "no_baseline"
        r2 = evaluate_watch(w, r1.state,
                            _evt(payload={"metric": {"ndvi": 0.50}}))
        assert r2.fired  # +25% >= 10%

    def test_delta_pct_negative_direction(self):
        w = _watch(C.WatchCondition(
            metric_name="metric.ndvi", metric_op="delta_pct_ge",
            metric_value=10.0))
        st = C.WatchState(last_metric_value=0.50)
        r = evaluate_watch(w, st, _evt(payload={"metric": {"ndvi": 0.20}}))
        assert r.fired  # -60% 幅度 >= 10%（变化幅度，非方向）


class TestConsecutiveN:
    def test_fires_on_n_th_consecutive(self):
        w = _watch(C.WatchCondition(
            metric_name="m.v", metric_op="gte", metric_value=5,
            consecutive_n=3))
        st = C.WatchState()
        results = []
        for v in (5, 6, 7):
            r = evaluate_watch(w, st, _evt(payload={"m": {"v": v}}))
            results.append(r.fired)
            st = r.state
        assert results == [False, False, True]

    def test_break_resets_counter(self):
        w = _watch(C.WatchCondition(
            metric_name="m.v", metric_op="gte", metric_value=5,
            consecutive_n=2))
        st = C.WatchState(consecutive_hits=1)
        st = evaluate_watch(w, st, _evt(payload={"m": {"v": 1}})).state
        r = evaluate_watch(w, st, _evt(payload={"m": {"v": 9}}))
        assert not r.fired  # 断裂清零后重新数


class TestAOI:
    def test_enter_bbox_fires_once(self):
        w = _watch(C.WatchCondition(
            position_path="position", aoi_bbox=[110.0, 29.0, 116.0, 33.0],
            aoi_mode="enter"))
        outside = _evt(kind="observation.spatial",
                       payload={"position": [118.0, 31.0]})
        r0 = evaluate_watch(w, C.WatchState(), outside)
        assert not r0.fired
        inside = _evt(kind="observation.spatial",
                      payload={"position": [112.0, 31.0]})
        r1 = evaluate_watch(w, r0.state, inside)
        assert r1.fired and r1.state.inside_aoi is True
        # 已在内 → 继续 inside 不重复 fire（enter 是边沿触发）
        r2 = evaluate_watch(w, r1.state, inside)
        assert not r2.fired

    def test_exit_mode(self):
        w = _watch(C.WatchCondition(
            aoi_bbox=[110.0, 29.0, 116.0, 33.0], aoi_mode="exit"))
        st = evaluate_watch(
            w, C.WatchState(),
            _evt(kind="observation.spatial", payload={"position": [112.0, 31.0]})).state
        r = evaluate_watch(
            w, st,
            _evt(kind="observation.spatial", payload={"position": [118.0, 31.0]}))
        assert r.fired and r.state.inside_aoi is False

    def test_polygon_geojson_resolver(self):
        poly = {
            "type": "Polygon",
            "coordinates": [[[110.0, 29.0], [116.0, 29.0], [116.0, 33.0],
                             [110.0, 33.0], [110.0, 29.0]]],
        }
        w = _watch(C.WatchCondition(
            aoi_ref="ref:aoi-1", aoi_mode="enter"))
        inside = _evt(kind="observation.spatial",
                      payload={"position": [113.0, 31.0]})
        r = evaluate_watch(
            w, C.WatchState(), inside, resolve_aoi=lambda ref: poly)
        assert r.fired

    def test_aoi_ref_without_resolver_not_met(self):
        w = _watch(C.WatchCondition(aoi_ref="ref:aoi-1"))
        r = evaluate_watch(
            w, C.WatchState(),
            _evt(kind="observation.spatial", payload={"position": [113.0, 31.0]}))
        assert not r.fired and r.reason == "aoi_ref_unresolvable"


class TestVersionChanged:
    def test_fires_only_on_change(self):
        w = _watch(C.WatchCondition(version_property="revision"))
        r1 = evaluate_watch(w, C.WatchState(),
                            _evt(payload={"revision": "v1"}))
        assert not r1.fired and r1.reason == "no_prior_version"
        r2 = evaluate_watch(w, r1.state, _evt(payload={"revision": "v1"}))
        assert not r2.fired and r2.reason == "version_unchanged"
        r3 = evaluate_watch(w, r2.state, _evt(payload={"revision": "v2"}))
        assert r3.fired and r3.state.last_version == "v2"


class TestWindow:
    def test_window_min_events(self):
        w = _watch(C.WatchCondition(window_s=300, window_min_events=3))
        st = C.WatchState()
        for i in range(2):
            st = evaluate_watch(
                w, st, _evt(occurred_at=NOW + timedelta(seconds=i * 10))).state
        r = evaluate_watch(w, st, _evt(occurred_at=NOW + timedelta(seconds=20)))
        assert r.fired

    def test_window_expires_old_events(self):
        w = _watch(C.WatchCondition(window_s=60, window_min_events=2))
        st = C.WatchState(
            window_event_times=[NOW - timedelta(seconds=120)])
        r = evaluate_watch(w, st, _evt(occurred_at=NOW))
        assert not r.fired

    def test_window_state_bounded(self):
        w = _watch(C.WatchCondition(window_s=3600, window_min_events=100))
        st = C.WatchState(
            window_event_times=[
                NOW - timedelta(seconds=i) for i in range(500)])
        r = evaluate_watch(w, st, _evt())
        assert len(r.state.window_event_times) <= 128


class TestCooldown:
    def test_cooldown_blocks_refire(self):
        w = _watch(C.WatchCondition(
            metric_name="m.v", metric_op="gte", metric_value=5),
            cooldown_s=60)
        st = C.WatchState(
            last_fired_at=NOW - timedelta(seconds=30),
            last_metric_value=1)
        r = evaluate_watch(w, st, _evt(payload={"m": {"v": 9}}))
        assert not r.fired and r.reason == "cooldown"
        # 冷却过后允许
        st2 = C.WatchState(
            last_fired_at=NOW - timedelta(seconds=90),
            last_metric_value=1)
        r2 = evaluate_watch(w, st2, _evt(payload={"m": {"v": 9}}))
        assert r2.fired


class TestStateHygiene:
    def test_state_tracks_last_event(self):
        w = _watch()
        r = evaluate_watch(w, C.WatchState(), _evt())
        assert r.state.last_event_id

    def test_no_predicates_fires_on_match(self):
        w = _watch()  # 恒真条件：kind+subject 匹配即触发
        r = evaluate_watch(w, C.WatchState(), _evt())
        assert isinstance(r, WatchEvalResult)
        assert r.fired
