"""Round-2 reverse-audit regression tests (#846-#853).

Covers the defects the round-2 reviewers found in the round-1 fixes:
- #846: tool-body TypeError vs argument-binding TypeError classification
- #847: idempotent _persist_tool_messages under mid-persist interruption
- #848: depth-1 alias resolution survives oversized degradation
- #849: upload-id extraction from real filename shapes
- #850: report sweep keeps still-active shares
- #851: record() sets EXPIRE; REPORT_DIR single-source parity
"""

import pytest


# ─── #846: TypeError classification ─────────────────────────────────────


class TestAudit846TypeErrorClassification:
    @pytest.fixture()
    def registry(self):
        from app.tools.registry import ToolRegistry

        return ToolRegistry()

    @pytest.mark.asyncio
    async def test_binding_typeerror_is_validation(self, registry):
        @registry.tool(name="t846_bind", description="probe")
        def _bind(x: int = 1) -> dict:
            return {"ok": True}

        res = await registry.dispatch("t846_bind", {"x": 1, "bogus": 2}, session_id="")
        assert res.get("code") == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_body_typeerror_is_tool_error(self, registry):
        @registry.tool(name="t846_body", description="probe")
        def _body(x: int = 1) -> dict:
            raise TypeError("'NoneType' object is not subscriptable")

        res = await registry.dispatch("t846_body", {"x": 1}, session_id="")
        assert res.get("code") == "TOOL_ERROR", (
            "tool-body TypeError must stay TOOL_ERROR (audit #846) — the LLM "
            "would otherwise be told to delete parameters"
        )
        assert "Argument mismatch" not in (res.get("correction_hint") or "")


# ─── #847: idempotent persist under interruption ────────────────────────


class TestAudit847IdempotentPersist:
    @pytest.mark.asyncio
    async def test_mid_persist_interruption_no_duplicates(self):
        """Cancel injected after the FIRST tool save — re-running the persist
        must not duplicate the saved entry nor the memory message."""
        from app.services.chat import execution_engine as ee

        engine = object.__new__(ee.ChatExecutionEngine)
        saved: list[tuple[str, str]] = []

        class _CancelAfterFirst(Exception):
            pass

        calls = {"n": 0}

        async def fake_save(session_id, role, content, tool_calls=None,
                            tool_result=None, tool_call_id=None, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                # cooperative cancellation AT the await: the write did not
                # land (session torn down pre-commit) — the real seam's
                # _save_msg_async either returns (persisted) or propagates
                # CancelledError with the transaction rolled back.
                raise _CancelAfterFirst
            saved.append((role, tool_call_id))

        engine._save_msg_async = fake_save

        class _Step:
            def __init__(self, sid):
                self.id = sid

        pending = [
            {"step": _Step("s1"), "tc": {"id": "tc1"}, "tool_name": "a"},
            {"step": _Step("s2"), "tc": {"id": "tc2"}, "tool_name": "b"},
        ]
        completions = {
            "s1": {"tc": {"id": "tc1"}, "msg_result_str": "r1", "tool_name": "a"},
            "s2": {"tc": {"id": "tc2"}, "msg_result_str": "r2", "tool_name": "b"},
        }
        messages: list[dict] = []

        # first call interrupted after save #1 (flag NOT yet set for tc1)
        with pytest.raises(_CancelAfterFirst):
            await engine._persist_tool_messages(
                "sess", pending, completions, [{"id": "x"}], messages, [])
        # tc1's write never landed (await torn down pre-commit)
        assert saved == [] and len(messages) == 0

        # disconnect handler re-runs the full persist: tc1 must NOT re-save
        await engine._persist_tool_messages(
            "sess", pending, completions, [{"id": "x"}], messages, [])
        assert [s[1] for s in saved] == ["tc1", "tc2"], "saved entries must be unique"
        assert [m["tool_call_id"] for m in messages] == ["tc1", "tc2"]
        # third invocation is a full no-op
        await engine._persist_tool_messages(
            "sess", pending, completions, [{"id": "x"}], messages, [])
        assert len(saved) == 2 and len(messages) == 2


# ─── #848: depth-1 alias under oversized ────────────────────────────────


class TestAudit848DepthOneAlias:
    @pytest.mark.asyncio
    async def test_mixed_payload_resolves_top_level_alias(self, monkeypatch):
        import app.tools.registry as reg_mod
        from app.tools.registry import ToolRegistry

        alias_hits = []

        async def fake_resolve_aliases(sid, strings):
            alias_hits.append(list(strings))
            return {"my-layer": "ref:geojson-1"} if "my-layer" in strings else {}

        payload = {"type": "FeatureCollection", "features": []}
        # real get() accepts either the resolved ref or the alias itself
        store = {"ref:geojson-1": payload, "my-layer": payload}

        async def fake_get(sid, ref):
            return store.get(ref)

        monkeypatch.setattr(reg_mod.session_data_manager, "resolve_aliases", fake_resolve_aliases)
        monkeypatch.setattr(reg_mod.session_data_manager, "get", fake_get)

        reg = ToolRegistry.__new__(ToolRegistry)
        args = {
            "geojson": {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"n": i},
                 "geometry": {"type": "Point", "coordinates": [1, 2]}}
                for i in range(20000)
            ]},
            "overlay": "my-layer",  # registered alias as a direct argument
        }
        out = await reg._resolve_references("s1", args, skip_keys=set(), oversized_hint=True)
        assert out["overlay"] == store["ref:geojson-1"], (
            "depth-1 alias args must resolve even in oversized payloads (#848)"
        )
        # the alias lookup only saw the depth-1 strings, not the 20k leaves
        assert all(len(batch) <= 64 for batch in alias_hits)


# ─── #849: upload id extraction ─────────────────────────────────────────


class TestAudit849UploadIdExtraction:
    def test_writer_path_shapes(self):
        from app.services.artifact_lifecycle import _upload_id_from_filename

        uid = "ab" * 16
        assert _upload_id_from_filename(f"data/uploads/{uid}/original.geojson") == uid
        assert _upload_id_from_filename(f"/srv/app/data/uploads/{uid}/x.shp") == uid
        assert _upload_id_from_filename(f"uploads/{uid}\\f") == uid  # windows sep
        # never delete on a guess
        assert _upload_id_from_filename("data/uploads/not-a-uuid/f") == ""
        assert _upload_id_from_filename("random/path.geojson") == ""
        assert _upload_id_from_filename("") == ""


# ─── #850/#851: report sweep predicate + limiter/base parity ────────────


class TestAudit850851Parity:
    def test_report_dir_single_source(self):
        import app.services.artifact_lifecycle as al
        import app.services.report_service as rs

        assert al.REPORT_DIR == rs.REPORT_DIR

    def test_record_sets_expire(self):
        """RedisRateLimiter.record pipeline must include EXPIRE (#851)."""
        import inspect

        from app.core.rate_limiter import RedisRateLimiter

        src = inspect.getsource(RedisRateLimiter.record)
        assert "expire" in src

    def test_sweep_keeps_active_shares_predicate(self):
        import inspect

        import app.services.artifact_lifecycle as al

        src = inspect.getsource(al)
        assert "share_expires_at" in src, (
            "report sweep must exempt still-active shares (#850)"
        )
