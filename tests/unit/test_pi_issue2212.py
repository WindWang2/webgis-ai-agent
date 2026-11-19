"""#2212: the vision gate must follow the ACTIVE model (post set_model),
not the spawn-time base model — and its runtime cache must not go stale
when the model switches within one spawn epoch."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent_pi_bridge import PiBridge


class _FakeLedger:
    def __init__(self, revoked=()):
        self._revoked = list(revoked)
        self.bumped = 0

    async def find_revoked(self, sid):
        return list(self._revoked)

    def record(self, sid, receipts):
        return None

    def bump(self, sid):
        self.bumped += 1


class _FakeResolveOutcome:
    def __init__(self):
        self.images = []
        self.receipts = []
        self.any_sent = False


def _make_bridge(monkeypatch, *, ledger, spawn_model_id, spawn_image, epoch,
                 state, vendor_state):
    """Wire a PiBridge shell with a fake vision/resolve seam; returns
    (bridge, captured, get_state_calls)."""
    from app.services.visual_context import resolver as resolver_mod

    captured: dict = {}

    async def _fake_resolve(turn_sid, attachments, *, images_allowed, allowed_reason):
        captured["images_allowed"] = images_allowed
        captured["allowed_reason"] = allowed_reason
        return _FakeResolveOutcome()

    monkeypatch.setattr(resolver_mod, "resolve_attachments", _fake_resolve)
    monkeypatch.setattr(resolver_mod, "get_sent_image_ledger", lambda: ledger)

    calls = {"get_state": 0}

    class _Rpc:
        def __init__(self):
            self.spawn_model_id = spawn_model_id
            self.spawn_declared_image_input = spawn_image
            self.spawn_epoch = epoch

        async def request(self, command, data=None):
            if command == "get_state":
                calls["get_state"] += 1
                if state is None:
                    raise RuntimeError("state down")
                return state
            return {}

    async def _drain():
        return None

    pi = PiBridge.__new__(PiBridge)
    pi._drain_stale_events = _drain
    pi._rpc = _Rpc()
    pi._vision_gate = None
    pi._vendor_model_state = vendor_state
    return pi, captured, calls


def _patch_descriptors(monkeypatch, vision_models):
    from app.services.visual_context import profile as profile_mod

    monkeypatch.setattr(
        profile_mod, "descriptor_vision_confirmed",
        lambda model_id, provider_id="webgis": vision_models.get(model_id),
    )


@pytest.mark.asyncio
async def test_explicit_vision_model_gate_ignores_text_only_spawn_anchor(monkeypatch):
    """2212(a): base model vision=False, user switched to a vision model —
    images must be judged against the ACTIVE model, not the spawn anchor."""
    _patch_descriptors(monkeypatch, {"vision-m": True, "base-m": False})
    ledger = _FakeLedger()
    state = {"model": {"id": "vision-m", "provider": "webgis",
                       "input": ["text", "image"]}}
    pi, captured, calls = _make_bridge(
        monkeypatch, ledger=ledger, spawn_model_id="base-m",
        spawn_image=False, epoch=3, state=state,
        vendor_state=("sid-1", "webgis", "vision-m", 3),
    )
    outcome = await pi._resolve_turn_images("sid-1", [{"kind": "image"}])
    assert outcome["blocked_reason"] == ""
    assert captured["images_allowed"] is True
    assert captured["allowed_reason"] == ""
    assert calls["get_state"] == 1


@pytest.mark.asyncio
async def test_gate_cache_invalidates_when_model_switches_within_epoch(monkeypatch):
    """2212(b): vision base, same spawn_epoch, switch to a text-only model —
    the stale runtime cache must not keep allowing images. (Descriptor claims
    vision — the runtime state is what must catch the mismatch.)"""
    _patch_descriptors(monkeypatch, {"base-m": True, "text-m": True})
    ledger = _FakeLedger()
    ok_state = {"model": {"id": "base-m", "input": ["text", "image"]}}
    pi, captured, calls = _make_bridge(
        monkeypatch, ledger=ledger, spawn_model_id="base-m",
        spawn_image=True, epoch=5, state=ok_state, vendor_state=None,
    )
    await pi._resolve_turn_images("sid-1", [{"kind": "image"}])
    assert captured["images_allowed"] is True
    assert calls["get_state"] == 1

    # set_model switched the vendor to a text-only model, same worker/epoch.
    pi._vendor_model_state = ("sid-1", "webgis", "text-m", 5)
    pi._rpc.request = AsyncMock(return_value={"model": {"id": "text-m",
                                                        "input": ["text"]}})
    await pi._resolve_turn_images("sid-1", [{"kind": "image"}])
    assert captured["images_allowed"] is False
    assert captured["allowed_reason"] == "runtime_model_lacks_image_input"
    assert pi._rpc.request.await_count == 1  # fresh get_state, not stale cache


@pytest.mark.asyncio
async def test_explicit_vision_model_fail_closed_on_lying_runtime(monkeypatch):
    """2212(c): descriptor says vision but the runtime model lacks image
    input — fail closed even when an explicit vision model was applied."""
    _patch_descriptors(monkeypatch, {"vision-m": True, "base-m": False})
    ledger = _FakeLedger()
    state = {"model": {"id": "vision-m", "input": ["text"]}}
    pi, captured, _ = _make_bridge(
        monkeypatch, ledger=ledger, spawn_model_id="base-m",
        spawn_image=False, epoch=3, state=state,
        vendor_state=("sid-1", "webgis", "vision-m", 3),
    )
    await pi._resolve_turn_images("sid-1", [{"kind": "image"}])
    assert captured["images_allowed"] is False
    assert captured["allowed_reason"] == "runtime_model_lacks_image_input"


@pytest.mark.asyncio
async def test_vision_gate_cache_keyed_by_model_identity(monkeypatch):
    """Unit pin: VisionGate re-fetches get_state when model_id changes,
    even if spawn_epoch is unchanged."""
    from app.services.visual_context import profile as profile_mod
    from app.services.visual_context.profile import VisionGate

    monkeypatch.setattr(profile_mod, "descriptor_vision_confirmed",
                        lambda model_id, provider_id="webgis": True)
    gate = VisionGate()
    calls = {"n": 0}

    class _Rpc:
        async def request(self, command, data=None):
            calls["n"] += 1
            return {"model": {"input": ["text", "image"]}}

    rpc = _Rpc()
    ok, _ = await gate.check(model_id="m-a", spawn_declared_image_input=True,
                             rpc=rpc, spawn_epoch=9)
    assert ok is True
    assert calls["n"] == 1
    # Same epoch, different model → cache miss → fresh runtime read.
    ok, _ = await gate.check(model_id="m-b", spawn_declared_image_input=True,
                             rpc=rpc, spawn_epoch=9)
    assert ok is True
    assert calls["n"] == 2


# ── #2212: webgis_visual_evidence_read 同一活动模型语义 ────────────────


@pytest.mark.asyncio
async def test_visual_evidence_read_follows_active_model(monkeypatch):
    """2212(a) tool 面：base vision=False 但会话已切到 vision 模型时，
    webgis_visual_evidence_read 的证据读取不得再被 spawn 锚点误拒。"""
    import app.tools.visual_context_tools as vc_tools
    from app.services.visual_context import profile as profile_mod

    monkeypatch.setattr(
        profile_mod, "descriptor_vision_confirmed",
        lambda model_id, provider_id="webgis": {"vision-m": True}.get(model_id),
    )

    class _Rpc:
        spawn_model_id = "base-m"
        spawn_declared_image_input = False
        spawn_epoch = 3

        async def request(self, command, data=None):
            assert command == "get_state"
            return {"model": {"id": "vision-m", "input": ["text", "image"]}}

    bridge = SimpleNamespace(
        rpc=_Rpc(),
        _vendor_model_state=("sid-1", "webgis", "vision-m", 3),
    )
    pool = SimpleNamespace(bridge_for_session=lambda sid: bridge)
    monkeypatch.setattr(
        "app.agent_pi_bridge.get_bridge_pool", lambda: pool,
    )

    assert await vc_tools._model_vision_allowed("sid-1") is True


@pytest.mark.asyncio
async def test_visual_evidence_read_fail_closed_on_text_active_model(monkeypatch):
    """2212(b) tool 面：同 epoch 切到纯文本模型后必须拒绝（fail-closed）。"""
    import app.tools.visual_context_tools as vc_tools
    from app.services.visual_context import profile as profile_mod

    monkeypatch.setattr(
        profile_mod, "descriptor_vision_confirmed",
        lambda model_id, provider_id="webgis": True,
    )

    class _Rpc:
        spawn_model_id = "base-m"
        spawn_declared_image_input = True
        spawn_epoch = 5

        async def request(self, command, data=None):
            return {"model": {"id": "text-m", "input": ["text"]}}

    bridge = SimpleNamespace(
        rpc=_Rpc(),
        _vendor_model_state=("sid-1", "webgis", "text-m", 5),
    )
    pool = SimpleNamespace(bridge_for_session=lambda sid: bridge)
    monkeypatch.setattr(
        "app.agent_pi_bridge.get_bridge_pool", lambda: pool,
    )

    assert await vc_tools._model_vision_allowed("sid-1") is False
