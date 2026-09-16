"""SpatialEventLedger 契约测试：去重 / 游标 / 抢批 / 崩溃恢复 / 租户隔离 / coalesce。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.spatial_events import contracts as C
from app.services.spatial_events.ledger import (
    LedgerError,
)


def _evt(event_id=None, occurred_at=None, payload=None, org="org-a", **over):
    kw = dict(
        kind="dataset.version_changed",
        org_id=org,
        subject_type="dataset",
        subject_key="dataset:ndvi-hubei",
        occurred_at=occurred_at
        or datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
        payload=payload if payload is not None else {"revision": "v7"},
    )
    if event_id:
        kw["event_id"] = event_id
    kw.update(over)
    return C.SpatialEventEnvelope(**kw)


class TestAppendDedupe:
    def test_first_append_pending(self, ledger):
        res = ledger.append(_evt())
        assert res.status == "appended"
        row = ledger.get_event(res.row_id, org_id="org-a")
        assert row["status"] == "pending"
        assert row["event_id"]

    def test_duplicate_event_id_acked_no_side_row(self, ledger):
        first = ledger.append(_evt(event_id="dup-1"))
        second = ledger.append(_evt(event_id="dup-1"))
        assert second.status == "duplicate"
        assert second.row_id == first.row_id
        assert ledger.count_events(org_id="org-a") == 1

    def test_derived_id_same_payload_dedupes(self, ledger):
        r1 = ledger.append(_evt())
        r2 = ledger.append(_evt())
        assert r1.event_id == r2.event_id
        assert r2.status == "duplicate"

    def test_org_stamp_must_match_envelope(self, ledger):
        with pytest.raises(LedgerError):
            ledger.append(_evt(org="org-a"), org_id="org-b")

    def test_runtime_flag_off_rejects_append(self, ledger, monkeypatch):
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "0")
        with pytest.raises(LedgerError):
            ledger.append(_evt())


class TestCursor:
    def test_cursor_starts_zero_and_advances_forward_only(self, ledger):
        assert ledger.get_cursor("worker-1") == 0
        r = ledger.append(_evt())
        assert ledger.advance_cursor("worker-1", r.row_id)
        assert ledger.get_cursor("worker-1") == r.row_id
        # 倒退拒收
        assert not ledger.advance_cursor("worker-1", 0)

    def test_read_after_returns_causal_order(self, ledger):
        ids = []
        for i in range(5):
            res = ledger.append(
                _evt(occurred_at=datetime(2026, 9, 16, 12, 0, i, tzinfo=timezone.utc),
                     payload={"revision": f"v{i}"})
            )
            ids.append(res.row_id)
        got = [e["id"] for e in ledger.list_events(org_id="org-a", after_id=0, limit=10)]
        assert got == sorted(ids)


class TestClaimAndProcess:
    def test_claim_batch_cas_and_mark_processed(self, ledger):
        r = ledger.append(_evt())
        rows = ledger.claim_batch("worker-a", limit=4)
        assert [x["id"] for x in rows] == [r.row_id]
        assert rows[0]["status"] == "processing"
        assert rows[0]["claimed_by"] == "worker-a"
        # 第二个 worker 抢不到同一批
        assert ledger.claim_batch("worker-b", limit=4) == []
        ledger.mark_processed(r.row_id)
        assert ledger.get_event(r.row_id, org_id="org-a")["status"] == "processed"

    def test_priority_orders_claim(self, ledger):
        low = ledger.append(_evt(payload={"revision": "batch"},
                                 priority="batch"))
        hi = ledger.append(_evt(payload={"revision": "int"},
                                priority="interactive"))
        rows = ledger.claim_batch("w", limit=2)
        assert [x["id"] for x in rows] == [hi.row_id, low.row_id]

    def test_failed_retry_backoff(self, ledger):
        r = ledger.append(_evt())
        ledger.claim_batch("w", limit=1)
        ledger.mark_failed(r.row_id, error_code="BRIDGE_DOWN", backoff_s=30)
        row = ledger.get_event(r.row_id, org_id="org-a")
        assert row["status"] == "pending"
        assert row["attempts"] == 1
        assert row["next_attempt_at"] is not None
        # 退避未到 → 不再被抢
        assert ledger.claim_batch("w2", limit=4) == []

    def test_failed_terminal_after_max_attempts(self, ledger):
        r = ledger.append(_evt())
        for _ in range(5):
            ledger.claim_batch("w", limit=1)
            ledger.mark_failed(r.row_id, error_code="X", backoff_s=0)
        row = ledger.get_event(r.row_id, org_id="org-a")
        assert row["status"] == "failed"

    def test_requeue_stale_processing(self, ledger):
        r = ledger.append(_evt())
        ledger.claim_batch("worker-dead", limit=1)
        # claimed_at 判为陈旧（人工回拨）
        ledger.requeue_stale_processing(older_than_s=0)
        rows = ledger.claim_batch("worker-new", limit=1)
        assert rows and rows[0]["id"] == r.row_id


class TestTenantIsolation:
    def test_list_and_get_scoped_by_org(self, ledger):
        a = ledger.append(_evt(org="org-a"))
        b = ledger.append(_evt(org="org-b", subject_key="dataset:x"))
        assert ledger.get_event(a.row_id, org_id="org-b") is None
        assert ledger.get_event(b.row_id, org_id="org-a") is None
        only_a = ledger.list_events(org_id="org-a", after_id=0, limit=10)
        assert [e["id"] for e in only_a] == [a.row_id]


class TestCoalesce:
    def test_coalesce_keeps_newest_and_counts(self, ledger):
        for i in range(5):
            ledger.append(
                _evt(occurred_at=datetime(2026, 9, 16, 12, 0, i, tzinfo=timezone.utc),
                     payload={"revision": f"v{i}"})
            )
        stats = ledger.coalesce_pending(org_id="org-a")
        assert stats["coalesced"] == 4
        remaining = ledger.list_events(org_id="org-a", after_id=0, limit=10,
                                       status="pending")
        assert len(remaining) == 1
        assert remaining[0]["collapsed_count"] == 4
        # coalesced 行是终态，不再被处理
        all_rows = ledger.list_events(org_id="org-a", after_id=0, limit=10)
        assert sorted(r["status"] for r in all_rows) == [
            "coalesced", "coalesced", "coalesced", "coalesced", "pending"]

    def test_coalesce_respects_subject_boundary(self, ledger):
        for i in range(3):
            ledger.append(_evt(payload={"revision": f"a{i}"}))
        ledger.append(_evt(subject_key="dataset:other", payload={"revision": "b"}))
        stats = ledger.coalesce_pending(org_id="org-a")
        assert stats["coalesced"] == 2  # 只合并同 subject 的 3 条
        assert len(ledger.list_events(org_id="org-a", after_id=0, limit=10,
                                      status="pending")) == 2

    def test_coalesce_never_touches_processing(self, ledger):
        ledger.append(_evt())
        ledger.append(_evt(payload={"revision": "v2"}))
        ledger.claim_batch("w", limit=1)
        stats = ledger.coalesce_pending(org_id="org-a")
        assert stats["coalesced"] == 0


class TestWatchStore:
    def _watch(self, **over):
        kw = dict(
            watch_id="w1", org_id="org-a", name="n",
            kinds=["dataset.version_changed"],
            condition=C.WatchCondition().model_dump(),
            actions=["notify_only"], cooldown_s=60.0,
        )
        kw.update(over)
        return C.SpatialWatch(**kw)

    def test_upsert_get_list_delete(self, ledger):
        ledger.upsert_watch(self._watch())
        got = ledger.get_watch("w1", org_id="org-a")
        assert got is not None and got.enabled
        assert ledger.list_watches(org_id="org-a") == [got.watch_id]
        assert ledger.get_watch("w1", org_id="org-b") is None  # 租户隔离
        assert ledger.delete_watch("w1", org_id="org-b") is False
        assert ledger.delete_watch("w1", org_id="org-a") is True
        assert ledger.get_watch("w1", org_id="org-a") is None

    def test_watch_state_roundtrip(self, ledger):
        ledger.upsert_watch(self._watch())
        st = C.WatchState(consecutive_hits=3, last_version="v7")
        assert ledger.update_watch_state("w1", st, org_id="org-a")
        got = ledger.get_watch_state("w1", org_id="org-a")
        assert got is not None and got.consecutive_hits == 3

    def test_fire_unique_per_watch_event(self, ledger):
        ledger.record_fire(C.WatchFireRecord(
            watch_id="w1", org_id="org-a", event_id="e1",
            fired_at=datetime.now(timezone.utc), action="notify_only",
            outcome="fired"))
        rec, created = ledger.record_fire(C.WatchFireRecord(
            watch_id="w1", org_id="org-a", event_id="e1",
            fired_at=datetime.now(timezone.utc), action="notify_only",
            outcome="duplicate"))
        assert created is False
        fires = ledger.list_fires(org_id="org-a", limit=10)
        assert len(fires) == 1
        # 跨租户读不到
        assert ledger.list_fires(org_id="org-b", limit=10) == []
