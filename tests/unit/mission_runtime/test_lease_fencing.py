"""Distributed ownership: contend, expiry, stale write, heartbeat."""
from __future__ import annotations

import datetime as _dt

import pytest
import sqlalchemy as sa

from app.models.mission import GISMissionRow
from app.services.mission_runtime.store import FencingError


def test_two_workers_contend(mission_store):
    m = mission_store.create_mission(org_id="1", user_id="u", root_goal="g")
    assert mission_store.acquire_lease(m.mission_id, owner="w-a") is not None
    assert mission_store.acquire_lease(m.mission_id, owner="w-b") is None


def test_lease_expiry_allows_takeover(mission_store):
    m = mission_store.create_mission(org_id="1", user_id="u", root_goal="g")
    ep, _ = mission_store.acquire_lease(m.mission_id, owner="w-dead", ttl_s=1.0)
    with mission_store._sf() as db:
        db.execute(
            sa.update(GISMissionRow)
            .where(GISMissionRow.mission_id == m.mission_id)
            .values(lease_expires_at=_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - _dt.timedelta(seconds=1))
        )
        db.commit()
    got = mission_store.acquire_lease(m.mission_id, owner="w-next")
    assert got is not None
    new_epoch, _ = got
    assert new_epoch == ep + 1


def test_stale_owner_write_rejected(mission_store):
    m = mission_store.create_mission(org_id="1", user_id="u", root_goal="g")
    ep_a, _ = mission_store.acquire_lease(m.mission_id, owner="w-a", ttl_s=1.0)
    with mission_store._sf() as db:
        db.execute(
            sa.update(GISMissionRow)
            .where(GISMissionRow.mission_id == m.mission_id)
            .values(lease_expires_at=_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - _dt.timedelta(seconds=1))
        )
        db.commit()
    ep_b, _ = mission_store.acquire_lease(m.mission_id, owner="w-b")
    assert ep_b == ep_a + 1
    with pytest.raises(FencingError):
        mission_store.transition(
            m.mission_id,
            to_state="planning",
            lease_epoch=ep_a,
            owner="w-a",
        )


def test_heartbeat_extension(mission_store):
    m = mission_store.create_mission(org_id="1", user_id="u", root_goal="g")
    ep, _ = mission_store.acquire_lease(m.mission_id, owner="w1", ttl_s=5.0)
    assert mission_store.heartbeat_lease(
        m.mission_id, owner="w1", lease_epoch=ep, ttl_s=30.0) is True
    assert mission_store.heartbeat_lease(
        m.mission_id, owner="w1", lease_epoch=ep + 99, ttl_s=30.0) is False


def test_release_only_by_holder(mission_store):
    m = mission_store.create_mission(org_id="1", user_id="u", root_goal="g")
    ep, _ = mission_store.acquire_lease(m.mission_id, owner="w1")
    assert mission_store.release_lease(
        m.mission_id, owner="other", lease_epoch=ep) is False
    assert mission_store.release_lease(
        m.mission_id, owner="w1", lease_epoch=ep) is True
