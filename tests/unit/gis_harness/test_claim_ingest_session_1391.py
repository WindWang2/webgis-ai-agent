"""#1391: map_product_block claim ingest must key by real session_id."""
from __future__ import annotations

from app.services.gis_harness.completion.pipeline import map_product_block
from app.services.gis_harness.completion.contracts import MapCompletionResult
from app.services.gis_harness.hotpath_convergence.session_ctx import (
    get_turn_context,
    reset_turn_context,
)


def _minimal_result() -> MapCompletionResult:
    # Construct with whatever fields the dataclass/model needs — fall back
    # to a lightweight stub if construction is heavy.
    try:
        return MapCompletionResult(
            status="ready",
            semantically_correct=True,
            projection="ok",
        )
    except TypeError:
        # Positional / different signature — use object with to_dict/projection_line
        class _R:
            def to_dict(self):
                return {"status": "ready", "semantically_correct": True}

            def projection_line(self):
                return "ok"

            repairs_applied = []
        return _R()  # type: ignore[return-value]


def test_claim_ingest_uses_explicit_session_id(monkeypatch):
    reset_turn_context()
    seen = {}

    def fake_get_or_create(sid, tenant_id=""):
        seen["sid"] = sid
        seen["tid"] = tenant_id
        from app.services.gis_harness.evidence_claim.store import ClaimStore
        return ClaimStore()

    monkeypatch.setattr(
        "app.services.gis_harness.hotpath_convergence.get_or_create_claim_store",
        fake_get_or_create,
    )
    # Also patch ingest to no-op bounded dict
    class _Ingest:
        claim_ids = []
        def to_bounded_dict(self):
            return {"ok": True}

    monkeypatch.setattr(
        "app.services.gis_harness.hotpath_convergence.ingest_map_product_settle",
        lambda *a, **k: _Ingest(),
    )
    monkeypatch.setattr(
        "app.services.gis_harness.hotpath_convergence.build_hotpath_pi_context",
        lambda **k: {"skill": None},
    )

    # Chapter without session_id (the bug shape)
    chapter = {"workflow_contract": {}}
    block = map_product_block(
        _minimal_result(), 1, chapter=chapter, session_id="sess-real-1391",
    )
    assert seen.get("sid") == "sess-real-1391"
    assert "claim_ingest" in block
    reset_turn_context()


def test_two_sessions_get_distinct_claim_stores():
    reset_turn_context()
    from app.services.gis_harness.hotpath_convergence.session_ctx import (
        get_or_create_claim_store,
    )
    a = get_or_create_claim_store("sess-a")
    b = get_or_create_claim_store("sess-b")
    assert a is not b
    assert get_turn_context("sess-a").claim_store is a
    assert get_turn_context("sess-b").claim_store is b
    reset_turn_context()
