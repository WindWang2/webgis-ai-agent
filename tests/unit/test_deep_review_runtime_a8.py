"""A8 (#1382 regression): authenticated session ref reads must not 403.

`_conversation_has_owner` used to treat any row with ``user_id`` as
"owned", so `_validate_owner_token` failed closed for logged-in users —
their conversations never get an ``owner_token_digest`` (minting only
happens for anonymous rows, history_service_async.py:393), making every
layer/chart/table ref read a 403.

Store-level token checks must stay exclusive to ANONYMOUS owner_token rows;
authenticated rows are authorized at the route layer
(`authorize_session_write` / `require_owned_session`).

Every test runs against MemorySessionStore; the helper probe is patched at
`app.core.database.SessionLocal` (the import site inside the helper).
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager

import pytest

import app.services.session_data_protocol as sdp
from app.services.session_data import MemorySessionStore


class _Conv:
    def __init__(self, user_id=None, owner_token=None):
        self.user_id = user_id
        self.owner_token = owner_token


class _FakeDB:
    def __init__(self, conv):
        self._conv = conv

    def get(self, model, key):
        return self._conv

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _patch_conv(monkeypatch, conv):
    monkeypatch.setattr(
        "app.core.database.SessionLocal", lambda: _FakeDB(conv)
    )


# ── helper contract ──────────────────────────────────────────────────────


def test_user_owned_conversation_does_not_demand_token(monkeypatch):
    _patch_conv(monkeypatch, _Conv(user_id="u-1"))
    assert sdp._conversation_has_owner("sess-u") is False


def test_anonymous_owner_token_conversation_demands_token(monkeypatch):
    _patch_conv(monkeypatch, _Conv(owner_token="tok"))
    assert sdp._conversation_has_owner("sess-a") is True


def test_anonymous_legacy_null_row_does_not_demand_token(monkeypatch):
    _patch_conv(monkeypatch, _Conv())
    assert sdp._conversation_has_owner("sess-legacy") is False


# ── end-to-end store reads ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authenticated_ref_read_allowed_without_token(monkeypatch):
    store = MemorySessionStore()
    sid = "sess-authenticated-read"
    ref_id = await store.store(sid, {"type": "FeatureCollection", "features": []})
    _patch_conv(monkeypatch, _Conv(user_id="u-1"))

    res = await store.get_ref_data(sid, ref_id)

    assert res.success is True, res.error
    assert res.data == {"type": "FeatureCollection", "features": []}


@pytest.mark.asyncio
async def test_authenticated_descriptor_read_allowed_without_token(monkeypatch):
    store = MemorySessionStore()
    sid = "sess-authenticated-descriptor"
    ref_id = await store.store(sid, {"type": "FeatureCollection", "features": []})
    _patch_conv(monkeypatch, _Conv(user_id="u-1"))

    res = await store.get_ref_descriptor_authorized(sid, ref_id)

    assert res.success is True, res.error
    assert res.data is not None


@pytest.mark.asyncio
async def test_anonymous_token_row_without_digest_still_denied(monkeypatch):
    """Anonymous protection unchanged: no digest anywhere → fail closed."""
    store = MemorySessionStore()
    sid = "sess-anon-no-digest"
    ref_id = await store.store(sid, {"secret": True})
    _patch_conv(monkeypatch, _Conv(owner_token="tok"))

    res = await store.get_ref_data(sid, ref_id)
    assert res.success is False
    assert res.error_type == "PermissionDenied"

    res_with_token = await store.get_ref_data(sid, ref_id, owner_token="tok")
    assert res_with_token.success is False
    assert res_with_token.error_type == "PermissionDenied"


@pytest.mark.asyncio
async def test_anonymous_digest_requires_matching_token(monkeypatch):
    store = MemorySessionStore()
    sid = "sess-anon-digest"
    ref_id = await store.store(sid, {"secret": True})
    await store.set_map_state(
        sid, "owner_token_digest", hashlib.sha256(b"tok").hexdigest()
    )
    _patch_conv(monkeypatch, _Conv(owner_token="tok"))

    denied = await store.get_ref_data(sid, ref_id)
    assert denied.success is False
    assert denied.error_type == "PermissionDenied"

    wrong = await store.get_ref_data(sid, ref_id, owner_token="other")
    assert wrong.success is False
    assert wrong.error_type == "PermissionDenied"

    allowed = await store.get_ref_data(sid, ref_id, owner_token="tok")
    assert allowed.success is True, allowed.error


@pytest.mark.asyncio
async def test_authenticated_read_survives_digest_presence(monkeypatch):
    """A stray digest must not re-break authenticated reads (route auth wins).

    There is no claim flow today, but the check must not regress if a
    user-bound row ever carries one.
    """
    store = MemorySessionStore()
    sid = "sess-auth-stray-digest"
    ref_id = await store.store(sid, {"ok": True})
    await store.set_map_state(
        sid, "owner_token_digest", hashlib.sha256(b"tok").hexdigest()
    )
    _patch_conv(monkeypatch, _Conv(user_id="u-1"))

    res = await store.get_ref_data(sid, ref_id)
    assert res.success is True, res.error
