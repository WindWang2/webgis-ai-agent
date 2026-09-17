"""SpatialEventEnvelope / Watch 契约测试（TDD 红测先行）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.services.spatial_events import contracts as C


def _base_kwargs(**over):
    kw = dict(
        kind="dataset.version_changed",
        org_id="org-a",
        subject_type="dataset",
        subject_key="dataset:ndvi-hubei",
        occurred_at=datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
        payload={"revision": "v7", "metric": {"ndvi": 0.42}},
    )
    kw.update(over)
    return kw


class TestEnvelopeContract:
    def test_valid_envelope_defaults(self):
        env = C.SpatialEventEnvelope(**_base_kwargs())
        assert env.schema_version == C.SCHEMA_VERSION
        assert env.priority == "normal"
        assert env.source == "internal"
        assert env.event_id  # ledger/appends must always carry one
        assert env.payload_ref is None

    def test_closed_kind_vocabulary(self):
        with pytest.raises(ValidationError):
            C.SpatialEventEnvelope(**_base_kwargs(kind="not.a.kind"))

    def test_org_id_required_nonempty(self):
        with pytest.raises(ValidationError):
            C.SpatialEventEnvelope(**_base_kwargs(org_id=""))
        with pytest.raises(ValidationError):
            C.SpatialEventEnvelope(**_base_kwargs(org_id=None))  # type: ignore[arg-type]

    def test_payload_size_bounded_inline(self):
        big = {"blob": "x" * 3000}
        with pytest.raises(ValidationError):
            C.SpatialEventEnvelope(**_base_kwargs(payload=big))

    def test_payload_ref_allowed_and_mutually_exclusive_with_inline_blob(self):
        env = C.SpatialEventEnvelope(
            **_base_kwargs(payload={}, payload_ref="ref:geojson-abc123")
        )
        assert env.payload_ref == "ref:geojson-abc123"
        # 大 inline payload 即便配了 ref 也拒绝（防歧义：大内容必须走 ref）
        with pytest.raises(ValidationError):
            C.SpatialEventEnvelope(
                **_base_kwargs(payload={"blob": "x" * 3000}, payload_ref="ref:x")
            )

    def test_subject_key_bounded(self):
        with pytest.raises(ValidationError):
            C.SpatialEventEnvelope(**_base_kwargs(subject_key="k" * 200))

    def test_priority_vocabulary(self):
        assert C.SpatialEventEnvelope(**_base_kwargs(priority="interactive"))
        with pytest.raises(ValidationError):
            C.SpatialEventEnvelope(**_base_kwargs(priority="urgent"))

    def test_source_reserved_webhook_rejected_for_internal(self):
        # webhook 源只能由 webhook seam 盖章（内部适配器不得伪造）
        with pytest.raises(ValidationError):
            C.SpatialEventEnvelope(**_base_kwargs(source="webhook"))

    def test_occurred_at_requires_tz(self):
        with pytest.raises(ValidationError):
            C.SpatialEventEnvelope(
                **_base_kwargs(occurred_at=datetime(2026, 9, 16, 12, 0, 0))
            )


class TestEventIdDerivation:
    def test_deterministic(self):
        a = C.derive_event_id(
            source="internal",
            kind="dataset.version_changed",
            subject_key="dataset:ndvi-hubei",
            occurred_at=datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
            payload={"revision": "v7"},
        )
        b = C.derive_event_id(
            source="internal",
            kind="dataset.version_changed",
            subject_key="dataset:ndvi-hubei",
            occurred_at=datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
            payload={"revision": "v7"},
        )
        assert a == b and len(a) == 32

    def test_differs_on_payload(self):
        a = C.derive_id_for(C.SpatialEventEnvelope(**_base_kwargs()))
        b = C.derive_id_for(
            C.SpatialEventEnvelope(**_base_kwargs(payload={"revision": "v8"}))
        )
        assert a != b

    def test_envelope_auto_derives_when_missing(self):
        env = C.SpatialEventEnvelope(**_base_kwargs())
        again = C.SpatialEventEnvelope(**_base_kwargs())
        assert env.event_id == again.event_id

    def test_explicit_event_id_preserved(self):
        env = C.SpatialEventEnvelope(**_base_kwargs(event_id="abc123"))
        assert env.event_id == "abc123"


class TestWatchContract:
    def test_watch_valid(self):
        w = C.SpatialWatch(
            watch_id="w1",
            org_id="org-a",
            name="ndvi-drop",
            kinds=["dataset.version_changed"],
            condition=C.WatchCondition(
                metric_name="metric.ndvi", metric_op="lt", metric_value=0.2
            ),
            actions=["notify_only"],
        )
        assert w.enabled is True
        assert w.cooldown_s > 0

    def test_watch_action_vocabulary(self):
        with pytest.raises(ValidationError):
            C.SpatialWatch(
                watch_id="w1",
                org_id="org-a",
                name="x",
                kinds=["dataset.version_changed"],
                condition=C.WatchCondition(),
                actions=["delete_everything"],
            )

    def test_mission_action_requires_goal_template(self):
        # mission_create/revise 必须携带有界 goal 模板——否则触发器无法生成
        # 可审计的 root_goal
        with pytest.raises(ValidationError):
            C.SpatialWatch(
                watch_id="w1",
                org_id="org-a",
                name="x",
                kinds=["dataset.version_changed"],
                condition=C.WatchCondition(),
                actions=["mission_create"],
            )

    def test_condition_ops_vocabulary(self):
        with pytest.raises(ValidationError):
            C.WatchCondition(metric_name="m", metric_op="~=", metric_value=1)
        # delta_pct_ge 的基线值语义在引擎内处理，契约层同样要求完整谓词
        assert C.WatchCondition(
            metric_name="m", metric_op="delta_pct_ge", metric_value=10.0
        )

    def test_condition_metric_requires_value(self):
        with pytest.raises(ValidationError):
            C.WatchCondition(metric_name="m", metric_op="lt")

    def test_aoi_bbox_shape(self):
        assert C.WatchCondition(aoi_bbox=[110.0, 29.0, 116.0, 33.0])
        with pytest.raises(ValidationError):
            C.WatchCondition(aoi_bbox=[1.0, 2.0, 3.0])  # 少一个分量
        with pytest.raises(ValidationError):
            C.WatchCondition(aoi_bbox=[116.0, 33.0, 110.0, 29.0])  # min>max

    def test_kind_must_be_envelope_kind(self):
        with pytest.raises(ValidationError):
            C.SpatialWatch(
                watch_id="w1",
                org_id="org-a",
                name="x",
                kinds=["bogus.kind"],
                condition=C.WatchCondition(),
                actions=["notify_only"],
            )

    def test_bounded_goal_template(self):
        with pytest.raises(ValidationError):
            C.SpatialWatch(
                watch_id="w1",
                org_id="org-a",
                name="x",
                kinds=["dataset.version_changed"],
                condition=C.WatchCondition(),
                actions=["mission_create"],
                mission_goal_template="g" * 600,
            )


class TestCanonicalPayload:
    def test_canonical_payload_json_deterministic(self):
        p1 = {"b": 1, "a": [1, 2]}
        p2 = {"a": [1, 2], "b": 1}
        assert C.canonical_payload_json(p1) == C.canonical_payload_json(p2)
        assert json.loads(C.canonical_payload_json(p1)) == p1
