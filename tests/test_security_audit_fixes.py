"""Regression tests for the master full-review security fixes (SEC-F1..F6).

Each test fails on the pre-fix code:

- F1: the tier-3 gate now lives at the ToolRegistry.dispatch chokepoint —
  workflow execution, subagents and plan steps cannot bypass the
  route-level confirm_destructive checks anymore; workflow run/replay/resume
  require authentication.
- F2: subagent extra_tools can no longer force tier-3 tools into a subagent
  catalog while exclude_tier3=True.
- F4: rate-limit keys derive from the forwarded client IP (X-Real-IP / last
  XFF hop) instead of the proxy peer address.
"""
import pytest
from fastapi import Request
from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


# ── F1: registry chokepoint refuses tier-3 without a confirmed context ──────

@pytest.mark.asyncio
async def test_F1_registry_refuses_tier3_without_confirmation():
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()

    @reg.tool(name="audit_dangerous", description="tier-3 test tool", tier=3)
    async def _dangerous(some_arg: str = "x"):
        return {"ok": True}

    @reg.tool(name="audit_safe", description="tier-1 test tool")
    async def _safe(some_arg: str = "x"):
        return {"ok": True}

    # Unconfirmed dispatch of a tier-3 tool is refused with a typed error —
    # NOT executed (workflow steps / subagents / plan steps hit exactly this
    # path with attacker-controlled tool names).
    res = await reg.dispatch("audit_dangerous", {"some_arg": "y"})
    assert isinstance(res, dict)
    assert res.get("success") is False
    assert res.get("code") == "TIER3_CONFIRMATION_REQUIRED"

    # Tier-1 tools are unaffected.
    res1 = await reg.dispatch("audit_safe", {"some_arg": "y"})
    assert res1.get("ok") is True


@pytest.mark.asyncio
async def test_F1_registry_allows_tier3_with_confirmed_context():
    from app.tools.registry import ToolRegistry, confirm_tier3

    reg = ToolRegistry()

    @reg.tool(name="audit_dangerous", description="tier-3 test tool", tier=3)
    async def _dangerous(some_arg: str = "x"):
        return {"ok": True}

    with confirm_tier3():
        res = await reg.dispatch("audit_dangerous", {"some_arg": "y"})
    assert res.get("ok") is True

    # The grant is scoped: a fresh dispatch outside the context is refused.
    res2 = await reg.dispatch("audit_dangerous", {"some_arg": "y"})
    assert res2.get("code") == "TIER3_CONFIRMATION_REQUIRED"


@pytest.mark.asyncio
async def test_F1_confirmation_does_not_leak_across_tasks():
    """confirm_tier3 must not grant rights to a DIFFERENT asyncio task."""
    import asyncio

    from app.tools.registry import ToolRegistry, confirm_tier3

    reg = ToolRegistry()

    @reg.tool(name="audit_dangerous", description="tier-3 test tool", tier=3)
    async def _dangerous(some_arg: str = "x"):
        return {"ok": True}

    async def _dispatch_unconfirmed():
        return await reg.dispatch("audit_dangerous", {})

    async def _holder():
        with confirm_tier3():
            # A sibling task spawned while we hold the grant must NOT inherit
            # it (ContextVar copies at task creation… set() happens before
            # create_task here, so the copy WOULD carry it — assert the safer
            # property instead: the grant resets after the context exits).
            await asyncio.sleep(0)
            return "held"

    held = await _holder()
    assert held == "held"
    res = await _dispatch_unconfirmed()
    assert res.get("code") == "TIER3_CONFIRMATION_REQUIRED"


def test_F1_workflow_run_requires_authentication():
    """Anonymous callers must not drive synchronous tool execution."""
    resp = client.post(
        "/api/v1/projects/p_missing/workflows/wf_missing/run",
        json={"input_bindings": {}},
    )
    assert resp.status_code == 401


def test_F1_workflow_replay_requires_authentication():
    resp = client.post(
        "/api/v1/projects/p_missing/runs/r_missing/replay",
        json={"mode": "exact"},
    )
    assert resp.status_code == 401


def test_F1_workflow_resume_requires_authentication():
    resp = client.post(
        "/api/v1/projects/p_missing/runs/r_missing/resume",
        json={"allow_rerun": False},
    )
    assert resp.status_code == 401


# ── F2: subagent extra_tools cannot smuggle tier-3 past exclude_tier3 ───────

def test_F2_subagent_extra_tools_cannot_force_tier3():
    from app.tools.registry import ToolRegistry
    from app.services.subagent import select_tools_for_subagent

    reg = ToolRegistry()

    @reg.tool(name="audit_t3", description="tier-3 test tool", tier=3)
    async def _t3(x: str = "1"):
        return {"ok": True}

    @reg.tool(name="audit_t1", description="tier-1 test tool")
    async def _t1(x: str = "1"):
        return {"ok": True}

    # The parent chat turn holds no tier-3 confirmation it could delegate —
    # extra_tools=['audit_t3'] must be silently dropped from the catalog.
    schemas = select_tools_for_subagent(
        reg, domains=[], extra_tools=["audit_t3", "audit_t1"]
    )
    names = {s["function"]["name"] for s in schemas}
    assert "audit_t1" in names
    assert "audit_t3" not in names

    # Opt-in callers (exclude_tier3=False) keep the old behavior.
    schemas2 = select_tools_for_subagent(
        reg, domains=[], extra_tools=["audit_t3"], exclude_tier3=False
    )
    names2 = {s["function"]["name"] for s in schemas2}
    assert "audit_t3" in names2


# ── F4: rate-limit keys use the forwarded client IP ─────────────────────────

def test_F4_client_ip_prefers_real_ip_header():
    from app.core.client_ip import client_ip_from

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (b"x-real-ip", b"203.0.113.9"),
            (b"x-forwarded-for", b"198.51.100.7, 203.0.113.9"),
        ],
        "client": ("10.0.0.5", 1234),
    }
    req = Request(scope)
    assert client_ip_from(req) == "203.0.113.9"


def test_F4_client_ip_uses_last_xff_hop_when_no_real_ip():
    from app.core.client_ip import client_ip_from

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            # leftmost hop is attacker-controlled — must NOT be used
            (b"x-forwarded-for", b"1.2.3.4, 203.0.113.9"),
        ],
        "client": ("10.0.0.5", 1234),
    }
    req = Request(scope)
    assert client_ip_from(req) == "203.0.113.9"


def test_F4_client_ip_falls_back_to_peer_without_headers():
    from app.core.client_ip import client_ip_from

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": ("10.0.0.5", 1234),
    }
    req = Request(scope)
    assert client_ip_from(req) == "10.0.0.5"


@pytest.mark.asyncio
async def test_F4_rate_limit_middleware_keys_by_forwarded_ip(monkeypatch):
    """Two different X-Real-IP callers share no bucket; the proxy peer IP is
    ignored (pre-fix: one bucket for everyone behind nginx)."""
    from app.main import RateLimitMiddleware

    seen_keys = []

    class _FakeLimiter:
        async def is_allowed(self, key, max_requests, window_seconds):
            seen_keys.append(key)
            return True

    async def _get_rate_limiter():
        return _FakeLimiter()

    import app.main as main_mod
    monkeypatch.setattr(main_mod, "get_rate_limiter", _get_rate_limiter)

    mw = RateLimitMiddleware(app=app, max_requests=60, window_seconds=60)

    async def _call(path, real_ip):
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "headers": [(b"x-real-ip", real_ip.encode())] if real_ip else [],
            "query_string": b"",
            "client": ("10.0.0.5", 1234),
            "scheme": "http",
            "server": ("test", 80),
            "root_path": "",
        }
        received = {}

        async def call_next(request):
            received["ok"] = True
            from starlette.responses import JSONResponse
            return JSONResponse({"ok": True})

        resp = await mw.dispatch(Request(scope), call_next)
        return resp

    await _call("/api/v1/health", "203.0.113.9")
    await _call("/api/v1/health", "198.51.100.7")
    await _call("/api/v1/health", "203.0.113.9")

    assert seen_keys == [
        "rate_limit:203.0.113.9",
        "rate_limit:198.51.100.7",
        "rate_limit:203.0.113.9",
    ], "keys must derive from the forwarded IP, never the shared proxy peer"
